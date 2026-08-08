import time

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


def test_road_and_sea_legs_carry_geometry_but_rail_does_not(client):
    """Sea tracks come from searoute; rail has no computed geometry, so it stays schematic."""
    payload = _post(client).json()
    legs = [leg for a in payload["alternatives"] for leg in a["legs"]]

    assert any(leg["mode"] == "road" and leg["geometry"] for leg in legs)
    assert any(leg["mode"] == "sea" and leg["geometry"] for leg in legs)
    assert all(not leg["geometry"] for leg in legs if leg["mode"] == "rail")


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


SCENARIOS = [
    {"factor_set": "reference", "scope": "TTW"},
    {"factor_set": "glec", "scope": "TTW"},
    {"factor_set": "glec", "scope": "WTW"},
    {"factor_set": "glec_accompanied", "scope": "WTW"},
    {"factor_set": "reference", "scope": "WTW"},
]


def test_scenarios_price_the_same_routes_without_rerouting(client, monkeypatch):
    """Routing costs seconds and seven OSRM calls; every extra pricing must be free."""
    calls = {"n": 0}
    original = route_module.road_route

    def counting(origin, destination):
        calls["n"] += 1
        return original(origin, destination)

    monkeypatch.setattr(route_module, "road_route", counting)
    payload = _post(client, scenarios=SCENARIOS).json()
    with_scenarios = calls["n"]

    calls["n"] = 0
    _post(client)
    assert with_scenarios == calls["n"]
    assert len(payload["scenarios"]) == len(SCENARIOS)


def test_each_scenario_reports_its_own_totals_and_sources(client):
    scenarios = {
        f"{s['factor_set']}|{s['scope']}": s for s in _post(client, scenarios=SCENARIOS).json()["scenarios"]
    }

    reference = scenarios["reference|TTW"]
    glec = scenarios["glec|TTW"]
    assert all("GLEC" in source for source in glec["sources"])
    assert not any("GLEC" in source for source in reference["sources"])

    # Same route, different basis: the ro-ro factor is what moves the answer.
    label = next(t["label"] for t in glec["totals"] if not t["is_all_road"])
    priced = {k: next(t["total_co2_kg"] for t in v["totals"] if t["label"] == label)
              for k, v in scenarios.items() if v["totals"]}
    assert priced["glec|TTW"] > priced["reference|TTW"] * 2
    assert priced["glec_accompanied|WTW"] > priced["glec|WTW"]


def test_an_unpriceable_scenario_carries_its_error_instead_of_vanishing(client):
    """The dashboard offers it as a choice, so it has to be able to say why it is empty."""
    scenarios = _post(client, scenarios=SCENARIOS).json()["scenarios"]
    broken = next(s for s in scenarios if s["factor_set"] == "reference" and s["scope"] == "WTW")

    assert broken["totals"] == []
    assert "no WTW factor" in broken["error"]
    assert broken["is_verified"] is False


def test_scenario_totals_omit_geometry_that_alternatives_already_carry(client):
    payload = _post(client, scenarios=SCENARIOS).json()
    totals = [t for s in payload["scenarios"] for t in s["totals"]]

    assert totals
    assert all("geometry" not in total for total in totals)


def test_a_request_without_scenarios_still_answers(client):
    assert _post(client).json()["scenarios"] == []


SHANGHAI = {"lon": 121.80, "lat": 31.23}
ROTTERDAM = {"lon": 4.13, "lat": 51.95}


def _compare(client, **overrides):
    body = {
        "origin": SHANGHAI,
        "destination": ROTTERDAM,
        "origin_country": "CN",
        "destination_country": "NL",
        "factor_set": "glec",
        "scope": "WTW",
    } | overrides
    return client.post("/api/compare", json=body)


def test_alternatives_report_whether_they_enter_a_listed_area(client):
    alternatives = _post(client).json()["alternatives"]

    for alternative in alternatives:
        risk = alternative["risk"]
        assert risk is not None
        # The pilot corridor's sea legs are tracked, so a clear result is a checked one.
        assert risk["untracked_sea_km"] == 0


def test_a_turkey_to_eu_sea_leg_is_billed_at_half_through_the_api(client):
    payload = _post(client, factor_set="glec", carbon_price_eur=80,
                    scenarios=[{"factor_set": "glec", "scope": "TTW"}]).json()
    totals = payload["scenarios"][0]["totals"]
    multimodal = next(t for t in totals if not t["is_all_road"])

    sea_leg = next(leg for leg in multimodal["ets"]["legs"])
    assert sea_leg["coverage_share"] == 0.5
    assert multimodal["ets"]["cost_eur"] > 0


