import os
from dataclasses import asdict, dataclass
from functools import lru_cache

import requests

from .cache import DiskCache

# Public demo server is rate limited; point at a self-hosted OSRM for real workloads.
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
REQUEST_TIMEOUT_S = 30

# OSRM silently snaps unreachable input to the nearest road and still returns a route
# (e.g. Reykjavik snaps 762 km to the Faroe Islands), so reject implausible snaps.
MAX_SNAP_DISTANCE_KM = 100


class RoadRoutingError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoadRoute:
    distance_km: float
    duration_h: float
    ferry_km: float = 0.0
    # Simplified [lon, lat] pairs, kept so a map can draw the road actually taken
    # rather than a straight line that crosses whatever lies between.
    geometry: tuple[tuple[float, float], ...] = ()

    @property
    def driving_km(self) -> float:
        return self.distance_km - self.ferry_km


_disk_cache: DiskCache | None = None


def _cache() -> DiskCache:
    global _disk_cache
    if _disk_cache is None:
        _disk_cache = DiskCache()
    return _disk_cache


@lru_cache(maxsize=2048)
def road_route(origin: tuple[float, float], destination: tuple[float, float]) -> RoadRoute:
    """Route two (lon, lat) points by road, separating any ferry distance.

    Answers are cached on disk as well as in process, so a restart does not replay
    every OSRM call and hand the next caller another cold wait.
    """
    # The version prefix retires cached entries whose shape predates the geometry field.
    key = f"road2|{OSRM_BASE_URL}|{origin[0]},{origin[1]}|{destination[0]},{destination[1]}"
    cached = _cache().get_or_compute(key, lambda: asdict(_query_osrm(origin, destination)))
    return RoadRoute(**{**cached, "geometry": tuple(map(tuple, cached.get("geometry", ())))})


def _query_osrm(origin: tuple[float, float], destination: tuple[float, float]) -> RoadRoute:
    """Ask OSRM for a driving route, separating any ferry distance from the road distance.

    OSRM's driving profile routes over ferries, so an all-road baseline that counted
    them as road would apply a road factor to a sea crossing and overstate the saving.
    """
    coords = f"{origin[0]},{origin[1]};{destination[0]},{destination[1]}"
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coords}"

    response = requests.get(
        url,
        params={"overview": "simplified", "geometries": "geojson", "steps": "true"},
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoadRoutingError(
            f"OSRM returned no route for {origin} -> {destination}: {payload.get('code')}"
        )

    for point, waypoint in zip((origin, destination), payload["waypoints"]):
        snap_km = waypoint["distance"] / 1000.0
        if snap_km > MAX_SNAP_DISTANCE_KM:
            raise RoadRoutingError(
                f"{point} is {snap_km:,.0f} km from the nearest road; no road access"
            )

    route = payload["routes"][0]
    ferry_m = sum(
        step["distance"]
        for leg in route["legs"]
        for step in leg["steps"]
        if step.get("mode") == "ferry"
    )
    coordinates = route.get("geometry", {}).get("coordinates", [])
    return RoadRoute(
        distance_km=route["distance"] / 1000.0,
        duration_h=route["duration"] / 3600.0,
        ferry_km=ferry_m / 1000.0,
        geometry=tuple((point[0], point[1]) for point in coordinates),
    )
