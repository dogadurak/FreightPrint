import csv
import os
from functools import lru_cache
import json
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import networkx as nx

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Where the route and geocode caches are written. Kept separate from DATA_DIR because
# that directory holds reference data — factors, terminals, risk zones — that ships with
# the code and must be replaced when the code is. A deployment that mounted a volume
# over DATA_DIR to keep its cache would pin the reference data too, and a corrected
# emission factor would never reach it. Defaults alongside the data for local work.
CACHE_DIR = Path(os.environ.get("FREIGHTPRINT_CACHE_DIR", DATA_DIR))

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Terminal:
    id: str
    name: str
    country: str
    type: str
    lon: float
    lat: float

    @property
    def coords(self) -> tuple[float, float]:
        return (self.lon, self.lat)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = radians(a[0]), radians(a[1])
    lon2, lat2 = radians(b[0]), radians(b[1])
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(h))


def load_terminals(path: Path | None = None) -> dict[str, Terminal]:
    path = path or DATA_DIR / "terminals.geojson"
    with open(path, encoding="utf-8") as f:
        collection = json.load(f)

    terminals = {}
    for feature in collection["features"]:
        props = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"]
        terminals[props["id"]] = Terminal(
            id=props["id"],
            name=props["name"],
            country=props["country"],
            type=props["type"],
            lon=lon,
            lat=lat,
        )
    return terminals


def load_service_legs(path: Path | None = None) -> list[dict]:
    path = path or DATA_DIR / "service_legs.csv"
    with open(path, encoding="utf-8") as f:
        return [
            {
                "from_terminal": row["from_terminal"],
                "to_terminal": row["to_terminal"],
                "mode": row["mode"],
                "ref_distance_km": float(row["ref_distance_km"]),
            }
            for row in csv.DictReader(f)
        ]


@dataclass(frozen=True)
class ServiceSchedule:
    """A service's published timetable, where the operator publishes one.

    Both fields are optional because most of these corridors do not publish. Absent is
    kept distinct from zero so a missing timetable is never read as "no wait".
    """

    transit_hours: float | None
    frequency_per_week: float | None
    source: str


@lru_cache(maxsize=1)
def load_service_schedules(path: Path | None = None) -> dict[tuple[str, str], ServiceSchedule]:
    """Schedules keyed both ways round: a service runs in both directions."""
    path = path or DATA_DIR / "service_legs.csv"
    schedules: dict[tuple[str, str], ServiceSchedule] = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            schedule = ServiceSchedule(
                transit_hours=float(row["transit_hours"]) if row.get("transit_hours") else None,
                frequency_per_week=(
                    float(row["frequency_per_week"]) if row.get("frequency_per_week") else None
                ),
                source=row.get("schedule_source", ""),
            )
            origin, destination = row["from_terminal"], row["to_terminal"]
            schedules[(origin, destination)] = schedule
            schedules[(destination, origin)] = schedule
    return schedules


def build_network(
    terminals: dict[str, Terminal] | None = None,
    service_legs: list[dict] | None = None,
    closed_terminals: frozenset[str] = frozenset(),
    suspended_legs: frozenset[tuple[str, str]] = frozenset(),
) -> nx.Graph:
    """The service network, optionally with parts of it taken out.

    The exclusions are how disruption is modelled: a terminal that has stopped handling
    and a service that has stopped sailing are the two things that actually happen, and
    both are answered by rebuilding the graph without them and routing again. Doing it
    here rather than by deleting from a built graph keeps one description of what the
    network is, so a disrupted run and a normal one cannot drift apart.

    A closed terminal is removed rather than merely disconnected, so nothing can route
    *through* it or hand it a shipment as an endpoint.
    """
    terminals = terminals if terminals is not None else load_terminals()
    service_legs = service_legs if service_legs is not None else load_service_legs()

    unknown_closures = closed_terminals - set(terminals)
    if unknown_closures:
        raise ValueError(f"cannot close unknown terminal(s): {sorted(unknown_closures)}")

    graph = nx.Graph()
    for terminal in terminals.values():
        if terminal.id in closed_terminals:
            continue
        graph.add_node(terminal.id, terminal=terminal)

    # Keyed both ways round: a service runs in both directions, so suspending it has to
    # suspend it in both. Naming only one would leave a one-way corridor nobody operates.
    suspended = {frozenset(pair) for pair in suspended_legs}

    for leg in service_legs:
        origin, destination = leg["from_terminal"], leg["to_terminal"]
        unknown = {origin, destination} - set(terminals)
        if unknown:
            raise ValueError(f"service_legs.csv references unknown terminal(s): {sorted(unknown)}")
        if {origin, destination} & closed_terminals or frozenset((origin, destination)) in suspended:
            continue
        graph.add_edge(
            origin,
            destination,
            mode=leg["mode"],
            ref_distance_km=leg["ref_distance_km"],
            computed_distance_km=None,
        )
    return graph


def nearest_terminals(
    point: tuple[float, float],
    terminals: dict[str, Terminal],
    limit: int = 3,
    connected_only: nx.Graph | None = None,
) -> list[Terminal]:
    candidates = terminals.values()
    if connected_only is not None:
        # `in` before `degree`: a closed terminal is not a node at all, and asking a
        # graph for the degree of something it does not contain raises rather than
        # answering zero.
        candidates = [
            t for t in candidates
            if t.id in connected_only and connected_only.degree(t.id) > 0
        ]
    return sorted(candidates, key=lambda t: haversine_km(point, t.coords))[:limit]
