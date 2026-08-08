import pytest

from app.core import road, route
from app.core.network import build_network, haversine_km, load_terminals
from app.core.road import RoadRoute
from app.core.route import Leg, RouteAlternative, find_route_alternatives

# Straight-line stand-in for OSRM so route selection can be tested without the network.
ROAD_DETOUR_FACTOR = 1.3
AVERAGE_ROAD_SPEED_KMH = 70.0


@pytest.fixture
def offline_road(monkeypatch):
    def fake_road_route(origin, destination):
        distance_km = haversine_km(origin, destination) * ROAD_DETOUR_FACTOR
        return RoadRoute(distance_km=distance_km, duration_h=distance_km / AVERAGE_ROAD_SPEED_KMH)

    monkeypatch.setattr(route, "road_route", fake_road_route)
    return fake_road_route


def _route(label, **distance_by_mode):
    legs = [
        Leg(mode=mode, from_name="a", to_name="b", distance_km=km)
        for mode, km in distance_by_mode.items()
    ]
    return RouteAlternative(legs=legs, label=label)


def test_dominated_route_is_dropped():
    better = _route("direct", sea=2500, road=23)
    detour = _route("overshoot", sea=2500, rail=380, road=478)

    assert route._drop_dominated([better, detour], tolerance_km=1.0) == [better]


def test_longer_sea_route_is_not_dropped_for_a_shorter_road_route():
    """A long sea leg can emit less than a short road leg, so neither may dominate."""
    all_sea = _route("sea", sea=3745, road=23)
    short_road = _route("road", road=1470)

    assert len(route._drop_dominated([all_sea, short_road], tolerance_km=1.0)) == 2


def test_routes_differing_only_within_tolerance_are_not_dropped():
    first = _route("a", sea=2500, road=20.0)
    second = _route("b", sea=2500, road=20.5)

    assert len(route._drop_dominated([first, second], tolerance_km=1.0)) == 2


def test_terminals_without_a_service_are_named_rather_than_silently_ignored():
    """A terminal no service reaches can never be routed through, so it is dead data.

    Ambarli is listed in the brief but has no service leg, and routing quietly skips it.
    This keeps that visible: connect it or drop it, do not let it look supported.
    """
    terminals = load_terminals()
    graph = build_network(terminals)
    unconnected = {terminals[node].name for node in graph.nodes if graph.degree(node) == 0}

    assert unconnected == {"Ambarli"}


def test_a_destination_off_the_network_still_gets_the_all_road_baseline(offline_road):
    """No multimodal option is an answer, not an error: the baseline always stands."""
    routes = find_route_alternatives((29.43, 40.78), (-9.14, 38.72))

    assert routes
    assert routes[0].is_all_road


def test_all_road_baseline_is_first_and_uses_only_road(offline_road):
    routes = find_route_alternatives((29.43, 40.78), (13.77, 45.64))

    assert routes[0].is_all_road
    assert routes[0].distance_by_mode.keys() == {"road"}


def test_alternatives_use_real_service_legs_only(offline_road):
    terminals = load_terminals()
    names = {terminal.name for terminal in terminals.values()}

    for alternative in find_route_alternatives((29.43, 40.78), (13.77, 45.64))[1:]:
        for leg in alternative.legs:
            if leg.mode in {"sea", "rail"}:
                assert {leg.from_name, leg.to_name} <= names
                assert leg.ref_distance_km is not None


def test_no_alternative_overshoots_the_destination(offline_road):
    """The Trieste->Lambach->back-to-Trieste style detour must not be offered."""
    destination = (13.77, 45.64)
    alternatives = find_route_alternatives((29.43, 40.78), destination)[1:]

    assert alternatives
    for alternative in alternatives:
        final_leg = alternative.legs[-1]
        assert final_leg.mode == "road"
        assert final_leg.distance_km < 100


def test_every_surviving_alternative_is_returned_for_the_caller_to_rank(offline_road):
    """Trimming here would trim by distance and could drop the cleanest option."""
    alternatives = find_route_alternatives((29.43, 40.78), (6.77, 51.22))[1:]

    assert len(alternatives) > 1
    assert alternatives == route._drop_dominated(alternatives, tolerance_km=1.0)


def _fake_osrm_response(monkeypatch, payload):
    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return payload

    monkeypatch.setattr(road.requests, "get", lambda *args, **kwargs: FakeResponse())
    road.road_route.cache_clear()


def test_unreachable_point_is_rejected_instead_of_snapped(monkeypatch):
    """OSRM answers for unreachable input by snapping to a far-away road; that must fail."""
    _fake_osrm_response(
        monkeypatch,
        {
            "code": "Ok",
            "routes": [{"distance": 2_027_000, "duration": 72_000, "legs": []}],
            "waypoints": [{"distance": 100}, {"distance": 762_500}],
        },
    )

    with pytest.raises(road.RoadRoutingError, match="no road access"):
        road.road_route((6.73, 51.45), (-21.9, 64.1))

    road.road_route.cache_clear()


