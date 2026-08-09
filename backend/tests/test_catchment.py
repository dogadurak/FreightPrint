import pytest

from app.core import catchment as catchment_module
from app.core.catchment import (
    DEFAULT_BOUNDS,
    build_catchment,
    grid_points,
)
from app.core.network import Terminal
from app.core.road import MAX_TABLE_COORDINATES, RoadRoutingError

WEST = Terminal(id="west", name="West", country="TR", type="port", lon=28.0, lat=40.0)
EAST = Terminal(id="east", name="East", country="TR", type="port", lon=32.0, lat=40.0)
TERMINALS = {"west": WEST, "east": EAST}
BOX = (28.0, 40.0, 32.0, 41.0)


@pytest.fixture
def table(monkeypatch):
    """Stand in for OSRM, returning a duration proportional to straight-line distance."""
    calls: list[int] = []

    def fake(sources, destinations):
        calls.append(len(sources) + len(destinations))
        return [
            [abs(lon - s_lon) + abs(lat - s_lat) for lon, lat in destinations]
            for s_lon, s_lat in sources
        ]

    monkeypatch.setattr(catchment_module, "table_durations", fake)
    return calls


class NoCache:
    """The disk cache would otherwise serve one test's stub answers to the next."""

    def get_or_compute(self, key, compute):
        return compute()


def test_the_grid_covers_both_edges_of_the_box():
    points = grid_points((0.0, 0.0, 2.0, 1.0), spacing_deg=1.0)

    assert points[0] == (0.0, 0.0)
    assert (2.0, 1.0) in points
    assert len(points) == 3 * 2


def test_a_grid_finer_than_the_floor_is_refused():
    with pytest.raises(ValueError, match="floor"):
        grid_points(DEFAULT_BOUNDS, spacing_deg=0.01)


def test_an_inverted_box_is_refused_rather_than_returning_nothing():
    with pytest.raises(ValueError, match="empty or inverted"):
        grid_points((10.0, 10.0, 5.0, 5.0), spacing_deg=1.0)


def test_a_grid_too_large_to_pay_for_is_refused(monkeypatch):
    monkeypatch.setattr(catchment_module, "MAX_GRID_POINTS", 10)

    with pytest.raises(ValueError, match="exceeds"):
        grid_points(DEFAULT_BOUNDS, spacing_deg=1.0)


def test_each_point_goes_to_the_terminal_that_reaches_it_fastest(table):
    result = build_catchment(TERMINALS, bounds=BOX, spacing_deg=1.0,
                             connected_only=False, cache=NoCache())

    by_point = {(c.lon, c.lat): c.terminal_id for c in result.cells}
    assert by_point[(28.0, 40.0)] == "west"
    assert by_point[(32.0, 40.0)] == "east"
    assert by_point[(29.0, 40.0)] == "west"
    assert by_point[(31.0, 40.0)] == "east"


def test_a_point_with_no_route_is_dropped_not_given_to_the_least_bad_terminal(monkeypatch):
    """None means OSRM found no road, which is what open water looks like. Treating it
    as a large number would paint the sea in a terminal's colour.

    The time limit is lifted for this test on purpose. With the default eight hours,
    substituting any big sentinel for None lands past the limit and the point drops out
    anyway — so the assertion would hold while the distinction it names was broken.
    """

    def no_route_anywhere(sources, destinations):
        return [[None for _ in destinations] for _ in sources]

    monkeypatch.setattr(catchment_module, "table_durations", no_route_anywhere)
    result = build_catchment(TERMINALS, bounds=BOX, spacing_deg=1.0,
                             max_duration_h=1e9, connected_only=False, cache=NoCache())

    assert result.cells == []
    assert result.unreachable == result.sampled


def test_one_terminal_being_unreachable_does_not_lose_the_point(monkeypatch):
    def west_only(sources, destinations):
        return [
            [1.0 for _ in destinations] if lon == 28.0 else [None for _ in destinations]
            for lon, _ in sources
        ]

    monkeypatch.setattr(catchment_module, "table_durations", west_only)
    result = build_catchment(TERMINALS, bounds=BOX, spacing_deg=1.0,
                             connected_only=False, cache=NoCache())

    assert result.unreachable == 0
    assert set(result.terminal_ids) == {"west"}


