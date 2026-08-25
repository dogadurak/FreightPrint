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
