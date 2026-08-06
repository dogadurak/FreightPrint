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
