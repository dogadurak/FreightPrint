"""Build the rail network from the register the infrastructure managers actually file.

Seven rail legs in `data/service_legs.csv` carry hand-typed distances, and six of them
carry no source at all. `data/distance_uncertainty.csv` says so in as many words — rail
is "a placeholder that happens to be non-zero rather than a finding". This replaces the
placeholder.

The source is the European Union Agency for Railways' **Register of Infrastructure**,
which infrastructure managers are required to file under Implementing Regulation (EU)
2019/777. It is the official record of the network, not a survey and not a crowd-sourced
map, and its SPARQL endpoint is open:

    https://graph.data.era.europa.eu/repositories/rinf-plus

Finding that address was the work — everything documented is closed. See the Faz 9.0
section of `data/external/README.md` for what was tried.

    python scripts/import_rinf.py --fetch   # download the graph (slow, once)
    python scripts/import_rinf.py           # re-derive distances from the stored graph
    python scripts/import_rinf.py --check   # is the committed CSV still what it produces?

Fetching is a separate step, as with Eurostat: a derivation that needs the network makes
the stored copy pointless.

**What this cannot do.** RINF covers 27 EU/EEA countries. Türkiye and Serbia do not file,
so the Turkish end of Halkalı–Chitila has no entry. That leg is reported as unavailable
rather than estimated — the same treatment the Eurostat empty-running gap gets, and for
the same reason: "we have no observation" and "we assume it looks like its neighbour"
are different statements and only one of them is true.
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import speak_utf8  # noqa: E402

speak_utf8()

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "external" / "rinf_rail_graph.json"
OUT = REPO / "data" / "external" / "rail_distances_rinf.csv"
TERMINALS = REPO / "data" / "terminals.geojson"
SERVICE_LEGS = REPO / "data" / "service_legs.csv"

ENDPOINT = "https://graph.data.era.europa.eu/repositories/rinf-plus"
ERA = "http://data.europa.eu/949/"
GEO = "http://www.opengis.net/ont/geosparql#"
AUTH = "http://publications.europa.eu/resource/authority/country/"

# Every country the corridor could cross, plus the neighbours a real routing would
# transit. Over-including is cheap; a missing country would surface later as a phantom
# "no route" that is an artefact of the download rather than of the railway.
COUNTRIES = ["ITA", "AUT", "SVN", "HRV", "HUN", "SVK", "CZE", "DEU", "POL",
             "ROU", "BGR", "GRC"]

PAGE = 5000

# Terminals are tied to operational points by a committed file, not by proximity.
#
# Matching by coordinates was the intention and it does not work here: RINF publishes a
# position for about 2,700 of its 60,571 operational points, and in the corridor
# countries **1,401 of the 1,415 located points are Austrian**. Germany, Italy, Czechia
# and Romania publish none at all, so nearest-point matching tied Wels and Lambach
# correctly and put every other terminal 100-500 km from its "nearest" point.
#
# So the tie is made by name and written down. That is a judgement - "Duisburg" returns
# a dozen operational points and choosing the freight one is a decision - and a judgement
# belongs in a reviewable file with its reasoning and its rejected alternatives, not
# buried in a fuzzy string match at runtime. What it is emphatically not is a hand-typed
# distance: the endpoints are chosen by a person, the kilometres between them come from
# the register.
#
# Rows carrying `is_verified=no` are exactly that, and say so in the output.
TERMINAL_MAP = REPO / "data" / "rinf_terminal_map.csv"


def _query(query: str, timeout: int = 300) -> list[dict]:
    import requests

    response = requests.get(
        ENDPOINT, params={"query": query},
        headers={"Accept": "application/sparql-results+json"}, timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["results"]["bindings"]


# Nodes are keyed by uopid - the official Unique Operational Point ID - and never by the
# URI's trailing segment.
#
# RINF uses more than one URI shape for the same operational point: a section's opStart
# resolves to things like `OperationalPoint_HR74860` while the point itself is published
# under `AT01080`. Keying on the URI tail produced a graph whose 36,714 nodes shared not
# one identifier with the 1,415 points that carry coordinates, so every terminal came
# back unmatched and the first run reported 0 of 7 legs. Every one of the 47,198 sections
# resolves to a uopid at both ends, so this join is complete rather than lossy.


def fetch() -> None:
    """Download the sections and their endpoints, page by page.

    Paged because the endpoint will not return 47,000 rows in one response, and ordered
    because an unordered LIMIT/OFFSET walk can repeat and skip rows.
    """
    values = " ".join(f"<{AUTH}{c}>" for c in COUNTRIES)

    edges = []
    offset = 0
    while True:
        rows = _query(f"""
            SELECT ?s ?ua ?ub ?len WHERE {{
              VALUES ?country {{ {values} }}
              ?s a <{ERA}SectionOfLine> ;
                 <{ERA}inCountry> ?country ;
                 <{ERA}opStart> ?a ; <{ERA}opEnd> ?b ;
                 <{ERA}lengthOfSectionOfLine> ?len .
              ?a <{ERA}uopid> ?ua .
              ?b <{ERA}uopid> ?ub .
            }} ORDER BY ?s OFFSET {offset} LIMIT {PAGE}""")
        if not rows:
            break
        for row in rows:
            edges.append({
                "start": row["ua"]["value"],
                "end": row["ub"]["value"],
                "km": float(row["len"]["value"]),
            })
        print(f"  kesim {offset:>6} - {offset + len(rows):>6}")
        offset += PAGE

    # The points that carry a position, so this project's terminals can be matched to
    # them. Far fewer than the graph has nodes - see COORDINATE_COVERAGE below.
    points = {}
    offset = 0
    while True:
        rows = _query(f"""
            SELECT ?uopid ?label ?wkt WHERE {{
              VALUES ?country {{ {values} }}
              ?op a <{ERA}OperationalPoint> ;
                  <{ERA}inCountry> ?country ;
                  <{ERA}uopid> ?uopid ;
                  <{GEO}hasGeometry>/<{GEO}asWKT> ?wkt .
              OPTIONAL {{ ?op <http://www.w3.org/2000/01/rdf-schema#label> ?label }}
            }} ORDER BY ?uopid OFFSET {offset} LIMIT {PAGE}""")
        if not rows:
            break
        for row in rows:
            wkt = row["wkt"]["value"]
            if "POINT" not in wkt.upper():
                continue
            lon, lat = wkt[wkt.index("(") + 1:wkt.index(")")].split()
            points[row["uopid"]["value"]] = {
                "name": row.get("label", {}).get("value", ""),
                "lon": float(lon), "lat": float(lat),
            }
        print(f"  nokta {offset:>6} - {offset + len(rows):>6}")
        offset += PAGE

    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(
        json.dumps({"endpoint": ENDPOINT, "countries": COUNTRIES,
                    "edges": edges, "points": points}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"{len(edges)} kesim, {len(points)} nokta -> {RAW}")


def load_terminals() -> dict[str, dict]:
    data = json.loads(TERMINALS.read_text(encoding="utf-8"))
    return {
        f["properties"]["id"]: {
            "name": f["properties"]["name"],
            "country": f["properties"]["country"],
            "lon": f["geometry"]["coordinates"][0],
            "lat": f["geometry"]["coordinates"][1],
        }
        for f in data["features"]
    }


def rail_legs() -> list[tuple[str, str, float]]:
    with SERVICE_LEGS.open(encoding="utf-8") as f:
        return [
            (row["from_terminal"], row["to_terminal"], float(row["ref_distance_km"]))
            for row in csv.DictReader(f) if row["mode"] == "rail"
        ]


# **One country's filing is broken, and it is the one every leg of this corridor crosses.**
#
# This is the finding of Faz 10a, and it took three wrong answers to reach. The method is
# sound: inside a well-filed network the register is accurate to a few per cent — Köln Hbf
# to Regensburg Hbf comes back as 498.5 km over 137 sections against a real ~500, and
# Duisburg to Köln as 60.3 km against a real ~65.
#
# What fails is the data. Counting connected components per country over the corridor
# download:
#
#     RO/EL/BG/PL/CZ/SI 100%   HU/DE 99%   SK 96%   HR 93%   IT 89%   **AT 23%**
#
# Eleven of twelve countries file a network that is essentially whole. Austria files 1,402
# operational points joined by only 1,334 sections — fewer edges than nodes — leaving 95
# islands, the largest holding 23% of the country. Arnoldstein sits in a 32-point island;
# Wels is in the main one; nothing joins them inside Austria.
#
# So Trieste to Wels crosses the border correctly at Tarvisio, arrives at Thörl-Maglern,
# and finds no Austrian railway to continue on. Dijkstra does what it must and goes back
# into Italy, round through Slovenia, Hungary and Slovakia, and enters Austria from the
# far side: 182 sections, all real, adding to a route no train has ever run.
#
# A route touching more countries than this has detoured, so it is marked rather than
# published. Four is generous for a corridor crossing at most Italy, Austria and one
# neighbour.
MAX_PLAUSIBLE_COUNTRIES = 4

# Below this share in one component, a country's filing cannot carry a long-distance
# route and any distance crossing it is an artefact. Austria is at 0.23.
MIN_NETWORK_INTEGRITY = 0.80

# The 14 EU-prefixed points are border installations rather than a country of their own,
# so they do not count towards the total.
BORDER_PREFIX = "EU"


def _country_sequence(path: list[str]) -> list[str]:
    """The countries a path enters, in order, without repeats."""
    sequence: list[str] = []
    for node in path:
        code = node[:2]
        if code != BORDER_PREFIX and (not sequence or sequence[-1] != code):
            sequence.append(code)
    return sequence


def border_pairs(graph) -> set[frozenset]:
    """Which country pairs the register actually joins.

    A border crossing in RINF is an `EU`-prefixed operational point with a section
    reaching it from each side. The corridor download carries 19 such pairs, and the
    Tarvisio crossing is among them: EU00116 joins IT03015 Tarvisio Boscoverde to AT03665
    Thörl-Maglern, 0.81 km and 5.46 km either side, exactly as the railway does.

    So the borders are not the problem, and an earlier reading of this that blamed them
    was wrong. See NETWORK_INTEGRITY for what actually breaks the routing.
    """
    pairs = set()
    for node in graph:
        if not node.startswith(BORDER_PREFIX):
            continue
        touching = {n[:2] for n in graph[node] if not n.startswith(BORDER_PREFIX)}
        for a in touching:
            for b in touching:
                if a != b:
                    pairs.add(frozenset((a, b)))
    return pairs


def network_integrity(graph) -> dict[str, tuple[float, int, int]]:
    """How whole each country's filed network is: (largest share, points, components).

    The diagnostic that explains every suspect row, and the one worth reporting on its
    own. It is measured from the download rather than asserted, so it stays true when
    a country improves its filing.
    """
    import networkx as nx

    report = {}
    for code in sorted({n[:2] for n in graph} - {BORDER_PREFIX}):
        sub = graph.subgraph([n for n in graph if n.startswith(code)])
        if not sub:
            continue
        components = sorted(nx.connected_components(sub), key=len, reverse=True)
        report[code] = (
            len(components[0]) / sub.number_of_nodes(), sub.number_of_nodes(),
            len(components),
        )
    return report


def _is_plausible(countries: str) -> bool:
    return 0 < len(countries.split()) <= MAX_PLAUSIBLE_COUNTRIES


def find_by_name(term: str, limit: int = 40) -> list[tuple[str, str]]:
    """Every operational point whose label contains `term`, deduplicated by uopid.

    This exists because its absence produced a wrong entry in the terminal map. The
    first pass searched the *located* points - the ones carrying coordinates - and
    Germany publishes none, so "Koln Eifeltor" and "Duisburg Hafen" came back empty and
    were written down as "did not appear by name". They are both in the register. The
    search was looking at 2,700 of 60,571 points.

    Labels are versioned: the same point appears once per validity period. Only the
    identifier and the name matter here, so the periods are collapsed.
    """
    rows = _query(f"""
        SELECT DISTINCT ?uopid ?label WHERE {{
          ?op a <{ERA}OperationalPoint> ;
              <{ERA}uopid> ?uopid ;
              <http://www.w3.org/2000/01/rdf-schema#label> ?label .
          FILTER(CONTAINS(LCASE(STR(?label)), "{term.lower()}"))
        }} ORDER BY ?label LIMIT {limit * 4}""")
    seen = {}
    for row in rows:
        uopid = row["uopid"]["value"]
        if uopid in seen:
            continue
        # "Koln Eifeltor (from 2026-01-01 until 2026-12-31)" -> "Koln Eifeltor"
        label = row["label"]["value"].split(" (from ")[0]
        seen[uopid] = label
    return sorted(seen.items(), key=lambda pair: pair[1])[:limit]


def load_terminal_map() -> dict[str, dict]:
    """Which operational point stands for each terminal, and on what grounds."""
    if not TERMINAL_MAP.exists():
        return {}
    with TERMINAL_MAP.open(encoding="utf-8") as f:
        return {row["terminal_id"]: row for row in csv.DictReader(f)}


def derive() -> int:
    if not RAW.exists():
        print(f"ham grafik yok: {RAW}\n--fetch ile indirin.", file=sys.stderr)
        return 0

    import networkx as nx

    raw = json.loads(RAW.read_text(encoding="utf-8"))
    points = raw["points"]

    # Undirected: a section of line is track, and freight runs both ways over it. Where
    # two operational points are joined by more than one section the shortest is kept,
    # which is what a router would use.
    graph = nx.Graph()
    for edge in raw["edges"]:
        existing = graph.get_edge_data(edge["start"], edge["end"], {}).get("km")
        if existing is None or edge["km"] < existing:
            graph.add_edge(edge["start"], edge["end"], km=edge["km"])

    terminals = load_terminals()
    mapping = load_terminal_map()

    out_rows = []
    for start, end, reference_km in rail_legs():
        row = {
            "from_terminal": start, "to_terminal": end,
            "reference_km": reference_km,
            "rinf_km": "", "delta_pct": "", "path_sections": "", "route_countries": "",
            "from_opid": "", "to_opid": "",
            "from_op_name": "", "to_op_name": "",
            "is_verified_choice": "", "status": "",
        }
        unmapped = [t for t in (start, end) if t not in mapping]
        if unmapped:
            # Named, never estimated. Türkiye does not file to RINF, so Halkalı has no
            # entry and this leg keeps its hand-typed number with the gap on the record.
            row["status"] = "kayitta yok: " + ", ".join(
                f"{t} ({terminals[t]['country'] if t in terminals else '?'})"
                for t in unmapped
            )
            out_rows.append(row)
            continue

        a, b = mapping[start], mapping[end]
        # A choice the map itself flags as unverified must not be presented as sourced.
        # Read from its own column: this was a substring search over the free-text note,
        # which a note that merely *discusses* an earlier unverified choice would trip.
        verified = all(m["is_verified"] == "yes" for m in (a, b))
        row.update({
            "from_opid": a["uopid"], "to_opid": b["uopid"],
            "from_op_name": a["op_name"], "to_op_name": b["op_name"],
            "is_verified_choice": "yes" if verified else "no",
        })
        try:
            path = nx.shortest_path(graph, a["uopid"], b["uopid"], weight="km")
            km = round(nx.path_weight(graph, path, weight="km"), 1)
            row["rinf_km"] = km
            row["delta_pct"] = round((km - reference_km) / reference_km * 100, 1)
            row["path_sections"] = len(path) - 1
            row["route_countries"] = " ".join(_country_sequence(path))
            # A path that visits countries the corridor has no business in is the
            # signature of a missing border link, not of a longer railway. Reported per
            # row so a wrong number cannot be read as a finding.
            row["status"] = (
                "ok" if _is_plausible(row["route_countries"]) else "supheli: sinir sapmasi"
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound) as error:
            # A disconnection in the register, or a point that carries no section. Left
            # empty rather than filled with a straight line.
            row["status"] = f"rota yok: {type(error).__name__}"
        out_rows.append(row)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)

    routed = [r for r in out_rows if r["status"].startswith(("ok", "supheli"))]
    trusted = [r for r in out_rows if r["status"] == "ok"]
    suspect = [r for r in out_rows if r["status"].startswith("supheli")]
    print(f"{len(graph)} dugum, {graph.number_of_edges()} kenar")
    print(f"{len(routed)}/{len(out_rows)} bacak icin bir yol bulundu, "
          f"{len(trusted)} tanesi makul -> {OUT}")
    for row in out_rows:
        if not row["status"].startswith(("ok", "supheli")):
            print(f"  {row['from_terminal']:>10}->{row['to_terminal']:<11} {row['status']}")
            continue
        mark = "" if row["is_verified_choice"] == "yes" else "  [ucu dogrulanmamis]"
        print(f"  {row['from_terminal']:>10}->{row['to_terminal']:<11} "
              f"elle {row['reference_km']:>6.0f} km | RINF {row['rinf_km']:>7.1f} km "
              f"| fark {row['delta_pct']:+6.1f}%  {row['route_countries']}{mark}")

    integrity = network_integrity(graph)
    broken = {c: v for c, v in integrity.items() if v[0] < MIN_NETWORK_INTEGRITY}
    print(f"\n  Ag butunlugu (en buyuk bilesenin ulkedeki payi):")
    print("    " + "  ".join(
        f"{c} %{share * 100:.0f}" for c, (share, _, _) in sorted(
            integrity.items(), key=lambda kv: -kv[1][0])
    ))
    for code, (share, points, components) in sorted(broken.items(), key=lambda kv: kv[1][0]):
        print(f"    {code}: {points} nokta {components} adaya bolunmus, en buyugu "
              f"%{share * 100:.0f} — bu ulkeden gecen hicbir mesafe guvenilir degil")

    if suspect:
        print(f"\n  {len(suspect)} bacak sinir sapmasi gosteriyor: yol gercek "
              f"kesimlerden olusuyor ama demiryolundan degil. Sinir gecisleri kayitta "
              f"var ({len(border_pairs(graph))} ulke cifti); kirik olan ulusal aglar.")
        print("  Bu mesafeler kullanilmamalidir.")
    unverified = [r for r in routed if r["is_verified_choice"] == "no"]
    if unverified:
        print(f"\n  {len(unverified)} bacak, hangi isletme noktasinin terminal oldugu "
              f"dogrulanmamis bir uca dayaniyor (bkz. {TERMINAL_MAP.name}).")
    return len(out_rows)


def check() -> int:
    """Is the committed CSV still exactly what the stored graph produces?"""
    if not OUT.exists():
        print(f"turetilmis dosya yok: {OUT}", file=sys.stderr)
        return 1
    before = OUT.read_text(encoding="utf-8")
    if not derive():
        return 1
    after = OUT.read_text(encoding="utf-8")
    if before != after:
        OUT.write_text(before, encoding="utf-8")
        print("FARK: islenmis CSV, ham grafikten uretilene esit degil.", file=sys.stderr)
        return 1
    print("esit: islenmis CSV ham grafikten birebir ureliyor")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fetch", action="store_true", help="once grafigi indir")
    parser.add_argument("--check", action="store_true",
                        help="islenmis CSV hala ham grafikle ayni mi, hicbir sey yazma")
    parser.add_argument("--find", metavar="AD",
                        help="isletme noktalarini ada gore ara (terminal esleme icin)")
    args = parser.parse_args()

    if args.find:
        matches = find_by_name(args.find)
        print(f"{len(matches)} isletme noktasi: {args.find!r}")
        for uopid, label in matches:
            print(f"  {uopid:8} {label}")
        return 0
    if args.fetch:
        fetch()
    if args.check:
        return check()
    return 0 if derive() else 1


if __name__ == "__main__":
    sys.exit(main())
