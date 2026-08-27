"""What a route's carbon costs in road tolls, country by country.

Germany began pricing carbon in its truck toll on 1 December 2023, at €200 a tonne —
two and a half times the allowance price this engine uses for shipping. On a
Turkey-to-Germany run six hundred and eighty of the road kilometres are German, so this
is not a rounding item: it is the first place a carrier's carbon figure turns directly
into an invoice they already receive.

Only Germany is priced here, and the reason matters. Austria has charged by CO2 class
since January 2024 and Czechia since March, but neither publishes a euro-per-tonne rate
— they band vehicles by emission class and set a rate per kilometre for each band.
There is no carbon price in those schemes to multiply by our carbon figure, so applying
one would be inventing it. They are listed as uncharged with that reason attached
rather than silently omitted.

What this is not: the toll itself. The infrastructure, noise and air-pollution
components are much the larger part of a German toll bill and none of them follow
carbon. This prices the CO2 component alone, which is the only part this engine has any
business estimating.
"""

import csv
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from .geography import road_distance_by_country
from .network import DATA_DIR

# Schemes that differentiate by emission class without publishing a carbon price. Named
# so a route through them says "not priced, and here is why" rather than "€0".
CLASS_BASED = {
    "AT": "Avusturya, Ocak 2024'ten beri CO2 sınıfına göre ücretlendiriyor ama ton başı "
          "karbon fiyatı yayımlamıyor; sınıf başına km ücreti belirliyor.",
    "CZ": "Çekya, Mart 2024'ten beri CO2 sınıfına göre ücretlendiriyor; aynı şekilde "
          "yayımlanmış bir ton başı fiyatı yok.",
}


@dataclass(frozen=True)
class TollScheme:
    iso: str
    country: str
    eur_per_tonne_co2: float
    in_force: date
    threshold_tonnes: float
    source: str
    notes: str


@dataclass
class CountryToll:
    iso: str
    country: str
    distance_km: float
    co2_kg: float
    cost_eur: float
    priced: bool
    reason: str = ""


@dataclass
class TollEstimate:
    countries: list[CountryToll]
    total_eur: float
    priced_co2_kg: float
    unpriced_co2_kg: float
    notes: list[str] = field(default_factory=list)

    @property
    def priced_countries(self) -> list[CountryToll]:
        return [c for c in self.countries if c.priced]


@lru_cache(maxsize=1)
def load_toll_schemes(path=None) -> dict[str, TollScheme]:
    path = path or DATA_DIR / "co2_tolls.csv"
    with open(path, encoding="utf-8") as f:
        return {
            row["iso"]: TollScheme(
                iso=row["iso"],
                country=row["country"],
                eur_per_tonne_co2=float(row["eur_per_tonne_co2"]),
                in_force=date.fromisoformat(row["in_force"]),
                threshold_tonnes=float(row["threshold_tonnes"]),
                source=row["source"],
                notes=row["notes"],
            )
            for row in csv.DictReader(f)
        }


def estimate_tolls(route, road_co2_kg: float, on_date: date | None = None) -> TollEstimate:
    """Price the CO2 component of the road tolls a route would attract.

    Carbon is attributed to a country by its share of the road distance. That assumes a
    kilometre in Bavaria emits what a kilometre in Bulgaria does, which is what a single
    published factor already assumes — the assumption is the factor's, not ours, and it
    is not made worse by splitting it geographically.
    """
    on_date = on_date or date.today()
    schemes = load_toll_schemes()
    parts = road_distance_by_country(route)
    total_km = sum(part.distance_km for part in parts)

    if total_km <= 0:
        return TollEstimate(countries=[], total_eur=0.0, priced_co2_kg=0.0,
                            unpriced_co2_kg=road_co2_kg,
                            notes=["Rota karayolu bacağı taşımıyor."])

    countries: list[CountryToll] = []
    for part in parts:
        share = part.distance_km / total_km
        co2 = road_co2_kg * share
        scheme = schemes.get(part.iso)

        if scheme and on_date >= scheme.in_force:
            countries.append(CountryToll(
                iso=part.iso, country=part.name, distance_km=part.distance_km,
                co2_kg=co2, cost_eur=co2 / 1000 * scheme.eur_per_tonne_co2, priced=True,
            ))
        elif scheme:
            countries.append(CountryToll(
                iso=part.iso, country=part.name, distance_km=part.distance_km,
                co2_kg=co2, cost_eur=0.0, priced=False,
                reason=f"{scheme.in_force.isoformat()} tarihinde yururluge giriyor",
            ))
        else:
            countries.append(CountryToll(
                iso=part.iso, country=part.name, distance_km=part.distance_km,
                co2_kg=co2, cost_eur=0.0, priced=False,
                reason=CLASS_BASED.get(part.iso, "Yayimlanmis bir CO2 gecis ucreti yok"),
            ))

    priced = [c for c in countries if c.priced]
    notes = [
        "Yalnızca geçiş ücretinin **CO2 bileşeni**. Altyapı, gürültü ve hava kirliliği "
        "bileşenleri faturanın çok daha büyük kısmıdır ve karbonla değişmez.",
        "Karbon, ülkelere karayolu mesafesi payına göre dağıtılır; tek bir faktör zaten "
        "her kilometrenin aynı salımı yaptığını varsayıyor.",
    ]
    for scheme in schemes.values():
        if any(c.iso == scheme.iso and c.priced for c in countries):
            notes.append(
                f"{scheme.country}: {scheme.eur_per_tonne_co2:.0f} EUR/ton, "
                f"{scheme.in_force.isoformat()}'ten beri, {scheme.threshold_tonnes:g} t ustu. "
                f"Kaynak: {scheme.source}"
            )
    if any(c.iso in CLASS_BASED for c in countries):
        notes.append(
            "Bazı ülkeler CO2 sınıfına göre ücretlendiriyor ama ton başı karbon fiyatı "
            "yayımlamıyor; onlar ücretlendirilmedi, sıfır sayılmadı."
        )

    return TollEstimate(
        countries=countries,
        total_eur=sum(c.cost_eur for c in countries),
        priced_co2_kg=sum(c.co2_kg for c in priced),
        unpriced_co2_kg=sum(c.co2_kg for c in countries if not c.priced),
        notes=notes,
    )