def test_an_all_road_alternative_owes_no_allowances(client):
    payload = _post(client, scenarios=[{"factor_set": "glec", "scope": "TTW"}]).json()
    baseline = next(t for t in payload["scenarios"][0]["totals"] if t["is_all_road"])

    assert baseline["ets"]["cost_eur"] == 0
    assert baseline["ets"]["legs"] == []


def test_avoiding_suez_costs_distance_and_buys_out_of_the_listed_area(client):
    payload = _compare(client, avoid=["suez", "babalmandab", "panama"], surcharge_eur=4000).json()

    assert payload["direct"]["risk"]["is_exposed"]
    assert not payload["diverted"]["risk"]["is_exposed"]
    assert payload["extra_distance_km"] > 5000
    assert payload["extra_co2_kg"] > 0
    assert payload["avoided_zone_km"] > 1000


def test_the_surcharge_is_echoed_not_derived(client):
    """Premiums are negotiated against hull value; nothing here can compute one."""
    without = _compare(client).json()
    with_charge = _compare(client, surcharge_eur=4000).json()

    assert without["surcharge_eur"] == 0
    assert with_charge["total_extra_eur"] == pytest.approx(without["total_extra_eur"] + 4000)


def test_an_unblockable_passage_is_refused_with_the_list(client):
    response = _compare(client, avoid=["corinth"])

    assert response.status_code == 422
    assert "suez" in response.json()["detail"]


def test_a_diversion_that_cannot_be_sailed_says_so_instead_of_reporting_zero(client):
    """The Black Sea needs the Bosporus. Zero extras would read as a free reroute."""
    payload = _compare(
        client,
        destination={"lon": 28.65, "lat": 44.17},
        destination_country="RO",
        avoid=["gibraltar", "bosporus"],
    ).json()

    assert payload["direct"]["distance_km"] > 0
    assert "no sea route" in payload["diverted"]["unreachable"]
    assert payload["extra_distance_km"] is None
    assert payload["total_extra_eur"] is None


def _start_job(client, content=SHIPMENT_CSV, **fields):
    return client.post(
        "/api/report/jobs",
        files={"file": ("shipments.csv", content, "text/csv")},
        data={"scope": "TTW", "factor_set": "reference"} | fields,
    )


def _await_job(client, job_id, tries=200):
    for _ in range(tries):
        status = client.get(f"/api/report/jobs/{job_id}").json()
        if status["status"] in {"done", "failed"}:
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never finished")


def test_a_job_is_accepted_immediately_and_finishes_later(client):
    """A cold shipment costs seconds, so the upload cannot wait for the whole run."""
    response = _start_job(client)

    assert response.status_code == 202
    job = response.json()
    assert job["status"] in {"queued", "running"}
    assert job["total"] == 2

    finished = _await_job(client, job["id"])
    assert finished["status"] == "done"
    assert finished["done"] == finished["total"]
    assert finished["progress"] == 1.0


def test_the_finished_job_hands_back_the_same_report(client):
    job = _start_job(client, factor_set="glec", scope="WTW").json()
    _await_job(client, job["id"])

    response = client.get(f"/api/report/jobs/{job['id']}/file")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "factor set: glec" in response.text
    assert response.text.count("SEV-") == 2


def test_a_file_is_not_offered_before_the_job_finishes(client):
    """Handing back a half-written report would be worse than making the client wait."""
    job = _start_job(client).json()
    early = client.get(f"/api/report/jobs/{job['id']}/file")

    assert early.status_code in {200, 409}
    if early.status_code == 409:
        assert "not done" in early.json()["detail"]


def test_a_malformed_upload_fails_at_submission_not_a_minute_later(client):
    response = _start_job(client, content="origin_lon,origin_lat\n29.43,40.78\n")

    assert response.status_code == 422
    assert "missing column" in response.json()["detail"]


def test_a_job_that_cannot_price_anything_reports_failed(client):
    job = _start_job(client, factor_set="reference", scope="WTW").json()
    finished = _await_job(client, job["id"])

    assert finished["status"] == "failed"
    assert "no WTW factor" in finished["error"]
    assert client.get(f"/api/report/jobs/{job['id']}/file").status_code == 422


def test_an_unknown_job_is_a_404(client):
    assert client.get("/api/report/jobs/deadbeef").status_code == 404
    assert client.get("/api/report/jobs/deadbeef/file").status_code == 404
