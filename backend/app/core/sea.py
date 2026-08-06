from dataclasses import dataclass

import searoute as sr
from shapely.geometry import LineString, box

# searoute's network lets routes cut through the Corinth Canal, which real ro-ro and
# container ships cannot transit. The library's `restrictions` argument has no option
# for it, so routes crossing this box are flagged as unrealistic instead.
CORINTH_CANAL_BOX = box(22.94, 37.88, 23.05, 37.98)


@dataclass
class SeaRoute:
    distance_km: float
    duration_h: float | None
    geometry: list[list[float]]
    crosses_corinth_canal: bool

    @property
    def is_realistic(self) -> bool:
        return not self.crosses_corinth_canal


def sea_distance(
    origin: tuple[float, float],
    destination: tuple[float, float],
    speed_knot: int = 16,
) -> SeaRoute:
    """Return the sea route between two (lon, lat) points."""
    route = sr.searoute(origin, destination, units="km", speed_knot=speed_knot)
    coordinates = route["geometry"]["coordinates"]

    return SeaRoute(
        distance_km=route["properties"]["length"],
        duration_h=route["properties"].get("duration_hours"),
        geometry=coordinates,
        crosses_corinth_canal=LineString(coordinates).intersects(CORINTH_CANAL_BOX),
    )
