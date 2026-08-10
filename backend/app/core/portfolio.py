"""A file of shipments read as a portfolio of lanes, and where acting on it pays.

The bulk report answers "what did this shipment emit". A carrier with thousands of
movements has a different question: **which lanes are worth changing, and what would
changing them cost me?** That is a ranking problem, not a calculation one, and it needs
three things the per-shipment view does not give.

First, tonne-kilometres. Totals reward long lanes and volume; intensity is what says a
lane is run badly. Both are here because acting on the worst intensity is pointless if
it carries two shipments a year.

Second, the trade-off. A saving that costs two days of transit is not free, and it may
change the allowance bill in either direction, so the time and the ETS delta travel
beside every abatement figure rather than being quoted separately.

Third, and this is the part only this engine can offer: **whether the saving survives a
change of accounting basis.** The same corridor can save carbon under one published
factor set and lose it under another — that is the finding this whole project rests on.
A lane whose advantage holds under every basis is one a carrier can act on and defend.
A lane that only wins on one is a lane that will be argued about in an audit, and it is
marked as such rather than ranked alongside the others.
"""

from dataclasses import dataclass, field
from typing import Callable

from .cost import CostInputError, calculate_ets
from .emissions import (
    DEFAULT_SCOPE,
    FactorNotFoundError,
    calculate_shipment,
    load_emission_factors,
    lowest_emission_first,
)
from .network import load_terminals
from .report import ShipmentRow
from .road import RoadRoutingError
from .route import find_route_alternatives
from .schedule import build_timeline

# Only sets that can price a whole route are worth testing a lane against; one that
# errors on a leg tells you nothing about robustness.
CANDIDATE_FACTOR_SETS = ("glec", "glec_accompanied", "glec_freight_average")


@dataclass
class LaneOption:
    """One routing of a lane, priced under one factor set."""

    label: str
    is_all_road: bool
    co2_kg: float
    hours: float
    ets_eur: float


@dataclass
class Lane:
    key: str
    origin_name: str
    destination_name: str
    shipments: int = 0
    tonnes: float = 0.0
    tonne_km: float = 0.0
    baseline_co2_kg: float = 0.0
    best_co2_kg: float = 0.0
    baseline_hours: float = 0.0
    best_hours: float = 0.0
    baseline_ets_eur: float = 0.0
    best_ets_eur: float = 0.0
    best_label: str = ""
    # Factor sets under which the best option beats the all-road baseline.
    wins_under: list[str] = field(default_factory=list)
    tested_under: list[str] = field(default_factory=list)

    @property
    def intensity_kg_per_tonne_km(self) -> float:
        return self.baseline_co2_kg / self.tonne_km if self.tonne_km else 0.0

    @property
    def saving_kg(self) -> float:
        return self.baseline_co2_kg - self.best_co2_kg

    @property
    def extra_hours(self) -> float:
        return self.best_hours - self.baseline_hours

    @property
    def ets_delta_eur(self) -> float:
        return self.best_ets_eur - self.baseline_ets_eur

    @property
    def is_robust(self) -> bool:
        """True when the alternative beats the baseline under every basis tested.

        The one claim a carrier can take to an auditor. A lane that wins under some
        bases and not others is a lane whose saving depends on how you count.
        """
        return bool(self.tested_under) and len(self.wins_under) == len(self.tested_under)

    @property
    def is_contested(self) -> bool:
        return bool(self.wins_under) and not self.is_robust

    @property
    def eur_per_tonne_abated(self) -> float | None:
        """Allowance cost of each tonne of CO2 saved, where the switch costs anything.

        None when the switch also lowers the allowance bill: there is no cost to divide,
        and a negative figure here would read as a price rather than as a gain.
        """
        if self.saving_kg <= 0 or self.ets_delta_eur <= 0:
            return None
        return self.ets_delta_eur / (self.saving_kg / 1000)


@dataclass
class Portfolio:
    lanes: list[Lane]
    scope: str
    factor_set: str
    tested_sets: list[str]
    failed: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_co2_kg(self) -> float:
        return sum(lane.baseline_co2_kg for lane in self.lanes)

    @property
    def addressable_co2_kg(self) -> float:
        """What the lanes that hold up under every basis would save between them."""
        return sum(lane.saving_kg for lane in self.lanes if lane.is_robust)

    def by_total(self) -> list[Lane]:
        return sorted(self.lanes, key=lambda lane: -lane.baseline_co2_kg)

    def by_intensity(self) -> list[Lane]:
        return sorted(self.lanes, key=lambda lane: -lane.intensity_kg_per_tonne_km)

    def by_abatement(self) -> list[Lane]:
        """Where to act first: the robust savings, largest first."""
        return sorted(
            (lane for lane in self.lanes if lane.is_robust and lane.saving_kg > 0),
            key=lambda lane: -lane.saving_kg,
        )


def lane_key(shipment: ShipmentRow) -> str:
    return f"{shipment.origin_name} → {shipment.destination_name}"


