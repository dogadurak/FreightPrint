"""The sea-distance arbiter, and the guards that stop it inventing one.

Sea is 87% of this corridor's emissions and until now its distance had one independent
cross-check in total. NGA Pub. 151 is the standard reference for port-to-port distance
and it settles the question — but it arrives as a two-column PDF, and every wrong number
this parser has produced so far looked entirely plausible until it was checked against
arithmetic. So most of what is defended here is the refusal, not the reading.
"""

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "import_pub151", REPO / "scripts" / "import_pub151.py"
)
import_pub151 = importlib.util.module_from_spec(spec)
sys.modules["import_pub151"] = import_pub151
spec.loader.exec_module(import_pub151)

needs_publication = pytest.mark.skipif(
    not import_pub151.TEXT.exists(), reason="Pub 151 not present locally"
)


def rows():
    with import_pub151.OUT.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


@needs_publication
def test_the_committed_table_is_what_the_publication_produces():
    assert import_pub151.check() == 0


@needs_publication
def test_a_distance_shorter_than_the_straight_line_is_refused():
    """The guard that matters most, and the one that was left out of this path once.

    Mersin's entry does not list Trieste at all. A block boundary overrunning into the
    next port produced "637 nautical miles" — 133% away from the reference and shorter
    than the straight line between the two, which is to say a voyage no ship could make.
    Reported as a disagreement it would have been the largest finding on the page.
    """
    import_pub151.derive()
    row = next(r for r in rows()
               if (r["from_terminal"], r["to_terminal"]) == ("mersin", "trieste"))

    assert row["adjusted_km"] == "", "an impossible distance was published as a finding"
    assert "kus ucusu" in row["status"]


@needs_publication
def test_a_leg_the_publication_omits_is_named_not_estimated():
    """Pub 151 lists a selection per port rather than every pair: Patrai's own entry names
    neither Istanbul nor Trieste. Absent has to read as absent."""
    import_pub151.derive()
    absent = [r for r in rows() if "yayimlanmamis" in r["status"]]

    assert {(r["from_terminal"], r["to_terminal"]) for r in absent} == {
        ("pendik", "patras"), ("trieste", "patras"),
    }
    assert all(r["adjusted_km"] == "" and r["delta_pct"] == "" for r in absent)


@needs_publication
def test_the_two_routes_round_greece_are_kept_apart():
    """A ro-ro ship cannot transit the Corinth Canal — the finding Faz 0 was built on —
    so collapsing the two published figures into one would average a voyage this traffic
    makes with one it cannot."""
    import_pub151.derive()
    trieste = [r for r in rows()
               if (r["from_terminal"], r["to_terminal"]) == ("pendik", "trieste")]

    routes = {r["via"] for r in trieste}
    assert routes == {"south of Greece", "via Corinth Canal"}
    around = next(r for r in trieste if r["via"] == "south of Greece")
    canal = next(r for r in trieste if r["via"] == "via Corinth Canal")
    assert float(around["nautical_miles"]) > float(canal["nautical_miles"])


@needs_publication
def test_the_marmara_offset_is_measured_rather_than_assumed():
    """Pendik and Yalova are not Istanbul, and Pub 151 lists neither. The gap is computed
    from the terminals' own coordinates; assuming it put Pendik at twice its real
    distance, which flattered the reference table by seven points."""
    import_pub151.derive()
    by_leg = {(r["from_terminal"], r["to_terminal"]): r for r in rows()}

    pendik = float(by_leg[("pendik", "trieste")]["terminal_offset_km"])
    yalova = float(by_leg[("yalova", "sete")]["terminal_offset_km"])

    assert 25 < pendik < 32, "Pendik is about 15 nautical miles from Istanbul"
    assert yalova > pendik, "Yalova is further down the Marmara than Pendik"


@needs_publication
def test_the_reference_table_reads_high_on_every_leg_that_has_an_arbiter():
    """The finding, such as it is. Three independent legs, all one way, between 9% and
    15% — a consistent bias rather than scatter. It is reported here and deliberately not
    yet applied to the engine: see the project README for what still has to be settled
    before the corridor's own distances change."""
    import_pub151.derive()
    priced = [r for r in rows() if r["status"] == "ok" and r["via"] != "via Corinth Canal"]

    assert len(priced) == 3
    deltas = [float(r["delta_pct"]) for r in priced]
    assert all(delta > 0 for delta in deltas), "the bias is no longer one-directional"
    assert 9 < min(deltas) and max(deltas) < 16


def test_a_port_block_never_runs_into_the_next_port():
    """The bug behind the false 637: with no next heading found the reader fell back to a
    fixed span and read a distance out of somebody else's list."""
    text = "\n".join([
        "ALFA,  ATLANTIS", "(10˚00'00\"N., 20˚00'00\"E.) to:", "Ports",
        "Beta, Atlantis, 100", "GAMMA,  ATLANTIS", "(11˚00'00\"N., 21˚00'00\"E.) to:",
        "Ports", "Beta, Atlantis, 900",
    ])

    block = import_pub151.port_block(text, "ALFA")

    assert "100" in block
    assert "900" not in block, "the block ran into the next port's entry"


def test_a_port_with_no_following_heading_is_refused_rather_than_guessed():
    text = "\n".join(["ALFA,  ATLANTIS", "(10˚00'00\"N., 20˚00'00\"E.) to:",
                      "Ports", "Beta, Atlantis, 100"])

    with pytest.raises(LookupError):
        import_pub151.port_block(text, "ALFA")


def test_a_wrapped_distance_is_rejoined_to_its_port():
    """The two-column layout splits an entry from its number. Read as it comes out of the
    extractor, "968" on its own line makes Istanbul-Ancona 96 nautical miles."""
    block = "\n".join(["Ports", "Ancona, Italy (via Corinth Canal),", "968",
                       "Bari, Italy, 851"])

    found = dict((via or name, nm) for via, nm, name in
                 [(v, n, r) for v, n, r in
                  import_pub151.published_distances(block, "Ancona")])

    assert list(found.values()) == [968], "the wrapped number was not rejoined"
