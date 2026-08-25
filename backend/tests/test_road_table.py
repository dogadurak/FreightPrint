"""OSRM's `/table`, which the catchment map is entirely built on.

Nothing here talks to a server; what is being checked is the arithmetic around the call,
and that is where this kind of client goes wrong. The request packs sources and
destinations into one coordinate list and then tells OSRM which indices are which — get
that split backwards and the matrix comes back transposed, every terminal claims the
wrong hinterland, and the map looks entirely plausible while being wrong.
"""

import pytest

from app.core import road
from app.core.road import MAX_TABLE_COORDINATES, RoadRoutingError, table_durations

SOURCES = [(29.0, 41.0), (28.0, 40.0)]
DESTINATIONS = [(13.8, 45.6), (6.9, 50.9), (12.1, 49.0)]


@pytest.fixture
def captured(monkeypatch):
    """Record the request instead of making it, and answer with a known matrix."""
    seen = {}

    def fake_fetch(url, params=None):
        seen["url"] = url
        seen["params"] = params
        return {
            "code": "Ok",
            # Hours are what the caller wants; OSRM answers in seconds.
            "durations": [[3600.0, 7200.0, None], [1800.0, 0.0, 5400.0]],
        }

    monkeypatch.setattr(road, "_fetch", fake_fetch)
    return seen


def test_durations_come_back_in_hours(captured):
    """OSRM answers in seconds and every caller here reasons in hours."""
    table = table_durations(SOURCES, DESTINATIONS)

    assert table[0][0] == pytest.approx(1.0)
    assert table[0][1] == pytest.approx(2.0)
    assert table[1][0] == pytest.approx(0.5)


def test_unreachable_stays_unknown_rather_than_becoming_zero():
    """`None` means OSRM found no route — open sea, an island with no ferry. Zero means
    it is right there. A catchment map that confused the two would colour the Adriatic
    as somebody's hinterland."""
    import app.core.road as road_module

    def fake_fetch(url, params=None):
        return {"code": "Ok", "durations": [[None, 3600.0]]}

    original = road_module._fetch
    road_module._fetch = fake_fetch
    try:
        table = table_durations([(29.0, 41.0)], [(13.8, 45.6), (6.9, 50.9)])
    finally:
        road_module._fetch = original

    assert table[0][0] is None
    assert table[0][1] == pytest.approx(1.0)


def test_the_matrix_is_sources_by_destinations(captured):
    table = table_durations(SOURCES, DESTINATIONS)

    assert len(table) == len(SOURCES)
    assert all(len(row) == len(DESTINATIONS) for row in table)


def test_sources_and_destinations_are_indexed_the_way_they_were_packed(captured):
    """The subtle one. Both lists go into a single coordinate string, so the indices are
    the only thing telling OSRM which is which — and a matrix that comes back transposed
    is not an error anywhere, just a wrong map."""
    table_durations(SOURCES, DESTINATIONS)

    assert captured["params"]["sources"] == "0;1"
    assert captured["params"]["destinations"] == "2;3;4"

    # And the coordinates appear in that same order, sources first.
    coordinates = captured["url"].rsplit("/", 1)[-1].split(";")
    assert coordinates[0] == "29.0,41.0"
    assert coordinates[2] == "13.8,45.6"


def test_an_empty_side_asks_nothing(monkeypatch):
    """Zero destinations is an empty answer, not a request with an empty index list —
    OSRM would reject that, and the caller would see a routing error for a question it
    never really asked."""
    def refuse(*args, **kwargs):
        raise AssertionError("a request was made for an empty table")

    monkeypatch.setattr(road, "_fetch", refuse)

    assert table_durations([], DESTINATIONS) == []
    assert table_durations(SOURCES, []) == []


def test_too_many_coordinates_is_refused_before_the_request(monkeypatch):
    """osrm-routed's own limit is 100. Sending more gets a rejection from the server that
    reads like an outage; refusing here says what to do instead."""
    def refuse(*args, **kwargs):
        raise AssertionError("an oversized table was sent to OSRM")

    monkeypatch.setattr(road, "_fetch", refuse)
    too_many = [(1.0 + i, 40.0) for i in range(MAX_TABLE_COORDINATES)]

    with pytest.raises(RoadRoutingError, match="batch the destinations"):
        table_durations(too_many, DESTINATIONS)


def test_the_limit_counts_both_sides_together(monkeypatch):
    """It is one request carrying both lists, so the ceiling applies to the sum. Counting
    only one side would let a request through that the server then refuses."""
    monkeypatch.setattr(road, "_fetch", lambda *a, **k: {"code": "Ok", "durations": [[0.0]]})
    half = MAX_TABLE_COORDINATES // 2

    ok = table_durations([(1.0, 40.0)] * 1, [(2.0, 41.0)] * 1)
    assert ok

    with pytest.raises(RoadRoutingError):
        table_durations([(1.0, 40.0)] * half, [(2.0, 41.0)] * (half + 1))


def test_a_refusal_from_osrm_is_raised_not_returned_as_data(monkeypatch):
    """A non-Ok code with a durations key would otherwise be read as a real matrix."""
    monkeypatch.setattr(
        road, "_fetch",
        lambda *a, **k: {"code": "NoTable", "durations": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]},
    )

    with pytest.raises(RoadRoutingError, match="NoTable"):
        table_durations(SOURCES, DESTINATIONS)


def test_the_request_is_held_to_the_shared_concurrency_limit(captured):
    """The semaphore belongs to the module rather than to any caller: a batch job of
    four rows, each with its own pool of four, was measured putting sixteen requests on
    the public demo server at once."""
    assert road._request_slots._initial_value == road.MAX_CONCURRENT_REQUESTS

    before = road._request_slots._value
    table_durations(SOURCES, DESTINATIONS)

    assert road._request_slots._value == before, "a slot was not given back"
