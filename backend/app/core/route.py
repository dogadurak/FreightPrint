from dataclasses import dataclass, field
from itertools import islice

import networkx as nx

from .network import Terminal, build_network, load_terminals, nearest_terminals
from .road import road_distance
from .sea import sea_distance

ORIGIN_NODE = "__origin__"
DESTINATION_NODE = "__destination__"
PATH_SEARCH_LIMIT = 30


@dataclass
class Leg:
    mode: str
    from_name: str
    to_name: str
    distance_km: float
    from_id: str | None = None
    to_id: str | None = None
    duration_h: float | None = None
    ref_distance_km: float | None = None
    computed_distance_km: float | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class RouteAlternative:
    legs: list[Leg]
    label: str

    @property
    def total_distance_km(self) -> float:
        return sum(leg.distance_km for leg in self.legs)

    @property
    def is_all_road(self) -> bool:
        return all(leg.mode == "road" for leg in self.legs)

    @property
    def distance_by_mode(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for leg in self.legs:
            totals[leg.mode] = totals.get(leg.mode, 0.0) + leg.distance_km
        return totals

    @property
    def notes(self) -> list[str]:
        return [note for leg in self.legs for note in leg.notes]


def _build_routing_graph(
    origin: tuple[float, float],
    destination: tuple[float, float],
    terminals: dict[str, Terminal],
    candidate_terminals: int,
) -> nx.Graph:
    graph = build_network(terminals)
    for _, _, data in graph.edges(data=True):
        data["distance_km"] = data["ref_distance_km"]

    graph.add_node(ORIGIN_NODE)
    graph.add_node(DESTINATION_NODE)

    direct_km, direct_h = road_distance(origin, destination)
    graph.add_edge(
        ORIGIN_NODE, DESTINATION_NODE, mode="road", distance_km=direct_km, duration_h=direct_h
    )

    for endpoint_node, point in ((ORIGIN_NODE, origin), (DESTINATION_NODE, destination)):
        for terminal in nearest_terminals(point, terminals, candidate_terminals, connected_only=graph):
            distance_km, duration_h = road_distance(point, terminal.coords)
            graph.add_edge(
                endpoint_node,
                terminal.id,
                mode="road",
                distance_km=distance_km,
                duration_h=duration_h,
            )
    return graph


def _leg_from_edge(
    graph: nx.Graph,
    terminals: dict[str, Terminal],
    from_node: str,
    to_node: str,
    endpoint_names: dict[str, str],
) -> Leg:
    edge = graph.edges[from_node, to_node]

    def name(node: str) -> str:
        return endpoint_names.get(node) or terminals[node].name

    return Leg(
        mode=edge["mode"],
        from_name=name(from_node),
        to_name=name(to_node),
        distance_km=edge["distance_km"],
        from_id=from_node,
        to_id=to_node,
        duration_h=edge.get("duration_h"),
        ref_distance_km=edge.get("ref_distance_km"),
        computed_distance_km=edge["distance_km"] if edge["mode"] == "road" else None,
    )


def _dominates(candidate: RouteAlternative, other: RouteAlternative, tolerance_km: float) -> bool:
    """True if `candidate` is at least as short in every mode and strictly shorter in one.

    Distance alone cannot rank alternatives here: a long sea leg can emit far less than
    a short road leg, so a dominated route is dropped only when it loses mode by mode.
    """
    candidate_km, other_km = candidate.distance_by_mode, other.distance_by_mode
    modes = set(candidate_km) | set(other_km)
    return all(
        candidate_km.get(mode, 0.0) <= other_km.get(mode, 0.0) + tolerance_km for mode in modes
    ) and any(candidate_km.get(mode, 0.0) < other_km.get(mode, 0.0) - tolerance_km for mode in modes)


def _drop_dominated(routes: list[RouteAlternative], tolerance_km: float) -> list[RouteAlternative]:
    return [
        route
        for route in routes
        if not any(_dominates(other, route, tolerance_km) for other in routes if other is not route)
    ]


def _add_computed_sea_distances(route: RouteAlternative, terminals: dict[str, Terminal]) -> None:
    for leg in route.legs:
        if leg.mode != "sea":
            continue
        computed = sea_distance(terminals[leg.from_id].coords, terminals[leg.to_id].coords)
        leg.computed_distance_km = computed.distance_km
        if not computed.is_realistic:
            leg.notes.append(
                f"searoute route {leg.from_name}->{leg.to_name} crosses the Corinth Canal; "
                "computed distance is not usable"
            )


def find_route_alternatives(
    origin: tuple[float, float],
    destination: tuple[float, float],
    origin_name: str = "origin",
    destination_name: str = "destination",
    candidate_terminals: int = 3,
    max_alternatives: int = 3,
    compare_computed_distances: bool = False,
    dominance_tolerance_km: float = 1.0,
) -> list[RouteAlternative]:
    """Find multimodal alternatives between two arbitrary (lon, lat) points.

    The all-road option is always returned first as the comparison baseline. Routes that
    lose to another alternative in every mode (detours that overshoot the destination and
    double back) are dropped rather than shown as choices.
    """
    terminals = load_terminals()
    graph = _build_routing_graph(origin, destination, terminals, candidate_terminals)
    endpoint_names = {ORIGIN_NODE: origin_name, DESTINATION_NODE: destination_name}

    all_road: RouteAlternative | None = None
    multimodal: list[RouteAlternative] = []

    paths = nx.shortest_simple_paths(graph, ORIGIN_NODE, DESTINATION_NODE, weight="distance_km")
    for path in islice(paths, PATH_SEARCH_LIMIT):
        legs = [
            _leg_from_edge(graph, terminals, a, b, endpoint_names) for a, b in zip(path, path[1:])
        ]
        terminal_names = [terminals[node].name for node in path[1:-1]]
        route = RouteAlternative(legs=legs, label=" -> ".join(terminal_names) or "all-road")

        if route.is_all_road:
            if all_road is None and len(path) == 2:
                all_road = route
        else:
            multimodal.append(route)

    surviving = _drop_dominated(multimodal, dominance_tolerance_km)[:max_alternatives]
    if compare_computed_distances:
        for route in surviving:
            _add_computed_sea_distances(route, terminals)

    return ([all_road] if all_road else []) + surviving
