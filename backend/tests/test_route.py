import pytest

from app.core import road, route
from app.core.network import haversine_km, load_terminals
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
