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
    # Extra pricings of the same routes. Routing costs seconds and several OSRM calls;
    # pricing costs nothing, so every scenario the dashboard offers is computed once
    # here and switched client-side without another round trip.
    scenarios: list[Scenario] = Field(default_factory=list, max_length=12)


class LegOut(BaseModel):
    mode: str
    from_name: str
    to_name: str
    distance_km: float
    co2_kg: float
    duration_h: float | None = None
    factor_value: float
    factor_source: str
    # Empty for sea and rail, which have no computed geometry yet; the map draws those
    # as straight lines and says so rather than implying a surveyed track.
    geometry: list[list[float]] = []


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
