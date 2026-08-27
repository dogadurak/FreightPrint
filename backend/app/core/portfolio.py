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
    DEFAULT_FACTOR_SET,
    DEFAULT_SCOPE,
    FactorNotFoundError,
    calculate_shipment,
    find_factor,
    leg_countries,
    load_emission_factors,
    lowest_emission_first,
)
from .report import ShipmentRow
from .road import RoadRoutingError
from .route import find_route_alternatives
from .schedule import build_timeline

# Only sets that can price a whole route are worth testing a lane against; one that
# errors on a leg tells you nothing about robustness.
CANDIDATE_FACTOR_SETS = ("glec", "glec_accompanied", "glec_freight_average")

# The feedstock the fuel lever is priced on. Named rather than left implicit: HVO runs
# from 14% to 73% of diesel depending on what it was made from, so "HVO" without a
# feedstock is not a number.
HVO_LEVER_FUEL = "hvo_uco"

# Thresholds for *flagging an opportunity to look at* — never for changing a carbon
# figure. They are conventions, not measurements, and they are named here so a reader
# can disagree with them: a 24 t trailer is treated as full-load, so a lane averaging
# under three quarters of that is carrying air worth consolidating, and a lane where
# three in four movements run one way has a return-leg problem worth asking about.
# Both need at least a few movements before the average means anything.
LTL_TONNES = 18.0
IMBALANCE_RATIO = 0.75
MIN_MOVEMENTS_FOR_IMBALANCE = 3


@dataclass
class LaneOption:
    """One routing of a lane, priced under one factor set."""

    label: str
    is_all_road: bool
    co2_kg: float
    hours: float
    ets_eur: float
    road_co2_kg: float = 0.0


@dataclass
class Lane:
    key: str
    origin_name: str
    destination_name: str
    origin_lon: float = 0.0
    origin_lat: float = 0.0
    destination_lon: float = 0.0
    destination_lat: float = 0.0
    shipments: int = 0
    tonnes: float = 0.0
    tonne_km: float = 0.0
    baseline_co2_kg: float = 0.0
    best_co2_kg: float = 0.0
    baseline_hours: float = 0.0
    best_hours: float = 0.0
    baseline_ets_eur: float = 0.0
    best_ets_eur: float = 0.0
    best_road_co2_kg: float = 0.0
    best_label: str = ""
    # Factor sets under which the best option beats the all-road baseline.
    wins_under: list[str] = field(default_factory=list)
    tested_under: list[str] = field(default_factory=list)
    empty_miles_risk: bool = False
    imbalance_ratio: float = 0.0

    @property
    def consolidation_potential(self) -> bool:
        """A lane worth asking about: more than one movement, averaging under a full load."""
        return self.shipments > 1 and (self.tonnes / self.shipments) < LTL_TONNES

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
class CarrierStats:
    carrier: str
    shipments: int = 0
    tonnes: float = 0.0
    tonne_km: float = 0.0
    total_co2_kg: float = 0.0
    
    @property
    def intensity_kg_per_tonne_km(self) -> float:
        return self.total_co2_kg / self.tonne_km if self.tonne_km else 0.0


@dataclass
class Portfolio:
    lanes: list[Lane]
    scope: str
    factor_set: str
    tested_sets: list[str]
    failed: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    carriers: list[CarrierStats] = field(default_factory=list)
    glidepath: dict[str, float] = field(default_factory=dict)

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


def _options(routes, shipment, scope, factor_set) -> list[LaneOption]:
    """Every alternative for one shipment, priced and timed under one basis."""
    priced = calculate_shipment(
        routes, tonnage=shipment.tonnage, scope=scope, factor_set=factor_set
    )
    options = []
    for route, emission in lowest_emission_first(routes, priced):
        try:
            ets = calculate_ets(emission, leg_countries(route)).cost_eur
        except CostInputError:
            ets = 0.0
        options.append(
            LaneOption(
                label=emission.label,
                is_all_road=route.is_all_road,
                co2_kg=emission.total_co2_kg,
                # Carried separately because the fuel levers only reach the road legs:
                # a target that scaled the whole total would credit a biofuel switch
                # with reducing the ship's emissions too.
                road_co2_kg=emission.co2_by_mode.get("road", 0.0),
                hours=build_timeline(route).total_hours,
                ets_eur=ets,
            )
        )
    return options