def test_a_point_beyond_the_time_limit_belongs_to_nobody(table):
    """Colouring the whole map by whoever is least far away would claim a catchment
    that does not exist."""
    near = build_catchment(TERMINALS, bounds=BOX, spacing_deg=1.0, max_duration_h=0.5,
                           connected_only=False, cache=NoCache())
    far = build_catchment(TERMINALS, bounds=BOX, spacing_deg=1.0, max_duration_h=8.0,
                          connected_only=False, cache=NoCache())

    assert len(near.cells) < len(far.cells)
    assert near.unreachable > 0
    assert all(cell.duration_h <= 0.5 for cell in near.cells)


def test_destinations_are_batched_under_the_table_limit(table):
    build_catchment(TERMINALS, bounds=DEFAULT_BOUNDS, spacing_deg=1.0,
                    connected_only=False, cache=NoCache())

    assert table, "no table request was made"
    assert max(table) <= MAX_TABLE_COORDINATES
    assert len(table) > 1, "a grid this size must have been split across requests"


def test_more_terminals_than_the_table_holds_is_an_error_not_a_silent_truncation(monkeypatch):
    crowd = {
        f"t{i}": Terminal(id=f"t{i}", name=f"T{i}", country="TR", type="port",
                          lon=28.0 + i * 0.01, lat=40.0)
        for i in range(MAX_TABLE_COORDINATES + 1)
    }

    with pytest.raises(RoadRoutingError, match="table limit"):
        build_catchment(crowd, bounds=BOX, spacing_deg=1.0,
                        connected_only=False, cache=NoCache())


def test_the_result_says_how_coarsely_it_was_measured(table):
    """A boundary drawn from 111 km samples must not be read as a surveyed line."""
    result = build_catchment(TERMINALS, bounds=BOX, spacing_deg=1.0,
                             connected_only=False, cache=NoCache())

    assert result.spacing_deg == 1.0
    assert any("aralıklı örnekleme" in note for note in result.notes)
    assert result.sampled == len(grid_points(BOX, 1.0))


def test_no_terminals_is_refused():
    with pytest.raises(ValueError, match="no terminals"):
        build_catchment({}, bounds=BOX, spacing_deg=1.0, cache=NoCache())


def test_a_terminal_no_service_calls_at_gets_no_catchment(table):
    """You can drive to Ambarli; nothing sails from it. Handing it a catchment maps a
    place you can deliver to and then be stuck."""
    from app.core.network import load_terminals

    terminals = load_terminals()
    result = build_catchment(
        terminals, bounds=(27.0, 40.0, 30.0, 41.5), spacing_deg=1.0, cache=NoCache()
    )

    assert "ambarli" not in result.terminal_ids
    assert any("ambarli" in note for note in result.notes)


def test_physical_proximity_can_still_be_asked_for(table):
    from app.core.network import load_terminals

    terminals = load_terminals()
    result = build_catchment(
        terminals, bounds=(27.0, 40.0, 30.0, 41.5), spacing_deg=1.0,
        connected_only=False, cache=NoCache(),
    )

    assert "ambarli" in result.terminal_ids


def test_raising_the_time_limit_does_not_re_fetch_identical_data(monkeypatch):
    """The cache holds the duration matrix; the limit is applied to it afterwards. If
    the limit were part of the key, widening it would pay the full cold cost again for
    data already in hand — 26 seconds against the public OSRM server."""
    calls = []

    def counting(sources, destinations):
        calls.append(len(destinations))
        return [[1.0 for _ in destinations] for _ in sources]

    monkeypatch.setattr(catchment_module, "table_durations", counting)

    class CountingCache:
        def __init__(self):
            self.store = {}

        def get_or_compute(self, key, compute):
            if key not in self.store:
                self.store[key] = compute()
            return self.store[key]

    cache = CountingCache()
    build_catchment(TERMINALS, bounds=BOX, spacing_deg=1.0, max_duration_h=8.0,
                    connected_only=False, cache=cache)
    after_first = len(calls)
    build_catchment(TERMINALS, bounds=BOX, spacing_deg=1.0, max_duration_h=10.0,
                    connected_only=False, cache=cache)

    assert len(calls) == after_first, "widening the limit re-fetched the same durations"


def test_the_cache_key_names_the_server_that_answered(monkeypatch):
    """Pointing at a self-hosted OSRM must not keep serving the demo server's answers."""
    keys = []

    class RecordingCache:
        def get_or_compute(self, key, compute):
            keys.append(key)
            return compute()

    monkeypatch.setattr(
        catchment_module, "table_durations",
        lambda sources, destinations: [[1.0 for _ in destinations] for _ in sources],
    )
    build_catchment(TERMINALS, bounds=BOX, spacing_deg=1.0,
                    connected_only=False, cache=RecordingCache())

    assert keys and all(catchment_module.OSRM_BASE_URL in key for key in keys)
