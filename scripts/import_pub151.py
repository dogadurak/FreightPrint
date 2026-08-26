"""The arbiter for sea distance, from the publication that is the reference for it.

Sea is 87% of this corridor's emissions and its distance has never had a second opinion.
`data/distance_uncertainty.csv` says so: one independent cross-check, n=1, and the
project's own known-limits list records a 4% disagreement between `service_legs.csv` and
the validation dataset on Pendik-Bari that nothing could settle.

NGA Pub. 151 *Distances Between Ports* settles it. It is the standard reference for
port-to-port distance, published by the US National Geospatial-Intelligence Agency, in
the public domain, and it lists 1,385 ports.

**It also answers the question this project already cares most about.** Faz 0 found that
searoute routes Turkey-Europe traffic through the Corinth Canal, which a ro-ro ship
cannot transit. Pub 151 publishes both figures separately:

    Trieste, Italy (south of Greece),   1,162
    Trieste, Italy (via Corinth Canal), 1,063

So it does not merely give a second number, it gives the right one.

    python scripts/import_pub151.py --fetch   # download the PDF (1.7 MB, once)
    python scripts/import_pub151.py           # parse it into the CSV
    python scripts/import_pub151.py --check   # is the CSV still what the PDF produces?

**The parsing is the risk, and it is not hypothetical.** The publication is a two-column
PDF and extraction wraps entries across lines, splitting a distance from its port:

    Ancona, Italy (via Corinth Canal),
    968

Read naively that reads as 96 nautical miles from Istanbul to Ancona. Every row is
therefore checked against the great-circle distance between the two ports' own published
coordinates: a sea route cannot be shorter than the straight line, so a truncated number
fails arithmetic rather than looking plausible. Rows that fail are dropped and counted,
never guessed at.
"""

import argparse
import csv
import math
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "external" / "pub151.pdf"
TEXT = REPO / "data" / "external" / "pub151.txt"
OUT = REPO / "data" / "external" / "port_distances_pub151.csv"

URL = ("https://msi.nga.mil/api/publications/download"
       "?key=16694076/SFH00000/Pub151bk.pdf&type=view")

KM_PER_NAUTICAL_MILE = 1.852
EARTH_RADIUS_NM = 3440.065

# How far below the straight line a published distance may fall before it is treated as
# damaged rather than merely imprecise.
#
# The check exists to catch a number truncated by the column break, and truncation loses
# a factor of ten: 968 becomes 96. Precision loses one per cent. The publication measures
# from a berth and quotes whole miles, while this computes from the position printed in
# the header, so Ajaccio-Toulon comes out as 155 published against 157 computed and
# Acapulco-Callao as 2,198 against 2,198.5. Rejecting those would throw away good data to
# catch nothing, and a guard that discards correct rows is one people learn to widen
# until it catches nothing at all.
MIN_OF_GREAT_CIRCLE = 0.95

# A port's own entry: its name and country in capitals on one line, its position on the
# next. The position is what makes the arithmetic check possible.
# The country half may itself contain a comma - "MESSINA,  SICILY, ITALY" - and leaving
# it out of the character class meant that heading did not match, so Mersin's block ran
# straight through Messina and read "Trieste, Italy, 637" out of *its* list. A correct
# distance from the wrong port, and the second time a block boundary produced exactly
# that number. Destination lines cannot be caught by widening this: they are Title Case
# and every class here demands capitals.
HEADER = re.compile(r"^([A-Z][A-Z'’ .-]{2,}),\s+([A-Z][A-Z'’ .,()-]+)\s*$", re.M)
POSITION = re.compile(
    r"\((\d+)[°˚](\d+)'(?:(\d+)\")?\s*([NS])\.,\s*(\d+)[°˚](\d+)'(?:(\d+)\")?\s*([EW])\.\)"
)

# A destination line: name, optional country, an optional route qualifier in brackets,
# and the distance. The distance may have been wrapped onto the following line, which is
# why this does not anchor to the end of a line.
ENTRY = re.compile(
    r"^(?P<name>[A-Z][^,\n(]{1,40}?)"
    r"(?:,\s*(?P<country>[A-Z][^,\n(]{1,30}?))?"
    r"(?:\s*\((?P<via>[^)]+)\))?"
    r",\s*(?P<distance>[\d,]+)\s*$",
    re.M,
)

# Lines the extractor injects that are not data: page furniture and footnotes.
NOISE = re.compile(r"^\s*(\d+[A-Z]|\*\s*JUNCTION POINT|Junction Points|Ports)\b")

