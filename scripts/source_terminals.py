"""Tie each terminal to an outside record, and check the coordinates we typed by hand.

`terminals.geojson` carried four properties — id, name, country, type — and not one of
them said where the place came from. Sixteen points typed from knowledge, holding up a
corridor whose every distance is measured between them.

Two sources already in this repository cover most of it, so nothing new is downloaded:

  * **NGA Pub. 151** publishes a position for every port it lists (5 terminals).
  * **ERA RINF** publishes a unique operational point ID for the rail terminals, already
    chosen and reasoned in `data/rinf_terminal_map.csv` (8 terminals).

    python scripts/source_terminals.py            # report what can be sourced
    python scripts/source_terminals.py --write    # write source/source_id back

**The rail terminals are now checked too, against OpenStreetMap** — see
`scripts/import_osm_rail_positions.py`. RINF publishes no position for most of them, so
until now nothing in the project could tell whether a rail coordinate was where its
source_id said. The answer is that they are city-level: every one sits 3.5-4.0 km from
the yard OSM names, where the ports sit under 1 km from their published position. That is
reported, not corrected — the coordinate is the project's own and OSM is the outside
observation beside it, which is the same treatment Eurostat and Pub. 151 get.

**Four terminals have no outside record and all four are Turkish** — Pendik, Yalova,
Ambarlı, Halkalı. That is the third time the same gap has appeared: Türkiye does not
report to Eurostat's road survey, does not file to RINF, and is not in Pub 151's port
list beyond Istanbul, Derince and Mersin. It is a fact about European reference data,
not about this project, and it is marked rather than filled in.

**UNECE's own UN/LOCODE download was the obvious fourth source and it is not reachable**:
every URL under unece.org and service.unece.org answers 403 to an automated request. The
community mirrors disagree with each other on size (2.0 MB against 7.3 MB), so neither
is usable under this project's rules without pinning the real publication first.
"""

import argparse
import csv
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import speak_utf8  # noqa: E402

speak_utf8()

REPO = Path(__file__).resolve().parent.parent
TERMINALS = REPO / "data" / "terminals.geojson"
RINF_MAP = REPO / "data" / "rinf_terminal_map.csv"
PUB151_TEXT = REPO / "data" / "external" / "pub151.txt"
OSM_POSITIONS = REPO / "data" / "external" / "rail_terminal_positions_osm.csv"

# How far a hand-typed coordinate may sit from the published one before it is a finding
# rather than a rounding. A port is a place with a footprint - a berth at one end of a
# harbour and the harbour master's office at the other can be a couple of kilometres
# apart - so this is generous. Beyond it, the point is somewhere else.
MAX_COORDINATE_GAP_KM = 15.0

# Tighter for rail, because the comparison is tighter: Pub 151 gives one point for a
# harbour that can be kilometres across, whereas OSM names the yard itself.
#
# **What this threshold cannot do**, said plainly because the measured gaps say it. Every
# rail terminal here comes back 3.5-4.0 km from the OSM node for the operational point it
# names, while the ports come back under 1 km. The rail coordinates were typed at city
# level - Köln's sat at the Hauptbahnhof while its source_id named Eifeltor - and in these
# cities the freight yard and the main station are under 5 km apart. So no threshold in
# this range separates "at the terminal" from "at the city", and this one does not claim
# to. It catches the gross error: a point in the wrong city, or a lat/lon transposed.
MAX_RAIL_COORDINATE_GAP_KM = 5.0

# Pub 151 spells some of these its own way, and Italy files in upper case.
PUB151_NAME = {"patras": "PATRAI", "sete": "SETE", "trieste": "TRIESTE",
               "bari": "BARI", "mersin": "MERSIN"}


