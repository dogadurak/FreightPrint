"""Catch Turkish text that reaches a reader with its diacritics stripped.

The dashboard is in Turkish. Some of its strings were typed without diacritics — "234
dogrulanmis ro-ro gemisi", "Karsilastirma TTW esasindadir" — and rendered directly under
headings that have them. Beside "Deniz faktörü — yayımlanan ve ölçülen", a note reading
"yakit uretimini olcmez" looks like a broken font rather than a choice, and the first
thing it costs is the reader's confidence in the numbers above it.

Nobody noticed until the dashboard was opened in a browser and photographed. The API
returned those strings correctly the whole time; there was simply nothing that read them.

    python scripts/check_turkish_text.py

**How a folded sentence is recognised.** Turkish function words — `ve`, `bir`, `bu`,
`için` — survive folding unchanged, so their presence marks a string as Turkish prose
rather than an identifier or an English comment. A stem that in real Turkish always
carries a diacritic (`olc`, `ucret`, `karsi`, `dogru`) marks it as folded. A string that
is Turkish, long, and contains no diacritic at all is the defect.

**What it deliberately does not check.** English text, identifiers, source lines quoted
from a publication (`Trieste, Italy (south of Greece)`), and anything under 20 characters
where a false positive is likelier than a real find. It reads the strings the engine
hands a user, not the comments explaining them.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import speak_utf8  # noqa: E402

speak_utf8()

REPO = Path(__file__).resolve().parent.parent
SCANNED = REPO / "backend" / "app"

DIACRITIC = re.compile("[çğıöşüÇĞİÖŞÜ]")

# Function words that survive folding, so they identify Turkish prose.
TURKISH = re.compile(
    r"\b(ve|bir|bu|ile|icin|olarak|daha|ama|yok|var|her|gibi|degil|ise|kadar|yalnizca)\b")

# Stems that in correctly written Turkish always carry a diacritic.
FOLDED = re.compile(r"\w*(icin|degil|deger|olc|gec|gore|uzerin|dusuk|yuksek|buyuk"
                    r"|kucuk|ulke|yuk|tasi|olus|dogru|karsi|yayin|bildir|sinif"
                    r"|ucret|sifir|baska|calis|kaynak|onem|sayil|agir|donus)\w*")

STRING = re.compile(r'"((?:[^"\\]|\\.){20,})"')

# Strings that are Turkish-looking but must stay as they are, with the reason.
ALLOWED = {
    # Nothing yet. An entry here means "this folded string is correct", which for a
    # quotation from an outside source can be true - Pub 151 prints ASCII.
}


def offenders() -> list[tuple[Path, int, str]]:
    found = []
    for path in sorted(SCANNED.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in STRING.finditer(source):
            text = match.group(1)
            if text in ALLOWED:
                continue
            if DIACRITIC.search(text):
                continue
            if not TURKISH.search(text) or not FOLDED.search(text):
                continue
            line = source[: match.start()].count("\n") + 1
            found.append((path.relative_to(REPO), line, text))
    return found


def main() -> int:
    found = offenders()
    if not found:
        scanned = sum(1 for _ in SCANNED.rglob("*.py"))
        print(f"temiz ({scanned} dosya tarandi)")
        return 0

    print("Turkce karakteri dusurulmus, kullaniciya giden metin:", file=sys.stderr)
    for path, line, text in found:
        print(f"  {path}:{line}", file=sys.stderr)
        print(f"    {text[:100]}", file=sys.stderr)
    print("\nBunlar panoda okunuyor. Duzeltin, ya da gercekten boyle olmasi gerekiyorsa "
          "ALLOWED'a gerekcesiyle ekleyin.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
