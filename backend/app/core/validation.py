import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from .emissions import calculate_route_emission, load_emission_factors
from .geocode import normalise_country
from .route import Leg, RouteAlternative

DEFAULT_DATASET = Path(__file__).resolve().parents[3] / "dogrulama_veriseti.csv"
MATCH_THRESHOLD = 0.01


@dataclass(frozen=True)
class ShipmentRecord:
    """One row of the customer reports, with the mixed spellings normalised."""

    source_report: str
    origin_country: str
    origin_city: str
    destination_country: str
    destination_city: str
    service_route: str
    tonnage: float
    pre_carriage_road_km: float
    sea_km: float
    rail_km: float
    post_carriage_road_km: float
    all_road_km: float
    reported_total_co2_kg: float
    reported_all_road_co2_kg: float
    reported_saving_co2_kg: float

    @property
    def is_multimodal(self) -> bool:
        return self.sea_km > 0 or self.rail_km > 0

    @property
    def road_km(self) -> float:
        return self.pre_carriage_road_km + self.post_carriage_road_km

    @property
    def origin(self) -> tuple[str, str]:
        return (self.origin_city, self.origin_country)

    @property
    def destination(self) -> tuple[str, str]:
        return (self.destination_city, self.destination_country)

    def as_route(self) -> RouteAlternative:
        """Rebuild the reported itinerary so it can be priced by the emission engine."""
        legs = [
            Leg(mode=mode, from_name="from", to_name="to", distance_km=km)
            for mode, km in (
                ("road", self.pre_carriage_road_km),
                ("sea", self.sea_km),
                ("rail", self.rail_km),
                ("road", self.post_carriage_road_km),
            )
            if km > 0
        ]
        return RouteAlternative(legs=legs, label=self.service_route or "reported")


@dataclass(frozen=True)
class EmissionComparison:
    record: ShipmentRecord
    reported_co2_kg: float
    recalculated_co2_kg: float

    @property
    def difference_kg(self) -> float:
        return self.recalculated_co2_kg - self.reported_co2_kg

    @property
    def relative_difference(self) -> float | None:
        if self.reported_co2_kg == 0:
            return None
        return self.difference_kg / self.reported_co2_kg

    @property
    def matches(self) -> bool:
        relative = self.relative_difference
        return relative is not None and abs(relative) < MATCH_THRESHOLD

    def implied_road_km(self, road_factor: float) -> float:
        """Road km the reported CO2 implies, holding sea and rail at their stated values."""
        road_kg = self.record.road_km * self.record.tonnage * road_factor
        non_road_kg = self.recalculated_co2_kg - road_kg
        return (self.reported_co2_kg - non_road_kg) / (self.record.tonnage * road_factor)


def load_validation_dataset(path: Path | None = None) -> list[ShipmentRecord]:
    path = path or DEFAULT_DATASET
    with open(path, encoding="utf-8") as f:
        return [_parse_row(row) for row in csv.DictReader(f)]


def _parse_row(row: dict[str, str]) -> ShipmentRecord:
    def number(key: str) -> float:
        return float(row[key]) if row[key].strip() else 0.0

    return ShipmentRecord(
        source_report=row["kaynak_rapor"],
        origin_country=normalise_country(row["yukleme_ulke"]),
        origin_city=row["yukleme_sehir"].strip(),
        destination_country=normalise_country(row["bosaltma_ulke"]),
        destination_city=row["bosaltma_sehir"].strip(),
        service_route=row["servis_rotasi"].strip(),
        tonnage=number("tonaj"),
        pre_carriage_road_km=number("on_tasima_karayolu_km"),
        sea_km=number("ana_tasima_deniz_km"),
        rail_km=number("ana_tasima_demiryolu_km"),
        post_carriage_road_km=number("son_tasima_karayolu_km"),
        all_road_km=number("tam_karayolu_km"),
        reported_total_co2_kg=number("toplam_co2_kg"),
        reported_all_road_co2_kg=number("tam_karayolu_co2_kg"),
        reported_saving_co2_kg=number("tasarruf_co2_kg"),
    )


def compare_emissions(
    records: list[ShipmentRecord], multimodal_only: bool = True
) -> list[EmissionComparison]:
    """Reprice each reported itinerary with our engine, holding distances and factors fixed.

    Any difference here is a difference in calculation logic, not in routing, which is
    what makes this the check that the engine itself is right.
    """
    factors = load_emission_factors()
    selected = [r for r in records if r.is_multimodal] if multimodal_only else records

    return [
        EmissionComparison(
            record=record,
            reported_co2_kg=record.reported_total_co2_kg,
            recalculated_co2_kg=calculate_route_emission(
                record.as_route(), tonnage=record.tonnage, factors=factors
            ).total_co2_kg,
        )
        for record in selected
    ]


def compare_all_road_baseline(records: list[ShipmentRecord]) -> list[EmissionComparison]:
    """The same check against the all-road column, which every row reports."""
    factors = load_emission_factors()
    return [
        EmissionComparison(
            record=record,
            reported_co2_kg=record.reported_all_road_co2_kg,
            recalculated_co2_kg=calculate_route_emission(
                RouteAlternative(
                    legs=[Leg("road", "from", "to", record.all_road_km)], label="all-road"
                ),
                tonnage=record.tonnage,
                factors=factors,
            ).total_co2_kg,
        )
        for record in records
        if record.all_road_km > 0
    ]


def mean_absolute_percentage_error(comparisons: list[EmissionComparison]) -> float:
    """Mean absolute relative error. Exact matches count as the zeros they are.

    Dropping zero-error rows would report the error of the worst rows alone, and an
    empty input would report a perfect score, so both are refused instead.
    """
    differences = [
        abs(c.relative_difference) for c in comparisons if c.relative_difference is not None
    ]
    if not differences:
        raise ValueError("no comparison carried a reported value to measure against")
    return mean(differences)
