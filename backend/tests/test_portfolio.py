"""The lane portfolio. What it must not do is make a saving look safer than it is.

A ranking is acted on. If a lane reaches the top of it because one accounting basis
happens to favour it, someone reroutes real freight on that basis and then cannot defend
the number. So the tests here are mostly about robustness being real, and about the cost
of a saving travelling with it.
"""

import pytest

from app.core import route as route_module
from app.core.network import haversine_km
from app.core.portfolio import build_portfolio, lane_key
from app.core.report import parse_shipments
from app.core.road import RoadRoute, RoadRoutingError

HEADER = (
    "reference,origin_name,origin_lon,origin_lat,"
    "destination_name,destination_lon,destination_lat,tonnage\n"
)
GEBZE_DUSSELDORF = "A1,Gebze,29.4306,40.7889,Dusseldorf,6.7735,51.2277,24\n"
GEBZE_VIENNA = "B1,Gebze,29.4306,40.7889,Viyana,16.3738,48.2082,22\n"


@pytest.fixture(autouse=True)
def offline_road(monkeypatch):
    def fake(origin, destination):
        km = haversine_km(origin, destination) * 1.3
        return RoadRoute(distance_km=km, duration_h=km / 70, geometry=(origin, destination))

    monkeypatch.setattr(route_module, "road_route", fake)


def portfolio(csv, **kwargs):
    return build_portfolio(parse_shipments(HEADER + csv), scope="WTW", **kwargs)


def test_shipments_on_the_same_pair_collapse_into_one_lane():
    result = portfolio(GEBZE_DUSSELDORF + "A2,Gebze,29.4306,40.7889,Dusseldorf,6.7735,51.2277,18\n")

    assert len(result.lanes) == 1
    lane = result.lanes[0]
    assert lane.shipments == 2
    assert lane.tonnes == 42


def test_intensity_is_measured_on_one_yardstick():
    """Tonne-kilometres come from the all-road distance for every lane. A multimodal
    routing covers more ground for the same job, and dividing by that would read the
    detour as efficiency."""
    result = portfolio(GEBZE_DUSSELDORF)
    lane = result.lanes[0]

    assert lane.tonne_km > 0
    assert lane.intensity_kg_per_tonne_km == pytest.approx(
        lane.baseline_co2_kg / lane.tonne_km
    )


def test_a_lane_is_robust_only_when_it_wins_under_every_basis():
    """The claim a carrier can take to an auditor. Anything less is marked contested
    rather than ranked alongside it."""
    result = portfolio(GEBZE_DUSSELDORF + GEBZE_VIENNA)

    for lane in result.lanes:
        if lane.is_robust:
            assert set(lane.wins_under) == set(lane.tested_under)
        if lane.is_contested:
            assert 0 < len(lane.wins_under) < len(lane.tested_under)
        assert not (lane.is_robust and lane.is_contested)


def test_only_robust_lanes_count_towards_what_is_addressable():
    result = portfolio(GEBZE_DUSSELDORF + GEBZE_VIENNA)

    expected = sum(lane.saving_kg for lane in result.lanes if lane.is_robust)
    assert result.addressable_co2_kg == pytest.approx(expected)
    assert all(lane.is_robust for lane in result.by_abatement())


def test_the_cost_of_a_saving_travels_with_it():
    """Two days of extra transit is not free, and the allowance bill can move either
    way. Quoting the carbon alone would be the optimistic half of the answer."""
    result = portfolio(GEBZE_DUSSELDORF)
    lane = result.lanes[0]

    assert lane.extra_hours != 0, "a multimodal alternative that costs no time is suspect"
    assert hasattr(lane, "ets_delta_eur")


def test_abatement_cost_is_none_when_the_switch_also_saves_money():
    """A negative euro-per-tonne would read as a price rather than as a gain."""
    result = portfolio(GEBZE_DUSSELDORF + GEBZE_VIENNA)

    for lane in result.lanes:
        if lane.saving_kg <= 0 or lane.ets_delta_eur <= 0:
            assert lane.eur_per_tonne_abated is None
        else:
            assert lane.eur_per_tonne_abated > 0


def test_a_lane_wins_only_if_it_wins_for_every_shipment_on_it():
    """One favourable shipment must not carry a lane; the recommendation applies to the
    whole lane or it does not apply."""
    result = portfolio(GEBZE_DUSSELDORF + "A2,Gebze,29.4306,40.7889,Dusseldorf,6.7735,51.2277,1\n")
    lane = result.lanes[0]

    assert set(lane.wins_under) <= set(lane.tested_under)


def test_an_unroutable_shipment_is_reported_and_left_out_of_every_lane():
    """Silently dropping it would shrink a lane's total and flatter its intensity."""
    calls = {"n": 0}
    original = route_module.road_route

    def sometimes_failing(origin, destination):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RoadRoutingError("no road access")
        return original(origin, destination)

    import app.core.route as rm

    rm.road_route = sometimes_failing
    try:
        result = portfolio(GEBZE_DUSSELDORF + GEBZE_VIENNA)
    finally:
        rm.road_route = original

    assert result.failed
    assert sum(lane.shipments for lane in result.lanes) == 1
    assert any("rotalanamadı" in note for note in result.notes)


def test_the_rankings_answer_different_questions():
    result = portfolio(GEBZE_DUSSELDORF + GEBZE_VIENNA)

    assert [lane.baseline_co2_kg for lane in result.by_total()] == sorted(
        (lane.baseline_co2_kg for lane in result.lanes), reverse=True
    )
    assert [lane.intensity_kg_per_tonne_km for lane in result.by_intensity()] == sorted(
        (lane.intensity_kg_per_tonne_km for lane in result.lanes), reverse=True
    )


def test_the_portfolio_says_what_it_was_tested_against():
    result = portfolio(GEBZE_DUSSELDORF)

    assert len(result.tested_sets) > 1, "robustness against one basis is not robustness"
    assert result.notes
    assert lane_key(parse_shipments(HEADER + GEBZE_DUSSELDORF)[0]) == "Gebze → Dusseldorf"
