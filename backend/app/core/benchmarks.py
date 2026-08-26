"""Checking this engine's own assumptions against somebody else's observations.

Every other module here computes. This one only compares, and the distinction is the
point: a model checked against its own assumptions is not checked at all.

Until now the project had exactly one external anchor — the carbon engine reproduces a
real customer's own figures to the kilogram on rows that match their method. That is a
strong claim and it covered one module out of twenty-seven. Everything built since,
including the empty-running analysis, rested on nothing but its own tests.

So the observations come from Eurostat's road freight survey: `EMPTY / TOTAL` vehicle-
kilometres, per country, per year, downloaded rather than typed (see
`data/external/README.md`). Two of this engine's assumptions can be held against them.

**The GLEC road factor assumes 30% empty running.** That number is inside every road
figure the engine produces, and nobody here had ever asked whether it matches what
European hauliers actually do.

**The backhaul model assumes each shipment is a dedicated round trip.** That makes a
one-way lane imply 100% empty running on the return, which is obviously an upper bound
— the module says so — but "obviously an upper bound" and "three times the observed
rate" are different statements, and only one of them is useful to somebody deciding
whether to act on it.

Neither comparison is expected to match, and a module built to produce agreement would
be worthless. What it produces is the size and direction of the gap.
"""

import csv
import statistics
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .network import DATA_DIR

EXTERNAL_DIR = DATA_DIR / "external"

# The reference geography for a corridor whose road legs run across the EU. Turkey does
# not report to this survey, so the Turkish end has no observation and the comparison
# says so rather than substituting a neighbour.
DEFAULT_REFERENCE_GEO = "EU27_2020"


class BenchmarkUnavailable(LookupError):
    """The observation asked for is not in the downloaded data."""


@dataclass(frozen=True)
class EmptyRunning:
    """One country-year of observed empty running, as reported."""

    geo: str
    geo_name: str
    year: int
    total_mio_vkm: float
    empty_mio_vkm: float
    empty_share: float
    # International haulage only, which is what this corridor is. Absent for countries
    # that report a total without the split.
    intl_empty_share: float | None = None

    @property
    def relevant_share(self) -> float:
        """The international rate where reported, else the overall one.

        International running is consistently fuller than national — long-haul is
        planned, local distribution is not — so using the overall rate to judge a
        Turkey-to-Germany corridor would compare it against the wrong traffic.
        """
        return self.intl_empty_share if self.intl_empty_share is not None else self.empty_share

    @property
    def basis(self) -> str:
        return "uluslararası" if self.intl_empty_share is not None else "toplam"


@lru_cache(maxsize=1)
def load_empty_running(path: Path | None = None) -> list[EmptyRunning]:
    path = path or EXTERNAL_DIR / "empty_running_eurostat.csv"
    with open(path, encoding="utf-8") as f:
        return [
            EmptyRunning(
                geo=row["geo"],
                geo_name=row["geo_name"],
                year=int(row["year"]),
                total_mio_vkm=float(row["total_mio_vkm"]),
                empty_mio_vkm=float(row["empty_mio_vkm"]),
                empty_share=float(row["empty_share"]),
                intl_empty_share=(
                    float(row["intl_empty_share"]) if row["intl_empty_share"] else None
                ),
            )
            for row in csv.DictReader(f)
        ]


def observed(geo: str = DEFAULT_REFERENCE_GEO, year: int | None = None) -> EmptyRunning:
    """The most recent observation for a country, or the year asked for.

    A missing country raises rather than falling back to the EU average: "we have no
    observation for Turkey" and "Turkey looks like the EU" are different statements and
    only one of them is true.
    """
    rows = [row for row in load_empty_running() if row.geo == geo]
    if not rows:
        available = sorted({row.geo for row in load_empty_running()})
        raise BenchmarkUnavailable(
            f"{geo} icin bos donus gozlemi yok. Eurostat yalnizca bildiren ulkeleri "
            f"yayimlar; mevcut olanlar: {', '.join(available)}"
        )
    if year is not None:
        matching = [row for row in rows if row.year == year]
        if not matching:
            years = sorted(row.year for row in rows)
            raise BenchmarkUnavailable(f"{geo} icin {year} yok; mevcut yillar: {years}")
        return matching[0]
    return max(rows, key=lambda row: row.year)


