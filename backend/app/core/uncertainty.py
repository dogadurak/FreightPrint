import csv
import random
from dataclasses import dataclass
from math import floor, log10
from pathlib import Path

from .network import DATA_DIR
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


@dataclass(frozen=True)
class DistanceUncertainty:
    mode: str
    relative: float
    sample_size: int
    is_measured: bool
    basis: str
    notes: str


def load_distance_uncertainty(path: Path | None = None) -> dict[str, DistanceUncertainty]:
    """How far each mode's distance might be out, per mode rather than per route.

    One band across every leg produces false confidence exactly where it is least
    deserved. Road distance is computed independently by OSRM and lands within 1.9% of
    the reported figure across thirty rows; the sea and rail legs use a reference table
    that has never been recomputed against anything but a single searoute crossing. A
    shared 5% was simultaneously too wide for road and too narrow for the leg the
    product's whole finding rests on.

    Like the emission factors, these live in a data file carrying their own provenance:
    what each was measured against, how many samples back it, and whether it is a
    measurement at all.
    """
    path = path or DATA_DIR / "distance_uncertainty.csv"
    with open(path, encoding="utf-8") as f:
        return {
            row["mode"]: DistanceUncertainty(
                mode=row["mode"],
                relative=float(row["relative_uncertainty"]),
                sample_size=int(row["sample_size"]),
                is_measured=row["is_measured"].strip().lower() == "yes",
                basis=row["basis"],
                notes=row["notes"],
            )
            for row in csv.DictReader(f)
        }


@dataclass
class EmissionRange:
    low_co2_kg: float
    median_co2_kg: float
    high_co2_kg: float
    samples: int
    confidence: float
    # What each mode's spread actually rests on, for the modes this route uses.
    #
    # Without this the band is a number with no provenance, and one of the numbers
    # feeding it has none. `distance_uncertainty.csv` says of rail: sample_size 0,
    # is_measured no, "a placeholder that happens to be non-zero rather than a finding" —
    # it borrows the sea figure because the two share a lineage, not because rail was
    # ever measured. That admission sat in a CSV nothing read, which is the same as not
    # admitting it. A band drawn partly from an unmeasured spread has to say so wherever
    # it is shown.
    bases: tuple["DistanceUncertainty", ...] = ()

    @property
    def unmeasured_modes(self) -> tuple[str, ...]:
        return tuple(b.mode for b in self.bases if not b.is_measured)

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
    distance_uncertainty: float | None = None,
    load_factor: float | None = None,
    load_uncertainty: float = 0.0,
    # None, not 0.0, and the difference is the whole range. `effective_factor_value`
    # reads 0.0 as "this vehicle never returns empty" and strips the empty-return
    # uplift the published factor already carries — 30% on GLEC road, 17% on rail. A
    # simulation defaulting to that put the all-road baseline's range at 3,705-3,804 kg
    # beside a headline of 4,882 kg: a band that does not contain the number it belongs to.
    empty_return_share: float | None = None,
    scope: str = DEFAULT_SCOPE,
    road_fuel_type: str | None = None,
    factor_set: str = DEFAULT_FACTOR_SET,
    samples: int = DEFAULT_SAMPLES,
    confidence: float = 0.9,
    seed: int | None = None,
    factors: list[EmissionFactor] | None = None,
) -> EmissionRange:
    """Turn distance and load-factor uncertainty into a range instead of one exact number.

    **Every argument this shares with `calculate_route_emission` has to default the same
    way there and here**, because the two describe one shipment: the point estimate and
    the band drawn around it. Where they disagree the band stops containing the number
    it is drawn around, which reads as a bug even when each function is right on its own.

    The load band is centred on `load_factor` and clipped at full capacity, so the range
    brackets the point estimate rather than sitting entirely above it.

    Distance uncertainty is taken per mode from `distance_uncertainty.csv` unless the
    caller overrides it with a single figure for every leg.
    """
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if distance_uncertainty is not None and not 0 <= distance_uncertainty < 1:
        raise ValueError(f"distance_uncertainty must be in [0, 1), got {distance_uncertainty}")
    if load_factor is None and load_uncertainty:
        raise ValueError("load_uncertainty needs an explicit load_factor to vary around")

    # Without an explicit load factor each leg keeps its own published basis, and there
    # is no single utilisation to sample: only distance varies.
    band = load_band(load_factor, load_uncertainty) if load_factor is not None else None

    factors = factors if factors is not None else load_emission_factors()
    by_mode = load_distance_uncertainty() if distance_uncertainty is None else {}

    def spread_for(mode: str) -> float:
        if distance_uncertainty is not None:
            return distance_uncertainty
        entry = by_mode.get(mode)
        # An unlisted mode gets the widest listed figure rather than zero: a mode nobody
        # has characterised is the last one that should look certain.
        return entry.relative if entry else max(e.relative for e in by_mode.values())

    leg_inputs = [
        (
            leg.distance_km,
            leg.mode,
            spread_for(leg.mode),
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
        for distance_km, mode, spread, factor in leg_inputs:
            sampled_km = rng.triangular(
                distance_km * (1 - spread),
                distance_km * (1 + spread),
                distance_km,
            )
            # Road only, exactly as the point estimate does it — the two describe one
            # shipment and an argument applied differently in each would put the band
            # somewhere other than around the number it belongs to.
            total += sampled_km * tonnage * effective_factor_value(
                factor, sampled_load, empty_return_share if mode == "road" else None
            )
        totals.append(total)

    totals.sort()
    tail = (1 - confidence) / 2
    # Only the modes this route actually uses, and only when the per-mode table was the
    # source. A caller who passed one spread for everything overrode the table, and
    # reporting its bases would describe a calculation that did not happen.
    used = dict.fromkeys(mode for _, mode, _, _ in leg_inputs)
    bases = tuple(by_mode[m] for m in used if m in by_mode) if by_mode else ()

    return EmissionRange(
        low_co2_kg=totals[int(tail * samples)],
        median_co2_kg=totals[samples // 2],
        high_co2_kg=totals[min(int((1 - tail) * samples), samples - 1)],
        samples=samples,
        confidence=confidence,
        bases=bases,
    )
