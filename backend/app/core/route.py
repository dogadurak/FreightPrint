from dataclasses import dataclass, field
from itertools import islice

import networkx as nx

from .network import Terminal, build_network, load_terminals, nearest_terminals
from .road import road_route
from .sea import SeaRoutingError, sea_route

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
    ferry_km: float = 0.0
    geometry: tuple[tuple[float, float], ...] = ()
    # Chokepoints a sea leg transits, from searoute's own edge labels.
    passages: tuple[str, ...] = ()
    # True when the track is drawable but not to be trusted as the route taken —
    # searoute's Corinth shortcut, which no ro-ro or container ship can sail.
    track_is_indicative: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def driving_km(self) -> float:
        """The part of a road leg actually spent on a road.

        OSRM reports a ferry crossing inside the driving distance, so anything charged
        to the road — its factor, a toll, a country split — has to net it out. Named to
        match `RoadRoute.driving_km`, which answers the same question one layer down.
        """
        return self.distance_km - self.ferry_km


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

    direct = road_route(origin, destination)
    graph.add_edge(
        ORIGIN_NODE,
        DESTINATION_NODE,
        mode="road",
        distance_km=direct.distance_km,
        duration_h=direct.duration_h,
        ferry_km=direct.ferry_km,
        geometry=direct.geometry,
        # Which end the geometry was computed from. The graph is undirected, so an edge
        # is stored once and can be walked either way; without this the track comes back
        # in whichever direction it happened to be routed. It draws the same line either
        # way, which is why this went unseen until the journey player walked along it and
        # the truck jumped to the destination and drove backwards to the terminal.
        geometry_from=ORIGIN_NODE,
    )

    for endpoint_node, point in ((ORIGIN_NODE, origin), (DESTINATION_NODE, destination)):
        for terminal in nearest_terminals(point, terminals, candidate_terminals, connected_only=graph):
            leg = road_route(point, terminal.coords)
            graph.add_edge(
                endpoint_node,
                terminal.id,
                mode="road",
                distance_km=leg.distance_km,
                duration_h=leg.duration_h,
                ferry_km=leg.ferry_km,
                geometry=leg.geometry,
                # Routed outward from the endpoint, including for the destination, where
                # the leg is actually travelled terminal-to-door.
                geometry_from=endpoint_node,
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

    # Walk the track the way the leg is travelled. An undirected edge holds one geometry
    # and both directions use it, so half of them would otherwise run backwards.
    geometry = edge.get("geometry", ())
    if geometry and edge.get("geometry_from") not in (None, from_node):
        geometry = tuple(reversed(geometry))

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
        ferry_km=edge.get("ferry_km", 0.0),
        geometry=geometry,
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


def _add_sea_tracks(
    route: RouteAlternative, terminals: dict[str, Terminal], compare_distances: bool
) -> None:
    """Attach each sea leg's track and the chokepoints it crosses.

    The distance stays the reference one — searoute's is unusable wherever it cuts the
    Corinth Canal. The track is still worth keeping: it is what the map draws and what
    risk zones are intersected against, and an approximate line is enough for both.
    """
    for leg in route.legs:
        if leg.mode != "sea" or not leg.from_id or not leg.to_id:
            continue
        try:
            computed = sea_route(terminals[leg.from_id].coords, terminals[leg.to_id].coords)
        except SeaRoutingError as error:
            # A leg without a track still has its reference distance, so the alternative
            # stays usable; only the map line and the risk check are lost.
            leg.notes.append(f"sea track unavailable for {leg.from_name}->{leg.to_name}: {error}")
            continue

        leg.geometry = tuple((point[0], point[1]) for point in computed.geometry)
        leg.passages = tuple(computed.passages)
        if compare_distances:
            leg.computed_distance_km = computed.distance_km
        if not computed.is_realistic:
            leg.track_is_indicative = True
            leg.notes.append(
                f"{leg.from_name}->{leg.to_name}: hesaplanan deniz izi Korint Kanalı'ndan "
                "geçiyor; gerçek gemiler geçemez, bu yüzden çizim göstergeseldir "
                "(mesafe referans tablodan alınır)"
            )


def find_route_alternatives(
    origin: tuple[float, float],
    destination: tuple[float, float],
    origin_name: str = "origin",
    destination_name: str = "destination",
    candidate_terminals: int = 3,
    compare_computed_distances: bool = False,
    with_sea_tracks: bool = True,
    dominance_tolerance_km: float = 1.0,
) -> list[RouteAlternative]:
    """Find multimodal alternatives between two arbitrary (lon, lat) points.

    The all-road option is always returned first as the comparison baseline. Routes that
    lose to another alternative in every mode (detours that overshoot the destination and
    double back) are dropped rather than shown as choices.

    Every surviving alternative is returned. Trimming the list here would trim it by
    distance, and distance ranks these badly: a leg 20 km longer can emit a third less
    by moving the journey off the road. Callers rank and trim on what they care about.
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

    surviving = _drop_dominated(multimodal, dominance_tolerance_km)
    if with_sea_tracks or compare_computed_distances:
        for route in surviving:
            _add_sea_tracks(route, terminals, compare_computed_distances)

    return ([all_road] if all_road else []) + surviving
