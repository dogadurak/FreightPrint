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


class FactorSetOut(BaseModel):
    name: str
    scopes: list[str]
    sea_factor_by_scope: dict[str, float]
    source: str
    is_verified: bool
    description: str


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
    load_uncertainty: float = Field(default=0.0, ge=0, lt=1)
    distance_uncertainty: float = Field(default=0.05, ge=0, lt=1)
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
    # Empty where no track was computed; the map draws those as straight schematic
    # lines. A track that is drawable but not sailable is flagged instead of hidden.
    geometry: list[list[float]] = []
    track_is_indicative: bool = False


class RangeOut(BaseModel):
    low_co2_kg: float
    high_co2_kg: float
    confidence: float


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
