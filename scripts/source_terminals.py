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

REPO = Path(__file__).resolve().parent.parent
TERMINALS = REPO / "data" / "terminals.geojson"
RINF_MAP = REPO / "data" / "rinf_terminal_map.csv"
PUB151_TEXT = REPO / "data" / "external" / "pub151.txt"

# How far a hand-typed coordinate may sit from the published one before it is a finding
# rather than a rounding. A port is a place with a footprint - a berth at one end of a
# harbour and the harbour master's office at the other can be a couple of kilometres
# apart - so this is generous. Beyond it, the point is somewhere else.
MAX_COORDINATE_GAP_KM = 15.0

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
    data = json.loads(TERMINALS.read_text(encoding="utf-8"))

    rows, problems = [], []
    for feature in data["features"]:
        props = feature["properties"]
        tid = props["id"]
        lon, lat = feature["geometry"]["coordinates"]

        source, source_id, gap = "", "", None
        heading = PUB151_NAME.get(tid, props["name"]).upper()
        if heading in positions:
            source = "NGA Pub. 151"
            source_id = heading
            gap = pub.great_circle_nm(positions[heading], (lon, lat)) * pub.KM_PER_NAUTICAL_MILE
            if gap > MAX_COORDINATE_GAP_KM:
                problems.append(
                    f"{tid}: elle yazilan konum, Pub 151'in verdiginden {gap:.1f} km uzakta"
                )
        elif tid in rinf:
            source = "ERA RINF"
            source_id = rinf[tid]["uopid"]

        rows.append({
            "id": tid, "name": props["name"], "country": props["country"],
            "type": props["type"], "source": source, "source_id": source_id,
            "coordinate_gap_km": round(gap, 1) if gap is not None else "",
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
        gap = f"  konum farki {row['coordinate_gap_km']:>5} km" if row["coordinate_gap_km"] != "" else ""
        origin = f"{row['source']} / {row['source_id']}" if row["source"] else "KAYNAK YOK"
        print(f"  {row['id']:11} {row['country']:3} {origin:34}{gap}")

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