def _options(routes, shipment, scope, factor_set, terminals) -> list[LaneOption]:
    """Every alternative for one shipment, priced and timed under one basis."""
    priced = calculate_shipment(
        routes, tonnage=shipment.tonnage, scope=scope, factor_set=factor_set
    )
    options = []
    for route, emission in lowest_emission_first(routes, priced):
        country = lambda node: terminals[node].country if node in terminals else None
        pairs = []
        for leg in route.legs:
            if leg.mode == "road" and leg.ferry_km > 0:
                pairs.append((country(leg.from_id), country(leg.to_id)))
            pairs.append((country(leg.from_id), country(leg.to_id)))
        try:
            ets = calculate_ets(emission, pairs).cost_eur
        except CostInputError:
            ets = 0.0
        options.append(
            LaneOption(
                label=emission.label,
                is_all_road=route.is_all_road,
                co2_kg=emission.total_co2_kg,
                hours=build_timeline(route).total_hours,
                ets_eur=ets,
            )
        )
    return options


def build_portfolio(
    shipments: list[ShipmentRow],
    scope: str = DEFAULT_SCOPE,
    factor_set: str = "glec",
    factor_sets: tuple[str, ...] = CANDIDATE_FACTOR_SETS,
    on_progress: Callable[[int], None] | None = None,
) -> Portfolio:
    """Group shipments into lanes and rank where changing them pays.

    Each shipment is routed once — that is the expensive part — and then priced under
    every basis, which costs nothing. Robustness comes free from that.
    """
    factors = load_emission_factors()
    terminals = load_terminals()
    tested = [s for s in factor_sets if any(f.factor_set == s for f in factors)]

    lanes: dict[str, Lane] = {}
    failed: list[tuple[str, str]] = []

    for done, shipment in enumerate(shipments, start=1):
        try:
            routes = find_route_alternatives(
                origin=shipment.origin,
                destination=shipment.destination,
                origin_name=shipment.origin_name,
                destination_name=shipment.destination_name,
            )
            primary = _options(routes, shipment, scope, factor_set, terminals)
        except (RoadRoutingError, LookupError, ValueError) as error:
            failed.append((shipment.reference, str(error)))
            if on_progress:
                on_progress(done)
            continue

        baseline = next((o for o in primary if o.is_all_road), None)
        best = next((o for o in primary if not o.is_all_road), None)
        if baseline is None or best is None:
            # A lane with no alternative has nothing to decide; it still counts towards
            # the total, so it is kept rather than dropped.
            baseline = baseline or primary[0]
            best = baseline

        key = lane_key(shipment)
        lane = lanes.setdefault(
            key,
            Lane(key=key, origin_name=shipment.origin_name,
                 destination_name=shipment.destination_name, tested_under=list(tested)),
        )
        lane.shipments += 1
        lane.tonnes += shipment.tonnage
        # Tonne-kilometres are measured on the all-road distance so every lane is on
        # one yardstick: a multimodal routing covers more ground for the same job, and
        # dividing by that would make the detour look like efficiency.
        yardstick = next((r for r in routes if r.is_all_road), routes[0])
        lane.tonne_km += shipment.tonnage * yardstick.total_distance_km
        lane.baseline_co2_kg += baseline.co2_kg
        lane.best_co2_kg += best.co2_kg
        # Hours describe the routing, not the volume, so they are held rather than
        # summed: every shipment on a lane takes the same road.
        lane.baseline_hours = baseline.hours
        lane.best_hours = best.hours
        lane.baseline_ets_eur += baseline.ets_eur
        lane.best_ets_eur += best.ets_eur
        lane.best_label = best.label

        # The same routes under every other basis, to see whether the advantage holds.
        wins = []
        for candidate in tested:
            try:
                options = _options(routes, shipment, scope, candidate, terminals)
            except (FactorNotFoundError, ValueError):
                continue
            base = next((o for o in options if o.is_all_road), None)
            alt = next((o for o in options if not o.is_all_road), None)
            if base and alt and alt.co2_kg < base.co2_kg:
                wins.append(candidate)
        # A lane wins under a basis only if it wins there for every shipment on it.
        lane.wins_under = sorted(set(wins) & set(lane.wins_under or wins))

        if on_progress:
            on_progress(done)

    notes = [
        "Yoğunluk, tam karayolu senaryosunun ton-km başına emisyonudur — hattın nasıl "
        "işletildiğini toplamdan daha iyi gösterir, ama az sevkiyatlı bir hatta göre "
        "hareket etmenin karşılığı azdır.",
        "Bir hattın kazancı yalnızca **test edilen her faktör esası altında** da "
        "kazanıyorsa 'dayanıklı' sayılır. Tek bir esasta kazanan hat, denetimde "
        "tartışılacak hattır.",
        "Süre ve ETS farkı kazancın bedelidir; iki gün uzayan bir transit bedelsiz "
        "değildir.",
    ]
    if failed:
        notes.append(f"{len(failed)} sevkiyat rotalanamadı ve hiçbir hatta sayılmadı.")

    return Portfolio(
        lanes=list(lanes.values()), scope=scope, factor_set=factor_set,
        tested_sets=tested, failed=failed, notes=notes,
    )
