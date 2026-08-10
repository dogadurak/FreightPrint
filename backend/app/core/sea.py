from dataclasses import asdict, dataclass, field
from functools import lru_cache

import searoute as sr
from shapely.geometry import LineString, box

from .cache import DiskCache
from .network import haversine_km

# searoute's network lets routes cut through the Corinth Canal, which no ro-ro or
# container ship can transit: the canal is 21 m wide at the bottom and takes vessels up
# to about 58 m beam-limited to 17.6 m. The library's `restrictions` argument cannot
# block it, because its network labels only the thirteen passages it knows about and
# Corinth is not one of them.
#
# So the canal is removed from the network instead. The single node below sits on the
# isthmus and is the only thing joining the Gulf of Corinth to the Saronic Gulf; deleting
# it forces the real route, south around the Peloponnese past Cape Malea. Measured on
# Pendik-Trieste that is 2,193 km against 2,002 through the canal, and the reference
# table says 2,500 -- so the corrected track is also the closer of the two.
CORINTH_CANAL_NODE = (22.947178, 37.960508)

# Kept as a check rather than a workaround now: with the node gone nothing should cross
# this box, and a route that does means the network changed under us.
CORINTH_CANAL_BOX = box(22.94, 37.88, 23.05, 37.98)

DEFAULT_SPEED_KNOT = 16

# searoute writes the restriction list onto its module-level graph, so a call leaks its
# restrictions into the next one. Every call here passes an explicit list for that reason.
DEFAULT_RESTRICTIONS = ("northwest",)

# Chokepoints searoute can both report and block. Naming them here keeps the risk module
# from having to know the library's vocabulary.
BLOCKABLE_PASSAGES = (
    "babalmandab",
    "bosporus",
    "gibraltar",
    "suez",
    "panama",
    "ormuz",
    "malacca",
    "sunda",
    "northwest",
)


class SeaRoutingError(RuntimeError):
    pass


@dataclass
class SeaRoute:
    distance_km: float
    duration_h: float | None
    geometry: list[list[float]]
    crosses_corinth_canal: bool
    # Chokepoints the route actually transits, straight from searoute's own edge labels.
    # Exact where a polygon test would only approximate, and free.
    passages: list[str] = field(default_factory=list)

    @property
    def is_realistic(self) -> bool:
        return not self.crosses_corinth_canal


_disk_cache: DiskCache | None = None


def _cache() -> DiskCache:
    global _disk_cache
    if _disk_cache is None:
        _disk_cache = DiskCache()
    return _disk_cache


@lru_cache(maxsize=2048)
def sea_route(
    origin: tuple[float, float],
    destination: tuple[float, float],
    speed_knot: int = DEFAULT_SPEED_KNOT,
    restrictions: tuple[str, ...] = DEFAULT_RESTRICTIONS,
) -> SeaRoute:
    """Route two (lon, lat) points by sea, keeping the track and the chokepoints crossed.

    Cached on disk as well as in process: searoute rebuilds a large graph per process and
    the answer for a fixed pair never changes.
    """
    # The version prefix retires stale tracks whenever the network is corrected: sea2
    # dropped everything computed while the Corinth Canal was still in it, sea3
    # everything from before the straits and capes were refined.
    key = (
        f"sea3|{origin[0]},{origin[1]}|{destination[0]},{destination[1]}"
        f"|{speed_knot}|{','.join(sorted(restrictions))}"
    )
    cached = _cache().get_or_compute(
        key, lambda: asdict(_query_searoute(origin, destination, speed_knot, restrictions))
    )
    return SeaRoute(**cached)


@lru_cache(maxsize=1)
def _refinements() -> dict:
    """Waypoint chains that pull a few network edges off the land they cut."""
    import json

    from .network import DATA_DIR

    with open(DATA_DIR / "sea_track_refinements.json", encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def _network():
    """searoute's own network with the Corinth Canal severed.

    Loaded once and shared: the file is 0.7 MB of GeoJSON and parsing it costs about
    half a second, which is per-process rather than per-route.
    """
    import os

    from searoute.classes.marnet import Marnet

    marnet = Marnet()
    marnet.load_geojson(os.path.join(os.path.dirname(sr.__file__), "data", "marnet_searoute.geojson"))
    if marnet.has_node(CORINTH_CANAL_NODE):
        marnet.remove_node(CORINTH_CANAL_NODE)

    # searoute's edges are straight lines between nodes as much as 170 km apart, so a
    # handful of them cut across capes and straits — the worst put 29 km of the
    # Pendik-Trieste track over the Gallipoli peninsula. Each is replaced by a chain of
    # waypoints through the water, which corrects the drawn track and the distance
    # together rather than leaving the two disagreeing.
    for chain in _refinements().values():
        start, end = tuple(chain["from"]), tuple(chain["to"])
        if not marnet.has_edge(start, end):
            continue  # the network changed; leave it alone rather than guess
        marnet.remove_edge(start, end)
        points = [start, *(tuple(p) for p in chain["via"]), end]
        for a, b in zip(points, points[1:]):
            marnet.add_edge(a, b, weight=haversine_km(a, b))

    marnet.update_kdtree()
    return marnet


def _query_searoute(
    origin: tuple[float, float],
    destination: tuple[float, float],
    speed_knot: int,
    restrictions: tuple[str, ...],
) -> SeaRoute:
    route = sr.searoute(
        origin,
        destination,
        units="km",
        speed_knot=speed_knot,
        restrictions=list(restrictions),
        return_passages=True,
        M=_network(),
    )
    properties = route["properties"]
    coordinates = route["geometry"]["coordinates"]

    # A blocked passage can leave no route at all; searoute answers with an infinite
    # length and an empty line rather than raising.
    length = properties.get("length")
    if not coordinates or length is None or length == float("inf"):
        raise SeaRoutingError(
            f"no sea route from {origin} to {destination} with {list(restrictions)} restricted"
        )

    return SeaRoute(
        distance_km=length,
        duration_h=properties.get("duration_hours"),
        geometry=[[point[0], point[1]] for point in coordinates],
        crosses_corinth_canal=LineString(coordinates).intersects(CORINTH_CANAL_BOX),
        passages=sorted(properties.get("traversed_passages") or []),
    )


def sea_distance(
    origin: tuple[float, float],
    destination: tuple[float, float],
    speed_knot: int = DEFAULT_SPEED_KNOT,
) -> SeaRoute:
    """Backwards-compatible alias kept for callers that only want the distance."""
    return sea_route(origin, destination, speed_knot)
