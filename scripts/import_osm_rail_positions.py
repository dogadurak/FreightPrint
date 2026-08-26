"""Ask OpenStreetMap where the rail terminals are, and measure what we typed by hand.

`scripts/source_terminals.py` checks every port coordinate against the position NGA
Pub. 151 publishes. The rail terminals had no such check, because the register they are
sourced from does not publish one: ERA RINF gives a position for about 2,700 of its
60,571 operational points, and **Germany, Italy, Czechia and Romania publish none at all**.

That gap turned into a real error. When `koln` and `duisburg` were corrected from the
passenger stations to the freight terminals they actually mean - Köln Eifeltor and
Duisburg Hafen - the `source_id` moved and the coordinate did not. The point kept
claiming to be a freight terminal while sitting at a passenger station several
kilometres away, and nothing in the project could tell.

OSM can. It is the same source the rail distances are already routed over
(`scripts/import_openrail.py`), so using it for the endpoints of those routes is
consistent rather than a new dependency.

    python scripts/import_osm_rail_positions.py            # download the positions
    python scripts/import_osm_rail_positions.py --check    # still what Overpass returns?

**The search term is a judgement and is written down.** OSM does not spell things the
way RINF does - RINF files `Ostrava-Kuncice` where OSM has `Ostrava-Kunčice`, and
Cologne's terminal is `Umschlagbahnhof Köln Eifeltor` in one and `Koln Eifeltor` in the
other. A fuzzy matcher would paper over that and pick the wrong yard silently, so the
term lives in `data/rinf_terminal_map.csv` beside the uopid, chosen by hand for the same
reason and reviewable in the same place. It is a regular expression, and several of them
are anchored on purpose: a bare `Lambach` also matches Lambach Markt and Neukirchen bei
Lambach, which are different places.

**The search is bounded by the coordinate it is checking**, which is circular by design:
it can tell you the point is 4 km from where you said, and it cannot tell you the point
is in the wrong country. That is the weaker claim and it is the one made.
"""

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import speak_utf8  # noqa: E402

speak_utf8()

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "external" / "rail_terminal_positions_osm.csv"
TERMINALS = REPO / "data" / "terminals.geojson"
TERMINAL_MAP = REPO / "data" / "rinf_terminal_map.csv"

# Tried in order, so a run works with either one up. The main instance is slow for this
# - forty seconds is normal - but it answers; the mirror is faster when it is up and was
# refusing connections entirely while this was written. Regional instances are not listed:
# overpass.osm.ch answers in a second and holds only its own country, so it would return
# an empty result that looks exactly like "this terminal is not in OSM".
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# How far around the coordinate under test to look. Wide enough that a terminal on the
# far side of a city is still found, narrow enough that a same-named station in the next
# region is not. Degrees, so the longitude span is generous at these latitudes.
SEARCH_LAT = 0.35
SEARCH_LON = 0.50

# Railway values that denote a place a train is loaded or stops, rather than a signal box,
# a switch or a stretch of line. `stop` is included because Italy files Campo Marzio that
# way; `yard` because that is what a freight terminal usually is.
PLACES = ("station", "halt", "yard", "stop")
PLACES_RE = "^(" + "|".join(PLACES) + ")$"

# How many times to go round the endpoint list before giving up on one terminal. Overpass
# answers 429 when it is busy and 504 when the query was too expensive for the moment;
# both clear on their own, so a failure here is a reason to wait rather than to stop.
ATTEMPTS = 3
BACKOFF_S = 15

EARTH_KM = 6371.0088


def great_circle_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lon1, lat1), (lon2, lat2) = a, b
    p = math.pi / 180
    cosine = (math.sin(lat1 * p) * math.sin(lat2 * p)
              + math.cos(lat1 * p) * math.cos(lat2 * p) * math.cos((lon2 - lon1) * p))
    return EARTH_KM * math.acos(max(-1.0, min(1.0, cosine)))


def _overpass(query: str) -> list[dict]:
    import requests

    last = ""
    for attempt in range(ATTEMPTS):
        for endpoint in ENDPOINTS:
            try:
                response = requests.post(
                    endpoint, data={"data": query},
                    headers={"User-Agent": "FreightPrint"}, timeout=180)
            except Exception as error:  # noqa: BLE001 - transport failure: try the mirror
                last = f"{endpoint} -> {error}"
                continue
            if response.status_code == 200:
                return response.json()["elements"]
            last = f"{endpoint} -> HTTP {response.status_code}"
        time.sleep(BACKOFF_S * (attempt + 1))
    raise RuntimeError(f"Overpass yanit vermedi: {last}")


