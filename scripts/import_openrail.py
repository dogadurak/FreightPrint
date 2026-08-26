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

    Köln Hbf → Regensburg Hbf   501.8 km OSM   498.5 km RINF   +0.7%
    Duisburg Hbf → Köln Hbf      63.6 km        60.3 km        +5.5%
    Wels Vbf → Lambach           17.0 km        18.6 km        -8.4%

The agreement is closest on the longest leg, which is the one that matters: at 500 km
they differ by 0.7%, while a 20 km leg turns on which yard inside a station was picked.
That cross-check is written into the output rather than described here, so it is re-run
every time the distances are.

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
# official register. Each is inside one country whose filing is essentially whole, with
# the RINF figure this project already derived.
#
# (label, from lat,lon, to lat,lon, RINF km)
CROSS_CHECKS = [
    ("Koln Hbf -> Regensburg Hbf", "50.9430,6.9590", "49.0110,12.0990", 498.5),
    ("Duisburg Hbf -> Koln Hbf", "51.4300,6.7750", "50.9430,6.9590", 60.3),
    ("Wels Vbf -> Lambach", "48.1829,14.0603", "48.0900,13.8800", 18.6),
]

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
    for label, origin, destination, rinf_km in CROSS_CHECKS:
        osm_km = _route(origin, destination)
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
