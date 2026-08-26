import csv
import json

import pytest
from shapely.geometry import Point

from app.core.network import DATA_DIR, build_network, haversine_km, load_terminals, nearest_terminals
from app.core.sea import CORINTH_CANAL_BOX


def test_terminals_and_legs_load_consistently():
    terminals = load_terminals()
    graph = build_network(terminals)

    with open(DATA_DIR / "service_legs.csv", encoding="utf-8") as f:
        legs = list(csv.DictReader(f))

    assert graph.number_of_nodes() == len(terminals)
    assert graph.number_of_edges() == len(legs)


def test_service_legs_only_reference_known_terminals():
    terminals = load_terminals()
    with open(DATA_DIR / "service_legs.csv", encoding="utf-8") as f:
        for leg in csv.DictReader(f):
            assert leg["from_terminal"] in terminals
            assert leg["to_terminal"] in terminals
            assert leg["mode"] in {"sea", "rail"}


def test_terminal_coordinates_are_plausible():
    with open(DATA_DIR / "terminals.geojson", encoding="utf-8") as f:
        features = json.load(f)["features"]

    for feature in features:
        lon, lat = feature["geometry"]["coordinates"]
        assert -25 < lon < 40, feature["properties"]["id"]
        assert 35 < lat < 55, feature["properties"]["id"]


def test_haversine_against_known_distance():
    istanbul = (28.9784, 41.0082)
    trieste = (13.7768, 45.6495)
    assert haversine_km(istanbul, trieste) == pytest.approx(1300, abs=50)


def test_nearest_terminals_skips_terminals_without_service_legs():
    terminals = load_terminals()
    graph = build_network(terminals)
    isolated = {t for t in graph.nodes if graph.degree(t) == 0}

    nearest = nearest_terminals((28.97, 41.00), terminals, limit=5, connected_only=graph)
    assert isolated
    assert not {t.id for t in nearest} & isolated


def test_corinth_canal_box_covers_the_isthmus_but_not_open_sea():
    assert CORINTH_CANAL_BOX.contains(Point(22.99, 37.93))
    assert not CORINTH_CANAL_BOX.intersects(Point(23.5, 37.0))


# ── where a terminal came from ────────────────────────────────────────────────

def test_most_terminals_are_tied_to_an_outside_record():
    """Sixteen points typed from knowledge held up a corridor whose every distance is
    measured between them, and none of them said where it came from."""
    from app.core.network import load_terminals

    terminals = load_terminals().values()
    sourced = [t for t in terminals if t.source]

    assert len(sourced) >= 12
    assert {t.source for t in sourced} == {"NGA Pub. 151", "ERA RINF"}
    assert all(t.source_id for t in sourced), "a source with no identifier in it"


def test_the_terminals_with_no_outside_record_are_all_turkish():
    """The third time this gap has appeared, and it is the same gap each time: Türkiye
    does not report to Eurostat's road survey, does not file to RINF, and is not in
    Pub 151's port list beyond Istanbul, Derince and Mersin. A fact about European
    reference data rather than about this project — so it is left visible."""
    from app.core.network import load_terminals

    unsourced = [t for t in load_terminals().values() if not t.source]

    assert {t.country for t in unsourced} == {"TR"}
    assert {t.id for t in unsourced} == {"pendik", "yalova", "ambarli", "halkali"}


def test_the_hand_typed_coordinates_agree_with_the_published_ones():
    """The check that came free with the provenance, and it is a pass: every port this
    project placed by hand sits within 1.8 km of the position NGA Pub. 151 prints."""
    import importlib.util
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "source_terminals", repo / "scripts" / "source_terminals.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["source_terminals"] = module
    spec.loader.exec_module(module)

    if not module.PUB151_TEXT.exists():
        pytest.skip("Pub 151 not present locally")

    rows, problems = module.review()
    gaps = [r["coordinate_gap_km"] for r in rows if r["coordinate_gap_km"] != ""]

    assert not problems, f"a terminal has drifted from its published position: {problems}"
    assert len(gaps) >= 5
    assert max(gaps) < module.MAX_COORDINATE_GAP_KM