@dataclass
class Comparison:
    """One of this engine's assumptions, held against an observation."""

    what: str
    ours: float
    observed_value: float
    observed_source: str
    note: str = ""

    @property
    def ratio(self) -> float:
        return self.ours / self.observed_value if self.observed_value else 0.0

    @property
    def verdict(self) -> str:
        """Whether the assumption sits above, below or near what was observed.

        Deliberately three-valued and deliberately not "pass/fail". These assumptions
        are not meant to equal the observation — GLEC's figure describes the fleet its
        factor was measured on, and the dedicated-trip model is an upper bound by
        construction. What matters is knowing which side you are on and by how much.
        """
        if self.ratio > 1.15:
            return "above"
        if self.ratio < 0.85:
            return "below"
        return "near"


@dataclass
class BenchmarkReport:
    comparisons: list[Comparison] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def worst(self) -> Comparison | None:
        """The assumption furthest from what was observed, either direction."""
        return max(
            self.comparisons,
            key=lambda c: abs(c.ratio - 1.0) if c.observed_value else 0.0,
            default=None,
        )


def check_empty_running_assumptions(
    factor_empty_share: float | None = None,
    modelled_empty_share: float | None = None,
    geo: str = DEFAULT_REFERENCE_GEO,
    year: int | None = None,
) -> BenchmarkReport:
    """Hold the engine's two empty-running assumptions against the survey.

    `factor_empty_share` is what the chosen emission factor builds in — GLEC road is
    0.30. `modelled_empty_share` is what the backhaul module's dedicated-trip assumption
    implies for the portfolio in hand, if it has been run.
    """
    reference = observed(geo=geo, year=year)
    source = (
        f"Eurostat road_go_ta_vm, {reference.geo_name}, {reference.year}, "
        f"{reference.basis} arac-km"
    )

    report = BenchmarkReport()
    if factor_empty_share is not None:
        report.comparisons.append(
            Comparison(
                what="Emisyon faktörünün varsaydığı boş dönüş payı",
                ours=factor_empty_share,
                observed_value=reference.relevant_share,
                observed_source=source,
                note=(
                    "Faktörün yayımcısı bu payı kendi ölçtüğü filo için belirledi; "
                    "gözlemle birebir eşleşmesi beklenmez. Fark, faktörün bu koridora "
                    "ne kadar iyi oturduğunun ölçüsüdür."
                ),
            )
        )
    if modelled_empty_share is not None:
        report.comparisons.append(
            Comparison(
                what="Boş dönüş modelinin ima ettiği pay",
                ours=modelled_empty_share,
                observed_value=reference.relevant_share,
                observed_source=source,
                note=(
                    "Model her sevkiyatı kendine ait bir gidiş-dönüş sayar, yani "
                    "tanımı gereği üst sınırdır. Buradaki oran, o üst sınırın "
                    "gerçekleşenin kaç katı olduğunu söyler."
                ),
            )
        )

    report.notes.append(
        f"Karşılaştırma kaynağı indirilmiştir, üretilmemiştir: {source}. "
        "Ham veri ve türetme data/external/ altında denetlenebilir."
    )
    report.notes.append(
        "Türkiye bu ankete bildirim yapmıyor, dolayısıyla koridorun Türkiye ayağı için "
        "gözlem yoktur. Karşılaştırma AB tarafıyla sınırlıdır ve bir komşu ülkeyle "
        "ikame edilmemiştir."
    )
    return report

@dataclass
class CorridorEmptyRunning:
    """Observed empty running weighted by the route's own kilometres, and what it misses.

    The EU-27 average is the wrong yardstick for a specific corridor: it is dominated by
    the countries that haul the most, not by the ones this freight crosses. Austria runs
    22% empty on international work and Bulgaria 12%, so a route's exposure depends on
    where its kilometres actually fall.

    The gap is the other half of the answer and is measured rather than apologised for.
    Turkey and Serbia do not report to this survey — between them they carry about a
    third of the pilot corridor's road distance — so the honest output is a rate *and*
    the share of the route it was computed over. A weighted mean quietly taken over 70%
    of a journey, presented as the journey's rate, is the kind of number that survives
    right up until somebody checks it.
    """

    rate: float
    covered_km: float
    total_km: float
    per_country: dict[str, float] = field(default_factory=dict)
    missing: dict[str, float] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        return self.covered_km / self.total_km if self.total_km else 0.0

    @property
    def is_representative(self) -> bool:
        """Whether enough of the route was observed for the rate to stand for it.

        Two thirds is a judgement, not a standard, and it is here to be argued with —
        the point is that a threshold exists at all rather than a rate being quoted over
        whatever happened to be available.
        """
        return self.coverage >= 0.667


