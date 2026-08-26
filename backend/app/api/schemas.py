from pydantic import BaseModel, Field


class Point(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)

    def as_tuple(self) -> tuple[float, float]:
        return (self.lon, self.lat)


class TerminalOut(BaseModel):
    id: str
    name: str
    country: str
    type: str
    lon: float
    lat: float
    is_connected: bool
    # The outside record this place is tied to. Empty for the four Turkish terminals,
    # which appear in none of the European reference sets this project can reach — the
    # same gap Eurostat and RINF already showed.
    source: str = ""
    source_id: str = ""


class RoadFuelOut(BaseModel):
    """One road fuel a set can price with.

    Listed rather than left for the caller to guess: the names are data, they change
    when the factor file does, and a caller who guesses "diesel" or "electric" gets an
    error naming eleven alternatives. `is_default` marks the one a request that names
    no fuel receives.
    """

    fuel_type: str
    label: str
    factor_by_scope: dict[str, float]
    is_verified: bool
    is_default: bool


class FactorSetOut(BaseModel):
    name: str
    scopes: list[str]
    sea_factor_by_scope: dict[str, float]
    source: str
    is_verified: bool
    description: str
    road_fuels: list[RoadFuelOut] = []


class Scenario(BaseModel):
    factor_set: str
    scope: str = Field(default="TTW", pattern="^(TTW|WTW)$")


class RouteRequest(BaseModel):
    origin: Point
    destination: Point
    origin_name: str = "origin"
    destination_name: str = "destination"
    tonnage: float = Field(default=24.0, gt=0, le=100_000)
    scope: str = Field(default="TTW", pattern="^(TTW|WTW)$")
    factor_set: str = "reference"
    road_fuel_type: str | None = None
    # Left unset, each factor keeps the utilisation its publisher assumed.
    load_factor: float | None = Field(default=None, gt=0, le=1)
    empty_return_share: float | None = Field(default=None, ge=0, le=1)
    # Refrigerated cargo. Priced against the clock rather than the odometer, so it is
    # orthogonal to the factor set and applies identically across every scenario.
    is_reefer: bool = False
    load_uncertainty: float = Field(default=0.0, ge=0, lt=1)
    # None means "use the per-mode table", which is the point of having one.
    #
    # This defaulted to 0.05 and so switched off the feature the README describes at
    # length: every band the dashboard ever drew was a flat five per cent on every mode.
    # The measured difference is not cosmetic — the all-road band on the pilot corridor
    # is 7.0% flat against 2.6% per-mode, because road distance is computed here by OSRM
    # and checked against 30 reported ones, while sea and rail come from a reference
    # table with one independent check between them. A single figure is simultaneously
    # too wide for road and too narrow for sea, which is the reason the table exists.
    distance_uncertainty: float | None = Field(default=None, ge=0, lt=1)
    max_alternatives: int | None = Field(default=None, ge=1, le=20)
    carbon_price_eur: float = Field(default=80.0, ge=0, le=10_000)
    ets_year: int = Field(default=2026, ge=2024, le=2100)
    # Extra pricings of the same routes. Routing costs seconds and several OSRM calls;
    # pricing costs nothing, so every scenario the dashboard offers is computed once
    # here and switched client-side without another round trip.
    scenarios: list[Scenario] = Field(default_factory=list, max_length=12)


class ZoneCrossingOut(BaseModel):
    id: str
    name: str
    source: str
    distance_km: float


class RouteRiskOut(BaseModel):
    """Route-level, not scenario-level: exposure follows the track, not the factor set."""

    is_exposed: bool
    distance_in_zones_km: float
    zones: list[ZoneCrossingOut] = []
    passages: list[str] = []
    # Sea distance with no track to test. Reported so a clear result cannot be mistaken
    # for a checked one.
    untracked_sea_km: float = 0.0


class EtsLegOut(BaseModel):
    from_name: str
    to_name: str
    co2_kg: float
    coverage_share: float
    cost_eur: float


class EtsCostOut(BaseModel):
    carbon_price_eur: float
    year: int
    covered_tonnes: float
    cost_eur: float
    legs: list[EtsLegOut] = []
    notes: list[str] = []


class ScheduleStepOut(BaseModel):
    kind: str
    mode: str | None = None
    label: str
    hours: float
    start_h: float
    is_estimated: bool = False
    # The published timetable behind these hours, empty where the row names none.
    source: str = ""


