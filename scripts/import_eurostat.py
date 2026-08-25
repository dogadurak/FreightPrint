"""Turn Eurostat's empty-running survey into the file this engine is checked against.

GLEC builds a 30% empty return into every road factor here. Nothing in this project
could say whether that describes the traffic this corridor crosses — the assumption was
inside the number and there was no way to look at it from outside.

Eurostat's `road_go_ta_vm` can: it publishes vehicle-kilometres cross-tabulated by
loading status, so the empty share is a division rather than a model. This script does
that division and nothing else.

    python scripts/import_eurostat.py            # re-derive from the stored JSON
    python scripts/import_eurostat.py --fetch    # download a fresh copy first
    python scripts/import_eurostat.py --check    # is the committed CSV still what the
                                                 # JSON produces? exits non-zero if not

The raw response is committed beside the derived CSV, and `--check` runs in the test
suite. Together that is the whole point: a reader can see the observation, the response
it came from, and the one line of arithmetic between them, without taking any of it on
trust.

**International rather than overall is what the corridor is judged against.** Long-haul
is planned and local distribution is not, so the international rate is consistently the
lower of the two, and holding a Turkey-to-Germany run against the overall figure would
judge it on traffic it does not do.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "external" / "eurostat_road_go_ta_vm.json"
OUT = REPO / "data" / "external" / "empty_running_eurostat.csv"

DATASET = "road_go_ta_vm"
API = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
    f"{DATASET}?format=JSON&lang=EN&unit=MIO_VKM&tra_type=TOTAL"
    "&loadstat=TOTAL&loadstat=LOADED&loadstat=EMPTY"
    "&tra_cov=TOTAL&tra_cov=NAT&tra_cov=INTL"
)

FIELDS = [
    "geo", "geo_name", "year",
    "total_mio_vkm", "empty_mio_vkm", "empty_share",
    "intl_total_mio_vkm", "intl_empty_mio_vkm", "intl_empty_share",
]


def fetch() -> None:
    """Replace the stored response. Kept separate from the derivation on purpose: a
    re-derivation must be reproducible offline, or the JSON beside the CSV proves
    nothing."""
    import requests

    print(f"indiriliyor: {DATASET}")
    response = requests.get(API, timeout=120)
    response.raise_for_status()
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(
        json.dumps(response.json(), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"  -> {RAW}")


def _cell(raw: dict, order: list[str], sizes: list[int], keys: dict[str, int]):
    """One value out of the flat array a JSON-stat response carries.

    Eurostat publishes the cube as a single list plus its dimension sizes, so a cell is
    addressed by turning the per-dimension indices into one offset. The values map is
    sparse — a country that did not report simply has no entry — and that absence is the
    thing this whole exercise refuses to paper over.
    """
    offset = 0
    for name, size in zip(order, sizes):
        offset = offset * size + keys[name]
    return raw["value"].get(str(offset))


def derive() -> int:
    if not RAW.exists():
        print(f"ham yanit yok: {RAW}\n--fetch ile indirin.", file=sys.stderr)
        return 0

    raw = json.loads(RAW.read_text(encoding="utf-8"))
    order, sizes = raw["id"], raw["size"]
    index = {name: raw["dimension"][name]["category"]["index"] for name in order}
    labels = raw["dimension"]["geo"]["category"]["label"]

    fixed = {"freq": index["freq"]["A"], "tra_type": index["tra_type"]["TOTAL"],
             "unit": index["unit"]["MIO_VKM"]}

    rows = []
    for geo, geo_i in index["geo"].items():
        for year, time_i in index["time"].items():
            def value(loadstat: str, tra_cov: str):
                return _cell(raw, order, sizes, fixed | {
                    "loadstat": index["loadstat"][loadstat],
                    "tra_cov": index["tra_cov"][tra_cov],
                    "geo": geo_i, "time": time_i,
                })

            total, empty = value("TOTAL", "TOTAL"), value("EMPTY", "TOTAL")
            # No total, no observation. A country-year that reported nothing is left out
            # rather than filled in from a neighbour or an earlier year.
            if not total or empty is None:
                continue

            intl_total, intl_empty = value("TOTAL", "INTL"), value("EMPTY", "INTL")
            has_split = bool(intl_total) and intl_empty is not None

            rows.append({
                "geo": geo,
                "geo_name": labels[geo],
                "year": int(year),
                "total_mio_vkm": round(total),
                "empty_mio_vkm": round(empty),
                "empty_share": round(empty / total, 4),
                # Reported rather than invented: some countries publish a total and no
                # breakdown, and the reader is told which.
                "intl_total_mio_vkm": round(intl_total) if has_split else "",
                "intl_empty_mio_vkm": round(intl_empty) if has_split else "",
                "intl_empty_share": round(intl_empty / intl_total, 4) if has_split else "",
            })

    # Eurostat's own dimension order, not alphabetical: it puts the aggregates first and
    # then the countries, which is the grouping the publisher chose and the one the
    # committed file already has. Re-sorting would churn the diff for no gain.
    rows.sort(key=lambda row: (index["geo"][row["geo"]], row["year"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    countries = {row["geo"] for row in rows}
    years = sorted({row["year"] for row in rows})
    print(f"{len(rows)} ulke-yili ({len(countries)} ulke, {years}) -> {OUT}")
    print(f"  Eurostat guncellemesi: {raw.get('updated', 'bilinmiyor')}")

    # Not every European country answers this survey, and the two that do not are the
    # two the pilot corridor needs most. Said on every run so it cannot be forgotten.
    absent = [iso for iso in ("TR", "RS") if iso not in countries]
    if absent:
        print(f"  bildirimi yok: {', '.join(absent)} — hicbiri ikame edilmedi")
    return len(rows)


def check() -> int:
    """Is the committed CSV still exactly what the stored JSON produces?

    A derived file that has drifted from its source is worse than no derived file: it
    looks like evidence. This runs in the test suite.
    """
    if not OUT.exists():
        print(f"turetilmis dosya yok: {OUT}", file=sys.stderr)
        return 1

    before = OUT.read_text(encoding="utf-8")
    if not derive():
        return 1
    after = OUT.read_text(encoding="utf-8")

    if before != after:
        OUT.write_text(before, encoding="utf-8")
        print(
            "FARK: islenmis CSV, ham yanittan uretilene esit degil.\n"
            "Betigi --check olmadan calistirip farki inceleyin.",
            file=sys.stderr,
        )
        return 1
    print("esit: islenmis CSV ham yanittan birebir ureliyor")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fetch", action="store_true", help="once taze bir kopya indir")
    parser.add_argument("--check", action="store_true",
                        help="islenmis CSV hala ham yanitla ayni mi, hicbir sey yazma")
    args = parser.parse_args()

    if args.fetch:
        fetch()
    if args.check:
        return check()
    return 0 if derive() else 1


if __name__ == "__main__":
    sys.exit(main())
