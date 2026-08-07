"""What a voyage costs in carbon allowances, and what a war-risk surcharge buys.

Two very different kinds of number live here, and the difference matters:

* **ETS** is computable. The scheme, its scope and its phase-in are published, so given
  a shipment's emissions the allowance cost follows.
* **War-risk surcharge** is not. Premiums are negotiated per vessel against hull value
  and never published, so the rate is a user input. What this module adds is the part
  that *is* computable — what the reroute costs in distance, time, fuel and allowances —
  so the carrier's line item can be checked against something.

The brief is explicit that this does not reduce the surcharge. It makes it checkable.
"""

from dataclasses import dataclass, field

from .emissions import ShipmentEmission

# EU ETS applies across the EU plus Norway and Iceland (EEA). A voyage between two of
# these is fully covered; one leg in, one leg out is covered at half.
EEA_COUNTRIES = frozenset(
    {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
        "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES",
        "SE", "IS", "NO",
    }
)

# Phase-in of the maritime scope. 2026 onwards is full coverage.
PHASE_IN_BY_YEAR = {2024: 0.40, 2025: 0.70}
FULL_PHASE_IN = 1.00

INTRA_EEA_SHARE = 1.00
CROSSING_SHARE = 0.50

DEFAULT_CARBON_PRICE_EUR = 80.0
DEFAULT_YEAR = 2026


class CostInputError(ValueError):
    pass


def phase_in_share(year: int) -> float:
    return PHASE_IN_BY_YEAR.get(year, FULL_PHASE_IN)


def voyage_coverage_share(origin_country: str | None, destination_country: str | None) -> float:
    """How much of a sea leg's emissions the scheme covers, by where it runs.

    An unknown endpoint is treated as outside the EEA: assuming coverage we cannot
    demonstrate would overstate the bill.
    """
    origin_in = (origin_country or "").upper() in EEA_COUNTRIES
    destination_in = (destination_country or "").upper() in EEA_COUNTRIES
    if origin_in and destination_in:
        return INTRA_EEA_SHARE
    if origin_in or destination_in:
        return CROSSING_SHARE
    return 0.0


@dataclass
class EtsLegCost:
    from_name: str
    to_name: str
    co2_kg: float
    coverage_share: float
    covered_tonnes: float
    cost_eur: float


@dataclass
class EtsCost:
    """Allowance cost for the sea legs of one shipment.

    The scheme bills the shipping company for the vessel's emissions; a shipper meets it
    as a pass-through on its own share of the cargo. This is that share, which is what
    a shipper can check an invoice against — not the vessel's liability.
    """

    carbon_price_eur: float
    year: int
    phase_in: float
    legs: list[EtsLegCost] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def covered_tonnes(self) -> float:
        return sum(leg.covered_tonnes for leg in self.legs)

    @property
    def cost_eur(self) -> float:
        return sum(leg.cost_eur for leg in self.legs)


def calculate_ets(
    shipment: ShipmentEmission,
    leg_countries: list[tuple[str | None, str | None]],
    carbon_price_eur: float = DEFAULT_CARBON_PRICE_EUR,
    year: int = DEFAULT_YEAR,
) -> EtsCost:
    """Price the sea legs' allowances.

    `leg_countries` gives the country pair for each leg of `shipment`, in order, so this
    module does not have to know how a route resolves terminals to countries.
    """
    if carbon_price_eur < 0:
        raise CostInputError(f"carbon price must be >= 0, got {carbon_price_eur}")
    if len(leg_countries) != len(shipment.legs):
        raise CostInputError(
            f"expected {len(shipment.legs)} country pairs, got {len(leg_countries)}"
        )

    phase = phase_in_share(year)
    cost = EtsCost(carbon_price_eur=carbon_price_eur, year=year, phase_in=phase)

    for leg, (origin_country, destination_country) in zip(shipment.legs, leg_countries):
        # Road and rail sit outside the maritime scheme; road joins ETS2 separately.
        if leg.mode != "sea":
            continue
        share = voyage_coverage_share(origin_country, destination_country) * phase
        covered_tonnes = leg.co2_kg / 1000 * share
        cost.legs.append(
            EtsLegCost(
                from_name=leg.from_name,
                to_name=leg.to_name,
                co2_kg=leg.co2_kg,
                coverage_share=share,
                covered_tonnes=covered_tonnes,
                cost_eur=covered_tonnes * carbon_price_eur,
            )
        )

    if cost.legs and shipment.scope == "WTW":
        cost.notes.append(
            "Emissions are priced on a WTW basis while the scheme bills tank-to-wake, "
            "so this overstates the allowance cost."
        )
    return cost


@dataclass
class RerouteCost:
    """What avoiding a risk area costs, beside the surcharge charged for it."""

    extra_distance_km: float
    extra_duration_h: float | None
    extra_co2_kg: float
    extra_ets_eur: float
    surcharge_eur: float

    @property
    def total_eur(self) -> float:
        return self.extra_ets_eur + self.surcharge_eur


def compare_reroute(
    direct_distance_km: float,
    direct_duration_h: float | None,
    direct_co2_kg: float,
    direct_ets_eur: float,
    diverted_distance_km: float,
    diverted_duration_h: float | None,
    diverted_co2_kg: float,
    diverted_ets_eur: float,
    surcharge_eur: float = 0.0,
) -> RerouteCost:
    """Difference between a direct route and one that avoids a risk area.

    `surcharge_eur` is whatever the carrier charged; nothing here derives it. The point
    of putting it beside the computed differences is that the two can be compared.
    """
    if surcharge_eur < 0:
        raise CostInputError(f"surcharge must be >= 0, got {surcharge_eur}")
    return RerouteCost(
        extra_distance_km=diverted_distance_km - direct_distance_km,
        extra_duration_h=(
            None
            if direct_duration_h is None or diverted_duration_h is None
            else diverted_duration_h - direct_duration_h
        ),
        extra_co2_kg=diverted_co2_kg - direct_co2_kg,
        extra_ets_eur=diverted_ets_eur - direct_ets_eur,
        surcharge_eur=surcharge_eur,
    )