class TimelineOut(BaseModel):
    """Door-to-door time, split into moving, being handled, and waiting to depart.

    Route-level like risk: the clock does not change with the emission factor set.
    """

    total_hours: float
    total_days: float
    hours_by_kind: dict[str, float]
    steps: list[ScheduleStepOut] = []
    notes: list[str] = []


class LegOut(BaseModel):
    mode: str
    from_name: str
    to_name: str
    distance_km: float
    co2_kg: float
    duration_h: float | None = None
    factor_value: float
    factor_source: str
    # Where the *distance* came from, which is a separate question from where the factor
    # did. On this corridor every sea leg's kilometres are the carrier's own figure and
    # none had a second opinion until NGA Pub. 151 was imported; the reader could not see
    # that from the number.
    distance_source: str = ""
    distance_is_verified: bool = False
    # Empty where no track was computed; the map draws those as straight schematic
    # lines. A track that is drawable but not sailable is flagged instead of hidden.
    geometry: list[list[float]] = []
    track_is_indicative: bool = False


class EmptyRunningOut(BaseModel):
    """What Eurostat observes on the countries this route crosses, and how much of it.

    The rate never travels without its coverage. A weighted mean quietly taken over part
    of a journey and presented as the journey's rate is the kind of number that survives
    until somebody checks it — and on the pilot corridor almost a third of the road
    distance is in countries that do not report at all.
    """

    observed_share: float
    coverage: float
    covered_km: float
    total_km: float
    per_country: dict[str, float] = {}
    # Country ISO to kilometres with no observation behind them.
    missing_km: dict[str, float] = {}
    is_representative: bool
    # What the chosen factor assumes, for comparison.
    factor_share: float | None = None
    ratio: float | None = None
    verdict: str | None = None
    source: str = ""
    notes: list[str] = []


class DistanceBasisOut(BaseModel):
    """What one mode's distance spread rests on, said out loud.

    `is_measured` is the field that matters. Road's spread comes from comparing 30
    reported distances against independently routed ones — that is accuracy. Rail's comes
    from nowhere: sample size zero, carried over from sea because the two share a source
    table, and the data file calls it "a placeholder that happens to be non-zero rather
    than a finding". Both used to arrive at the caller as an identical anonymous number.
    """

    mode: str
    relative: float
    sample_size: int
    is_measured: bool
    basis: str


class SeaDistanceOut(BaseModel):
    """One sea leg's distance, as the carrier gives it and as NGA Pub. 151 surveys it.

    Reported beside the engine's figure and never substituted for it, on the same footing
    as the Eurostat empty-running survey and the EU MRV fleet. The two answer different
    questions — what this service is planned at, and how far the ports are apart — and
    collapsing them would lose the disagreement, which is the part worth reading.
    """

    from_terminal: str
    to_terminal: str
    engine_km: float
    published_km: float
    delta_pct: float
    # The published figure and the hop added to reach a terminal Pub 151 does not list,
    # kept apart so a reader can see how much of the comparison is survey.
    published_nm: float
    estimated_nm: float
    estimated_share: float
    is_representative: bool
    via: str = ""
    source_line: str = ""


class RangeOut(BaseModel):
    low_co2_kg: float
    high_co2_kg: float
    confidence: float
    # The provenance of the band, per mode. A range assembled partly from an unmeasured
    # spread must not look like one that was measured throughout.
    bases: list[DistanceBasisOut] = []
    unmeasured_modes: list[str] = []


class AlternativeOut(BaseModel):
    label: str
    is_all_road: bool
    total_distance_km: float
    total_co2_kg: float
    distance_by_mode: dict[str, float]
    co2_by_mode: dict[str, float]
    all_road_co2_kg: float | None = None
    saving_co2_kg: float | None = None
    tree_equivalent: dict[str, float] = {}
    legs: list[LegOut]
    emission_range: RangeOut | None = None
    risk: RouteRiskOut | None = None
    timeline: TimelineOut | None = None
    # Observed empty running for the countries this alternative crosses. Route-level
    # because it follows the geography: an all-road run through seven countries and a
    # multimodal one through two are not exposed to the same haulage.
    empty_running: "EmptyRunningOut | None" = None
    # The sea factor against the verified EU fleet. Present only where this alternative
    # actually sails: an all-road option has no sea factor to check.
    sea_factor: "SeaFactorOut | None" = None
    # Per sea leg, so a route with two crossings reports both.
    sea_distances: list[SeaDistanceOut] = []
    reefer: "ReeferOut | None" = None
    total_with_reefer_co2_kg: float | None = None
    playback: "PlaybackOut | None" = None
    notes: list[str] = []


