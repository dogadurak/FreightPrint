import csv
import json
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import networkx as nx

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
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


def build_network(
    terminals: dict[str, Terminal] | None = None,
    service_legs: list[dict] | None = None,
) -> nx.Graph:
    terminals = terminals if terminals is not None else load_terminals()
    service_legs = service_legs if service_legs is not None else load_service_legs()

    graph = nx.Graph()
    for terminal in terminals.values():
        graph.add_node(terminal.id, terminal=terminal)

    for leg in service_legs:
        origin, destination = leg["from_terminal"], leg["to_terminal"]
        unknown = {origin, destination} - set(terminals)
        if unknown:
            raise ValueError(f"service_legs.csv references unknown terminal(s): {sorted(unknown)}")
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
        candidates = [t for t in candidates if connected_only.degree(t.id) > 0]
    return sorted(candidates, key=lambda t: haversine_km(point, t.coords))[:limit]
