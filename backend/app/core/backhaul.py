"""Where the vehicles come back empty, and what could be loaded into them instead.

Empty running is the largest avoidable cost in road freight and one of the largest
avoidable emissions: a vehicle deadheading home burns nearly what it burned loaded and
carries nothing, so every tonne-kilometre it does not do makes the loaded ones look
worse. It is also, unusually, a purely **spatial** problem — the question is not "how
much empty running is there" but "what else is leaving from near where this vehicle
just finished".

What this module can and cannot see is worth stating plainly, because the temptation to
overclaim here is strong.

**It does not observe vehicles.** A shipment file records freight moving, not trucks
moving. So nothing here measures empty kilometres; it measures **flow imbalance**, which
is a property of the freight, and then says what that imbalance would imply *if* each
shipment were a dedicated vehicle round trip. That assumption is named in
`TRIPS_ARE_DEDICATED` and travels with every figure, because on a lane served by a
carrier's shared network it is wrong, and the honest form of the answer is "this is what
your own fleet would be doing" rather than "this is what is happening".

**A match is a candidate, not a plan.** Two lanes whose ends are near each other can be
paired only if the dates work, the trailer suits both loads, and the carrier is the same
or willing. None of that is in a shipment file. So the output ranks opportunities by the
empty distance they would remove and leaves the feasibility to whoever knows it.
"""

from dataclasses import dataclass, field

import requests

from . import route as routing
from .network import haversine_km
from .report import ShipmentRow
from .road import RoadRoutingError

# Every shipment is treated as one dedicated vehicle movement. Stated as a constant so
# it is visible rather than buried: a carrier running a shared network would reposition
# a vehicle across several customers and this would overstate their empty running.
TRIPS_ARE_DEDICATED = True

# How far a vehicle may reasonably reposition to pick up a return load. Beyond this the
# repositioning is its own journey rather than a short hop, and calling it a backhaul
# would flatter the saving. A convention, not a measurement — override it per fleet.
DEFAULT_REPOSITION_RADIUS_KM = 200.0

# A match has to remove meaningfully more empty distance than it creates, or it is noise
# dressed as an opportunity.
MIN_SAVING_SHARE = 0.25


@dataclass(frozen=True)
class Movement:
    """One direction of one lane: freight going one way, aggregated."""

    origin_name: str
    destination_name: str
    origin: tuple[float, float]
    destination: tuple[float, float]
    trips: int
    tonnes: float

    @property
    def key(self) -> str:
        return f"{self.origin_name} → {self.destination_name}"

    @property
    def reversed_key(self) -> str:
        return f"{self.destination_name} → {self.origin_name}"


@dataclass
class Imbalance:
    """A lane seen in both directions, and the trips that have no return load."""

    heavy: Movement
    light: Movement | None
    return_km: float
    # True when the deadhead distance is a straight line because no road route came back.
    return_is_straight_line: bool = False

    @property
    def lane(self) -> str:
        return f"{self.heavy.origin_name} ⇄ {self.heavy.destination_name}"

    @property
    def inbound_trips(self) -> int:
        return self.light.trips if self.light else 0

    @property
    def surplus_trips(self) -> int:
        """Vehicles that arrive and have nothing to carry back."""
        return max(0, self.heavy.trips - self.inbound_trips)

    @property
    def ratio(self) -> float:
        """1.0 is one-way traffic, 0.0 is perfectly balanced."""
        total = self.heavy.trips + self.inbound_trips
        return (self.heavy.trips - self.inbound_trips) / total if total else 0.0

    @property
    def empty_km(self) -> float:
        """Distance those vehicles would cover carrying nothing, under the assumption."""
        return self.surplus_trips * self.return_km

    @property
    def stranded_at(self) -> tuple[float, float]:
        """Where the empty vehicles end up — the spatial question this all turns on."""
        return self.heavy.destination

    @property
    def stranded_at_name(self) -> str:
        return self.heavy.destination_name


@dataclass
class BackhaulMatch:
    """A load leaving near where a vehicle was left empty."""

    empty_at: str
    empty_lane: str
    reload_lane: str
    reload_at: str
    reposition_km: float
    return_km: float
    trips: int
    is_straight_line: bool = False
    # Both ends of the repositioning hop, so a map can draw the thing being proposed.
    empty_at_point: tuple[float, float] = (0.0, 0.0)
    reload_at_point: tuple[float, float] = (0.0, 0.0)

    @property
    def avoided_empty_km(self) -> float:
        """Empty distance removed: the deadhead home, less the hop to the new load."""
        return max(0.0, (self.return_km - self.reposition_km) * self.trips)

    @property
    def saving_share(self) -> float:
        return (self.return_km - self.reposition_km) / self.return_km if self.return_km else 0.0


@dataclass
class BackhaulReport:
    imbalances: list[Imbalance] = field(default_factory=list)
    matches: list[BackhaulMatch] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def empty_km(self) -> float:
        return sum(item.empty_km for item in self.imbalances)

    @property
    def avoidable_empty_km(self) -> float:
        """What the matches would remove. Never more than there is to remove."""
        return min(self.empty_km, sum(match.avoided_empty_km for match in self.matches))

    @property
    def worst(self) -> Imbalance | None:
        return max(self.imbalances, key=lambda i: i.empty_km, default=None)