class ReeferOut(BaseModel):
    """Refrigeration's own emissions, kept apart from the transport figure.

    Deliberately additive rather than folded into `total_co2_kg`: the transport number
    comes from published GLEC tables, this one is derived, and merging them would hide
    which half rests on assumption. `stationary_co2_kg` is the part a per-kilometre
    model cannot see — the unit still drawing while the box waits for a sailing.
    """

    co2_kg: float
    stationary_co2_kg: float
    co2_by_kind: dict[str, float]
    hours: float
    source: str
    is_verified: bool
    warnings: list[str] = []


class PlaybackSegmentOut(BaseModel):
    kind: str
    mode: str | None = None
    label: str
    start_h: float
    hours: float
    co2_kg: float
    reefer_co2_kg: float = 0.0
    geometry: list[list[float]] = []
    is_estimated: bool = False
    track_is_indicative: bool = False


class PlaybackOut(BaseModel):
    """The journey laid out so a client can play it back.

    Segments run back to back with no gaps, so any hour on the clock lands in exactly
    one of them. Carbon is attributed to the segment that produced it rather than
    spread evenly over elapsed time — a truck parked at a terminal burns nothing, and
    an animation that kept the counter climbing through an eighteen-hour handling stop
    would be lying in a way nobody watching could catch.
    """

    segments: list[PlaybackSegmentOut]
    total_hours: float
    total_co2_kg: float
    total_reefer_co2_kg: float = 0.0
    stationary_hours: float = 0.0


class CountryTollOut(BaseModel):
    iso: str
    country: str
    distance_km: float
    co2_kg: float
    cost_eur: float
    priced: bool
    reason: str = ""


class TollOut(BaseModel):
    """The CO2 component of the road tolls a route attracts, country by country.

    Not the toll: infrastructure, noise and air pollution are the larger part of a
    German bill and none of them follow carbon. Countries that charge by CO2 class
    without publishing a per-tonne rate appear unpriced with the reason attached, never
    as zero.
    """

    countries: list[CountryTollOut]
    total_eur: float
    priced_co2_kg: float
    unpriced_co2_kg: float
    notes: list[str] = []


class ScenarioTotalOut(BaseModel):
    """One alternative priced under one scenario. Geometry is deliberately absent —
    it is identical across scenarios and already carried by `alternatives`."""

    label: str
    is_all_road: bool
    total_co2_kg: float
    co2_by_mode: dict[str, float]
    saving_co2_kg: float | None = None
    emission_range: RangeOut | None = None
    ets: EtsCostOut | None = None
    reefer: ReeferOut | None = None
    total_with_reefer_co2_kg: float | None = None
    co2_toll: TollOut | None = None
    total_cost_eur: float | None = None
    total_hours: float | None = None
    tradeoff_tags: list[str] = []
    # The same route repriced with the road legs at the empty running Eurostat actually
    # observes on the countries it crosses, instead of the share the factor assumes.
    # Absent where no observation covers enough of the route to stand for it.
    co2_at_observed_empty_kg: float | None = None


class ScenarioOut(BaseModel):
    factor_set: str
    scope: str
    sources: list[str]
    is_verified: bool
    totals: list[ScenarioTotalOut]
    warnings: list[str] = []
    error: str | None = None


class RouteResponse(BaseModel):
    factor_set: str
    scope: str
    tonnage: float
    sources: list[str]
    alternatives: list[AlternativeOut]
    warnings: list[str] = []
    scenarios: list[ScenarioOut] = []


class CompareRequest(BaseModel):
    """Two sailings of the same voyage: one direct, one avoiding a chokepoint.

    This is the Red Sea question the sea-freight interview raised, asked in the general
    form — any blockable passage, any port pair, so the layer does not depend on the
    pilot corridor.
    """

    origin: Point
    destination: Point
    origin_name: str = "origin"
    destination_name: str = "destination"
    origin_country: str | None = None
    destination_country: str | None = None
    tonnage: float = Field(default=24.0, gt=0, le=100_000)
    scope: str = Field(default="TTW", pattern="^(TTW|WTW)$")
    factor_set: str = "glec"
    avoid: list[str] = Field(default_factory=lambda: ["suez", "babalmandab"], max_length=6)
    carbon_price_eur: float = Field(default=80.0, ge=0, le=10_000)
    ets_year: int = Field(default=2026, ge=2024, le=2100)
    # What the carrier actually charged for the diversion. Nothing here derives it:
    # premiums are negotiated against hull value and never published.
    surcharge_eur: float = Field(default=0.0, ge=0)


