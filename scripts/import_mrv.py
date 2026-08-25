"""Turn a THETIS-MRV annual report into a factor this engine can be checked against.

The EU publishes, for every ship over 5,000 GT calling at an EEA port, its **verified**
annual CO2, fuel burn, distance sailed and transport work. That is the only independent
observation of the number this project's headline rests on: GLEC's ro-ro figure of
0.068 kg CO2 per tonne-kilometre, which nothing here has ever been able to check.

It cannot be fetched automatically. The portal is a JavaScript application, the download
sits behind a reCAPTCHA, and EMSA publishes no direct file URL — so this script takes
the file after a person has clicked once:

    1. https://mrv.emsa.europa.eu/#public/emission-report
    2. Choose a reporting period and export the table (Excel)
    3. Save it under data/external/, then:

       python scripts/import_mrv.py data/external/<the file>.xlsx

Run with no arguments it only reports what the workbook contains, which is the right
first step: EMSA has changed the column names between reporting periods, and guessing at
a header is exactly the kind of shortcut this project does not take. Nothing is written
until the columns it needs are found and named in the output.
"""

import argparse
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "external" / "roro_intensity_mrv.csv"

# What the derivation needs, and the header spellings seen across reporting periods.
# Matched case-insensitively on a substring so a changed suffix does not break it, but
# never guessed: a column that matches nothing is reported and the run stops.
WANTED = {
    "imo": ["imo number", "imo"],
    "ship_type": ["ship type"],
    "co2_total": ["total co2 emissions", "annual total co2"],
    "transport_work_mass": ["co2 emissions per transport work (mass)", "transport work (mass)"],
    "distance": ["annual total time spent at sea", "total distance travelled", "distance"],
    "reporting_period": ["reporting period"],
}

# The ship types this project actually prices. GLEC Table 45 describes a ro-ro fleet, so
# comparing it against a container or bulk figure would be comparing two different ships.
RORO_TYPES = ("ro-ro", "roro", "ro-pax", "ropax", "vehicle carrier", "passenger ship")


def find_columns(header: list[str]) -> tuple[dict[str, int], list[str]]:
    """Map each wanted field onto a column, and say which were not found."""
    lowered = [str(cell or "").strip().lower() for cell in header]
    found: dict[str, int] = {}
    for field, spellings in WANTED.items():
        for spelling in spellings:
            match = next((i for i, cell in enumerate(lowered) if spelling in cell), None)
            if match is not None:
                found[field] = match
                break
    return found, [field for field in WANTED if field not in found]


def describe(path: Path) -> None:
    """Say what is in the workbook without writing anything."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for name in book.sheetnames:
            sheet = book[name]
            rows = list(sheet.iter_rows(max_row=3, values_only=True))
            print(f"\n--- {name}  ({sheet.max_row} satir x {sheet.max_column} sutun) ---")
            if not rows:
                continue
            header = list(rows[0])
            found, missing = find_columns(header)
            for field, index in found.items():
                print(f"    {field:22} <- sutun {index}: {str(header[index])[:52]}")
            for field in missing:
                print(f"    {field:22} BULUNAMADI (aranan: {', '.join(WANTED[field])})")
    finally:
        book.close()


def derive(path: Path, sheet_name: str | None = None) -> int:
    """Write one row per ro-ro ship: its verified transport-work intensity."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = book[sheet_name] if sheet_name else book[book.sheetnames[0]]
        rows = sheet.iter_rows(values_only=True)
        header = list(next(rows))
        found, missing = find_columns(header)

        needed = {"imo", "ship_type", "transport_work_mass"}
        absent = needed - set(found)
        if absent:
            print(
                f"HATA: su sutunlar bulunamadi: {', '.join(sorted(absent))}.\n"
                f"Once '--describe' ile calistirin; EMSA basliklari donemler arasinda "
                f"degistirdi ve tahmin edilmeyecek.",
                file=sys.stderr,
            )
            return 0

        out_rows = []
        for row in rows:
            ship_type = str(row[found["ship_type"]] or "").strip()
            if not any(kind in ship_type.lower() for kind in RORO_TYPES):
                continue
            intensity = row[found["transport_work_mass"]]
            if intensity in (None, "", "Division by zero!"):
                continue
            try:
                value = float(intensity)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            out_rows.append({
                "imo": row[found["imo"]],
                "ship_type": ship_type,
                # MRV reports grams of CO2 per tonne-nautical-mile.
                "g_co2_per_tonne_nm": round(value, 4),
                # Converted for comparison with GLEC, which is per tonne-kilometre.
                # 1 nautical mile = 1.852 km, so dividing by that converts the
                # denominator; /1000 turns grams into kilograms.
                "kg_co2_per_tonne_km": round(value / 1.852 / 1000, 6),
                "reporting_period": (
                    row[found["reporting_period"]] if "reporting_period" in found else ""
                ),
            })

        if not out_rows:
            print("HATA: ro-ro tipinde hicbir gemi bulunamadi.", file=sys.stderr)
            return 0

        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(out_rows[0]))
            writer.writeheader()
            writer.writerows(out_rows)

        values = sorted(r["kg_co2_per_tonne_km"] for r in out_rows)
        median = values[len(values) // 2]
        print(f"{len(out_rows)} ro-ro gemisi -> {OUT}")
        print(f"  medyan  {median:.4f} kg CO2/ton-km")
        print(f"  aralik  {values[0]:.4f} - {values[-1]:.4f}")
        print(f"  GLEC Tablo 45 (WTW): 0.068 — karsilastirma icin")
        return len(out_rows)
    finally:
        book.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("workbook", nargs="?", type=Path, help="THETIS-MRV export (.xlsx)")
    parser.add_argument("--describe", action="store_true",
                        help="only report the workbook's columns, write nothing")
    parser.add_argument("--sheet", default=None, help="which worksheet to read")
    args = parser.parse_args()

    if args.workbook is None:
        print(__doc__)
        return 1
    if not args.workbook.exists():
        print(f"dosya yok: {args.workbook}", file=sys.stderr)
        return 1

    if args.describe:
        describe(args.workbook)
        return 0
    return 0 if derive(args.workbook, args.sheet) else 1


if __name__ == "__main__":
    sys.exit(main())