def _movements(shipments: list[ShipmentRow]) -> dict[str, Movement]:
    """Directed lanes, aggregated. Direction is the whole point, so it is kept."""
    grouped: dict[str, dict] = {}
    for shipment in shipments:
        key = f"{shipment.origin_name} → {shipment.destination_name}"
        entry = grouped.setdefault(
            key,
            {
                "origin_name": shipment.origin_name,
                "destination_name": shipment.destination_name,
                "origin": shipment.origin,
                "destination": shipment.destination,
                "trips": 0,
                "tonnes": 0.0,
            },
        )
        entry["trips"] += 1
        entry["tonnes"] += shipment.tonnage
    return {key: Movement(**value) for key, value in grouped.items()}


def _road_km(origin: tuple[float, float], destination: tuple[float, float]) -> tuple[float, bool]:
    """Road distance, falling back to a straight line that admits to being one.

    A straight line under-reads badly wherever geography intervenes — the Alps, the
    Marmara — so a match resting on one is flagged rather than presented alongside a
    routed figure as though the two were the same kind of number.

    Only `RoadRoutingError` is caught, and the distinction is the point. That one means
    *these two points* have no road between them — an island, a bad coordinate — which a
    straight line can stand in for. A `RequestException` means the routing server is
    unreachable, so every distance in the answer would be a straight line and every
    empty kilometre would read about a third short, while the response looked exactly
    like a real one. That is left to propagate, and the endpoint reports the outage.

    A bare `except Exception` would swallow both, and the test suite's own network guard
    with them — which is how an unsourced terrain lookup once ran in production and
    never once under test.
    """
    try:
        # Called through the module rather than by an imported name, so it goes through
        # the same seam every other module and every test in this repo already uses.
        # Importing the name directly looked identical and silently ignored the stub,
        # which meant a unit test reaching the live routing server.
        return routing.road_route(origin, destination).distance_km, False
    except RoadRoutingError:
        return haversine_km(origin, destination), True


def find_imbalances(shipments: list[ShipmentRow]) -> list[Imbalance]:
    """Lanes whose traffic runs mostly one way, worst first."""
    movements = _movements(shipments)
    seen: set[frozenset[str]] = set()

    imbalances = []
    for movement in movements.values():
        pair = frozenset({movement.origin_name, movement.destination_name})
        if pair in seen:
            continue
        seen.add(pair)

        opposite = movements.get(movement.reversed_key)
        heavy, light = movement, opposite
        if opposite and opposite.trips > movement.trips:
            heavy, light = opposite, movement
        if heavy.trips == (light.trips if light else 0):
            continue  # balanced: nothing comes back empty under this model

        return_km, indicative = _road_km(heavy.destination, heavy.origin)
        imbalances.append(
            Imbalance(heavy=heavy, light=light, return_km=return_km,
                      return_is_straight_line=indicative)
        )

    return sorted(imbalances, key=lambda i: -i.empty_km)


def find_backhauls(
    shipments: list[ShipmentRow],
    radius_km: float = DEFAULT_REPOSITION_RADIUS_KM,
) -> BackhaulReport:
    """Pair vehicles left empty with loads departing from nearby.

    The screen is by straight line and the answer by road, in that order, because the
    screen is free and the answer is an OSRM call: a candidate 190 km away as the crow
    flies may be 400 km by road around a gulf, and only the second number decides
    whether the repositioning is worth making.
    """
    report = BackhaulReport()
    report.imbalances = find_imbalances(shipments)

    movements = _movements(shipments)

    if TRIPS_ARE_DEDICATED:
        report.notes.append(
            "Her sevkiyat, kendine ait bir araç seferi sayıldı. Ortak filo işleten bir "
            "taşıyıcı aracı birden çok müşteri arasında konumlandırır; bu varsayım o "
            "durumda boş dönüşü olduğundan fazla gösterir."
        )
    report.notes.append(
        "Bu bir ölçüm değil: sevkiyat dosyası yükün hareketini kaydeder, aracınkini "
        "değil. Ölçülen şey akış dengesizliğidir; boş kilometre ondan türetilmiştir."
    )
    report.notes.append(
        f"Eşleşmeler adaydır, plan değildir. Tarihlerin, römork tipinin ve taşıyıcının "
        f"uyması gerekir — hiçbiri sevkiyat dosyasında yoktur. Yeniden konumlanma "
        f"yarıçapı {radius_km:.0f} km."
    )

    for imbalance in report.imbalances:
        empty_at = imbalance.stranded_at
        for candidate in movements.values():
            if candidate.key in {imbalance.heavy.key, imbalance.heavy.reversed_key}:
                continue
            # Free screen first; the road call is what costs.
            if haversine_km(empty_at, candidate.origin) > radius_km:
                continue

            reposition_km, indicative = _road_km(empty_at, candidate.origin)
            if reposition_km > radius_km:
                continue

            match = BackhaulMatch(
                empty_at=imbalance.stranded_at_name,
                empty_lane=imbalance.heavy.key,
                reload_lane=candidate.key,
                reload_at=candidate.origin_name,
                reposition_km=reposition_km,
                return_km=imbalance.return_km,
                trips=min(imbalance.surplus_trips, candidate.trips),
                is_straight_line=indicative,
                empty_at_point=empty_at,
                reload_at_point=candidate.origin,
            )
            if match.trips and match.saving_share >= MIN_SAVING_SHARE:
                report.matches.append(match)

    report.matches.sort(key=lambda m: -m.avoided_empty_km)
    return report
