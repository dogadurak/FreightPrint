import csv
from dataclasses import dataclass, field
from pathlib import Path

from .network import DATA_DIR
from .route import RouteAlternative

DEFAULT_FACTOR_SET = "reference"
DEFAULT_SCOPE = "TTW"


class FactorNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class EmissionFactor:
    factor_set: str
    mode: str
    vehicle_type: str
    fuel_type: str
    scope: str
    value: float
    source: str
    year: int
    is_verified: bool
    notes: str
    # Utilisation the published value already accounts for. Without these the same
    # correction gets applied twice: once by whoever published the factor, once by us.
    basis_load_factor: float = 1.0
    basis_empty_share: float = 0.0

    @property
    def value_at_full_load(self) -> float:
        """The factor with the publisher's own utilisation assumptions removed."""
        return self.value * self.basis_load_factor / (1 + self.basis_empty_share)

    @property
    def label(self) -> str:
        return f"{self.mode}/{self.fuel_type} {self.value} kg CO2/t-km ({self.scope}, {self.source})"


@dataclass(frozen=True)
class ResolvedLeg:
    """A route leg after ferry distance has been split out of its road distance."""

    mode: str
    distance_km: float
    from_name: str
    to_name: str
    is_ferry: bool = False


@dataclass
class LegEmission:
    mode: str
    from_name: str
    to_name: str
    distance_km: float
    tonnage: float
    factor: EmissionFactor
    co2_kg: float


