"""Whether a route enters a war-risk area, and by how much.

The premium itself is not computed here. Rates are negotiated per vessel against hull
value and are not published, so the brief makes the rate a user input. What this module
supplies is the part that is computable: which listed areas a route enters, how far it
runs inside them, and which chokepoints it transits.
"""

import json
from dataclasses import dataclass, field
from functools import lru_cache
from math import cos, hypot, radians
from pathlib import Path

from shapely.geometry import LineString, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .network import DATA_DIR
from .route import RouteAlternative

# Degrees of latitude per kilometre, for turning a clipped line's length into km. Good
# to a few percent away from the poles, which is far finer than polygons drawn by hand.
KM_PER_DEGREE = 111.32


@dataclass(frozen=True)
class RiskZone:
    id: str
    name: str
    zone_type: str
    source: str
    valid_from: str | None
    valid_to: str | None
    chokepoints: tuple[str, ...]
    geometry: BaseGeometry


@dataclass
class ZoneCrossing:
    zone: RiskZone
    distance_km: float

    @property
    def name(self) -> str:
        return self.zone.name


@dataclass
class RouteRisk:
    """What a route meets on the way, per zone and in total."""

    crossings: list[ZoneCrossing] = field(default_factory=list)
    passages: list[str] = field(default_factory=list)
    untracked_sea_km: float = 0.0
    # Listed areas overlap — the southern Red Sea and the Gulf of Aden share a corner —
    # so the distance actually spent inside any of them is the union, not the sum of the
    # per-zone figures below. Summing those would report a ship as exposed for longer
    # than its voyage is.
    distance_in_zones_km: float = 0.0

    @property
    def is_exposed(self) -> bool:
        return bool(self.crossings)

    @property
    def zone_names(self) -> list[str]:
        return [crossing.name for crossing in self.crossings]


@lru_cache(maxsize=1)
def load_risk_zones(path: Path | None = None) -> tuple[RiskZone, ...]:
    path = path or DATA_DIR / "risk_zones.geojson"
    with open(path, encoding="utf-8") as f:
        collection = json.load(f)

    return tuple(
        RiskZone(
            id=feature["properties"]["id"],
            name=feature["properties"]["name"],
            zone_type=feature["properties"]["zone_type"],
            source=feature["properties"]["source"],
            valid_from=feature["properties"].get("valid_from"),
            valid_to=feature["properties"].get("valid_to"),
            chokepoints=tuple(feature["properties"].get("chokepoints", [])),
            geometry=shape(feature["geometry"]),
        )
        for feature in collection["features"]
    )


def _length_km(geometry: BaseGeometry) -> float:
    """Approximate length of a lon/lat line in kilometres.

    Longitude degrees shrink towards the poles, so each segment is scaled by the cosine
    of its own latitude rather than treating the plane as square.
    """
    lines = (
        [geometry]
        if geometry.geom_type == "LineString"
        else list(getattr(geometry, "geoms", []))
    )
    total = 0.0
    for line in lines:
        if line.is_empty or line.geom_type != "LineString":
            continue
        points = list(line.coords)
        for (lon1, lat1), (lon2, lat2) in zip(points, points[1:]):
            mid_lat = radians((lat1 + lat2) / 2)
            total += hypot((lon2 - lon1) * cos(mid_lat), lat2 - lat1) * KM_PER_DEGREE
    return total


def assess_route(
    route: RouteAlternative, zones: tuple[RiskZone, ...] | None = None
) -> RouteRisk:
    """Intersect a route's sea tracks with the listed areas it passes through.

    Only sea legs are tested: the listed areas are maritime, and a road leg's exposure is
    a different question this project does not answer. A sea leg with no track is
    reported as unassessed rather than assumed safe.
    """
    zones = zones if zones is not None else load_risk_zones()
    risk = RouteRisk()
    by_zone: dict[str, float] = {}
    union = unary_union([zone.geometry for zone in zones]) if zones else None

    for leg in route.legs:
        if leg.mode != "sea":
            continue
        risk.passages.extend(p for p in leg.passages if p not in risk.passages)

        if len(leg.geometry) < 2:
            risk.untracked_sea_km += leg.distance_km
            continue

        track = LineString([(point[0], point[1]) for point in leg.geometry])
        for zone in zones:
            if not track.intersects(zone.geometry):
                continue
            by_zone[zone.id] = by_zone.get(zone.id, 0.0) + _length_km(
                track.intersection(zone.geometry)
            )
        if union is not None and track.intersects(union):
            risk.distance_in_zones_km += _length_km(track.intersection(union))

    lookup = {zone.id: zone for zone in zones}
    risk.crossings = [
        ZoneCrossing(zone=lookup[zone_id], distance_km=distance)
        for zone_id, distance in sorted(by_zone.items(), key=lambda item: -item[1])
    ]
    risk.passages.sort()
    return risk