def test_ferry_distance_is_separated_from_driving_distance(monkeypatch):
    """OSRM routes over ferries under the driving profile; that km is not road."""
    _fake_osrm_response(
        monkeypatch,
        {
            "code": "Ok",
            "routes": [
                {
                    "distance": 643_000,
                    "duration": 67_000,
                    "legs": [
                        {
                            "steps": [
                                {"mode": "driving", "distance": 506_000},
                                {"mode": "ferry", "distance": 137_000},
                            ]
                        }
                    ],
                }
            ],
            "waypoints": [{"distance": 50}, {"distance": 50}],
        },
    )

    result = road.road_route((25.14, 35.34), (23.72, 37.98))
    assert result.ferry_km == pytest.approx(137)
    assert result.driving_km == pytest.approx(506)

    road.road_route.cache_clear()


def test_road_time_includes_the_rest_the_law_requires():
    """Driving hours alone put Turkey to Germany inside two days; the daily rest is most
    of the difference between that and a real transit."""
    from app.core.schedule import road_elapsed_hours

    driving_only = 2515 / 70
    elapsed = road_elapsed_hours(2515)

    assert elapsed > driving_only * 2
    assert 3 <= elapsed / 24 <= 4


def test_a_short_hop_needs_no_daily_rest():
    from app.core.schedule import road_elapsed_hours

    # Under 4.5 h of driving: no break, no rest, so elapsed is just the driving.
    assert road_elapsed_hours(200) == pytest.approx(200 / 70, rel=0.01)


def test_waiting_for_a_weekly_service_is_half_the_gap():
    """Arriving at random against an even schedule, the mean wait is half the interval."""
    from app.core.schedule import expected_wait_hours

    assert expected_wait_hours(7) == pytest.approx(12)
    assert expected_wait_hours(1) == pytest.approx(84)


def test_an_unknown_frequency_adds_no_wait_rather_than_guessing():
    from app.core.schedule import expected_wait_hours

    assert expected_wait_hours(None) == 0
    assert expected_wait_hours(0) == 0


def test_a_timeline_says_which_of_its_figures_are_estimates():
    """Rail times are derived and terminal dwell is typical, not measured."""
    from app.core.schedule import build_timeline

    route = RouteAlternative(
        legs=[
            Leg("road", "Gebze", "Pendik", 20, from_id="__origin__", to_id="pendik"),
            Leg("sea", "Pendik", "Trieste", 2500, from_id="pendik", to_id="trieste"),
            Leg("rail", "Trieste", "Koln", 950, from_id="trieste", to_id="koln"),
        ],
        label="multimodal",
    )
    timeline = build_timeline(route)

    assert timeline.any_estimated
    assert timeline.notes
    # The published Pendik-Trieste crossing is 64 h and must not be re-derived.
    sea = next(s for s in timeline.steps if s.mode == "sea" and s.kind == "transit")
    assert sea.hours == 64 and not sea.is_estimated


def test_handling_and_waiting_are_counted_not_folded_into_transit():
    """Most of a multimodal disadvantage is time not spent moving."""
    from app.core.schedule import build_timeline

    route = RouteAlternative(
        legs=[
            Leg("road", "a", "Pendik", 20, from_id="__origin__", to_id="pendik"),
            Leg("sea", "Pendik", "Trieste", 2500, from_id="pendik", to_id="trieste"),
            Leg("rail", "Trieste", "Koln", 950, from_id="trieste", to_id="koln"),
        ],
        label="multimodal",
    )
    kinds = build_timeline(route).hours_by_kind

    assert kinds["dwell"] > 0
    assert kinds["wait"] > 0
    assert kinds["transit"] > kinds["dwell"] + kinds["wait"]


def test_osrm_requests_are_capped_across_every_caller():
    """A batch job pool wrapping a per-row pool was measured putting sixteen requests on
    the demo server at once. The cap belongs to the client, not to whoever calls it."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from app.core import road

    peak = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow_fetch(url):
        with lock:
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
        time.sleep(0.02)
        with lock:
            peak["now"] -= 1
        return {
            "code": "Ok",
            "routes": [{"distance": 1000.0, "duration": 60.0, "legs": [{"steps": []}]}],
            "waypoints": [{"distance": 1}, {"distance": 1}],
        }

    original = road._fetch
    road._fetch = slow_fetch
    try:
        # Two nested pools, sixteen possible in flight; the semaphore must hold the line.
        with ThreadPoolExecutor(max_workers=8) as outer:
            list(outer.map(lambda i: road._query_osrm((i, 0.0), (i + 1, 1.0)), range(24)))
    finally:
        road._fetch = original

    assert peak["max"] <= road.MAX_CONCURRENT_REQUESTS
