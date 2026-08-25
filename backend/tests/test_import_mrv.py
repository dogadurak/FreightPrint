"""The THETIS-MRV importer, which is the one piece of this project a person has to run.

EMSA publishes verified per-ship CO2 for every vessel over 5,000 GT calling at an EEA
port — the only independent observation of the ro-ro figure this project's headline
rests on. It cannot be fetched: the portal is a JavaScript application, the export sits
behind a reCAPTCHA, and no direct file URL is published. So the importer takes a file
somebody downloaded, and what these tests defend is that it never guesses.
"""

import importlib.util
import sys
from pathlib import Path

import openpyxl
import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("import_mrv", REPO / "scripts" / "import_mrv.py")
import_mrv = importlib.util.module_from_spec(spec)
sys.modules["import_mrv"] = import_mrv
spec.loader.exec_module(import_mrv)

HEADER = [
    "IMO Number", "Name", "Ship type", "Reporting Period",
    "Total CO2 emissions [m tonnes]",
    "CO2 emissions per transport work (mass) [g CO2 / m tonnes · n miles]",
]
SHIPS = [
    (9000001, "A", "Ro-ro ship", 2023, 41000, 25.4),
    (9000002, "B", "Ro-pax ship", 2023, 62000, 41.9),
    (9000003, "C", "Container ship", 2023, 90000, 8.1),
    (9000004, "D", "Ro-ro ship", 2023, 38000, 31.2),
    # MRV writes this literal string where a ship reported no transport work.
    (9000005, "E", "Ro-ro ship", 2023, 45000, "Division by zero!"),
    (9000006, "F", "Bulk carrier", 2023, 20000, 3.4),
]


@pytest.fixture
def workbook(tmp_path, monkeypatch):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Emission report"
    sheet.append(HEADER)
    for ship in SHIPS:
        sheet.append(ship)
    path = tmp_path / "mrv.xlsx"
    book.save(path)
    monkeypatch.setattr(import_mrv, "OUT", tmp_path / "roro.csv")
    return path


def test_it_finds_the_columns_it_needs_by_meaning_not_position():
    """EMSA has changed these headers between reporting periods, so matching is on a
    substring — but only on spellings that were actually seen, never on a guess."""
    found, missing = import_mrv.find_columns(HEADER)

    assert found["imo"] == 0
    assert found["ship_type"] == 2
    assert found["transport_work_mass"] == 5
    assert "distance" in missing, "a column that is genuinely absent must be reported"


def test_a_missing_column_stops_the_run_rather_than_being_filled_in(tmp_path, monkeypatch, capsys):
    """Guessing at a header is exactly the shortcut this project does not take."""
    book = openpyxl.Workbook()
    book.active.append(["IMO Number", "Name"])
    path = tmp_path / "thin.xlsx"
    book.save(path)
    monkeypatch.setattr(import_mrv, "OUT", tmp_path / "out.csv")

    assert import_mrv.derive(path) == 0
    assert "bulunamadi" in capsys.readouterr().err
    assert not (tmp_path / "out.csv").exists(), "wrote a file it could not fill honestly"


def test_only_ro_ro_ships_are_kept(workbook):
    """GLEC Table 45 describes a ro-ro fleet. Comparing it against a container ship's
    intensity would be comparing two different vessels and calling it validation."""
    import csv

    assert import_mrv.derive(workbook) == 3
    rows = list(csv.DictReader(import_mrv.OUT.open(encoding="utf-8")))

    kept = {row["ship_type"] for row in rows}
    assert kept == {"Ro-ro ship", "Ro-pax ship"}
    assert "Container ship" not in kept and "Bulk carrier" not in kept


def test_a_ship_that_reported_no_transport_work_is_dropped_not_zeroed(workbook):
    """MRV writes "Division by zero!" there. Read as a number it would be nothing; read
    as a ship with no intensity it would drag the median to the floor."""
    import csv

    import_mrv.derive(workbook)
    rows = list(csv.DictReader(import_mrv.OUT.open(encoding="utf-8")))

    assert "9000005" not in {row["imo"] for row in rows}
    assert all(float(row["kg_co2_per_tonne_km"]) > 0 for row in rows)


def test_the_unit_conversion_is_stated_and_correct(workbook):
    """MRV reports grams per tonne-nautical-mile; GLEC is kilograms per tonne-kilometre.
    Comparing the two without converting would be wrong by a factor of 1,852."""
    import csv

    import_mrv.derive(workbook)
    row = next(
        r for r in csv.DictReader(import_mrv.OUT.open(encoding="utf-8"))
        if r["imo"] == "9000001"
    )

    assert float(row["g_co2_per_tonne_nm"]) == pytest.approx(25.4)
    assert float(row["kg_co2_per_tonne_km"]) == pytest.approx(25.4 / 1.852 / 1000, rel=1e-4)


def test_running_it_with_no_file_explains_where_to_get_one(capsys):
    """The one step a person has to take, so the instruction has to be in the tool."""
    sys.argv = ["import_mrv.py"]

    assert import_mrv.main() == 1
    out = capsys.readouterr().out
    assert "mrv.emsa.europa.eu" in out
    assert "reCAPTCHA" in out, "does not say why it cannot fetch the file itself"
