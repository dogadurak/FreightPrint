"""Where a road leg actually is, country by country.

The engine has always known a leg's endpoints and never what it crosses in between, and
several things a carrier cares about live in the middle. Germany prices a truck's carbon
in its toll; Austria and Czechia charge by emission class; the allowance rules turn on
whether a movement is inside the EEA. All of those need kilometres per country, not a
pair of endpoints.

The split is geometric: the route's own track intersected with country polygons. That
makes it as good as the track — OSRM's road geometry is faithful, so a road leg splits
well; a sea leg's track is a network path and is not offered here at all, because
territorial waters are not what these polygons describe.

Lengths use the same cos(latitude) correction as the risk module rather than a plain
degree count, which at 50°N would overstate east-west distance by half.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from math import cos, radians

from shapely.geometry import LineString, shape
from shapely.strtree import STRtree

from .network import DATA_DIR

# One kilometre of simplification on the boundaries. A border is not where a truck's
# toll liability changes to the metre anyway — the operator's own toll record is — so
# the extra precision would be false comfort at ten times the file size.
BOUNDARY_TOLERANCE_KM = 1.0

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class CountryLeg:
    iso: str
    name: str
    distance_km: float


def _length_km(line: LineString) -> float:
    """Length of a lon/lat line in kilometres, corrected for the meridian convergence."""
    total = 0.0
    points = list(line.coords)
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        mid = radians((y1 + y2) / 2)
        dx = (x2 - x1) * cos(mid)
        dy = y2 - y1
        total += ((dx * dx + dy * dy) ** 0.5) * 111.32
    return total


@lru_cache(maxsize=1)
def _countries():
    with open(DATA_DIR / "countries.geojson", encoding="utf-8") as f:
        collection = json.load(f)
    shapes, meta = [], []
    for feature in collection["features"]:
        shapes.append(shape(feature["geometry"]))
        meta.append((feature["properties"]["iso"], feature["properties"]["name"]))
    return shapes, meta, STRtree(shapes)


def distance_by_country(geometry) -> list[CountryLeg]:
    """Split a track into the countries it runs through, longest first.

    Distance that falls in no country — sea crossings inside a road leg, or a stretch
    outside the bundled extent — is reported under an empty ISO code rather than
    silently attributed to a neighbour. A toll or an allowance charged on a kilometre
    nobody can place is worse than an admitted gap.
    """
    points = [tuple(p) for p in geometry]
    if len(points) < 2:
        return []

    line = LineString(points)
    shapes, meta, tree = _countries()

    totals: dict[str, float] = {}
    names: dict[str, str] = {}
    placed = 0.0
    for index in tree.query(line):
        piece = line.intersection(shapes[index])
        if piece.is_empty:
            continue
        iso, name = meta[index]
        km = sum(
            _length_km(part)
            for part in (piece.geoms if hasattr(piece, "geoms") else [piece])
            if isinstance(part, LineString)
        )
        if km <= 0:
            continue
        totals[iso] = totals.get(iso, 0.0) + km
        names[iso] = name
        placed += km

    unplaced = _length_km(line) - placed
    if unplaced > 1.0:
        totals[""] = unplaced
        names[""] = "yerleştirilemedi"

    return sorted(
        (CountryLeg(iso=iso, name=names[iso], distance_km=km) for iso, km in totals.items()),
        key=lambda leg: -leg.distance_km,
    )


def road_distance_by_country(route) -> list[CountryLeg]:
    """The same, for every road leg of a route taken together.

    The **shares** come from the geometry; the **total** comes from the leg's own
    distance. OSRM returns a simplified polyline, which is about eight percent shorter
    than the road it stands for, so measuring the drawn line would quietly lose two
    hundred kilometres of a Turkey-Germany run — and every toll and allowance computed
    from it with them. Scaling the geometric proportions onto the leg's real distance
    keeps both right.

    Road only. A sea leg's track comes from a shipping network rather than a survey and
    these polygons describe land borders, not territorial waters, so splitting one would
    produce a number that looks authoritative and means nothing.

    Ferry distance is taken out for the same reason. OSRM's driving profile routes over
    ferries and reports the crossing inside a road leg's distance, but a trailer on a
    ship is not on anybody's road: `expand_route_legs` already charges those kilometres
    at the sea factor, so counting them here would spread a road leg's carbon over a
    distance that includes the crossing and hand the countries either side of it a share
    of a toll no authority levies.
    """
    totals: dict[str, float] = {}
    names: dict[str, str] = {}
    for leg in route.legs:
        if leg.mode != "road" or not leg.geometry:
            continue
        parts = distance_by_country(leg.geometry)
        drawn = sum(part.distance_km for part in parts)
        if drawn <= 0 or leg.driving_km <= 0:
            continue
        scale = leg.driving_km / drawn
        for part in parts:
            totals[part.iso] = totals.get(part.iso, 0.0) + part.distance_km * scale
            names[part.iso] = part.name

    return sorted(
        (CountryLeg(iso=iso, name=names[iso], distance_km=km) for iso, km in totals.items()),
        key=lambda leg: -leg.distance_km,
    )
