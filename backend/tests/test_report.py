import pytest

from app.core import route as route_module
from app.core.network import haversine_km
from app.core.report import (
    ReportInputError,
    build_report,
    parse_shipments,
    report_to_csv,
)
from app.core.road import RoadRoute, RoadRoutingError

HEADER = "reference,origin_lon,origin_lat,destination_lon,destination_lat,tonnage\n"
ROW = "SEV-1,29.4306,40.7889,6.7735,51.2277,24\n"


@pytest.fixture(autouse=True)
def offline_road(monkeypatch):
    def fake_road_route(origin, destination):
        distance_km = haversine_km(origin, destination) * 1.3
        return RoadRoute(distance_km=distance_km, duration_h=distance_km / 70)

    monkeypatch.setattr(route_module, "road_route", fake_road_route)


def test_parses_a_minimal_file():
    shipments = parse_shipments(HEADER + ROW)

    assert len(shipments) == 1
    assert shipments[0].reference == "SEV-1"
    assert shipments[0].origin == (29.4306, 40.7889)
    assert shipments[0].tonnage == 24


def test_tonnage_defaults_when_the_column_is_absent():
    content = "origin_lon,origin_lat,destination_lon,destination_lat\n29.43,40.78,6.77,51.22\n"

    assert parse_shipments(content)[0].tonnage == 24


@pytest.mark.parametrize(
    "content",
    [
        "",
        "origin_lon,origin_lat\n29.43,40.78\n",
        HEADER,
        HEADER + "SEV-1,999,40.78,6.77,51.22,24\n",
        HEADER + "SEV-1,29.43,40.78,6.77,51.22,0\n",
        HEADER + "SEV-1,not-a-number,40.78,6.77,51.22,24\n",
    ],
)
def test_a_bad_file_is_refused_whole(content):
    """Half a report is worse than none: the caller cannot see which rows were misread."""
    with pytest.raises(ReportInputError):
        parse_shipments(content)


def test_report_prices_every_shipment():
    report = build_report(parse_shipments(HEADER + ROW + "SEV-2,29.43,40.78,16.37,48.21,18\n"))

    assert len(report.calculated) == 2
    assert report.total_co2_kg > 0
    assert all(row.route_label for row in report.calculated)


def test_one_unroutable_shipment_does_not_lose_the_others(monkeypatch):
    calls = {"n": 0}
    original = route_module.road_route

    def sometimes_failing(origin, destination):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RoadRoutingError("no road access")
        return original(origin, destination)

    monkeypatch.setattr(route_module, "road_route", sometimes_failing)
    report = build_report(parse_shipments(HEADER + ROW + "SEV-2,29.43,40.78,16.37,48.21,18\n"))

    assert len(report.failed) == 1
    assert len(report.calculated) == 1
    assert "no road access" in report.failed[0].status


def test_the_reported_option_is_the_lowest_emission_one():
    report = build_report(parse_shipments(HEADER + ROW), factor_set="glec")
    row = report.calculated[0]

    assert row.total_co2_kg <= (row.all_road_co2_kg or float("inf"))


def test_csv_carries_the_factor_set_and_its_sources():
    """A carbon figure whose basis is not stated cannot be checked by whoever receives it."""
    report = build_report(parse_shipments(HEADER + ROW), factor_set="glec", scope="WTW")
    output = report_to_csv(report)

    assert "factor set: glec" in output
    assert "scope: WTW" in output
    assert "GLEC Framework" in output


def test_csv_has_one_row_per_shipment_and_a_summary():
    report = build_report(parse_shipments(HEADER + ROW + "SEV-2,29.43,40.78,16.37,48.21,18\n"))
    lines = report_to_csv(report).splitlines()

    assert sum(line.startswith("SEV-") for line in lines) == 2
    assert any(line.startswith("# shipments calculated,2") for line in lines)
    assert any(line.startswith("# total CO2 kg,") for line in lines)


def test_a_failed_shipment_is_listed_with_its_reason(monkeypatch):
    def always_failing(origin, destination):
        raise RoadRoutingError("no road access")

    monkeypatch.setattr(route_module, "road_route", always_failing)
    report = build_report(parse_shipments(HEADER + ROW))
    output = report_to_csv(report)

    assert "failed: no road access" in output
    assert "# shipments failed,1" in output


def test_an_unusable_factor_set_fails_every_row_rather_than_inventing_one():
    report = build_report(parse_shipments(HEADER + ROW), factor_set="reference", scope="WTW")

    assert not report.calculated
    assert all("no WTW factor" in row.status for row in report.failed)
