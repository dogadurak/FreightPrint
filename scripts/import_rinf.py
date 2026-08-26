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
# Rows whose `basis` is UNVERIFIED are exactly that, and say so in the output.
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


# The graph is not yet complete enough to route across borders, and this is how that
# shows up.
#
# Every section resolves and every section carries a length, but the network they form
# has 94 connected components and an average degree of 2.19 — too sparse for a railway.
# Trieste to Wels comes back as IT-SI-HU-SK-AT over 182 sections: the Tarvisio crossing
# straight into Austria is not in the graph, so the shortest path available is a lap
# around the Alps. It is a real path over real sections, and it is not the railway.
#
# So a route whose country sequence is longer than this is marked suspect rather than
# published as a distance. Four countries is generous for a corridor that crosses at most
# Italy, Austria and one neighbour; a route touching more has almost certainly detoured.
MAX_PLAUSIBLE_COUNTRIES = 4

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


def _is_plausible(countries: str) -> bool:
    return 0 < len(countries.split()) <= MAX_PLAUSIBLE_COUNTRIES


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
        verified = not any("UNVERIFIED" in m["note"].upper() for m in (a, b))
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

    if suspect:
        print(f"\n  {len(suspect)} bacak sinir sapmasi gosteriyor: yol gercek "
              f"kesimlerden olusuyor ama demiryolundan degil. Grafikte eksik sinir "
              f"baglantisi var; bu mesafeler kullanilmamalidir.")
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
    args = parser.parse_args()

    if args.fetch:
        fetch()
    if args.check:
        return check()
    return 0 if derive() else 1


if __name__ == "__main__":
    sys.exit(main())
