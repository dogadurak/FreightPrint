"""Does any code actually use each field in `data/`?

This project's most persistent defect is not a wrong number. It is a right number that
reaches nobody: something built, measured, documented, and then wired to nothing. Three
were found by hand in one afternoon.

  * `is_measured` was parsed into a dataclass and read by no code at all, so the file's
    own admission that rail distance has never been checked reached no user.
  * The per-mode uncertainty table it belonged to was switched off entirely, because the
    API's `distance_uncertainty` defaulted to a number instead of None.
  * `valid_to` on a risk zone was parsed and honoured by nothing, so a war-risk area
    could be retired in the data and the engine would keep routing around it.

Finding them by hand is not a method. The pattern is specific enough to search for: a
field that appears in the code exactly once, inside the function that parses it, got as
far as being read and no further.

    python scripts/check_data_fields.py

Exits non-zero when a field is neither used nor declared below. A new dead field is then
a decision somebody has to make in writing - wire it, delete it, or say why it is there -
rather than something that quietly persists.
"""

import csv
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
SEARCH_DIRS = ["backend/app", "frontend", "scripts"]

# Fields that legitimately appear nowhere in the code, each with the reason.
#
# Three kinds live here, and the reason matters more than the entry. **Documentary**
# fields exist to be read by a person opening the file. **Provenance** fields let a
# reader check a derivation without rerunning it. **Renamed** fields are used constantly
# under a different name, because the loader maps a column to a shorter attribute.
#
# Adding to this list is how a field gets excused, and writing the reason is the price.
ALLOWED = {
    # Documentary: for the human reading the file.
    "notes": "her satirin kendi aciklamasi; koda girmesi beklenmez",
    "note": "satirin kendi aciklamasi; notes ile ayni is",
    "basis": "olcumun neye karsi yapildigi; DistanceBasisOut ile disari cikiyor",
    "alternatives": "rinf_terminal_map: reddedilen adaylar, secimi denetlemek icin",
    "geo_name": "Eurostat'in ulke adi; kod ISO kodunu kullanir",
    "op_name": "isletme noktasinin adi; eslesme uopid uzerinden yapilir",

    # Provenance: lets a reader audit a derivation without rerunning it.
    "intl_total_mio_vkm": "intl_empty_share'in payda tarafi; bolmeyi denetlenebilir kilar",
    "intl_empty_mio_vkm": "ayni bolmenin pay tarafi",
    "total_mio_vkm": "empty_share'in paydasi",
    "empty_mio_vkm": "empty_share'in payi",
    "g_co2_per_tonne_nm": "MRV'nin kendi birimi; ton-deniz-mili -> ton-km cevrimini denetlenebilir kilar",
    "path_sections": "RINF yolunun kac kesimden olustugu; sapmayi tesihs etmeye yarar",
    "route_countries": "RINF yolunun gectigi ulkeler; sinir sapmasinin kaniti",
    "delta_pct": "elle yazilan ile turetilen arasindaki fark; okuyucu icin hesaplanmis",
    "reporting_period": "MRV donemi; load_roro_intensity icinde okunur",

    # Renamed by the loader, and used everywhere under the new name.
    "relative_uncertainty": "DistanceUncertainty.relative olarak kullanilir",
    "value_kg_co2_per_ton_km": "EmissionFactor.value olarak kullanilir",
    "kg_co2_per_tree_per_year": "tree_factors sozlugunun degeri; okundugu yerde kullanilir",
    "kg_co2_per_tonne_km": "ShipIntensity.kg_co2_per_tonne_km; benchmarks icinde kullanilir",
    "schedule_source": "ServiceSchedule.source olarak tasinir ve zaman cizelgesinde gosterilir",
}

# Files that are outputs rather than inputs. Their columns exist to be read by people and
# by the check that compares them against their own source, not by the engine.
DERIVED = ("rail_distances_rinf.csv", "roro_intensity_mrv.csv", "empty_running_eurostat.csv")


def csv_fields(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as f:
        return [h.strip() for h in next(csv.reader(f), []) if h.strip()]


def geojson_fields(path: Path) -> list[str]:
    collection = json.loads(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for feature in collection.get("features", []):
        keys.update(feature.get("properties", {}))
    return sorted(keys)


# A field mentioned this many times or fewer has been declared and parsed and nothing
# else. Two is the exact signature of the defect: one line in the dataclass, one in the
# constructor that fills it, and no third line that ever reads it back.
#
# Counting *files* instead was the first attempt and it was too blunt - it flagged
# `eur_per_tonne_co2`, which is parsed and then used to compute the toll a few lines
# below, in the same module where it belongs. Where a field is used is not the question.
# Whether anything reads it is.
DEAD_AT_OR_BELOW = 2


def mentions(field: str) -> list[str]:
    """Every line that mentions the field, whole-word, as "file:line"."""
    found: list[str] = []
    for directory in SEARCH_DIRS:
        if not (REPO / directory).exists():
            continue
        # Run from the repo root with a relative path: an absolute Windows path puts a
        # drive-letter colon into grep's output and splitting on ":" then eats it.
        result = subprocess.run(
            ["grep", "-rnw", "--include=*.py", "--include=*.js", field, directory],
            capture_output=True, text=True, cwd=REPO,
            # The files being searched carry Turkish text and arrows, and on Windows the
            # default console codepage cannot decode them - grep's output then killed the
            # reader thread and the whole check with it. Replacing an undecodable byte is
            # right here: this only ever looks for a field name in the line.
            encoding="utf-8", errors="replace",
        )
        for line in result.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                found.append(f"{parts[0].replace(chr(92), '/')}:{parts[1]}")
    return found


def main() -> int:
    sources: dict[Path, list[str]] = {}
    for path in sorted(DATA.rglob("*.csv")):
        if path.name in DERIVED:
            continue
        sources[path] = csv_fields(path)
    for path in sorted(DATA.rglob("*.geojson")):
        sources[path] = geojson_fields(path)

    problems: list[str] = []
    for path, fields in sources.items():
        rel = path.relative_to(REPO).as_posix()
        for field in fields:
            if field in ALLOWED:
                continue
            found = mentions(field)
            if not found:
                problems.append(f"{rel}: '{field}' hicbir kodda gecmiyor")
            elif len(found) <= DEAD_AT_OR_BELOW:
                problems.append(
                    f"{rel}: '{field}' yalnizca {len(found)} yerde geciyor "
                    f"({', '.join(found)}) — okunuyor ama hicbir yerde kullanilmiyor"
                )

    if problems:
        print("Kullanilmayan alan(lar):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nHer biri icin bir karar verin: bagla, sil, ya da ALLOWED'a gerekcesiyle "
            "ekleyin. Okunup kullanilmayan bir alan, dosyanin verdigi sozu tutmuyor.",
            file=sys.stderr,
        )
        return 1

    checked = sum(len(f) for f in sources.values())
    print(f"temiz ({checked} alan, {len(sources)} dosyada tarandi; "
          f"{len(ALLOWED)} tanesi gerekceli muaf)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
