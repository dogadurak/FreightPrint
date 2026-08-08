import pytest

from app.core import route as route_module
from app.core.network import haversine_km
from app.core.report import (
    ReportInputError,
    build_report,
    parse_shipments,
    read_upload,
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


def test_pricing_rows_in_parallel_gives_the_same_report(monkeypatch):
    """Rows are priced concurrently; a mismatched pairing would put one shipment's
    figures against another's reference and never announce itself."""
    import random
    import time

    random.seed(3)
    original = route_module.road_route

    def jittery(origin, destination):
        # Uneven timing, so a wrong pairing shows up instead of hiding behind even work.
        time.sleep(random.uniform(0.001, 0.02))
        return original(origin, destination)

    monkeypatch.setattr(route_module, "road_route", jittery)
    content = HEADER + "".join(
        f"SEV-{i:02d},29.4,40.7,{6.0 + i * 0.5:.2f},48.0,24\n" for i in range(8)
    )
    shipments = parse_shipments(content)

    serial = build_report(shipments, concurrency=1)
    parallel = build_report(shipments, concurrency=4)

    assert [r.shipment.reference for r in parallel.rows] == [s.reference for s in shipments]
    assert report_to_csv(serial) == report_to_csv(parallel)


def test_progress_is_reported_once_per_row_and_only_moves_forward():
    shipments = parse_shipments(
        HEADER + "".join(f"SEV-{i},29.4,40.7,{6.0 + i:.1f},48.0,24\n" for i in range(5))
    )
    seen: list[int] = []

    build_report(shipments, concurrency=2, on_progress=seen.append)

    assert seen == sorted(seen)
    assert seen[-1] == len(shipments)


def _workbook(rows) -> bytes:
    """A real .xlsx, built the way Excel would: numbers as numbers, not as text."""
    import io

    import openpyxl

    workbook = openpyxl.Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


SHEET_HEADER = ["reference", "origin_lon", "origin_lat", "destination_lon", "destination_lat", "tonnage"]


def test_a_workbook_reads_the_same_as_the_equivalent_csv():
    raw = _workbook([SHEET_HEADER, ["SEV-1", 29.4306, 40.7889, 6.7735, 51.2277, 24]])

    from_sheet = parse_shipments(read_upload(raw, "shipments.xlsx"))
    from_csv = parse_shipments(HEADER + ROW)

    assert from_sheet == from_csv


def test_excel_integers_do_not_reach_the_report_as_decimals():
    """Excel stores every number as a float, so a reference of 1001 arrives as 1001.0."""
    raw = _workbook([SHEET_HEADER, [1001, 29.4306, 40.7889, 6.7735, 51.2277, 24]])

    shipment = parse_shipments(read_upload(raw, "shipments.xlsx"))[0]

    assert shipment.reference == "1001"
    assert shipment.tonnage == 24


def test_trailing_blank_rows_excel_leaves_behind_are_not_shipments():
    raw = _workbook(
        [SHEET_HEADER, ["SEV-1", 29.4306, 40.7889, 6.7735, 51.2277, 24], [None] * 6, [None] * 6]
    )

    assert len(parse_shipments(read_upload(raw, "shipments.xlsx"))) == 1


def test_a_semicolon_separated_export_is_read_as_one():
    """Excel across most of Europe writes CSV with semicolons, because the comma is
    already its decimal mark."""
    content = (
        "reference;origin_lon;origin_lat;destination_lon;destination_lat;tonnage\n"
        "SEV-1;29,4306;40,7889;6,7735;51,2277;24\n"
    )

    shipment = parse_shipments(content)[0]

    assert shipment.origin == (29.4306, 40.7889)
    assert shipment.tonnage == 24


def test_a_grouped_number_is_refused_rather_than_guessed():
    """1,234 is one thousand to an English Excel and 1.234 to a Turkish one. Guessing
    would put a shipment a thousand kilometres from where it is."""
    content = HEADER.replace("origin_lon", "origin_lon") + "SEV-1,\"1,234\",40.78,6.77,51.22,24\n"

    with pytest.raises(ReportInputError, match="origin_lon"):
        parse_shipments(content)


def test_a_file_named_xlsx_that_is_not_one_says_so():
    with pytest.raises(ReportInputError, match="named like a workbook"):
        read_upload(b"reference,origin_lon\nSEV-1,29.4\n", "shipments.xlsx")


def test_plain_csv_still_arrives_through_the_same_door():
    assert read_upload((HEADER + ROW).encode("utf-8-sig")) == HEADER + ROW