def corridor_empty_running(
    route, year: int | None = None
) -> CorridorEmptyRunning:
    """Weight the survey by how far the route actually runs through each country.

    Uses the same country split the CO2 toll does, so the two answers cannot disagree
    about where the freight is.
    """
    from .geography import road_distance_by_country

    parts = road_distance_by_country(route)
    total_km = sum(part.distance_km for part in parts)

    weighted = 0.0
    covered = 0.0
    per_country: dict[str, float] = {}
    missing: dict[str, float] = {}

    for part in parts:
        if not part.iso:
            missing["yerleştirilemedi"] = missing.get("yerleştirilemedi", 0.0) + part.distance_km
            continue
        try:
            reference = observed(part.iso, year=year)
        except BenchmarkUnavailable:
            missing[part.iso] = missing.get(part.iso, 0.0) + part.distance_km
            continue
        per_country[part.iso] = reference.relevant_share
        weighted += part.distance_km * reference.relevant_share
        covered += part.distance_km

    return CorridorEmptyRunning(
        rate=weighted / covered if covered else 0.0,
        covered_km=covered,
        total_km=total_km,
        per_country=per_country,
        missing=missing,
    )


# ── the sea factor, against the ships that actually sail ─────────────────────

# MRV reports what a ship emitted from the fuel it burned, which is tank-to-wake by
# construction. GLEC's well-to-wake figure additionally carries the emissions of making
# and delivering that fuel — something no ship reports and no verifier checks — so the
# comparison is against GLEC's TTW row. Holding the WTW value against an MRV figure
# would charge the observation for something it never measured.
MRV_SCOPE = "TTW"

# What the observation cannot see, and it is not a small thing.
#
# The importer accepts ro-pax and container/ro-ro vessels, and in the published exports
# not one of them survives: every ro-pax ship and every container/ro-ro ship reports no
# mass-based transport work at all. MRV measures a ro-pax's work in passengers, so the
# tonne-mile column comes back "Division by zero!" for the entire class. The observed
# fleet is therefore pure ro-ro cargo ships.
#
# That matters for which factor may honestly be held against it. GLEC's trailer-only row
# describes unaccompanied traffic on exactly these ships. Its accompanied row describes a
# tractor and driver travelling with the load — traffic that largely sails ro-pax, which
# this observation does not contain.
ACCOMPANIED_BASES = ("roro_truck_trailer",)


@dataclass(frozen=True)
class ShipIntensity:
    """One verified ship-year, as EMSA published it."""

    imo: str
    ship_type: str
    reporting_period: int
    kg_co2_per_tonne_km: float
    laden_only: float | None = None
    freight_only: float | None = None


@lru_cache(maxsize=1)
def load_roro_intensity(path: Path | None = None) -> list[ShipIntensity]:
    path = path or EXTERNAL_DIR / "roro_intensity_mrv.csv"
    with open(path, encoding="utf-8") as f:
        return [
            ShipIntensity(
                imo=row["imo"],
                ship_type=row["ship_type"],
                reporting_period=int(row["reporting_period"]),
                kg_co2_per_tonne_km=float(row["kg_co2_per_tonne_km"]),
                laden_only=(
                    float(row["kg_co2_per_tonne_km_laden"])
                    if row.get("kg_co2_per_tonne_km_laden") else None
                ),
                freight_only=(
                    float(row["kg_co2_per_tonne_km_freight"])
                    if row.get("kg_co2_per_tonne_km_freight") else None
                ),
            )
            for row in csv.DictReader(f)
        ]