def build_portfolio(
    shipments: list[ShipmentRow],
    scope: str = DEFAULT_SCOPE,
    factor_set: str = DEFAULT_FACTOR_SET,
    factor_sets: tuple[str, ...] = CANDIDATE_FACTOR_SETS,
    on_progress: Callable[[int], None] | None = None,
) -> Portfolio:
    """Group shipments into lanes and rank where changing them pays.

    Each shipment is routed once — that is the expensive part — and then priced under
    every basis, which costs nothing. Robustness comes free from that.
    """
    factors = load_emission_factors()
    tested = [s for s in factor_sets if any(f.factor_set == s for f in factors)]

    lanes: dict[str, Lane] = {}
    carriers: dict[str, CarrierStats] = {}
    failed: list[tuple[str, str]] = []

    for done, shipment in enumerate(shipments, start=1):
        try:
            routes = find_route_alternatives(
                origin=shipment.origin,
                destination=shipment.destination,
                origin_name=shipment.origin_name,
                destination_name=shipment.destination_name,
            )
            primary = _options(routes, shipment, scope, factor_set)
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
                 destination_name=shipment.destination_name, 
                 origin_lon=shipment.origin[0], origin_lat=shipment.origin[1],
                 destination_lon=shipment.destination[0], destination_lat=shipment.destination[1],
                 tested_under=list(tested)),
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
        lane.best_road_co2_kg += best.road_co2_kg
        lane.best_label = best.label

        # The same routes under every other basis, to see whether the advantage holds.
        wins = []
        for candidate in tested:
            try:
                options = _options(routes, shipment, scope, candidate)
            except (FactorNotFoundError, ValueError):
                continue
            base = next((o for o in options if o.is_all_road), None)
            alt = next((o for o in options if not o.is_all_road), None)
            if base and alt and alt.co2_kg < base.co2_kg:
                wins.append(candidate)
        lane.wins_under = sorted(set(wins) & set(lane.wins_under or wins))

        # Carrier stats
        carrier_name = getattr(shipment, "carrier", "Unknown")
        c_stats = carriers.setdefault(carrier_name, CarrierStats(carrier=carrier_name))
        c_stats.shipments += 1
        c_stats.tonnes += shipment.tonnage
        c_stats.tonne_km += shipment.tonnage * yardstick.total_distance_km
        c_stats.total_co2_kg += baseline.co2_kg

        if on_progress:
            on_progress(done)

    # Empty Miles Anomaly Detection
    for lane in lanes.values():
        inverse_key = f"{lane.destination_name} → {lane.origin_name}"
        inverse_lane = lanes.get(inverse_key)
        outbound = lane.shipments
        inbound = inverse_lane.shipments if inverse_lane else 0
        total = outbound + inbound
        if total > 0:
            lane.imbalance_ratio = outbound / total
            if (lane.imbalance_ratio > IMBALANCE_RATIO
                    and outbound >= MIN_MOVEMENTS_FOR_IMBALANCE):
                lane.empty_miles_risk = True

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

    carriers_list = sorted(carriers.values(), key=lambda c: -c.intensity_kg_per_tonne_km)
    
    baseline_total = sum(lane.baseline_co2_kg for lane in lanes.values())
    best_total = sum(lane.best_co2_kg for lane in lanes.values())
    best_road_total = sum(lane.best_road_co2_kg for lane in lanes.values())

    # This third figure used to be `best_total * 0.70` — a 30% reduction "by 2030 via
    # biofuels", chosen rather than computed. A reduction target is the sum of specific
    # levers, and an engine that already carries HVO factors by feedstock has no excuse
    # for guessing at one. So the lever is priced from the factor file instead, on the
    # road legs alone, because that is all a fuel switch can reach.
    glidepath = {
        "baseline_co2_kg": baseline_total,
        "best_scenario_co2_kg": best_total,
    }
    try:
        diesel = find_factor(factors, "road", scope=scope, factor_set=factor_set)
        hvo = find_factor(
            factors, "road", scope=scope, fuel_type=HVO_LEVER_FUEL, factor_set=factor_set
        )
    except FactorNotFoundError:
        notes.append(
            f"{factor_set} setinde HVO satırı yok; yakıt değişimi kaldıracı hesaplanmadı."
        )
    else:
        switched = best_road_total * (hvo.value / diesel.value)
        glidepath["road_on_hvo_co2_kg"] = best_total - best_road_total + switched
        glidepath["hvo_fuel"] = HVO_LEVER_FUEL
        glidepath["hvo_source"] = hvo.source
        glidepath["hvo_is_verified"] = hvo.is_verified
        notes.append(
            f"Yakıt kaldıracı, çok modlu senaryonun karayolu bacakları {hvo.label} ile "
            f"fiyatlanarak hesaplandı. Seçilmiş bir hedef değil; besleme stoğu "
            f"değiştiğinde sonuç da değişir."
        )

    return Portfolio(
        lanes=list(lanes.values()), scope=scope, factor_set=factor_set,
        tested_sets=tested, failed=failed, notes=notes,
        carriers=carriers_list, glidepath=glidepath
    )
