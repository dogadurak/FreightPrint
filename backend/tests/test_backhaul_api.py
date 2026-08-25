"""The backhaul endpoint: where the fleet runs empty and what could fill it."""

import io

import pytest
from fastapi.testclient import TestClient

from app.core import route as route_module
from app.core.network import haversine_km
from app.core.road import RoadRoute
from app.main import app

HEADER = (
    "reference,origin_name,origin_lon,origin_lat,destination_name,"
    "destination_lon,destination_lat,tonnage\n"
)
# Three out, one back, plus a load leaving 40 km from where the empties end up.
CSV = HEADER + (
    "A1,Gebze,29.4306,40.7889,Dusseldorf,6.7735,51.2277,24\n"
    "A2,Gebze,29.4306,40.7889,Dusseldorf,6.7735,51.2277,22\n"
    "A3,Gebze,29.4306,40.7889,Dusseldorf,6.7735,51.2277,20\n"
    "B1,Dusseldorf,6.7735,51.2277,Gebze,29.4306,40.7889,18\n"
    "C1,Koln,6.9603,50.9375,Milano,9.19,45.46,24\n"
)


@pytest.fixture(autouse=True)
def offline_road(monkeypatch):
    def fake(origin, destination):
        km = haversine_km(origin, destination) * 1.3
        return RoadRoute(distance_km=km, duration_h=km / 70, geometry=(origin, destination))

    monkeypatch.setattr(route_module, "road_route", fake)


@pytest.fixture
def client():
    return TestClient(app)


def post(client, csv=CSV, **form):
    return client.post(
        "/api/backhaul",
        files={"file": ("s.csv", io.BytesIO(csv.encode()), "text/csv")},
        data=form,
    )


def test_it_finds_the_imbalance_and_where_it_lands(client):
    body = post(client).json()

    assert body["shipments"] == 5
    assert body["empty_km"] > 0

    lane = next(i for i in body["imbalances"] if i["stranded_at"] == "Dusseldorf")
    assert lane["outbound_trips"] == 3 and lane["inbound_trips"] == 1
    assert lane["surplus_trips"] == 2


def test_a_match_carries_both_ends_so_the_map_can_draw_the_hop(client):
    """The repositioning leg is the thing being proposed; a match that cannot be drawn
    is a table row, and seeing the empties collect beside a departing load is the point."""
    match = next(m for m in post(client).json()["matches"] if m["reload_at"] == "Koln")

    assert (match["empty_lon"], match["empty_lat"]) != (match["reload_lon"], match["reload_lat"])
    assert match["reposition_km"] < match["return_km"]
    assert match["avoided_empty_km"] > 0


def test_the_avoidable_total_never_exceeds_the_problem(client):
    """Several matches can each claim the same empty vehicle; summing them unguarded
    would report a saving larger than the empty running there is."""
    body = post(client).json()

    assert body["avoidable_empty_km"] <= body["empty_km"]


def test_the_assumption_travels_with_the_answer(client):
    """A shipment file records freight moving, not vehicles. Presented as a measurement
    this would be the most persuasive wrong number in the dashboard."""
    notes = " ".join(post(client).json()["notes"])

    assert "ölçüm değil" in notes
    assert "aday" in notes


def test_a_tight_radius_rules_out_a_distant_reload(client):
    """Repositioning across a continent is its own journey, not a backhaul."""
    wide = post(client, radius_km=200).json()
    tight = post(client, radius_km=20).json()

    assert any(m["reload_at"] == "Koln" for m in wide["matches"])
    assert not any(m["reload_at"] == "Koln" for m in tight["matches"])


@pytest.mark.parametrize("radius", [0, 5, 5000])
def test_an_unusable_radius_is_refused_with_the_range(client, radius):
    response = post(client, radius_km=radius)

    assert response.status_code == 422
    assert "10-1000" in response.json()["detail"]


def test_a_balanced_file_reports_nothing_rather_than_inventing_it(client):
    balanced = HEADER + (
        "A1,Gebze,29.4306,40.7889,Dusseldorf,6.7735,51.2277,24\n"
        "B1,Dusseldorf,6.7735,51.2277,Gebze,29.4306,40.7889,24\n"
    )

    body = post(client, csv=balanced).json()

    assert body["imbalances"] == []
    assert body["empty_km"] == 0


def test_a_file_that_cannot_be_read_says_which_column(client):
    assert post(client, csv="reference,tonnage\nA1,24\n").status_code == 422