@dataclass
class FleetComparison:
    """A published factor against the distribution of ships it claims to describe."""

    factor: float
    factor_source: str
    year: int
    ships: int
    median: float
    q1: float
    q3: float
    share_below: float
    observed_source: str
    # How many ships of each type are behind the comparison, so a reader can see that
    # ro-pax is absent rather than assume it was included.
    ship_types: dict[str, int] = field(default_factory=dict)
    # False where the factor describes traffic this fleet does not carry. The comparison
    # is still reported — it is the nearest observation there is — but it is not a test.
    is_comparable: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.factor / self.median if self.median else 0.0

    @property
    def spread(self) -> float:
        """How wide the middle half is, as a multiple.

        The number that matters most and the one a single factor cannot express: where
        the interquartile range spans a factor of two or more, *any* fleet average is a
        poor description of the ship actually carrying the load.
        """
        return self.q3 / self.q1 if self.q1 else 0.0

    @property
    def verdict(self) -> str:
        """Whether the factor sits inside the fleet it describes.

        Inside the interquartile range is the honest pass: a fleet average is not meant
        to equal any ship, it is meant to be a fair middle. Outside it is a real finding.
        """
        if self.q1 <= self.factor <= self.q3:
            return "within"
        return "above" if self.factor > self.q3 else "below"


def compare_sea_factor(
    factor: float,
    factor_source: str = "GLEC Framework 2019 (Jul 2022) Table 45",
    year: int | None = None,
    vehicle_type: str | None = None,
) -> FleetComparison:
    """Hold a ro-ro emission factor against the verified EU fleet.

    Only the most recent reporting period by default. Pooling years would blur a fleet
    that is measurably changing — the observed median fell 14% across the three periods
    published — and a factor's fairness is a question about the fleet sailing now.
    """
    ships = load_roro_intensity()
    if not ships:
        raise BenchmarkUnavailable("MRV turetmesi bos; once scripts/import_mrv.py calistirin")

    year = year or max(ship.reporting_period for ship in ships)
    fleet = [ship for ship in ships if ship.reporting_period == year]
    if not fleet:
        years = sorted({ship.reporting_period for ship in ships})
        raise BenchmarkUnavailable(f"{year} donemi yok; mevcut: {years}")

    values = sorted(ship.kg_co2_per_tonne_km for ship in fleet)
    quartiles = statistics.quantiles(values, n=4)

    types: dict[str, int] = {}
    for ship in fleet:
        types[ship.ship_type] = types.get(ship.ship_type, 0) + 1

    accompanied = vehicle_type in ACCOMPANIED_BASES
    notes = [
        f"Karsilastirma {MRV_SCOPE} esasindadir. MRV geminin yaktigi yakittan cikan "
        "CO2'yi bildirir; yakit uretimini olcmez, bu yuzden WTW bir faktorle "
        "karsilastirilmaz.",
        "Medyan kullanilir, ortalama degil: birkac gemi tasima isi bildirmedigi icin "
        "asiri deger uretiyor ve ortalamayi tek basina tasiyabiliyor.",
        "Gozlem saf ro-ro yuk gemilerinden olusuyor. Ro-pax gemileri tasima islerini "
        "yolcu uzerinden bildirdigi icin ton-mil sutunu tum sinif icin bos donuyor; "
        "yayinin kendisinde yoklar, burada elenmediler.",
    ]
    if accompanied:
        notes.append(
            "DIKKAT: karsilastirilan faktor refakatli (cekici ve surucu yukle birlikte) "
            "tasimayi tanimliyor; bu trafik agirlikla ro-pax gemilerinde seyrediyor ve "
            "bu gozlemde ro-pax yok. Rakam yine de en yakin gozlem, ama bir sinama degil."
        )

    return FleetComparison(
        factor=factor,
        factor_source=factor_source,
        year=year,
        ships=len(fleet),
        median=statistics.median(values),
        q1=quartiles[0],
        q3=quartiles[2],
        share_below=sum(1 for v in values if v <= factor) / len(values),
        observed_source=f"EU MRV (THETIS-MRV), {year}, {len(fleet)} dogrulanmis ro-ro gemisi",
        ship_types=types,
        is_comparable=not accompanied,
        notes=notes,
    )


# ── the sea distance against the publication that surveys it ──────────────────

