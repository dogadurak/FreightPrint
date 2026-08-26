"""Building the rail network from the EU's own register.

Six of the seven rail legs in this project carry a hand-typed distance and no source at
all. What these tests defend is not that the new numbers are prettier, but that every
way of quietly inventing one is closed: a terminal that cannot be matched is refused
rather than snapped to whatever is nearest, a country that does not file is named rather
than estimated, and the match distance travels with the answer so a reader can see how
much to trust it.
"""

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "import_rinf", REPO / "scripts" / "import_rinf.py"
)
import_rinf = importlib.util.module_from_spec(spec)
sys.modules["import_rinf"] = import_rinf
spec.loader.exec_module(import_rinf)


# A toy network shaped like the real problem: two terminals with a station beside each,
# a direct-but-long way round and a shorter path through a junction, plus a station
# nobody should ever match to.
POINTS = {
    "opA": {"name": "Alfa Vbf", "lon": 10.0, "lat": 45.0},
    "opB": {"name": "Beta Gbf", "lon": 11.0, "lat": 45.0},
    "opJ": {"name": "Junction", "lon": 10.5, "lat": 45.2},
    "opFar": {"name": "Distant Halt", "lon": 20.0, "lat": 50.0},
}
EDGES = [
    {"start": "opA", "end": "opB", "km": 500.0},   # the long way round
    {"start": "opA", "end": "opJ", "km": 100.0},
    {"start": "opJ", "end": "opB", "km": 120.0},   # 220 via the junction: shorter
]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point the importer at a small graph, terminal file and leg list of our own."""
    raw = tmp_path / "graph.json"
    raw.write_text(json.dumps({"edges": EDGES, "points": POINTS}), encoding="utf-8")

    terminals = tmp_path / "terminals.geojson"
    terminals.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": "alfa", "name": "Alfa", "country": "IT",
                                           "type": "rail_terminal"},
         "geometry": {"type": "Point", "coordinates": [10.001, 45.001]}},
        {"type": "Feature", "properties": {"id": "beta", "name": "Beta", "country": "AT",
                                           "type": "rail_terminal"},
         "geometry": {"type": "Point", "coordinates": [11.002, 45.002]}},
        # Nowhere near anything in the graph, standing in for a terminal in a country
        # that does not file to the register.
        {"type": "Feature", "properties": {"id": "yonder", "name": "Yonder", "country": "TR",
                                           "type": "rail_terminal"},
         "geometry": {"type": "Point", "coordinates": [29.0, 41.0]}},
    ]}), encoding="utf-8")

    legs = tmp_path / "service_legs.csv"
    legs.write_text(
        "from_terminal,to_terminal,mode,ref_distance_km,transit_hours,"
        "frequency_per_week,schedule_source\n"
        "alfa,beta,rail,300,8,3,\n"
        "yonder,beta,rail,515,14,2,\n"
        "alfa,beta,sea,999,40,1,\n",       # a sea leg, which must be ignored
        encoding="utf-8",
    )

    # The tie from terminal to operational point is a committed decision, so the fixture
    # has to make one too. "yonder" is deliberately absent, standing in for a country
    # that does not file to the register.
    mapping = tmp_path / "map.csv"
    mapping.write_text(
        "terminal_id,uopid,op_name,country,basis,alternatives,note\n"
        "alfa,opA,Alfa Vbf,IT,freight station,none,chosen for the yard\n"
        "beta,opB,Beta Gbf,AT,freight station,none,chosen for the yard\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(import_rinf, "RAW", raw)
    monkeypatch.setattr(import_rinf, "TERMINALS", terminals)
    monkeypatch.setattr(import_rinf, "SERVICE_LEGS", legs)
    monkeypatch.setattr(import_rinf, "TERMINAL_MAP", mapping)
    monkeypatch.setattr(import_rinf, "OUT", tmp_path / "out.csv")
    return tmp_path


def rows():
    with import_rinf.OUT.open(encoding="utf-8") as f:
        return {(r["from_terminal"], r["to_terminal"]): r for r in csv.DictReader(f)}


def test_the_shortest_path_is_taken_not_the_direct_section(wired):
    """Two operational points can be joined directly and also, more cheaply, through a
    junction. A router uses the latter and so must this."""
    import_rinf.derive()

    row = rows()[("alfa", "beta")]
    assert float(row["rinf_km"]) == pytest.approx(220.0)
    assert int(row["path_sections"]) == 2


def test_the_graph_runs_both_ways(wired):
    """A section of line is track. Freight runs over it in both directions, so a
    directed graph would invent dead ends that the railway does not have."""
    import_rinf.derive()
    before = rows()[("alfa", "beta")]["rinf_km"]

    legs = import_rinf.SERVICE_LEGS
    legs.write_text(
        "from_terminal,to_terminal,mode,ref_distance_km,transit_hours,"
        "frequency_per_week,schedule_source\nbeta,alfa,rail,300,8,3,\n",
        encoding="utf-8",
    )
    import_rinf.derive()

    assert rows()[("beta", "alfa")]["rinf_km"] == before


def test_parallel_sections_keep_the_shorter(wired):
    """The register lists more than one section between the same pair of points. Keeping
    whichever arrived last would make the answer depend on download order."""
    raw = json.loads(import_rinf.RAW.read_text(encoding="utf-8"))
    raw["edges"].append({"start": "opA", "end": "opJ", "km": 40.0})
    import_rinf.RAW.write_text(json.dumps(raw), encoding="utf-8")

    import_rinf.derive()

    assert float(rows()[("alfa", "beta")]["rinf_km"]) == pytest.approx(160.0)


def test_a_terminal_with_no_entry_in_the_map_is_refused_not_guessed(wired):
    """Türkiye does not file to RINF, so Halkalı has no operational point. Reaching for
    the nearest foreign station instead would produce a confident wrong distance — which
    is what nearest-point matching actually did on the first attempt, tying German and
    Italian terminals to Austrian stations hundreds of kilometres away."""
    import_rinf.derive()

    row = rows()[("yonder", "beta")]
    assert row["rinf_km"] == "", "a terminal with no entry still produced a distance"
    assert "kayitta yok" in row["status"]
    assert "yonder (TR)" in row["status"], "the missing country is not named"


def test_the_hand_typed_distance_is_kept_beside_the_official_one(wired):
    """The point is the comparison. Overwriting the old number would hide whether the
    register agrees with what this project has been claiming."""
    import_rinf.derive()

    assert float(rows()[("alfa", "beta")]["reference_km"]) == 300.0
    assert float(rows()[("yonder", "beta")]["reference_km"]) == 515.0


def test_the_chosen_operational_point_is_named_in_every_row(wired):
    """The endpoint choice is a human decision, so the row has to say which point was
    chosen. A distance whose ends nobody can check is a distance nobody can check."""
    import_rinf.derive()

    row = rows()[("alfa", "beta")]
    assert row["from_opid"] == "opA"
    assert row["from_op_name"] == "Alfa Vbf"
    assert row["to_opid"] == "opB"


def test_an_unverified_endpoint_choice_is_declared(wired):
    """Two of the real terminals are tied to a city's main station because the freight
    terminal could not be found by name. That is a weaker claim and must not read as a
    sourced one."""
    mapping = import_rinf.TERMINAL_MAP
    mapping.write_text(
        mapping.read_text(encoding="utf-8").replace(
            "chosen for the yard,", "chosen for the yard,", 1
        ).replace("beta,opB,Beta Gbf,AT,freight station,none,chosen for the yard",
                  "beta,opB,Beta Gbf,AT,central station,none,UNVERIFIED CHOICE placeholder"),
        encoding="utf-8",
    )

    import_rinf.derive()

    assert rows()[("alfa", "beta")]["is_verified_choice"] == "no"


def test_only_rail_legs_are_derived(wired):
    """A sea leg has no business in a railway register."""
    import_rinf.derive()

    assert ("alfa", "beta") in rows()
    assert len(rows()) == 2, "a non-rail leg was routed"


def test_a_pair_with_no_connection_says_so_instead_of_guessing(wired):
    """The register has 94 disconnected components, so an unreachable pair is a real
    case rather than a hypothetical. A straight line drawn across the gap would look
    exactly like a route."""
    mapping = import_rinf.TERMINAL_MAP
    mapping.write_text(
        mapping.read_text(encoding="utf-8").replace(
            "beta,opB,Beta Gbf", "beta,opLone,Lone Halt"),
        encoding="utf-8",
    )

    import_rinf.derive()

    row = rows()[("alfa", "beta")]
    assert row["rinf_km"] == "", "routed to a point that carries no section"
    assert "rota yok" in row["status"]


def test_the_derivation_never_reaches_the_network(wired):
    """--fetch is a separate step, as with Eurostat: a re-derivation that needs the
    internet makes the stored graph pointless."""
    import inspect

    assert "requests" not in inspect.getsource(import_rinf.derive)
    assert "requests" in inspect.getsource(import_rinf._query)


def test_running_without_the_graph_says_where_to_get_it(wired, capsys):
    import_rinf.RAW.unlink()

    assert import_rinf.derive() == 0
    assert "--fetch" in capsys.readouterr().err
    assert not import_rinf.OUT.exists()


def test_the_check_fails_when_the_derived_file_has_drifted(wired, capsys):
    import_rinf.derive()
    import_rinf.OUT.write_text("from_terminal,to_terminal\nx,y\n", encoding="utf-8")

    assert import_rinf.check() == 1
    assert "FARK" in capsys.readouterr().err
    assert import_rinf.OUT.read_text(encoding="utf-8").startswith("from_terminal,to_terminal\nx,y")


def test_the_committed_derivation_matches_the_committed_graph():
    """The claim the whole file rests on, run against the real download."""
    if not import_rinf.RAW.exists():
        pytest.skip("rinf graph not present locally")

    assert import_rinf.check() == 0


def test_the_points_and_the_graph_share_identifiers():
    """The bug that produced 0 of 7 legs on the first run, and that nothing else caught.

    RINF publishes the same operational point under more than one URI shape: a section's
    opStart resolved to `OperationalPoint_HR74860` while the point itself was published
    as `AT01080`. Keying nodes on the URI tail gave a graph and a coordinate list with
    not one identifier in common, so every terminal was refused and the run looked like a
    coverage problem rather than a join bug.

    Every test above still passed, because they build both sides of the toy fixture with
    matching keys. Only the real download can show the two halves have drifted apart, so
    this asserts against it.
    """
    if not import_rinf.RAW.exists():
        pytest.skip("rinf graph not present locally")

    raw = json.loads(import_rinf.RAW.read_text(encoding="utf-8"))
    nodes = {e["start"] for e in raw["edges"]} | {e["end"] for e in raw["edges"]}
    located = set(raw["points"])

    assert located, "no point carries a position"
    shared = nodes & located
    assert len(shared) > len(located) * 0.9, (
        f"only {len(shared)} of {len(located)} located points appear in the graph — "
        "the two queries are keyed differently again"
    )


def test_the_committed_map_points_at_operational_points_that_exist():
    """A map entry naming a uopid the register does not carry would route from nowhere."""
    if not import_rinf.RAW.exists():
        pytest.skip("rinf graph not present locally")

    raw = json.loads(import_rinf.RAW.read_text(encoding="utf-8"))
    nodes = {e["start"] for e in raw["edges"]} | {e["end"] for e in raw["edges"]}
    mapping = import_rinf.load_terminal_map()

    assert mapping, "the terminal map is empty"
    missing = {t: m["uopid"] for t, m in mapping.items() if m["uopid"] not in nodes}
    assert not missing, f"mapped to points that carry no section: {missing}"


def test_a_border_detour_is_flagged_rather_than_published():
    """The state this derivation is actually in, pinned so it cannot be forgotten.

    Every section resolves and every section carries a length, but the graph has 94
    components and an average degree of 2.19 — too sparse for a railway. Trieste to Wels
    comes back as IT-SI-HU-SK-AT: the Tarvisio crossing into Austria is missing, so the
    shortest available path laps the Alps. Those kilometres are real sections and they
    are not the railway, so no row may claim "ok" while its route says otherwise.
    """
    if not import_rinf.OUT.exists():
        pytest.skip("rail distances not derived locally")

    with import_rinf.OUT.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row["route_countries"]:
                continue
            plausible = import_rinf._is_plausible(row["route_countries"])
            assert (row["status"] == "ok") == plausible, (
                f"{row['from_terminal']}->{row['to_terminal']} claims "
                f"{row['status']} over {row['route_countries']}"
            )


def test_the_method_is_right_where_the_filing_is_complete():
    """The evidence that makes the negative result credible.

    A derivation that produced only wrong answers could be a broken derivation. Inside
    Germany's network - 99% of it in one component - the register agrees with reality to
    a few per cent. So when the same code returns 748 km for a 420 km leg, the code is
    not what is wrong.
    """
    if not import_rinf.RAW.exists():
        pytest.skip("rinf graph not present locally")

    import networkx as nx

    raw = json.loads(import_rinf.RAW.read_text(encoding="utf-8"))
    graph = nx.Graph()
    for edge in raw["edges"]:
        existing = graph.get_edge_data(edge["start"], edge["end"], {}).get("km")
        if existing is None or edge["km"] < existing:
            graph.add_edge(edge["start"], edge["end"], km=edge["km"])

    # Köln Hbf to Regensburg Hbf, which is about 500 km of railway.
    path = nx.shortest_path(graph, "DE000KK", "DE00NRH", weight="km")
    km = nx.path_weight(graph, path, weight="km")

    assert 450 < km < 550, f"a domestic German route came back as {km:.0f} km"
    assert import_rinf._country_sequence(path) == ["DE"], "left the country to cross it"


def test_the_broken_filing_is_measured_rather_than_asserted():
    """Austria files 1,402 operational points joined by 1,334 sections - fewer edges than
    nodes - so its network is 95 islands and the largest holds under a quarter of it.
    Every rail leg in this corridor crosses Austria, which is why none of them can be
    sourced yet.

    Measured from the download, so the day Austria files the missing sections this test
    fails and the finding gets revisited rather than repeated from memory.
    """
    if not import_rinf.RAW.exists():
        pytest.skip("rinf graph not present locally")

    import networkx as nx

    raw = json.loads(import_rinf.RAW.read_text(encoding="utf-8"))
    graph = nx.Graph()
    for edge in raw["edges"]:
        graph.add_edge(edge["start"], edge["end"], km=edge["km"])
    integrity = import_rinf.network_integrity(graph)

    assert integrity["AT"][0] < import_rinf.MIN_NETWORK_INTEGRITY
    whole = [c for c, (share, _, _) in integrity.items()
             if share >= import_rinf.MIN_NETWORK_INTEGRITY]
    assert len(whole) >= 10, "the problem is no longer specific to one country"


def test_the_tarvisio_border_is_present_and_not_the_problem():
    """Pinned because this was read wrong once. The Italy-Austria crossing is filed:
    EU00116 joins Tarvisio Boscoverde to Thörl-Maglern. Blaming the border sent the
    investigation in the wrong direction for an hour."""
    if not import_rinf.RAW.exists():
        pytest.skip("rinf graph not present locally")

    raw = json.loads(import_rinf.RAW.read_text(encoding="utf-8"))
    edges = {(e["start"], e["end"]) for e in raw["edges"]}
    edges |= {(b, a) for a, b in edges}

    assert ("EU00116", "IT03015") in edges
    assert ("EU00116", "AT03665") in edges