@dataclass
class ShipmentEmission:
    label: str
    legs: list[LegEmission]
    tonnage: float
    factor_set: str
    scope: str
    all_road_co2_kg: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def total_co2_kg(self) -> float:
        return sum(leg.co2_kg for leg in self.legs)

    @property
    def co2_by_mode(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for leg in self.legs:
            totals[leg.mode] = totals.get(leg.mode, 0.0) + leg.co2_kg
        return totals

    @property
    def saving_co2_kg(self) -> float | None:
        if self.all_road_co2_kg is None:
            return None
        return self.all_road_co2_kg - self.total_co2_kg


def load_emission_factors(path: Path | None = None) -> list[EmissionFactor]:
    path = path or DATA_DIR / "emission_factors.csv"
    with open(path, encoding="utf-8") as f:
        return [
            EmissionFactor(
                factor_set=row["factor_set"],
                mode=row["mode"],
                vehicle_type=row["vehicle_type"],
                fuel_type=row["fuel_type"],
                scope=row["scope"],
                value=float(row["value_kg_co2_per_ton_km"]),
                source=row["source"],
                year=int(row["year"]),
                is_verified=row["is_verified"].strip().lower() == "yes",
                notes=row["notes"],
                basis_load_factor=float(row["basis_load_factor"]),
                basis_empty_share=float(row["basis_empty_share"]),
            )
            for row in csv.DictReader(f)
        ]


def load_tree_factors(path: Path | None = None) -> dict[str, float]:
    path = path or DATA_DIR / "tree_factors.csv"
    with open(path, encoding="utf-8") as f:
        return {row["species"]: float(row["kg_co2_per_tree_per_year"]) for row in csv.DictReader(f)}


def find_factor(
    factors: list[EmissionFactor],
    mode: str,
    scope: str = DEFAULT_SCOPE,
    fuel_type: str | None = None,
    factor_set: str = DEFAULT_FACTOR_SET,
) -> EmissionFactor:
    """Look up exactly one factor within the requested set.

    A miss is an error, never a quiet fall back to another set: a report that says it
    priced with GLEC while a leg came from somewhere else misstates its own source, and
    that is the one thing a standards claim cannot survive. Ambiguity is refused for the
    same reason — silently taking the first of several matches is a guess, not a lookup.
    """
    in_set = [f for f in factors if f.factor_set == factor_set and f.mode == mode]
    matches = [f for f in in_set if f.scope == scope]
    if fuel_type is not None:
        matches = [f for f in matches if f.fuel_type == fuel_type]

    if not matches:
        available = sorted({f.fuel_type for f in in_set if f.scope == scope})
        detail = f"; {mode} fuels in this set: {', '.join(available)}" if available else ""
        raise FactorNotFoundError(
            f"no {scope} factor for mode={mode} fuel={fuel_type} in set={factor_set}{detail}"
        )
    if len(matches) > 1:
        options = ", ".join(sorted(f"{f.vehicle_type}/{f.fuel_type}" for f in matches))
        raise FactorNotFoundError(
            f"{len(matches)} {scope} factors match mode={mode} in set={factor_set}: {options}. "
            "Pass a fuel type to choose one."
        )
    return matches[0]


def expand_route_legs(route: RouteAlternative) -> list[ResolvedLeg]:
    """Split ferry distance out of road legs so every consumer charges it as sea.

    Both the point estimate and the Monte Carlo range read the route through here; if
    only one of them did, the two would disagree on the same shipment.
    """
    resolved = []
    for leg in route.legs:
        if leg.mode == "road" and leg.ferry_km > 0:
            resolved.append(
                ResolvedLeg("road", leg.distance_km - leg.ferry_km, leg.from_name, leg.to_name)
            )
            resolved.append(
                ResolvedLeg("sea", leg.ferry_km, f"{leg.from_name} (ferry)", leg.to_name, True)
            )
        else:
            resolved.append(ResolvedLeg(leg.mode, leg.distance_km, leg.from_name, leg.to_name))
    return resolved


def effective_factor_value(
    factor: EmissionFactor,
    load_factor: float | None = None,
    empty_return_share: float | None = None,
) -> float:
    """Reprice a published factor for utilisation the caller knows better than its publisher.

    A half-loaded vehicle burns nearly the same fuel while carrying half the cargo, so its
    emissions per ton-km roughly double. Empty return distance is allocated to the loaded
    trip, which is why it scales the factor rather than the payload.

    Published factors already assume some utilisation, so the publisher's assumption is
    removed before the caller's is applied. Passing neither returns the factor as
    published, which is what its source recommends for a default.
    """
    if load_factor is None and empty_return_share is None:
        return factor.value
    if load_factor is not None and not 0 < load_factor <= 1:
        raise ValueError(f"load_factor must be in (0, 1], got {load_factor}")
    if empty_return_share is not None and not 0 <= empty_return_share <= 1:
        raise ValueError(f"empty_return_share must be in [0, 1], got {empty_return_share}")

    load = load_factor if load_factor is not None else factor.basis_load_factor
    empty = empty_return_share if empty_return_share is not None else factor.basis_empty_share
    return factor.value_at_full_load / load * (1 + empty)


def calculate_route_emission(
    route: RouteAlternative,
    tonnage: float,
    scope: str = DEFAULT_SCOPE,
    road_fuel_type: str | None = None,
    factor_set: str = DEFAULT_FACTOR_SET,
    load_factor: float | None = None,
    empty_return_share: float | None = None,
    factors: list[EmissionFactor] | None = None,
) -> ShipmentEmission:
    factors = factors if factors is not None else load_emission_factors()
    warnings: list[str] = []
    legs = []

    for resolved in expand_route_legs(route):
        factor = find_factor(
            factors,
            mode=resolved.mode,
            scope=scope,
            fuel_type=road_fuel_type if resolved.mode == "road" else None,
            factor_set=factor_set,
        )
        if not factor.is_verified:
            warnings.append(f"unverified factor used: {factor.label} - {factor.notes}")
        if resolved.is_ferry:
            warnings.append(
                f"{resolved.to_name} leg includes {resolved.distance_km:,.0f} km of ferry, "
                "charged at the sea factor"
            )

        value = effective_factor_value(factor, load_factor, empty_return_share)
        legs.append(
            LegEmission(
                mode=resolved.mode,
                from_name=resolved.from_name,
                to_name=resolved.to_name,
                distance_km=resolved.distance_km,
                tonnage=tonnage,
                factor=factor,
                co2_kg=resolved.distance_km * tonnage * value,
            )
        )

    return ShipmentEmission(
        label=route.label,
        legs=legs,
        tonnage=tonnage,
        factor_set=factor_set,
        scope=scope,
        warnings=warnings,
    )


def calculate_shipment(
    routes: list[RouteAlternative],
    tonnage: float,
    scope: str = DEFAULT_SCOPE,
    road_fuel_type: str | None = None,
    factor_set: str = DEFAULT_FACTOR_SET,
    load_factor: float | None = None,
    empty_return_share: float | None = None,
) -> list[ShipmentEmission]:
    """Emissions for every alternative, each compared against the all-road baseline."""
    factors = load_emission_factors()
    results = [
        calculate_route_emission(
            route,
            tonnage=tonnage,
            scope=scope,
            road_fuel_type=road_fuel_type,
            factor_set=factor_set,
            load_factor=load_factor,
            empty_return_share=empty_return_share,
            factors=factors,
        )
        for route in routes
    ]

    baseline = next(
        (
            result
            for result, route in zip(results, routes)
            if route.is_all_road and len(route.legs) == 1
        ),
        None,
    )
    if baseline is not None:
        for result in results:
            result.all_road_co2_kg = baseline.total_co2_kg
    return results


def lowest_emission_first(
    routes: list[RouteAlternative],
    shipments: list[ShipmentEmission],
    limit: int | None = None,
) -> list[tuple[RouteAlternative, ShipmentEmission]]:
    """Order alternatives by emissions, keeping the all-road baseline first.

    Ranking by distance and then trimming hides the answer this tool exists to give:
    the shortest alternative is regularly not the cleanest, so a trimmed distance
    ranking can drop the lowest-emission option entirely.
    """
    paired = list(zip(routes, shipments))
    baseline = [pair for pair in paired if pair[0].is_all_road]
    alternatives = sorted(
        (pair for pair in paired if not pair[0].is_all_road),
        key=lambda pair: pair[1].total_co2_kg,
    )
    return baseline + (alternatives[:limit] if limit is not None else alternatives)


def tree_equivalent(saving_co2_kg: float, species_factors: dict[str, float]) -> dict[str, float]:
    """Trees whose annual uptake matches the saving. Meaningless for a negative saving."""
    if saving_co2_kg <= 0:
        return {species: 0.0 for species in species_factors}
    return {
        species: saving_co2_kg / kg_per_year for species, kg_per_year in species_factors.items()
    }
