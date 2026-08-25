"""The hub-plan endpoint: where consolidating would pay, and whether it was proved."""

import io

import pytest
from fastapi.testclient import TestClient

from app.core import route as route_module
from app.core.network import haversine_km
from app.core.road import RoadRoute
from app.main import app

# Part-loads from four Marmara suppliers, all bound for one German destination.
CSV = (
    "reference,origin_name,origin_lon,origin_lat,destination_name,"
    "destination_lon,destination_lat,tonnage\n"
    "P1,Gebze,29.43,40.79,Dusseldorf,6.7735,51.2277,9\n"
    "P2,Izmit,29.28,40.87,Dusseldorf,6.7735,51.2277,9\n"
    "P3,Sakarya,29.10,40.98,Dusseldorf,6.7735,51.2277,9\n"
    "P4,Corlu,28.36,40.98,Dusseldorf,6.7735,51.2277,9\n"
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
        "/api/hub-plan",
        files={"file": ("s.csv", io.BytesIO(csv.encode()), "text/csv")},
        data={"scope": "WTW", "factor_set": "glec", **form},
    )


def test_part_loads_to_one_destination_get_a_hub(client):
    body = post(client).json()

    assert body["is_optimal"], "the solver did not prove the optimum at this size"
    assert body["opened"], "four part-loads sharing a destination and no hub opened"
    assert body["saved_vehicle_km"] > 0
    assert 0 < body["saved_share"] <= 1


def test_the_hub_carries_a_point_so_the_map_can_draw_it(client):
    """The answer to this question is a place. A ranking that cannot be drawn is a
    table, and the reason to compute it spatially is to see the suppliers around it."""
    site = post(client).json()["opened"][0]

    assert site["lon"] and site["lat"]
    assert site["shipments"] > 0


def test_the_plan_never_costs_more_than_going_direct(client):
    """Direct is always available, so the optimum cannot be worse than not having a hub."""
    body = post(client).json()

    assert body["planned_vehicle_km"] <= body["direct_vehicle_km"]


def test_full_loads_get_no_hub(client):
    """A full vehicle has nothing to share; routing it via a hub is pure detour."""
    csv = CSV.replace(",9\n", ",24\n")

    body = post(client, csv=csv).json()

    assert body["is_optimal"]
    assert not any(a["is_consolidated"] for a in body["assignments"])


def test_the_objective_and_its_blind_spot_ship_with_the_answer(client):
    notes = " ".join(post(client).json()["notes"])

    assert "araç-kilometredir" in notes, "the vehicle-km choice is not stated"
    assert "tarih yoktur" in notes, "the consolidation-needs-dates caveat is missing"


def test_carbon_is_absent_rather_than_zero_when_no_factor_matches(client):
    body = post(client, factor_set="no-such-set").json()

    assert body["saved_co2_kg"] is None
    assert body["saved_vehicle_km"] >= 0


@pytest.mark.parametrize("form", [{"scope": "WTV"}, {"capacity_tonnes": 0}, {"max_hubs": 0}])
def test_an_impossible_request_is_refused(client, form):
    assert post(client, **form).status_code == 422