def terminal_points() -> dict[str, tuple[float, float]]:
    data = json.loads(TERMINALS.read_text(encoding="utf-8"))
    return {f["properties"]["id"]: tuple(f["geometry"]["coordinates"])
            for f in data["features"]}


def mapped_terminals() -> list[dict]:
    with TERMINAL_MAP.open(encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("osm_search")]


def find(name: str, near: tuple[float, float]) -> list[dict]:
    """Every railway place matching `name` within the search box around `near`."""
    lon, lat = near
    box = f"{lat - SEARCH_LAT},{lon - SEARCH_LON},{lat + SEARCH_LAT},{lon + SEARCH_LON}"
    # Filtered on the server, not here. Asking for every `railway` object in the box and
    # sorting it out locally made the query expensive enough that Overpass answered 504:
    # a box this size holds every rail, switch and signal in a region.
    elements = _overpass(f"""[out:json][timeout:120];
(node[railway~"{PLACES_RE}"][name~"{name}",i]({box});
 way[railway~"{PLACES_RE}"][name~"{name}",i]({box}););
out center 40;""")
    found = []
    for element in elements:
        tags = element.get("tags", {})
        centre = element.get("center") or element
        found.append({
            "osm_type": element["type"], "osm_id": element["id"],
            "osm_name": tags.get("name", ""), "railway": tags["railway"],
            "lon": centre["lon"], "lat": centre["lat"],
        })
    return found


def derive() -> int:
    points = terminal_points()
    rows = []
    for entry in mapped_terminals():
        tid = entry["terminal_id"]
        if tid not in points:
            continue
        here = points[tid]
        # Printed before the call, not after: Overpass takes the better part of a minute
        # per terminal on a busy day, and a run that says nothing for five minutes is
        # indistinguishable from one that has hung.
        print(f"  {tid:11} sorgulanıyor: {entry['osm_search']}", flush=True)
        try:
            matches = find(entry["osm_search"], here)
        except RuntimeError as error:
            # One terminal the service would not answer for must not throw away the
            # seven it did. The row says so and `--check` will fail until it is refetched.
            matches = []
            print(f"  {tid:11} {error}", file=sys.stderr)
        if not matches:
            rows.append({
                "terminal_id": tid, "uopid": entry["uopid"],
                "searched_for": entry["osm_search"],
                "osm_type": "", "osm_id": "", "osm_name": "", "railway": "",
                "lon": "", "lat": "", "gap_km": "", "candidates": 0,
                "note": "OSM'de bu adla bir demiryolu yeri bulunamadi",
            })
            continue
        # Nearest match wins. The name already narrowed it to one terminal; what is left
        # to choose between is which node of that terminal OSM happens to carry, and the
        # nearest is the least surprising answer to "is our point where we said".
        best = min(matches, key=lambda m: great_circle_km(here, (m["lon"], m["lat"])))
        rows.append({
            "terminal_id": tid, "uopid": entry["uopid"],
            "searched_for": entry["osm_search"],
            "osm_type": best["osm_type"], "osm_id": best["osm_id"],
            "osm_name": best["osm_name"], "railway": best["railway"],
            "lon": round(best["lon"], 6), "lat": round(best["lat"], 6),
            "gap_km": round(great_circle_km(here, (best["lon"], best["lat"])), 2),
            "candidates": len(matches), "note": "",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    found = [r for r in rows if r["gap_km"] != ""]
    print(f"{len(found)}/{len(rows)} demiryolu terminali OSM'de bulundu -> {OUT}")
    for row in rows:
        if row["gap_km"] == "":
            print(f"  {row['terminal_id']:11} {row['note']}")
            continue
        print(f"  {row['terminal_id']:11} {row['gap_km']:>6.2f} km  "
              f"{row['railway']:8} {row['osm_name']} ({row['candidates']} aday)")
    return len(rows)


def check() -> int:
    if not OUT.exists():
        print(f"turetilmis dosya yok: {OUT}", file=sys.stderr)
        return 1
    before = OUT.read_text(encoding="utf-8")
    if not derive():
        return 1
    after = OUT.read_text(encoding="utf-8")
    if before != after:
        OUT.write_text(before, encoding="utf-8")
        print("FARK: islenmis CSV, Overpass'in dondurdugune esit degil.", file=sys.stderr)
        return 1
    print("esit")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="hicbir sey yazma, karsilastir")
    args = parser.parse_args()
    if args.check:
        return check()
    return 0 if derive() else 1


if __name__ == "__main__":
    sys.exit(main())
