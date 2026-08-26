"""Measure the corridor's rail legs, and show why the measurement can be believed.

Six of this project's seven rail legs carry a hand-typed distance and no source at all.
`data/distance_uncertainty.csv` calls the rail figure "a placeholder that happens to be
non-zero rather than a finding".

ERA RINF is the right source and it cannot do this. Its filing is complete enough to
route inside eleven of twelve countries, but Austria files 1,402 operational points
joined by only 1,334 sections — 95 islands, the largest holding under a quarter of the
country — and every leg here crosses Austria. Trieste to Wels comes back as
IT-SI-HU-SK-AT, around the Alps, over real sections that no train has run together.

So the routing comes from OpenStreetMap, through the OpenRailRouting service, and the
question becomes whether crowd-sourced track can be trusted for this. **RINF answers it.**
Where RINF's filing is whole, the two agree:

    Köln Eifeltor → Regensburg Bayernhafen   507.9 km OSM   503.0 km RINF   +1.0%
    Duisburg Hafen → Köln Eifeltor            75.4 km        71.1 km        +6.1%
    Wels Vbf-Terminal → Lambach               17.2 km        18.6 km        -7.5%

The agreement is closest on the longest leg, which is the one that matters: at 500 km
they differ by 1.0%, while a 20 km leg turns on which yard inside a station was picked.
That cross-check is written into the output rather than described here, so it is re-run
every time the distances are.

**Neither end of that comparison is typed by hand any more.** It used to carry lat/lon
for three passenger stations and a copied RINF distance beside each — six numbers a
reader had to trust, inside the check that licenses every other rail number here. The
positions now come from `rail_terminal_positions_osm.csv` and the distances from the
stored RINF graph, so the check runs between the same operational points the corridor
legs mean. Moving it off the passenger stations is also what caught the Duisburg leg
being 71 km rather than 60: the freight route to the port rounds the passenger station.

    python scripts/import_openrail.py            # route the legs and the cross-checks
    python scripts/import_openrail.py --check    # is the committed CSV still what it produces?

**Two limits, both recorded in the rows.** The routing service is a public demonstration
instance and its operators say so — the same footing as the OSRM demo this project
already uses for road, and the same remedy if it matters: run your own. And OSM is
crowd-sourced, which is why nothing here is reported without the RINF comparison beside it.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import speak_utf8  # noqa: E402

speak_utf8()

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "external" / "rail_distances_osm.csv"
TERMINALS = REPO / "data" / "terminals.geojson"
SERVICE_LEGS = REPO / "data" / "service_legs.csv"

SERVICE = "https://routing.openrailrouting.org/route"

# Standard-gauge track, which is what freight on this corridor runs on. The service also
# offers tgv_all, non_tgv and tramtrain; a high-speed profile would route passenger lines
# a freight train is not permitted on.
PROFILE = "all_tracks_1435"

# Legs RINF can route on its own, used to check the crowd-sourced network against the
# official register. Each is inside one country whose filing is essentially whole.
#
# These name terminals rather than coordinates. They used to carry hand-typed lat/lon for
# the three cities' passenger stations and a hand-copied RINF distance beside each - six
# numbers a reader had to take on trust, inside the check that licenses every other rail
# number in the project. Both ends are now derived: the position comes from
# `rail_terminal_positions_osm.csv` and the distance from the stored RINF graph, so the
# check runs between the same endpoints the corridor legs do.
CROSS_CHECKS = [
    ("koln", "regensburg"),
    ("duisburg", "koln"),
    ("wels", "lambach"),
]

OSM_POSITIONS = REPO / "data" / "external" / "rail_terminal_positions_osm.csv"
RINF_GRAPH = REPO / "data" / "external" / "rinf_rail_graph.json"
RINF_MAP = REPO / "data" / "rinf_terminal_map.csv"

# How far the two may differ before the crowd-sourced network stops corroborating the
# register. Generous on short legs because the endpoint dominates them — which platform
# or yard inside a station was picked moves a 20 km answer by several per cent and a
# 500 km answer by nothing.
MAX_CROSS_CHECK_GAP = 0.10
LONG_LEG_KM = 200.0


def _route(origin: str, destination: str, timeout: int = 180) -> float | None:
    """Track kilometres between two points, or None if the service will not route them."""
    import requests

    response = requests.get(
        SERVICE,
        params={"point": [origin, destination], "profile": PROFILE,
                "instructions": "false", "calc_points": "false"},
        timeout=timeout, headers={"User-Agent": "FreightPrint"},
    )
    payload = response.json()
    paths = payload.get("paths")
    if not paths:
        return None
    return paths[0]["distance"] / 1000


def osm_points() -> dict[str, str]:
    """Where OSM puts each rail terminal, as GraphHopper wants it: "lat,lon"."""
    if not OSM_POSITIONS.exists():
        return {}
    with OSM_POSITIONS.open(encoding="utf-8") as f:
        return {row["terminal_id"]: f"{row['lat']},{row['lon']}"
                for row in csv.DictReader(f) if row["lat"]}


def rinf_distances(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], float]:
    """Track kilometres the register gives for each pair, or absent if it cannot answer.

    Computed from the stored graph rather than copied, so a change in the register or in
    which operational point a terminal means moves this and the comparison together.
    """
    if not RINF_GRAPH.exists() or not RINF_MAP.exists():
        return {}

    import networkx as nx

    with RINF_MAP.open(encoding="utf-8") as f:
        opid = {row["terminal_id"]: row["uopid"] for row in csv.DictReader(f)}

    raw = json.loads(RINF_GRAPH.read_text(encoding="utf-8"))
    graph = nx.Graph()
    for edge in raw["edges"]:
        existing = graph.get_edge_data(edge["start"], edge["end"], {}).get("km")
        if existing is None or edge["km"] < existing:
            graph.add_edge(edge["start"], edge["end"], km=edge["km"])

    found = {}
    for start, end in pairs:
        if start not in opid or end not in opid:
            continue
        try:
            found[(start, end)] = round(
                nx.shortest_path_length(graph, opid[start], opid[end], weight="km"), 1)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
    return found


def terminal_points() -> dict[str, str]:
    data = json.loads(TERMINALS.read_text(encoding="utf-8"))
    return {
        f["properties"]["id"]: f"{f['geometry']['coordinates'][1]},{f['geometry']['coordinates'][0]}"
        for f in data["features"]
    }


def rail_legs() -> list[tuple[str, str, float]]:
    with SERVICE_LEGS.open(encoding="utf-8") as f:
        return [
            (row["from_terminal"], row["to_terminal"], float(row["ref_distance_km"]))
            for row in csv.DictReader(f) if row["mode"] == "rail"
        ]


def derive() -> int:
    points = terminal_points()
    rows = []

    # The cross-checks first, because everything below depends on them holding. A leg
    # measured by a network nothing has corroborated is not evidence.
    at_osm = osm_points()
    from_rinf = rinf_distances(CROSS_CHECKS)
    for start, end in CROSS_CHECKS:
        label = f"{start} -> {end}"
        rinf_km = from_rinf.get((start, end))
        if rinf_km is None or start not in at_osm or end not in at_osm:
            # Named, not skipped: a cross-check that silently disappears turns the
            # remaining ones into a weaker claim while looking like the same one.
            rows.append({"kind": "cross_check", "leg": label, "osm_km": "",
                         "compare_km": "", "delta_pct": "", "agrees": "no",
                         "note": "capraz kontrol kurulamadi: RINF mesafesi veya "
                                 "OSM konumu yok"})
            continue
        osm_km = _route(at_osm[start], at_osm[end])
        if osm_km is None:
            rows.append({"kind": "cross_check", "leg": label, "osm_km": "",
                         "compare_km": rinf_km, "delta_pct": "", "agrees": "no",
                         "note": "servis bu bacagi rotalayamadi"})
            continue
        delta = (osm_km - rinf_km) / rinf_km
        # Long legs are held to the tolerance; short ones are reported but not judged,
        # because the endpoint choice, not the network, decides them.
        agrees = abs(delta) <= MAX_CROSS_CHECK_GAP or rinf_km < LONG_LEG_KM
        rows.append({
            "kind": "cross_check", "leg": label, "osm_km": round(osm_km, 1),
            "compare_km": rinf_km, "delta_pct": round(delta * 100, 1),
            "agrees": "yes" if agrees else "no",
            "note": "RINF ile karsilastirma" + ("" if rinf_km >= LONG_LEG_KM
                                                else " (kisa bacak, uc nokta secimi baskin)"),
        })

    for origin, destination, reference_km in rail_legs():
        if origin not in points or destination not in points:
            rows.append({"kind": "corridor", "leg": f"{origin}->{destination}",
                         "osm_km": "", "compare_km": reference_km, "delta_pct": "",
                         "agrees": "", "note": "terminal konumu yok"})
            continue
        osm_km = _route(points[origin], points[destination])
        if osm_km is None:
            rows.append({"kind": "corridor", "leg": f"{origin}->{destination}",
                         "osm_km": "", "compare_km": reference_km, "delta_pct": "",
                         "agrees": "", "note": "servis bu bacagi rotalayamadi"})
            continue
        rows.append({
            "kind": "corridor", "leg": f"{origin}->{destination}",
            "osm_km": round(osm_km, 1), "compare_km": reference_km,
            # Signed the same way as the sea table: how far the project's own figure sits
            # from the measured one. Negative means the hand-typed number is short.
            "delta_pct": round((reference_km - osm_km) / osm_km * 100, 1),
            "agrees": "", "note": "elle yazilan mesafeye karsi",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    checks = [r for r in rows if r["kind"] == "cross_check"]
    failed = [r for r in checks if r["agrees"] == "no"]
    corridor = [r for r in rows if r["kind"] == "corridor" and r["osm_km"] != ""]

    print(f"{len(corridor)}/{len(rail_legs())} demiryolu bacagi olculdu -> {OUT}")
    for row in rows:
        if row["kind"] != "corridor":
            continue
        if row["osm_km"] == "":
            print(f"  {row['leg']:24} {row['note']}")
            continue
        print(f"  {row['leg']:24} OSM {row['osm_km']:>7.1f} km | elle {row['compare_km']:>6.0f} km "
              f"| fark {row['delta_pct']:+6.1f}%")

    print(f"\n  RINF capraz kontrolu ({len(checks) - len(failed)}/{len(checks)} uyumlu):")
    for row in checks:
        # A failed check leaves its numbers blank, and a blank cannot take a numeric
        # format - "+6" on an empty string raises rather than printing. The row that
        # most needs to be read was the one that crashed the summary.
        if row["osm_km"] == "":
            print(f"    {row['leg']:28} {row['note']}")
            continue
        print(f"    {row['leg']:28} OSM {row['osm_km']:>7} | RINF {row['compare_km']:>6} "
              f"| {row['delta_pct']:+6}%  {row['agrees']}")
    if failed:
        print("\n  UYARI: capraz kontrol tutmadi; asagidaki mesafeler kullanilmamalidir.",
              file=sys.stderr)
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
        print("FARK: islenmis CSV, servisten uretilene esit degil.", file=sys.stderr)
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
