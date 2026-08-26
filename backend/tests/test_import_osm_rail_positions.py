"""Checking a hand-typed coordinate against the map everyone else uses.

The rail terminals were the only points in this project whose position nothing checked.
The ports are held against NGA Pub. 151; the rail terminals are sourced from ERA RINF,
which publishes a position for about one point in twenty and none at all in Germany,
Italy, Czechia or Romania.

That gap let a real error stand. Köln and Duisburg were corrected from the passenger
stations to the freight terminals they actually mean, the `source_id` moved, and the
coordinate did not - so the point claimed to be a terminal it was kilometres away from.
What these tests defend is that the check cannot quietly stop checking: a terminal OSM
does not know is named rather than dropped, the nearest match is the one measured, and
a search that is really a regular expression stays one.
"""

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "import_osm_rail_positions", REPO / "scripts" / "import_osm_rail_positions.py"
)
osm = importlib.util.module_from_spec(spec)
sys.modules["import_osm_rail_positions"] = osm
spec.loader.exec_module(osm)


# Two nodes for the same terminal, as OSM really carries them: the yard and the
# combined-transport terminal inside it, a couple of kilometres apart.
ELEMENTS = [
    {"type": "node", "id": 1, "lat": 50.90804, "lon": 6.93149,
     "tags": {"name": "Alfa Yard", "railway": "yard"}},
    {"type": "node", "id": 2, "lat": 50.88867, "lon": 6.91847,
     "tags": {"name": "Umschlagbahnhof Alfa", "railway": "yard"}},
]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point the importer at a terminal file and map of our own, and at no network."""
    terminals = tmp_path / "terminals.geojson"
    terminals.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "properties": {"id": "alfa", "name": "Alfa", "country": "DE", "type": "rail"},
         # Deliberately at the far node, so the nearest-match rule has something to pick.
         "geometry": {"type": "Point", "coordinates": [6.9603, 50.9375]}},
        {"type": "Feature",
         "properties": {"id": "beta", "name": "Beta", "country": "TR", "type": "rail"},
         "geometry": {"type": "Point", "coordinates": [29.0, 41.0]}},
    ]}), encoding="utf-8")

    mapping = tmp_path / "map.csv"
    mapping.write_text(
        "terminal_id,uopid,op_name,osm_search,is_verified,note\n"
        "alfa,opA,Alfa Gbf,Alfa,yes,the yard\n"
        "beta,opB,Beta Gbf,,yes,no OSM search chosen\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(osm, "TERMINALS", terminals)
    monkeypatch.setattr(osm, "TERMINAL_MAP", mapping)
    monkeypatch.setattr(osm, "OUT", tmp_path / "out.csv")
    return tmp_path


def rows():
    with osm.OUT.open(encoding="utf-8") as f:
        return {r["terminal_id"]: r for r in csv.DictReader(f)}


def test_the_nearest_match_is_the_one_measured(wired, monkeypatch):
    """A terminal is several OSM nodes. Which one is picked decides the gap, so it is a
    rule and not a coincidence of result ordering."""
    monkeypatch.setattr(osm, "_overpass", lambda query: ELEMENTS)

    osm.derive()

    row = rows()["alfa"]
    assert row["osm_name"] == "Alfa Yard"
    assert row["candidates"] == "2"
    # Roughly 3 km from the coordinate under test to the yard, and further to the other.
    assert 2.0 < float(row["gap_km"]) < 4.0


def test_a_terminal_osm_does_not_know_is_named_not_dropped(wired, monkeypatch):
    """A row that disappears turns "we could not check this" into "there was nothing to
    check", which is the mistake this whole file exists to prevent."""
    monkeypatch.setattr(osm, "_overpass", lambda query: [])

    osm.derive()

    row = rows()["alfa"]
    assert row["gap_km"] == ""
    assert row["note"]


def test_a_terminal_with_no_search_term_is_not_guessed_at(wired, monkeypatch):
    """Türkiye's terminals have no RINF entry and no chosen OSM name. Searching for
    whatever the terminal happens to be called would match a road, a district or
    nothing, and report the result as a position check."""
    monkeypatch.setattr(osm, "_overpass", lambda query: ELEMENTS)

    osm.derive()

    assert "beta" not in rows()


def test_the_query_filters_on_the_server(wired, monkeypatch):
    """Asking for every `railway` object in the box and sorting it out locally made the
    query expensive enough that Overpass answered 504."""
    seen = {}
    monkeypatch.setattr(osm, "_overpass", lambda query: seen.setdefault("q", query) and [])

    osm.derive()

    assert osm.PLACES_RE in seen["q"]
    for place in osm.PLACES:
        assert place in osm.PLACES_RE


def test_the_search_term_is_a_regular_expression(wired, monkeypatch):
    """`^Lambach$` must reach Overpass intact: a bare `Lambach` also matches Lambach
    Markt and Neukirchen bei Lambach, which are different places."""
    mapping = osm.TERMINAL_MAP
    mapping.write_text(
        mapping.read_text(encoding="utf-8").replace(",Alfa,yes,", ",^Alfa Yard$,yes,"),
        encoding="utf-8")
    seen = {}
    monkeypatch.setattr(osm, "_overpass", lambda query: seen.setdefault("q", query) and [])

    osm.derive()

    assert "^Alfa Yard$" in seen["q"]


def test_the_committed_map_gives_every_rail_terminal_a_search_term():
    """A terminal that quietly has no search term is never checked and never complains."""
    with (REPO / "data" / "rinf_terminal_map.csv").open(encoding="utf-8") as f:
        mapping = list(csv.DictReader(f))

    missing = [row["terminal_id"] for row in mapping if not row["osm_search"]]
    assert not missing, f"OSM arama terimi olmayan terminaller: {missing}"


def test_every_committed_position_was_actually_found():
    """The committed file is evidence only for the terminals OSM answered on. A blank
    row is a to-do, and this is what stops it becoming a permanent one."""
    path = REPO / "data" / "external" / "rail_terminal_positions_osm.csv"
    if not path.exists():
        pytest.skip("konumlar henuz indirilmedi")
    with path.open(encoding="utf-8") as f:
        found = list(csv.DictReader(f))

    unchecked = [row["terminal_id"] for row in found if not row["gap_km"]]
    assert not unchecked, f"OSM'de bulunamayan terminaller: {unchecked}"
