"""The Eurostat derivation, and whether the committed file is still what it claims.

`data/external/` holds two things: a response downloaded from Eurostat, and a CSV
derived from it that the engine's road assumption is checked against. The CSV is only
evidence for as long as those two agree — a derived file that has drifted from its
source is worse than no derived file, because it still looks like evidence.

So the derivation is a committed script rather than something done once by hand, and
this runs it.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "import_eurostat", REPO / "scripts" / "import_eurostat.py"
)
import_eurostat = importlib.util.module_from_spec(spec)
sys.modules["import_eurostat"] = import_eurostat
spec.loader.exec_module(import_eurostat)


def test_the_committed_csv_is_exactly_what_the_stored_response_produces():
    """The claim the whole external-data folder rests on."""
    assert import_eurostat.check() == 0


def test_the_raw_response_is_kept_beside_the_derivation():
    """Without it the CSV is a table somebody typed, and the point was that it is not."""
    raw = json.loads(import_eurostat.RAW.read_text(encoding="utf-8"))

    assert raw["dimension"]["geo"]["category"]["index"], "no geography in the response"
    assert "road_go_ta_vm" in import_eurostat.API


def test_a_reported_zero_is_written_as_zero_not_as_missing(tmp_path, monkeypatch):
    """The distinction this module exists for.

    Cyprus is an island with almost no international road haulage: in 2022 it reported
    2 million international vehicle-km, 1 loaded and 0 empty. That zero is an
    observation. Writing it as blank would say Cyprus did not report — the same mark the
    file uses for Turkey, which genuinely does not — and the first derivation did
    exactly that.
    """
    import csv

    monkeypatch.setattr(import_eurostat, "OUT", tmp_path / "out.csv")
    import_eurostat.derive()
    rows = {
        (r["geo"], r["year"]): r
        for r in csv.DictReader(import_eurostat.OUT.open(encoding="utf-8"))
    }

    cyprus = rows[("CY", "2022")]
    assert cyprus["intl_empty_mio_vkm"] == "0"
    assert cyprus["intl_empty_share"] == "0.0"

    # And a country-year with no breakdown at all still reads as absent.
    without = [r for r in rows.values() if r["intl_total_mio_vkm"] == ""]
    assert without, "every row has the split; this test no longer distinguishes anything"
    assert all(r["intl_empty_share"] == "" for r in without)


def test_a_country_that_did_not_answer_the_survey_is_absent_not_zero(tmp_path, monkeypatch):
    """Turkey and Serbia carry 754 km of the pilot corridor and report nothing. A zero
    for them would read as "they run full", which is the opposite of what is known."""
    import csv

    monkeypatch.setattr(import_eurostat, "OUT", tmp_path / "out.csv")
    import_eurostat.derive()
    present = {
        r["geo"] for r in csv.DictReader(import_eurostat.OUT.open(encoding="utf-8"))
    }

    assert "TR" not in present
    assert "RS" not in present


def test_the_check_fails_when_the_derived_file_has_drifted(tmp_path, monkeypatch, capsys):
    """A guard that cannot fail is decoration, and this one is the folder's only guard."""
    drifted = tmp_path / "drifted.csv"
    drifted.write_text("geo,geo_name,year\nXX,Nowhere,2024\n", encoding="utf-8")
    monkeypatch.setattr(import_eurostat, "OUT", drifted)

    assert import_eurostat.check() == 1
    assert "FARK" in capsys.readouterr().err
    assert drifted.read_text(encoding="utf-8").startswith("geo,geo_name,year\nXX"), \
        "a check that fails must leave the file it was checking alone"


def test_the_derivation_never_reaches_the_network():
    """`--fetch` is a separate step on purpose: re-deriving must work offline, or the
    response committed beside the CSV proves nothing."""
    import inspect

    body = inspect.getsource(import_eurostat.derive)

    assert "requests" not in body
    assert "requests" in inspect.getsource(import_eurostat.fetch)


def test_running_it_without_the_response_says_so_rather_than_writing_nothing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(import_eurostat, "RAW", tmp_path / "absent.json")
    monkeypatch.setattr(import_eurostat, "OUT", tmp_path / "out.csv")

    assert import_eurostat.derive() == 0
    assert "--fetch" in capsys.readouterr().err
    assert not (tmp_path / "out.csv").exists()
