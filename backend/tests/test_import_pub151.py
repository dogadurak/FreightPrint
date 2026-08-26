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
def test_the_mersin_leg_is_read_from_mersins_own_entry():
    """The number 637 arrived twice, from two different block-boundary faults, and both
    times looked like the largest finding on the page.

    Mersin's entry does not list Trieste. The first fault fell back to a fixed span when
    no next heading was found; the second was subtler - the heading pattern did not allow
    a comma inside the country, so "MESSINA,  SICILY, ITALY" was not recognised as a
    heading at all and Mersin's block ran through Messina, whose list does contain
    "Trieste, Italy, 637". A correct distance from the wrong port.

    The great-circle guard caught it both times, which is why the leg is now chained
    through Pula rather than silently wrong.
    """
    import_pub151.derive()
    row = {(r["from_terminal"], r["to_terminal"]): r for r in rows()}[("mersin", "trieste")]

    assert "Pula" in row["status"], "no longer chained through the Adriatic stand-in"
    assert float(row["nautical_miles"]) > 1077, "shorter than the straight line"
    assert 2300 < float(row["adjusted_km"]) < 2500


@needs_publication
def test_the_arithmetic_guard_still_rejects_an_impossible_distance():
    """No real leg trips it any more, so it is exercised directly. A guard that has
    stopped firing is indistinguishable from one that has stopped working."""
    mersin, trieste = (34.63, 36.79), (13.76, 45.64)
    straight = import_pub151.great_circle_nm(mersin, trieste)

    assert 637 < straight * import_pub151.MIN_OF_GREAT_CIRCLE
    assert 1306 > straight * import_pub151.MIN_OF_GREAT_CIRCLE


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

    pendik = float(by_leg[("pendik", "trieste")]["estimated_nm"])
    yalova = float(by_leg[("yalova", "sete")]["estimated_nm"])

    assert 14 < pendik < 17, "Pendik is about 15 nautical miles from Istanbul"
    assert yalova > pendik, "Yalova is further down the Marmara than Pendik"


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


@needs_publication
def test_a_leg_the_publication_omits_can_be_chained_through_a_neighbour():
    """Pub 151 lists neither Istanbul-Patrai nor Patrai-Trieste, and those were the legs
    the way-call correction depended on. Patrai's own entry names two ports that stand in:
    Derince, 27 nm east of Pendik, and Pula, 46 nm south of Trieste.

    This is not substitution. Each is a published distance to a named port plus the
    measured offset to the terminal wanted, with the sign argued from geography.
    """
    import_pub151.derive()
    by_leg = {(r["from_terminal"], r["to_terminal"]): r for r in rows()}

    pendik = by_leg[("pendik", "patras")]
    trieste = by_leg[("trieste", "patras")]

    assert "Derince" in pendik["status"] and "Pula" in trieste["status"]
    # Derince is further from the Dardanelles, so Pendik's leg is shorter than what was
    # published; Pula is short of Trieste, so Trieste's leg is longer.
    assert float(pendik["estimated_nm"]) < 0
    assert float(trieste["estimated_nm"]) > 0


@needs_publication
def test_the_neighbour_stands_in_for_the_right_terminal():
    """The regression that made Pula substitute for Patras instead of Trieste.

    Inferring which end a neighbouring port replaced from the sign of the correction gave
    Patras-Trieste as 2,014 km against a reference of 1,225 - a 39% disagreement pointing
    the opposite way to every other leg on the page. The terminal is named outright now.
    """
    import_pub151.derive()
    row = {(r["from_terminal"], r["to_terminal"]): r for r in rows()}[("trieste", "patras")]

    assert 1050 < float(row["adjusted_km"]) < 1200, "chained onto the wrong terminal"
    assert float(row["delta_pct"]) > 0, "this leg now disagrees the other way"


@needs_publication
def test_every_leg_with_an_arbiter_says_the_reference_is_high():
    """All six legs, one direction, 9% to 26%.

    The way-call explains far less than it first appeared. Weighting the published routes
    by the service mix - two Patras calls, one Bari, three direct - gives about 2,211 km
    against the carrier's 2,500, so roughly 1.6 points of the 14.7 are the way-call and
    the rest is not explained. The first reading put it at half, because it costed the
    detour using the project's own Patras figures, and those turned out to be the most
    inflated numbers in the table.
    """
    import_pub151.derive()
    priced = [r for r in rows()
              if r["adjusted_km"] and r["via"] != "via Corinth Canal"]

    assert len(priced) == 6, "every leg now has an arbiter; one has gone missing"
    deltas = [float(r["delta_pct"]) for r in priced]
    assert all(delta > 0 for delta in deltas), "the bias is no longer one-directional"
    assert 9 < min(deltas) and max(deltas) < 27


def test_an_entry_wrapped_inside_its_route_qualifier_is_rejoined():
    """The second wrap shape, and the one that made every Patrai entry unreadable. The
    publication breaks a line mid-bracket, not only after a comma."""
    block = "\n".join(["Ports", "Derince, Turkey (south of", "Greece), 648",
                       "Kerkira, Greece, 138"])

    found = import_pub151.published_distances(block, "Derince")

    assert found == [("south of Greece", 648, "Derince, Turkey (south of Greece), 648")]


@needs_publication
def test_the_published_number_is_kept_apart_from_this_project_s_arithmetic():
    """Every figure here is a published distance plus a hop to the terminal actually
    wanted — Istanbul to Pendik, Derince to Pendik, Pula to Trieste. Those hops are
    measured rather than guessed, but a great circle across open water is still an
    approximation of a sailing distance, and folded into one column it stops announcing
    itself. On this corridor it runs from 1.3% to 7.7% of the answer.
    """
    import_pub151.derive()
    priced = [r for r in rows() if r["adjusted_km"]]

    assert priced
    for row in priced:
        total = float(row["published_nm"]) + float(row["estimated_nm"])
        assert float(row["nautical_miles"]) == pytest.approx(total, abs=0.2)
        assert float(row["estimated_share"]) < 0.10
        assert row["is_representative"] == "yes"


def test_a_figure_that_is_mostly_estimate_is_flagged():
    """The threshold has to be able to fail. Past a fifth the number is describing this
    project's geometry more than the publication's survey, and calling it a reading of
    Pub 151 would be a claim about a source that supplied a minority of it."""
    mostly = import_pub151._row("a", "b", 1000.0, "", published_nm=100, estimated_nm=60)

    assert mostly["is_representative"] == "no"
    assert float(mostly["estimated_share"]) > import_pub151.MAX_ESTIMATED_SHARE


def test_a_rejected_row_keeps_the_number_it_refused():
    """So the refusal can be checked. A row that dropped its evidence would leave the
    next reader with a status line and nothing to test it against."""
    refused = import_pub151._row("a", "b", 2750.0, "", published_nm=637, rejected=True,
                                 status="elendi: kus ucusundan kisa")

    assert refused["published_nm"] == 637
    assert refused["adjusted_km"] == "" and refused["delta_pct"] == ""
