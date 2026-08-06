import os
from functools import lru_cache

import requests

# Public demo server is rate limited; point at a self-hosted OSRM for real workloads.
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
REQUEST_TIMEOUT_S = 30

# OSRM silently snaps unreachable input to the nearest road and still returns a route
# (e.g. Reykjavik snaps 762 km to the Faroe Islands), so reject implausible snaps.
MAX_SNAP_DISTANCE_KM = 100


class RoadRoutingError(RuntimeError):
    pass


@lru_cache(maxsize=2048)
def road_distance(
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> tuple[float, float]:
    """Return (distance_km, duration_h) by road between two (lon, lat) points."""
    coords = f"{origin[0]},{origin[1]};{destination[0]},{destination[1]}"
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coords}"

    response = requests.get(url, params={"overview": "false"}, timeout=REQUEST_TIMEOUT_S)
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
    return route["distance"] / 1000.0, route["duration"] / 3600.0
