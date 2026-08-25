"""What every endpoint does when something underneath it fails.

These paths are the least exercised and the most consequential, because the status code
is an instruction. 503 says "this is ours, try again"; 422 says "this is yours, change
the request". Getting them the wrong way round has a client retrying a query that will
never work, or giving up on an outage that would have cleared in a second — and neither
shows up as an error anywhere.
"""

import io

import pytest
import requests
from fastapi.testclient import TestClient

from app.core import geocode as geocode_module
from app.core import route as route_module
from app.core.geocode import GeocodingBusy, GeocodingError
from app.core.network import haversine_km
from app.core.road import RoadRoute, RoadRoutingError
from app.main import app

CSV = (
    "reference,origin_name,origin_lon,origin_lat,destination_name,"
    "destination_lon,destination_lat,tonnage\n"
    "A1,Gebze,29.4306,40.7889,Dusseldorf,6.7735,51.2277,24\n"
)
UPLOAD_ENDPOINTS = ["/api/portfolio", "/api/vulnerability", "/api/backhaul", "/api/hub-plan"]


@pytest.fixture(autouse=True)
def offline_road(monkeypatch):
    def fake(origin, destination):
        km = haversine_km(origin, destination) * 1.3
        return RoadRoute(distance_km=km, duration_h=km / 70, geometry=(origin, destination))

    monkeypatch.setattr(route_module, "road_route", fake)


@pytest.fixture
def client():
    return TestClient(app)


def upload(client, endpoint, csv=CSV, **form):
    return client.post(
        endpoint,
        files={"file": ("s.csv", io.BytesIO(csv.encode()), "text/csv")},
        data=form,
    )


@pytest.mark.parametrize("endpoint", UPLOAD_ENDPOINTS)
def test_a_routing_outage_is_theirs_to_retry_not_the_callers_to_fix(client, endpoint, monkeypatch):
    """503, not 422 and not a 500. The request was fine; the routing server was not, and
    the same request will work in a minute."""
    def unreachable(origin, destination):
        raise requests.ConnectionError("OSRM is down")

    monkeypatch.setattr(route_module, "road_route", unreachable)

    response = upload(client, endpoint)

    assert response.status_code == 503, f"{endpoint} did not report the outage as ours"
    assert "routing" in response.json()["detail"].lower()


@pytest.mark.parametrize("endpoint", UPLOAD_ENDPOINTS)
def test_a_malformed_file_is_the_callers_to_fix(client, endpoint):
    """422 with the column named, so the person can go and correct the file."""
    response = upload(client, endpoint, csv="reference,tonnage\nA1,24\n")

    assert response.status_code == 422
    assert "origin_lon" in response.json()["detail"]


@pytest.mark.parametrize("endpoint", UPLOAD_ENDPOINTS)
def test_an_empty_file_is_refused_rather_than_answered_over_nothing(client, endpoint):
    response = upload(client, endpoint, csv=CSV.split("\n")[0] + "\n")

    assert response.status_code == 422


@pytest.mark.parametrize("endpoint", ["/api/portfolio", "/api/vulnerability"])
def test_a_factor_set_that_cannot_price_the_route_is_refused(client, endpoint):
    """Falling back to another set would price a leg from a basis the answer does not
    name, which is the one thing a standards claim cannot survive."""
    response = upload(client, endpoint, factor_set="no-such-set", scope="WTW")

    assert response.status_code == 422


def test_the_hub_plan_survives_a_missing_factor_because_geometry_does_not_need_one(client):
    """Deliberately different from the endpoints above. The hub answer is vehicle-
    kilometres, which is geometry; the factor only converts that to carbon. So a basis
    that cannot price road still yields a usable plan, with the carbon absent rather
    than zero."""
    body = upload(client, "/api/hub-plan", factor_set="no-such-set", scope="WTW").json()

    assert body["saved_vehicle_km"] >= 0
    assert body["saved_co2_kg"] is None


def test_a_shipment_with_no_road_access_says_so_rather_than_failing_silently(client, monkeypatch):
    """OSRM snaps unreachable input to the nearest road and still answers, so this is a
    refusal the engine has to make itself."""
    def no_access(origin, destination):
        raise RoadRoutingError("(0.0, 0.0) is 4,231 km from the nearest road; no road access")

    monkeypatch.setattr(route_module, "road_route", no_access)

    response = upload(client, "/api/portfolio")

    assert response.status_code == 422
    assert "no road access" in response.json()["detail"]


def test_a_busy_geocoder_asks_the_caller_to_wait_rather_than_to_rewrite(client, monkeypatch):
    """Nominatim allows one request a second, so a queue is ours to clear. 422 would tell
    the caller their query was wrong, and they would keep changing a query that was fine."""
    def busy(*args, **kwargs):
        raise GeocodingBusy("8 geocoding requests are already queued; try again shortly")

    monkeypatch.setattr("app.api.routes.search", busy)

    response = client.get("/api/places", params={"q": "Gebze"})

    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "2"


def test_an_unusable_country_code_is_the_callers_to_fix(client, monkeypatch):
    """Different from the queue: no amount of waiting makes "Ruritania" a country."""
    def unknown(*args, **kwargs):
        raise GeocodingError("unknown country 'Ruritania'; add it to COUNTRY_ALIASES")

    monkeypatch.setattr("app.api.routes.search", unknown)

    response = client.get("/api/places", params={"q": "x", "country": "Ruritania"})

    assert response.status_code == 422


def test_a_geocoder_outage_is_reported_as_an_outage(client, monkeypatch):
    def down(*args, **kwargs):
        raise requests.ConnectionError("nominatim unreachable")

    monkeypatch.setattr("app.api.routes.search", down)

    response = client.get("/api/places", params={"q": "Gebze"})

    assert response.status_code == 503


def test_an_empty_search_is_refused_before_any_request(client, monkeypatch):
    """A blank query would ask the public geocoder for everything."""
    def refuse(*args, **kwargs):
        raise AssertionError("an empty query reached the geocoder")

    monkeypatch.setattr("app.api.routes.search", refuse)

    assert client.get("/api/places", params={"q": "   "}).status_code == 422


@pytest.mark.parametrize(
    ("endpoint", "form"),
    [
        ("/api/portfolio", {"scope": "WTV"}),
        ("/api/vulnerability", {"scope": "WTV"}),
        ("/api/hub-plan", {"scope": "WTV"}),
        ("/api/hub-plan", {"capacity_tonnes": "-5"}),
        ("/api/backhaul", {"radius_km": "0"}),
    ],
)
def test_an_out_of_range_parameter_names_what_is_allowed(client, endpoint, form):
    response = upload(client, endpoint, **form)

    assert response.status_code == 422
    assert response.json()["detail"], "refused without saying what would be accepted"