# A route through the canal and one around the Peloponnese are different voyages, so the
# qualifier is kept rather than collapsed. Anything else is the only route published.
CORINTH = "via Corinth Canal"
AROUND = "south of Greece"


def fetch() -> None:
    import requests

    print("indiriliyor: NGA Pub. 151")
    response = requests.get(URL, timeout=300, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_bytes(response.content)
    print(f"  -> {RAW} ({len(response.content)} bayt)")
    _extract_text()


def _extract_text() -> None:
    """Cache the extracted text beside the PDF.

    Kept as its own file so the parsing can be re-run and audited without a PDF library,
    and so a reader can see exactly what the parser was given.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(RAW))
    TEXT.write_text(
        "\n\f".join(page.extract_text() or "" for page in reader.pages), encoding="utf-8"
    )
    print(f"  -> {TEXT} ({len(reader.pages)} sayfa)")


def _position(text: str) -> tuple[float, float] | None:
    """Decimal degrees from the publication's sexagesimal position line."""
    found = POSITION.search(text)
    if not found:
        return None
    lat_d, lat_m, lat_s, lat_h, lon_d, lon_m, lon_s, lon_h = found.groups()
    lat = int(lat_d) + int(lat_m) / 60 + int(lat_s or 0) / 3600
    lon = int(lon_d) + int(lon_m) / 60 + int(lon_s or 0) / 3600
    return (-lon if lon_h == "W" else lon, -lat if lat_h == "S" else lat)


def great_circle_nm(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(h))


def _reflow(block: str) -> str:
    """Put a wrapped distance back on its own entry's line.

    The two-column layout breaks an entry between its name and its number, and the number
    then reads as belonging to nothing — or worse, the leading digits of it do. Joining a
    line that ends in a comma to the line after is what the layout meant.
    """
    lines, joined = block.splitlines(), []
    for line in lines:
        line = line.rstrip()
        if joined and joined[-1].endswith(",") and re.fullmatch(r"[\d,]+", line.strip()):
            joined[-1] = f"{joined[-1]} {line.strip()}"
        elif joined and joined[-1].endswith(",") and not NOISE.match(line):
            joined[-1] = f"{joined[-1]} {line.strip()}"
        else:
            joined.append(line)
    return "\n".join(joined)


def parse(text: str) -> tuple[list[dict], dict[str, int]]:
    """Every published distance, with the ones that fail arithmetic dropped and counted."""
    # Keyed by name **and country**, because the publication gives both and the names
    # alone are not unique. Keying on the name put Cartagena in Colombia when the entry
    # said Spain, and Portland in Oregon when the entry said England - a straight line of
    # 8,992 miles against a published 564, which then failed the arithmetic check and was
    # thrown away as damaged. Discarding the country was discarding the answer.
    positions: dict[tuple[str, str], tuple[float, float]] = {}
    by_name: dict[str, list[tuple[str, str]]] = {}
    blocks: list[tuple[str, str, str]] = []

    matches = list(HEADER.finditer(text))
    for current, following in zip(matches, matches[1:] + [None]):
        name = current.group(1).strip().title()
        country = current.group(2).strip().title()
        end = following.start() if following else len(text)
        body = text[current.end():end]
        if (position := _position(body[:120])) is not None:
            if (name, country) not in positions:
                positions[(name, country)] = position
                by_name.setdefault(name, []).append((name, country))
        blocks.append((name, country, body))

    def locate(name: str, country: str) -> tuple[float, float] | None:
        """A port's position, refusing to guess between same-named ports."""
        if country and (name, country) in positions:
            return positions[(name, country)]
        # No country given - only safe where the name belongs to exactly one port.
        candidates = by_name.get(name, [])
        return positions[candidates[0]] if len(candidates) == 1 else None

    rows, rejected = [], {"konum yok": 0, "kus ucusundan kisa": 0}
    for origin, origin_country, body in blocks:
        here = locate(origin, origin_country)
        for entry in ENTRY.finditer(_reflow(body)):
            destination = entry.group("name").strip().title()
            country = (entry.group("country") or "").strip().title()
            distance = int(entry.group("distance").replace(",", ""))
            via = (entry.group("via") or "").strip()

            there = locate(destination, country)
            if here is None or there is None:
                rejected["konum yok"] += 1
                continue
            straight = great_circle_nm(here, there)
            # A sea route cannot be meaningfully shorter than the straight line between
            # its ends. This is the check that catches a distance truncated by the column
            # break, and it catches it as arithmetic rather than as a judgement about
            # whether the number looks plausible.
            if distance < straight * MIN_OF_GREAT_CIRCLE:
                rejected["kus ucusundan kisa"] += 1
                continue

            rows.append({
                "from_port": origin, "from_country": origin_country,
                "to_port": destination, "to_country": country,
                "via": via, "nautical_miles": distance,
                "km": round(distance * KM_PER_NAUTICAL_MILE, 1),
                "great_circle_nm": round(straight, 1),
                "detour_ratio": round(distance / straight, 3) if straight else "",
            })

    rows.sort(key=lambda r: (r["from_port"], r["from_country"],
                             r["to_port"], r["to_country"], r["via"]))
    return rows, rejected


# The corridor's own sea legs, tied to the ports the publication lists.
#
# Deliberately six rows rather than eleven thousand. The bulk parse works and is kept
# below for auditing, but roughly a seventh of its rows fail the arithmetic check, and a
# derived table that cannot vouch for itself is not evidence. These are the only sea
# distances this project uses; they get read individually and carry the line they came
# from, so a person can check each against the PDF.
#
# Pendik and Yalova are in the Sea of Marmara and Pub 151 lists neither, so Istanbul
# stands for them and the gap is added rather than ignored. It is measured from the
# terminal's own coordinates against the position the publication prints, not estimated -
# Pendik is 15.1 nautical miles out, half the 30 first assumed, and assuming it made the
# reference table look better than it is.
CORRIDOR_LEGS = [
    ("pendik", "trieste", "ISTANBUL", "Trieste"),
    ("pendik", "bari", "ISTANBUL", "Bari"),
    ("pendik", "patras", "ISTANBUL", "Patrai"),
    ("yalova", "sete", "ISTANBUL", "Sete"),
    ("mersin", "trieste", "MERSIN", "Trieste"),
    ("trieste", "patras", "TRIESTE", "Patrai"),
]

# Legs Pub 151 does not publish, reached through the nearest port it does.
#
# The publication lists neither Istanbul-Patrai nor Patrai-Trieste, which left the Patras
# way-call resting on the project's own unverified figures - and those turned out to be
# the most inflated numbers in the table, so the correction built on them understated
# itself. Patrai's own entry names two ports that stand in.
#
# Neither is a substitution. Each is a published distance to a named port plus the
# measured offset to the terminal actually wanted, and the sign of that offset is argued
# from the geography rather than assumed:
#
#   Derince is 27 nm east of Pendik, so it is further from the Dardanelles and its
#   published distance to Patrai is longer. The offset is subtracted.
#
#   Pula is 46 nm south of Trieste, at the mouth of the gulf rather than its head, so its
#   published distance to Patrai is shorter. The offset is added, and the published figure
#   stands on its own as a floor.
# (published-from, published-to, required route, the neighbour's position, the terminal
# it stands in for, and the sign of the correction).
#
# Naming the terminal outright rather than inferring it from the sign, because inferring
# it took Pula to stand in for Patras instead of Trieste and produced 2,014 km where the
# answer is 1,114 - a 39% disagreement pointing the opposite way to every other leg.
VIA_NEIGHBOUR = {
    ("pendik", "patras"): ("PATRAI", "Derince", "south of Greece",
                           (29.825, 40.743056), "pendik", -1),
    ("trieste", "patras"): ("PATRAI", "Pula", "",
                            (13.835278, 44.868889), "trieste", +1),
    # Mersin's own entry names Pula and not Trieste, so the same Adriatic stand-in works
    # from the other end. The south-of-Greece route, because a ro-ro cannot use Corinth.
    ("mersin", "trieste"): ("MERSIN", "Pula", "south of Greece",
                            (13.835278, 44.868889), "trieste", +1),
}

# How much of a figure may be this project's own arithmetic before it stops being a
# reading of the publication.
#
# Every distance here is a published number plus or minus a hop: Istanbul to Pendik,
# Derince to Pendik, Pula to Trieste. Those hops are measured from coordinates rather
# than guessed, but a measured great-circle across open water is still an approximation
# of a sailing distance, and an approximation folded into a chain stops announcing
# itself. So the published half and the estimated half are written to separate columns
# and their ratio travels with the answer.
#
# The worst case here is Trieste-Patras at 8% estimated; the Marmara offsets are 1-2%.
# Beyond a fifth the row is flagged, because at that point the figure is describing this
# project's geometry more than the publication's survey.
MAX_ESTIMATED_SHARE = 0.20

TERMINALS = REPO / "data" / "terminals.geojson"
SERVICE_LEGS = REPO / "data" / "service_legs.csv"


def _terminal_positions() -> dict:
    import json

    data = json.loads(TERMINALS.read_text(encoding="utf-8"))
    return {f["properties"]["id"]: tuple(f["geometry"]["coordinates"])
            for f in data["features"]}


def _reference_km() -> dict:
    with SERVICE_LEGS.open(encoding="utf-8") as f:
        return {(row["from_terminal"], row["to_terminal"]): float(row["ref_distance_km"])
                for row in csv.DictReader(f) if row["mode"] == "sea"}


def port_block(text: str, port: str) -> str:
    """One port's published entry, from after its heading to the next port's.

    Searching from the heading itself matched it at offset zero and returned a block one
    character long, which reported every leg as absent.
    """
    # Anchored to the start of a line rather than to a preceding newline, so a port that
    # opens the text is found like any other. The publication always has one; a fixture
    # need not, and a reader that only works on real input is a reader nothing can test.
    heading = re.search(rf"^{re.escape(port)},", text, re.M)
    if heading is None:
        raise LookupError(f"{port} yayinda bulunamadi")
    body = text.index("\n", heading.start()) + 1
    following = re.search(HEADER.pattern, text[body:], re.M)
    # Never past the next heading. Falling back to a fixed span ran Mersin's entry into
    # the port after it and produced a distance from somebody else's list.
    if following is None:
        raise LookupError(f"{port} bloklarinin sonu bulunamadi")
    return text[body:body + following.start()]


def published_distances(block: str, target: str) -> list:
    """Every distance the block publishes for one destination: (via, nm, raw line)."""
    joined = []
    for line in (l.rstrip() for l in block.splitlines()):
        # Continue the previous entry when it ends mid-clause. Two shapes occur: a line
        # broken after its comma, and a line broken inside the route qualifier -
        # "Derince, Turkey (south of" / "Greece), 648". Only handling the comma left
        # every Patrai entry unreadable, and Patrai is the leg with no other arbiter.
        unclosed = joined and joined[-1].count("(") > joined[-1].count(")")
        if joined and line.strip() and (joined[-1].endswith(",") or unclosed):
            joined[-1] = joined[-1] + " " + line.strip()
        else:
            joined.append(line)

    found = []
    for line in joined:
        if not re.match(r"\s*" + re.escape(target) + r"\b", line):
            continue
        number = re.search(r"([\d,]+)\s*$", line)
        if not number:
            continue
        via = re.search(r"\(([^)]+)\)", line)
        found.append((via.group(1) if via else "",
                      int(number.group(1).replace(",", "")), line.strip()))
    return found


def _row(origin, destination, reference_km, via, published_nm=None, estimated_nm=0.0,
         status="", source_line="", rejected=False) -> dict:
    """One leg, with the publication's own number kept apart from this project's.

    `published_nm` is what the page literally says. `estimated_nm` is the hop added to
    reach the terminal actually wanted, signed, and measured from coordinates. Keeping
    them in one column would make a figure that is 92% survey look identical to one that
    is 50% arithmetic.
    """
    row = {
        "from_terminal": origin, "to_terminal": destination,
        "reference_km": reference_km, "via": via,
        "published_nm": "" if published_nm is None else round(published_nm, 1),
        "estimated_nm": round(estimated_nm, 1) if published_nm is not None else "",
        "estimated_share": "", "nautical_miles": "", "adjusted_km": "",
        "delta_pct": "", "is_representative": "", "status": status,
        "source_line": source_line,
    }
    if published_nm is None or rejected:
        return row

    total = published_nm + estimated_nm
    km = round(total * KM_PER_NAUTICAL_MILE, 1)
    share = abs(estimated_nm) / total if total else 1.0
    row.update({
        "nautical_miles": round(total, 1), "adjusted_km": km,
        "estimated_share": round(share, 3),
        "is_representative": "yes" if share <= MAX_ESTIMATED_SHARE else "no",
        "delta_pct": (round((reference_km - km) / km * 100, 1) if reference_km else ""),
    })
    return row


def derive() -> int:
    if not TEXT.exists():
        if RAW.exists():
            _extract_text()
        else:
            print(f"yayin yok: {RAW}\n--fetch ile indirin.", file=sys.stderr)
            return 0

    text = TEXT.read_text(encoding="utf-8")
    terminals = _terminal_positions()
    reference = _reference_km()

    istanbul_start = text.find("\nISTANBUL,")
    istanbul = _position(text[istanbul_start:istanbul_start + 200])

    rows = []
    for origin, destination, port, target in CORRIDOR_LEGS:
        reference_km = reference.get((origin, destination), "")
        offset_km = 0.0
        if port == "ISTANBUL" and istanbul and origin in terminals:
            offset_km = round(
                great_circle_nm(istanbul, terminals[origin]) * KM_PER_NAUTICAL_MILE, 1)

        try:
            entries = published_distances(port_block(text, port), target)
        except (ValueError, LookupError):
            entries = []

        if not entries and (origin, destination) in VIA_NEIGHBOUR:
            near_port, near_name, want_via, near_pos, stands_for, sign = VIA_NEIGHBOUR[
                (origin, destination)]
            for via, nm, raw in published_distances(port_block(text, near_port), near_name):
                if want_via and via != want_via:
                    continue
                hop = great_circle_nm(near_pos, terminals[stands_for])
                chained = nm + sign * hop
                km = chained * KM_PER_NAUTICAL_MILE
                rows.append(_row(
                    origin, destination, reference_km, via or "tek rota",
                    published_nm=nm, estimated_nm=sign * hop,
                    status=f"komsu limandan zincirlendi: {near_port}->{near_name} {nm} nm",
                    source_line=raw,
                ))
            if any(r["from_terminal"] == origin and r["to_terminal"] == destination
                   for r in rows):
                continue

        if not entries:
            # Pub 151 lists a selection per port, not every pair. Where no neighbouring
            # port can stand in either, absent is reported and never filled in.
            rows.append(_row(
                origin, destination, reference_km, "",
                status=port + " listesinde " + target + " yayimlanmamis",
            ))
            continue

        for via, nm, raw in entries:
            km = nm * KM_PER_NAUTICAL_MILE
            adjusted = round(km + offset_km, 1)
            # The same arithmetic guard the bulk parse uses, which was left out of this
            # path and let a stray match through: Mersin's entry does not list Trieste at
            # all, but a block boundary overrunning into a neighbouring port produced
            # "637 nm" - less than the straight line between them, and 133% away from the
            # reference. A number that cannot be sailed is not a disagreement to report.
            straight = great_circle_nm(terminals[origin], terminals[destination])
            if nm < straight * MIN_OF_GREAT_CIRCLE:
                rows.append(_row(
                    origin, destination, reference_km, via, published_nm=nm,
                    rejected=True, source_line=raw,
                    status=(f"elendi: {nm} nm, kus ucusu {straight:.0f} nm'den kisa "
                            f"(blok tasmasi ya da kirpilma)"),
                ))
                continue
            rows.append(_row(
                origin, destination, reference_km, via,
                published_nm=nm, estimated_nm=offset_km / KM_PER_NAUTICAL_MILE,
                status="ok", source_line=raw,
            ))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    published = [r for r in rows if r["status"] == "ok"]
    print(str(len(published)) + "/" + str(len(rows)) + " satir yayindan okundu -> " + str(OUT))
    for row in rows:
        if row["status"] != "ok":
            print("  %8s->%-8s %s" % (row["from_terminal"], row["to_terminal"], row["status"]))
            continue
        print("  %8s->%-8s %6s nm = %7.1f km (%s) | proje %.0f km | fark %+6.1f%% "
              "| tahmin payi %%%.1f%s" % (
                  row["from_terminal"], row["to_terminal"], row["published_nm"],
                  row["adjusted_km"], row["via"] or "tek rota", row["reference_km"],
                  row["delta_pct"], float(row["estimated_share"]) * 100,
                  "" if row["is_representative"] == "yes" else "  [TAHMIN AGIR BASIYOR]"))
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
        print("FARK: islenmis CSV, yayindan uretilene esit degil.", file=sys.stderr)
        return 1
    print("esit: islenmis CSV yayindan birebir ureliyor")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fetch", action="store_true", help="once yayini indir")
    parser.add_argument("--check", action="store_true",
                        help="islenmis CSV hala yayinla ayni mi, hicbir sey yazma")
    args = parser.parse_args()

    if args.fetch:
        fetch()
    if args.check:
        return check()
    return 0 if derive() else 1


if __name__ == "__main__":
    sys.exit(main())