class SailingOut(BaseModel):
    label: str
    distance_km: float
    duration_h: float | None
    co2_kg: float
    ets_eur: float
    risk: RouteRiskOut
    geometry: list[list[float]] = []
    unreachable: str | None = None


class CompareResponse(BaseModel):
    factor_set: str
    scope: str
    tonnage: float
    avoided: list[str]
    direct: SailingOut
    diverted: SailingOut
    # None when the diversion is impossible: there is no second sailing to subtract, and
    # a zero would read as "the reroute is free" rather than "there is no reroute".
    extra_distance_km: float | None = None
    extra_duration_h: float | None = None
    extra_co2_kg: float | None = None
    extra_ets_eur: float | None = None
    surcharge_eur: float = 0.0
    total_extra_eur: float | None = None
    avoided_zone_km: float | None = None


class JobOut(BaseModel):
    """A background run's state. The file itself is fetched separately once done."""

    id: str
    status: str
    total: int
    done: int
    progress: float
    filename: str
    error: str | None = None


class PlaceOut(BaseModel):
    name: str
    lon: float
    lat: float
    kind: str = ""


class CatchmentCellOut(BaseModel):
    lon: float
    lat: float
    terminal_id: str
    duration_h: float


class CatchmentOut(BaseModel):
    """Which terminal serves where, by road time rather than by straight line.

    `spacing_deg` is part of the answer, not a detail: the cells are samples, and the
    boundary between two of them was never measured. A client that draws a smooth
    polygon over this claims a precision that does not exist.
    """

    cells: list[CatchmentCellOut]
    spacing_deg: float
    bounds: tuple[float, float, float, float]
    max_duration_h: float
    sampled: int
    unreachable: int
    cells_by_terminal: dict[str, int]
    notes: list[str] = []


class LaneOut(BaseModel):
    """One lane of the portfolio, with the cost of acting on it beside the gain."""

    key: str
    origin_name: str
    destination_name: str
    origin_lon: float
    origin_lat: float
    destination_lon: float
    destination_lat: float
    shipments: int
    tonnes: float
    tonne_km: float
    intensity_kg_per_tonne_km: float
    baseline_co2_kg: float
    best_co2_kg: float
    best_label: str
    saving_kg: float
    extra_hours: float
    ets_delta_eur: float
    eur_per_tonne_abated: float | None = None
    # Which accounting bases the alternative actually beats the road baseline under.
    # A lane that wins under all of them can be acted on and defended; one that wins
    # under some is flagged rather than ranked beside it.
    wins_under: list[str] = []
    tested_under: list[str] = []
    is_robust: bool = False
    is_contested: bool = False
    empty_miles_risk: bool = False
    imbalance_ratio: float = 0.0
    consolidation_potential: bool = False


class CarrierStatsOut(BaseModel):
    carrier: str
    shipments: int
    tonnes: float
    tonne_km: float
    total_co2_kg: float
    intensity_kg_per_tonne_km: float


class GlidepathOut(BaseModel):
    """Two computed states of the portfolio, and one lever priced from the factor file.

    There is no target here. A reduction target is the sum of specific levers with
    specific costs and dates; the figure this replaced was `best_scenario * 0.70`, a
    percentage chosen rather than derived, and it is the kind of number a
    sustainability reviewer discards the whole report over.
    """

    baseline_co2_kg: float
    best_scenario_co2_kg: float
    # Absent when the chosen factor set carries no HVO row — never silently zero.
    road_on_hvo_co2_kg: float | None = None
    hvo_fuel: str | None = None
    hvo_source: str | None = None
    hvo_is_verified: bool | None = None


class PortfolioOut(BaseModel):
    lanes: list[LaneOut]
    scope: str
    factor_set: str
    tested_sets: list[str]
    total_co2_kg: float
    addressable_co2_kg: float
    carriers: list[CarrierStatsOut] = []
    glidepath: GlidepathOut | None = None
    failed: list[list[str]] = []
    notes: list[str] = []


class ConformanceCheckOut(BaseModel):
    id: str
    clause: str
    requirement: str
    status: str
    evidence: str
    gap: str = ""
    is_blocking: bool = False


class ConformanceOut(BaseModel):
    """A self-assessment against ISO 14083 — explicitly not a certification.

    The gaps are the product. Two of them can never close from this engine's own data:
    hub emissions are not computed at all, and every factor is a published default where
    the standard ranks operator-measured fuel above them. Both are reported as absent
    rather than dropped from the checklist.
    """

    factor_set: str
    scope: str
    verdict: str
    verdict_tr: str
    data_quality: float
    data_quality_note: str
    checks: list[ConformanceCheckOut]
    notes: list[str] = []


