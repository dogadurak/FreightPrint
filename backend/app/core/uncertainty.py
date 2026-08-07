import random
from dataclasses import dataclass
from math import floor, log10

from .emissions import (
    DEFAULT_FACTOR_SET,
    DEFAULT_SCOPE,
    EmissionFactor,
    effective_factor_value,
    expand_route_legs,
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


def load_band(load_factor: float, load_uncertainty: float) -> tuple[float, float]:
    """The utilisation range to explore, clipped at full capacity.

    Callers should price the point estimate at this band's midpoint. Quoting the number
    at `load_factor` while the band is clipped below it puts the headline figure outside
    its own range, which reads as a bug even though both numbers are right.
    """
    if not 0 < load_factor <= 1:
        raise ValueError(f"load_factor must be in (0, 1], got {load_factor}")
    if not 0 <= load_uncertainty < 1:
        raise ValueError(f"load_uncertainty must be in [0, 1), got {load_uncertainty}")
    return (load_factor * (1 - load_uncertainty), min(1.0, load_factor * (1 + load_uncertainty)))


def simulate_emission_range(
    route: RouteAlternative,
    tonnage: float,
    distance_uncertainty: float = 0.05,
    load_factor: float | None = None,
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

    The load band is centred on `load_factor` and clipped at full capacity, so the range
    brackets the point estimate rather than sitting entirely above it.
    """
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if not 0 <= distance_uncertainty < 1:
        raise ValueError(f"distance_uncertainty must be in [0, 1), got {distance_uncertainty}")
    if load_factor is None and load_uncertainty:
        raise ValueError("load_uncertainty needs an explicit load_factor to vary around")

    # Without an explicit load factor each leg keeps its own published basis, and there
    # is no single utilisation to sample: only distance varies.
    band = load_band(load_factor, load_uncertainty) if load_factor is not None else None

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
        for leg in expand_route_legs(route)
    ]

    rng = random.Random(seed)
    totals = []
    for _ in range(samples):
        sampled_load = rng.uniform(*band) if band is not None else None
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
