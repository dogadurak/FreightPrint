"""Refuse to let customer data reach a commit.

The validation dataset holds real customer routes, and the repository history already
carries an earlier leak. This checks staged changes for values taken from that file, so
a demo coordinate or a test fixture cannot quietly carry one in.

    python scripts/check_privacy.py            # check staged changes
    python scripts/check_privacy.py --range HEAD~3..HEAD

Exits non-zero when something is found. Wire it into a pre-commit hook to make it
automatic.
"""

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "dogrulama_veriseti.csv"

# Values from the dataset that are also ordinary English or code words, or public
# infrastructure named in the brief, would fire on every diff without meaning anything.
ALLOWED = {
    "GEBZE",  # major industrial region used as the demo origin; a city centre, not a site
    "ISTANBUL",
    "TRING",  # collides with "stringify", "LineString"
    "YORK",  # collides with "New York" and is a substring of nothing sensitive alone
}

MINIMUM_LENGTH = 4

# Place names and report tags identify a customer. Postal codes in the same columns are
# bare numbers that collide with timeouts and port numbers, and a digit string on its own
# identifies nobody, so they are not searched for.
NAME_COLUMNS = ("yukleme_sehir", "bosaltma_sehir", "kaynak_rapor", "servis_rotasi")

# The reported figures are distinctive to two decimal places and would be a real leak.
FIGURE_COLUMNS = ("toplam_co2_kg", "tam_karayolu_co2_kg", "tasarruf_co2_kg")


def dataset_values() -> set[str]:
    if not DATASET.exists():
        return set()
    with open(DATASET, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    values = set()
    for row in rows:
        for column in NAME_COLUMNS:
            value = row.get(column, "").strip().upper()
            if len(value) >= MINIMUM_LENGTH and value not in ALLOWED and not value.isdigit():
                values.add(value)
        for column in FIGURE_COLUMNS:
            figure = row.get(column, "").strip()
            # Only figures carrying decimals; a round number is not distinctive.
            if "." in figure and float(figure) != 0:
                values.add(figure)
    return values


def added_lines(diff_range: str | None) -> list[str]:
    command = ["git", "diff", "--cached"] if diff_range is None else ["git", "diff", diff_range]
    diff = subprocess.run(
        command, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    return [line for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--range", dest="diff_range", default=None, help="e.g. HEAD~1..HEAD")
    args = parser.parse_args()

    values = dataset_values()
    if not values:
        print("dogrulama_veriseti.csv yok; kontrol atlandi")
        return 0

    lines = added_lines(args.diff_range)
    # Word boundaries, so a city name inside a longer identifier is not a false alarm.
    patterns = {value: re.compile(rf"\b{re.escape(value)}\b", re.IGNORECASE) for value in values}

    findings: list[tuple[str, str]] = []
    for line in lines:
        for value, pattern in patterns.items():
            if pattern.search(line):
                findings.append((value, line.strip()[:110]))

    if findings:
        print(f"MUSTERI VERISI SIZINTISI: {len(findings)} eslesme")
        for value, line in findings[:20]:
            print(f"  {value}: {line}")
        return 1

    print(f"temiz ({len(values)} hassas deger, {len(lines)} eklenen satir tarandi)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