class CriticalityOut(BaseModel):
    """One piece of the network, ranked by what losing it costs."""

    id: str
    name: str
    kind: str  # "terminal" | "leg"
    severity: str  # "stranded" | "rerouted" | "no-effect"
    stranded: int
    rerouted: int
    extra_co2_kg: float
    # Signed on purpose: the route is chosen by lowest emissions, not by time, so a
    # forced alternative can genuinely be quicker while emitting more.
    extra_hours: float
    # Present for a terminal closure, so the map can colour the point that failed.
    terminal_id: str | None = None
    lon: float | None = None
    lat: float | None = None


class VulnerabilityOut(BaseModel):
    """What the network costs when each of its pieces stops working, worst first."""

    scope: str
    factor_set: str
    shipments: int
    lanes: int
    ranked: list[CriticalityOut]
    # Terminals the chosen routes actually depend on. A closure only matters where
    # traffic goes, so this is what separates a critical node from a merely central one.
    depended_on: list[str] = []
    notes: list[str] = []


class ImbalanceOut(BaseModel):
    """A lane whose traffic runs mostly one way, and where that leaves the vehicles."""

    lane: str
    heavy_direction: str
    outbound_trips: int
    inbound_trips: int
    surplus_trips: int
    ratio: float
    return_km: float
    empty_km: float
    stranded_at: str
    lon: float
    lat: float


class BackhaulMatchOut(BaseModel):
    """A load leaving near where a vehicle was left empty. A candidate, not a plan."""

    empty_at: str
    empty_lane: str
    reload_at: str
    reload_lane: str
    reposition_km: float
    return_km: float
    trips: int
    avoided_empty_km: float
    saving_share: float
    empty_lon: float
    empty_lat: float
    reload_lon: float
    reload_lat: float
    # True when no road route could be computed and a straight line stood in. Flagged
    # rather than blended: a straight line under-reads wherever geography intervenes.
    is_straight_line: bool = False


class BackhaulOut(BaseModel):
    shipments: int
    empty_km: float
    avoidable_empty_km: float
    imbalances: list[ImbalanceOut] = []
    matches: list[BackhaulMatchOut] = []
    notes: list[str] = []


class HubAssignmentOut(BaseModel):
    reference: str
    lane: str
    tonnes: float
    hub_id: str | None = None
    hub_name: str
    is_consolidated: bool
    direct_vehicle_km: float
    collection_vehicle_km: float


class HubSiteOut(BaseModel):
    id: str
    name: str
    lon: float
    lat: float
    shipments: int


class HubPlanOut(BaseModel):
    """Where a consolidation hub belongs, measured in vehicle-kilometres.

    Not tonne-kilometres: going via anywhere is at least as far as going direct, so on
    tonne-km the triangle inequality makes every hub a loss and the model would open
    none. Consolidation pays by *sharing the trunk leg*, which only a model counting
    vehicles can see.
    """

    is_optimal: bool
    opened: list[HubSiteOut] = []
    assignments: list[HubAssignmentOut] = []
    direct_vehicle_km: float
    planned_vehicle_km: float
    saved_vehicle_km: float
    saved_share: float
    capacity_tonnes: float
    # Absent where no road factor matched the chosen basis — never a zero standing in.
    saved_co2_kg: float | None = None
    notes: list[str] = []


class SeaFactorOut(BaseModel):
    """The sea factor this route priced with, against the ships that actually sail it.

    EU MRV publishes verified annual CO2 and transport work for every vessel over
    5,000 GT calling at an EEA port. It is the only independent observation of the ro-ro
    figure in this engine, and until it was imported nothing here could check that number
    at all.

    Always reported on the TTW basis whatever the request selected, because MRV measures
    fuel burned: holding a well-to-wake factor against it would charge the observation
    for fuel production it never saw. `compared_row` names the row actually used.
    """

    factor: float
    compared_row: str
    compared_scope: str
    factor_source: str
    year: int
    ships: int
    median: float
    q1: float
    q3: float
    ratio: float
    spread: float
    share_below: float
    verdict: str
    # False where the factor describes traffic the observed fleet does not carry — the
    # accompanied row, whose traffic largely sails ro-pax, and ro-pax reports no
    # mass-based transport work at all.
    is_comparable: bool
    ship_types: dict[str, int] = {}
    observed_source: str = ""
    notes: list[str] = []