# NGA Pub. 151 *Distances Between Ports*, the standard reference for port-to-port
# distance. Derived by `scripts/import_pub151.py`, which keeps the published figure apart
# from the hop it adds to reach a terminal the publication does not list.
#
# **Reported beside the engine's number, never substituted for it.** That is the same
# footing as the Eurostat empty-running survey and the EU MRV fleet: this project's job
# is to say what an assumption rests on, not to overwrite a carrier's own figure with a
# survey taken for a different purpose. The two answer different questions - what this
# service is planned at, and how far the ports are apart - and collapsing them would lose
# the disagreement, which is the interesting part.
PUB151 = EXTERNAL_DIR / "port_distances_pub151.csv"

# A ro-ro ship cannot transit the Corinth Canal, so the canal figures are read but never
# used for comparison. Faz 0 established this; it is the reason the corridor exists.
SEA_ROUTE_BASIS = "south of Greece"


@dataclass(frozen=True)
class SeaDistanceCheck:
    """One sea leg's distance, as the carrier gives it and as the publication surveys it."""

    from_terminal: str
    to_terminal: str
    engine_km: float
    published_km: float
    published_nm: float
    estimated_nm: float
    estimated_share: float
    via: str
    source_line: str

    @property
    def delta_pct(self) -> float:
        return (self.engine_km - self.published_km) / self.published_km * 100

    @property
    def is_representative(self) -> bool:
        """Whether the published half of the figure dominates it."""
        return self.estimated_share <= 0.20


@lru_cache(maxsize=1)
def load_sea_distances(path: Path | None = None) -> dict[tuple[str, str], SeaDistanceCheck]:
    path = path or PUB151
    if not path.exists():
        return {}
    checks: dict[tuple[str, str], SeaDistanceCheck] = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # Only the route a ro-ro can actually sail, and only rows that survived the
            # importer's arithmetic guard.
            if not row["adjusted_km"] or row["via"] == "via Corinth Canal":
                continue
            checks[(row["from_terminal"], row["to_terminal"])] = SeaDistanceCheck(
                from_terminal=row["from_terminal"], to_terminal=row["to_terminal"],
                engine_km=float(row["reference_km"]) if row["reference_km"] else 0.0,
                published_km=float(row["adjusted_km"]),
                published_nm=float(row["published_nm"]),
                estimated_nm=float(row["estimated_nm"]),
                estimated_share=float(row["estimated_share"]),
                via=row["via"], source_line=row["source_line"],
            )
    return checks


def check_sea_distance(from_id: str | None, to_id: str | None) -> SeaDistanceCheck | None:
    """The publication's figure for one leg, whichever way round it is sailed.

    A service runs both ways over the same water, and the publication lists the pair once.
    """
    if not from_id or not to_id:
        return None
    checks = load_sea_distances()
    return checks.get((from_id, to_id)) or checks.get((to_id, from_id))


# ── the rail distance, measured on track rather than typed ────────────────────

# OpenStreetMap track through the OpenRailRouting service, cross-checked against ERA RINF
# on the legs RINF can route by itself. `scripts/import_openrail.py` writes both, so the
# corroboration is re-run whenever the distances are.
#
# RINF is the better source and cannot do this corridor: Austria files 1,402 operational
# points joined by 1,334 sections, and every rail leg here crosses it. Where RINF's
# filing is whole the two agree to 0.7% over 500 km, which is what makes the
# crowd-sourced network usable for the rest.
OPENRAIL = EXTERNAL_DIR / "rail_distances_osm.csv"


@lru_cache(maxsize=1)
def load_rail_distances(path: Path | None = None) -> dict[tuple[str, str], float]:
    """Measured track kilometres per leg, empty if the corroboration failed.

    An all-or-nothing read on purpose. The OSM figures are only evidence because RINF
    agrees with them where it can; if that stops holding, the right answer is to report
    nothing rather than to report numbers whose backing has gone.
    """
    path = path or OPENRAIL
    if not path.exists():
        return {}
    measured: dict[tuple[str, str], float] = {}
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if any(row["kind"] == "cross_check" and row["agrees"] == "no" for row in rows):
        return {}
    for row in rows:
        if row["kind"] != "corridor" or not row["osm_km"]:
            continue
        origin, destination = row["leg"].split("->")
        measured[(origin, destination)] = float(row["osm_km"])
    return measured


def check_rail_distance(from_id: str | None, to_id: str | None) -> float | None:
    if not from_id or not to_id:
        return None
    measured = load_rail_distances()
    return measured.get((from_id, to_id)) or measured.get((to_id, from_id))
