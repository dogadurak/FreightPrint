import pytest
from fastapi.testclient import TestClient

from app.core import route as route_module
from app.core.road import RoadRoute, RoadRoutingError
from app.core.network import haversine_km
from app.main import app

ROAD_DETOUR_FACTOR = 1.3
AVERAGE_ROAD_SPEED_KMH = 70.0

GEBZE = {"lon": 29.4306, "lat": 40.7889}
DUSSELDORF = {"lon": 6.7735, "lat": 51.2277}


@pytest.fixture
def client(monkeypatch):
    """Serve routes without touching OSRM, so the API contract is what is under test."""

    def fake_road_route(origin, destination):
        distance_km = haversine_km(origin, destination) * ROAD_DETOUR_FACTOR
        return RoadRoute(
            distance_km=distance_km,
            duration_h=distance_km / AVERAGE_ROAD_SPEED_KMH,
            geometry=(origin, destination),
        )

    monkeypatch.setattr(route_module, "road_route", fake_road_route)
    return TestClient(app)


def _post(client, **overrides):
    body = {"origin": GEBZE, "destination": DUSSELDORF} | overrides
    return client.post("/api/routes", json=body)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_terminals_flag_the_ones_no_service_reaches(client):
    terminals = client.get("/api/terminals").json()

    assert len(terminals) == 16
    assert [t["name"] for t in terminals if not t["is_connected"]] == ["Ambarli"]


def test_factor_sets_expose_the_sea_basis_behind_each_choice(client):
    sets = {s["name"]: s for s in client.get("/api/factor-sets").json()}

    assert sets["glec"]["sea_factor_by_scope"]["TTW"] == 0.063
    assert sets["glec_accompanied"]["sea_factor_by_scope"]["TTW"] == 0.093
    assert sets["glec_freight_average"]["sea_factor_by_scope"]["TTW"] == 0.042
    assert sets["placeholder"]["is_verified"] is False


def test_routes_return_the_baseline_first_then_cleanest(client):
    payload = _post(client).json()
    alternatives = payload["alternatives"]

    assert alternatives[0]["is_all_road"]
    others = [a["total_co2_kg"] for a in alternatives[1:]]
    assert others == sorted(others)


def test_response_names_the_factor_set_and_its_sources(client):
    payload = _post(client, factor_set="glec", scope="WTW").json()

    assert payload["factor_set"] == "glec"
    assert payload["scope"] == "WTW"
    assert all("GLEC" in source for source in payload["sources"])


def test_a_negative_saving_is_reported_rather_than_hidden(client):
    """On a ro-ro corridor the multimodal option can lose, and the API must say so."""
    payload = _post(client, factor_set="glec", scope="WTW").json()
    multimodal = [a for a in payload["alternatives"] if not a["is_all_road"]]

    assert multimodal
    assert any(a["saving_co2_kg"] < 0 for a in multimodal)
    for alternative in multimodal:
        if alternative["saving_co2_kg"] < 0:
            assert set(alternative["tree_equivalent"].values()) == {0}


def test_road_legs_carry_geometry_and_other_modes_do_not(client):
    """The map draws real geometry where it exists and a schematic line where it does not."""
    payload = _post(client).json()
    legs = [leg for a in payload["alternatives"] for leg in a["legs"]]

    assert any(leg["mode"] == "road" and leg["geometry"] for leg in legs)
    assert all(not leg["geometry"] for leg in legs if leg["mode"] in {"sea", "rail"})


def test_max_alternatives_trims_after_ranking_by_emissions(client):
    full = _post(client, factor_set="glec").json()["alternatives"]
    trimmed = _post(client, factor_set="glec", max_alternatives=1).json()["alternatives"]

    assert len(trimmed) == 2
    assert trimmed[1]["label"] == full[1]["label"]


def test_a_fuel_missing_from_the_set_is_a_422_not_another_set(client):
    response = _post(client, factor_set="glec", road_fuel_type="diesel")

    assert response.status_code == 422
    assert "diesel_b5" in response.json()["detail"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"origin": {"lon": 999, "lat": 40}},
        {"tonnage": 0},
        {"scope": "CRADLE"},
        {"load_factor": 0},
        {"empty_return_share": 5},
    ],
)
def test_impossible_input_is_refused(client, overrides):
    assert _post(client, **overrides).status_code == 422


def test_unreachable_origin_answers_422_not_500(client, monkeypatch):
    def unreachable(origin, destination):
        raise RoadRoutingError("(-21.9, 64.1) is 762 km from the nearest road; no road access")

    monkeypatch.setattr(route_module, "road_route", unreachable)

    response = _post(client)
    assert response.status_code == 422
    assert "no road access" in response.json()["detail"]


def test_the_page_and_its_assets_are_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


SHIPMENT_CSV = (
    "reference,origin_lon,origin_lat,destination_lon,destination_lat,tonnage\n"
    "SEV-1,29.4306,40.7889,6.7735,51.2277,24\n"
    "SEV-2,29.4306,40.7889,16.3738,48.2082,18\n"
)


def _upload(client, content=SHIPMENT_CSV, **fields):
    return client.post(
        "/api/report",
        files={"file": ("shipments.csv", content, "text/csv")},
        data={"scope": "TTW", "factor_set": "reference"} | fields,
    )


def test_report_returns_a_downloadable_csv(client):
    response = _upload(client)

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.count("SEV-") == 2


def test_report_states_the_factor_set_it_priced_with(client):
    response = _upload(client, factor_set="glec", scope="WTW")

    assert "factor set: glec" in response.text
    assert "GLEC Framework" in response.text


def test_report_rejects_a_file_missing_required_columns(client):
    response = _upload(client, content="origin_lon,origin_lat\n29.43,40.78\n")

    assert response.status_code == 422
    assert "missing column" in response.json()["detail"]


def test_report_rejects_a_factor_set_that_cannot_price_any_row(client):
    response = _upload(client, factor_set="reference", scope="WTW")

    assert response.status_code == 422
    assert "no WTW factor" in response.json()["detail"]


def test_report_rejects_a_non_utf8_upload(client):
    response = client.post(
        "/api/report",
        files={"file": ("shipments.csv", b"\xff\xfe\x00bad", "text/csv")},
        data={"scope": "TTW", "factor_set": "reference"},
    )

    assert response.status_code == 422


def test_the_sample_shipment_file_is_served_and_parses(client):
    response = client.get("/static/ornek_sevkiyatlar.csv")

    assert response.status_code == 200
    assert _upload(client, content=response.text).status_code == 200
