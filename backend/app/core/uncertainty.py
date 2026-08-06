import random
from dataclasses import dataclass
from math import floor, log10

from .emissions import (
    DEFAULT_FACTOR_SET,
    DEFAULT_SCOPE,
    EmissionFactor,
    effective_factor_value,
    find_factor,
    load_emission_factors,
)
from .route import RouteAlternative

DEFAULT_SAMPLES = 500


@dataclass
class EmissionRange:
    low_co2_kg: float
    median_co2_kg: float
    high_co2_kg: float
    samples: int
    confidence: float

    def rounded(self, digits: int = 3) -> tuple[float, float]:
        return (
            round_to_significant(self.low_co2_kg, digits),
            round_to_significant(self.high_co2_kg, digits),
        )


def round_to_significant(value: float, digits: int = 3) -> float:
    """Drop digits the inputs never justified, so 502.67999999 does not reach a report."""
    if value == 0:
        return 0.0
    return round(value, -int(floor(log10(abs(value)))) + (digits - 1))


def simulate_emission_range(
    route: RouteAlternative,
    tonnage: float,
    distance_uncertainty: float = 0.05,
    load_factor: float = 1.0,
    load_uncertainty: float = 0.0,
    empty_return_share: float = 0.0,
    scope: str = DEFAULT_SCOPE,
    road_fuel_type: str | None = None,
    factor_set: str = DEFAULT_FACTOR_SET,
    samples: int = DEFAULT_SAMPLES,
    confidence: float = 0.9,
    seed: int | None = None,
    factors: list[EmissionFactor] | None = None,
) -> EmissionRange:
    """Turn distance and load-factor uncertainty into a range instead of one exact number.

    `load_uncertainty` only widens downwards: a vehicle cannot be loaded past capacity, so
    the reported band is skewed upwards from the point estimate rather than centred on it.
    """
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if not 0 <= load_uncertainty < 1:
        raise ValueError(f"load_uncertainty must be in [0, 1), got {load_uncertainty}")

    factors = factors if factors is not None else load_emission_factors()
    leg_inputs = [
        (
            leg.distance_km,
            find_factor(
                factors,
                mode=leg.mode,
                scope=scope,
                fuel_type=road_fuel_type if leg.mode == "road" else None,
                factor_set=factor_set,
            ),
        )
        for leg in route.legs
    ]

    rng = random.Random(seed)
    lowest_load = load_factor * (1 - load_uncertainty)
    totals = []
    for _ in range(samples):
        sampled_load = rng.uniform(lowest_load, load_factor)
        total = 0.0
        for distance_km, factor in leg_inputs:
            sampled_km = rng.triangular(
                distance_km * (1 - distance_uncertainty),
                distance_km * (1 + distance_uncertainty),
                distance_km,
            )
            total += sampled_km * tonnage * effective_factor_value(
                factor, sampled_load, empty_return_share
            )
        totals.append(total)

    totals.sort()
    tail = (1 - confidence) / 2
    return EmissionRange(
        low_co2_kg=totals[int(tail * samples)],
        median_co2_kg=totals[samples // 2],
        high_co2_kg=totals[min(int((1 - tail) * samples), samples - 1)],
        samples=samples,
        confidence=confidence,
    )
