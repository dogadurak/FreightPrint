"""Empty running, and the loads that could fill it.

The line these tests keep drawing is between what a shipment file *records* and what it
*implies*. It records freight moving. It does not record vehicles, so nothing here can
measure empty kilometres — and a module that quietly presented an implication as a
measurement would be the most persuasive wrong answer in the whole engine.
"""

import pytest

from app.core import route as route_module
from app.core.backhaul import (
    DEFAULT_REPOSITION_RADIUS_KM,
    find_backhauls,
    find_imbalances,
)
from app.core.network import haversine_km
from app.core.report import ShipmentRow
from app.core.road import RoadRoute

GEBZE = (29.4306, 40.7889)
DUSSELDORF = (6.7735, 51.2277)
KOLN = (6.9603, 50.9375)      # ~40 km from Düsseldorf: a plausible reload point
MILAN = (9.19, 45.46)         # far enough to be its own journey


@pytest.fixture(autouse=True)
def offline_road(monkeypatch):
    def fake(origin, destination):
        km = haversine_km(origin, destination) * 1.3
        return RoadRoute(distance_km=km, duration_h=km / 70, geometry=(origin, destination))

    monkeypatch.setattr(route_module, "road_route", fake)


def _ship(reference, origin, origin_name, destination, destination_name, tonnage=24.0):
    return ShipmentRow(
        reference=reference, carrier="t",
        origin=origin, destination=destination,
        origin_name=origin_name, destination_name=destination_name,
        tonnage=tonnage,
    )


def _outbound(count, prefix="A"):
    return [
        _ship(f"{prefix}{i}", GEBZE, "Gebze", DUSSELDORF, "Dusseldorf")
        for i in range(count)
    ]


def _inbound(count, prefix="B"):
    return [
        _ship(f"{prefix}{i}", DUSSELDORF, "Dusseldorf", GEBZE, "Gebze")
        for i in range(count)
    ]


def test_a_balanced_lane_leaves_nothing_running_empty():
    """Under the dedicated-trip model, a vehicle that has a load home is not empty."""
    assert find_imbalances(_outbound(3) + _inbound(3)) == []


def test_one_way_traffic_is_the_worst_case():
    imbalances = find_imbalances(_outbound(4))

    assert len(imbalances) == 1
    item = imbalances[0]
    assert item.surplus_trips == 4
    assert item.ratio == pytest.approx(1.0), "one-way traffic should read as fully imbalanced"
    assert item.empty_km > 0


def test_only_the_unmatched_trips_come_back_empty():
    """Three out, one back: two vehicles have nothing to carry, not three."""
    imbalances = find_imbalances(_outbound(3) + _inbound(1))

    item = imbalances[0]
    assert item.inbound_trips == 1
    assert item.surplus_trips == 2
    assert item.empty_km == pytest.approx(2 * item.return_km)


def test_the_heavy_direction_decides_where_the_vehicles_end_up():
    """The spatial question the whole module turns on: empty *where*."""
    item = find_imbalances(_outbound(4) + _inbound(1))[0]

    assert item.stranded_at_name == "Dusseldorf"
    assert item.stranded_at == DUSSELDORF

    reversed_item = find_imbalances(_outbound(1) + _inbound(4))[0]
    assert reversed_item.stranded_at_name == "Gebze"


def test_a_load_leaving_nearby_is_offered_as_a_backhaul():
    """Köln is about forty kilometres from Düsseldorf, so a vehicle left empty there can
    reposition and load instead of running two and a half thousand kilometres home."""
    shipments = _outbound(3) + [_ship("C1", KOLN, "Koln", MILAN, "Milano")]

    report = find_backhauls(shipments)

    assert report.matches, "a load leaving 40 km away was not offered"
    match = report.matches[0]
    assert match.empty_at == "Dusseldorf" and match.reload_at == "Koln"
    assert match.reposition_km < match.return_km
    assert match.avoided_empty_km > 0


def test_a_load_too_far_away_is_not_a_backhaul():
    """Repositioning across a continent is its own journey; calling it a backhaul would
    flatter the saving."""
    shipments = _outbound(3) + [_ship("C1", MILAN, "Milano", GEBZE, "Gebze")]

    report = find_backhauls(shipments, radius_km=100.0)

    assert not any(m.reload_at == "Milano" for m in report.matches)


def test_the_saving_is_the_deadhead_less_the_repositioning():
    shipments = _outbound(2) + [_ship("C1", KOLN, "Koln", MILAN, "Milano")]

    match = find_backhauls(shipments).matches[0]

    assert match.avoided_empty_km == pytest.approx(
        (match.return_km - match.reposition_km) * match.trips
    )


def test_a_match_cannot_fill_more_vehicles_than_are_empty():
    """Two empty vehicles and five outbound loads is still two vehicles."""
    shipments = _outbound(2) + [
        _ship(f"C{i}", KOLN, "Koln", MILAN, "Milano") for i in range(5)
    ]

    match = find_backhauls(shipments).matches[0]

    assert match.trips == 2


def test_avoidable_empty_km_never_exceeds_the_empty_km_there_is():
    """Several matches can each claim the same vehicle; the total must not double count
    it into a saving larger than the problem."""
    shipments = _outbound(2) + [
        _ship("C1", KOLN, "Koln", MILAN, "Milano"),
        _ship("D1", DUSSELDORF, "Duss-2", MILAN, "Milano"),
    ]

    report = find_backhauls(shipments)

    assert report.avoidable_empty_km <= report.empty_km


def test_the_assumption_travels_with_the_answer():
    """This is an implication of the freight data, not a measurement of vehicles, and
    the output has to say so wherever it is read."""
    report = find_backhauls(_outbound(3))
    notes = " ".join(report.notes)

    assert "ölçüm değil" in notes
    assert "araç seferi sayıldı" in notes
    assert "aday" in notes, "matches must not read as a plan"


def test_a_straight_line_fallback_admits_to_being_one(monkeypatch):
    """A straight line under-reads wherever geography intervenes, so a match resting on
    one must not sit beside a routed figure as though they were the same kind of number."""
    from app.core.road import RoadRoutingError

    def refuse(origin, destination):
        raise RoadRoutingError("no routing available")

    monkeypatch.setattr(route_module, "road_route", refuse)
    shipments = _outbound(2) + [_ship("C1", KOLN, "Koln", MILAN, "Milano")]

    report = find_backhauls(shipments)

    assert report.matches
    assert all(m.is_straight_line for m in report.matches)


def test_the_default_radius_is_a_short_hop_not_a_journey():
    assert 50 <= DEFAULT_REPOSITION_RADIUS_KM <= 400


def test_road_lookups_go_through_the_seam_the_tests_stub():
    """`from .route import road_route` copies the name at import time, so a stub on the
    module never reaches it — and the module quietly calls the live routing server from
    inside a unit test. Every other module here goes through `route.road_route`; this
    one must too, or the next person to copy the pattern gets a green test that was
    talking to the internet.
    """
    import inspect

    from app.core import backhaul

    source = inspect.getsource(backhaul)
    assert "from .route import road_route" not in source, (
        "an imported name bypasses the stub every test in this repo relies on"
    )
    assert "routing.road_route(" in source