def _pub151():
    spec = importlib.util.spec_from_file_location(
        "import_pub151", REPO / "scripts" / "import_pub151.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["import_pub151"] = module
    spec.loader.exec_module(module)
    return module


def published_positions() -> dict[str, tuple[float, float]]:
    """Every port position Pub 151 prints, keyed by the heading it appears under."""
    if not PUB151_TEXT.exists():
        return {}
    pub = _pub151()
    text = PUB151_TEXT.read_text(encoding="utf-8")
    found = {}
    for match in pub.HEADER.finditer(text):
        name = match.group(1).strip().upper()
        if name in found:
            continue
        position = pub._position(text[match.end():match.end() + 160])
        if position:
            found[name] = position
    return found


def osm_positions() -> dict[str, dict]:
    """Where OSM puts each rail terminal, downloaded by import_osm_rail_positions.py.

    Read from the committed file rather than fetched, so this script stays offline.
    """
    if not OSM_POSITIONS.exists():
        return {}
    with OSM_POSITIONS.open(encoding="utf-8") as f:
        return {row["terminal_id"]: row for row in csv.DictReader(f) if row["gap_km"]}


def rinf_ids() -> dict[str, dict]:
    if not RINF_MAP.exists():
        return {}
    with RINF_MAP.open(encoding="utf-8") as f:
        return {row["terminal_id"]: row for row in csv.DictReader(f)}


def review() -> tuple[list[dict], list[str]]:
    """What each terminal can be tied to, and how far off its coordinate is."""
    pub = _pub151()
    positions = published_positions()
    rinf = rinf_ids()
    osm = osm_positions()
    data = json.loads(TERMINALS.read_text(encoding="utf-8"))

    rows, problems = [], []
    for feature in data["features"]:
        props = feature["properties"]
        tid = props["id"]
        lon, lat = feature["geometry"]["coordinates"]

        source, source_id, gap, checked_against = "", "", None, ""
        heading = PUB151_NAME.get(tid, props["name"]).upper()
        if heading in positions:
            source = "NGA Pub. 151"
            source_id = heading
            gap = pub.great_circle_nm(positions[heading], (lon, lat)) * pub.KM_PER_NAUTICAL_MILE
            checked_against = "NGA Pub. 151"
            if gap > MAX_COORDINATE_GAP_KM:
                problems.append(
                    f"{tid}: elle yazilan konum, Pub 151'in verdiginden {gap:.1f} km uzakta"
                )
        elif tid in rinf:
            source = "ERA RINF"
            source_id = rinf[tid]["uopid"]

        # RINF publishes no position for most of these, so the coordinate is checked
        # against OSM instead - the same network the rail distances are routed over.
        # A port already checked against Pub 151 keeps that answer: it is the published
        # one, and two gaps in one column would say less than either alone.
        if gap is None and tid in osm:
            gap = float(osm[tid]["gap_km"])
            checked_against = f"OSM {osm[tid]['osm_name']}"
            if gap > MAX_RAIL_COORDINATE_GAP_KM:
                problems.append(
                    f"{tid}: konum, OSM'in {osm[tid]['osm_name']} icin verdiginden "
                    f"{gap:.1f} km uzakta (kaynak {source_id} o terminali gosteriyor)"
                )

        rows.append({
            "id": tid, "name": props["name"], "country": props["country"],
            "type": props["type"], "source": source, "source_id": source_id,
            "coordinate_gap_km": round(gap, 1) if gap is not None else "",
            "checked_against": checked_against,
        })
    return rows, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true",
                        help="source ve source_id alanlarini terminals.geojson'a yaz")
    args = parser.parse_args()

    rows, problems = review()
    sourced = [r for r in rows if r["source"]]
    print(f"{len(sourced)}/{len(rows)} terminal bir dis kayda baglanabiliyor")
    for row in rows:
        gap = (f"  konum farki {row['coordinate_gap_km']:>5} km "
               f"({row['checked_against']})") if row["coordinate_gap_km"] != "" else ""
        origin = f"{row['source']} / {row['source_id']}" if row["source"] else "KAYNAK YOK"
        print(f"  {row['id']:11} {row['country']:3} {origin:34}{gap}")

    unchecked = [r for r in rows if r["coordinate_gap_km"] == ""]
    if unchecked:
        print("\n  konumu hicbir dis kayda karsi olculmemis: "
              + ", ".join(r["id"] for r in unchecked))

    unsourced = [r for r in rows if not r["source"]]
    if unsourced:
        countries = sorted({r["country"] for r in unsourced})
        print(f"\n  kaynaksiz: {', '.join(r['id'] for r in unsourced)} "
              f"(hepsi {', '.join(countries)})")

    if problems:
        print("\nKONUM UYUSMAZLIGI:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)

    if args.write:
        data = json.loads(TERMINALS.read_text(encoding="utf-8"))
        by_id = {r["id"]: r for r in rows}
        for feature in data["features"]:
            row = by_id[feature["properties"]["id"]]
            # Written even when empty: "no outside record" is the finding for the Turkish
            # terminals, and a missing key would read as an oversight instead.
            feature["properties"]["source"] = row["source"]
            feature["properties"]["source_id"] = row["source_id"]
        TERMINALS.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nyazildi -> {TERMINALS}")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
