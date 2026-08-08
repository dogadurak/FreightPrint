"""The extra emissions of carrying refrigerated cargo.

Refrigeration is charged **per hour, not per kilometre**, and that choice is the whole
point of this module.

GLEC publishes reefer only for container ships, as a ratio: 145 g CO2e/TEU-km against
76 dry, so reefer is roughly twice dry. Carrying that *ratio* to another mode is wrong.
A container ship's own per-tonne-km emissions are small, which is why the refrigeration
unit doubles them; a ro-ro's are already an order of magnitude larger, so the same unit
is a small addition. Applying x1.9 to ro-ro overstates the overhead ninefold.

What transfers between modes is the unit's **energy draw**, and that is a function of
time. So the overhead is derived once as g CO2e per tonne per hour and applied across
the whole door-to-door clock — including the hours a box spends on a terminal and
waiting for a sailing, where it is still plugged in and still drawing. That is a real
cost of a multimodal route that a per-kilometre figure cannot see.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .network import DATA_DIR
from .schedule import Timeline

DEFAULT_FACTOR_ID = "derived_glec_container"


class ReeferFactorError(LookupError):
    pass


@dataclass(frozen=True)
class ReeferFactor:
    id: str
    g_co2e_per_tonne_hour: float
    scope: str
    source: str
    is_verified: bool
    notes: str


@dataclass
class ReeferEmission:
    """Refrigeration's share, split by what the cargo was doing at the time."""

    factor: ReeferFactor
    tonnage: float
    hours_by_kind: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_hours(self) -> float:
        return sum(self.hours_by_kind.values())

    @property
    def co2_kg(self) -> float:
        return self.factor.g_co2e_per_tonne_hour * self.tonnage * self.total_hours / 1000

    @property
    def co2_by_kind(self) -> dict[str, float]:
        rate = self.factor.g_co2e_per_tonne_hour * self.tonnage / 1000
        return {kind: rate * hours for kind, hours in self.hours_by_kind.items()}

    @property
    def stationary_co2_kg(self) -> float:
        """What the unit burns while the cargo is not moving — the part km cannot show."""
        by_kind = self.co2_by_kind
        return by_kind.get("dwell", 0.0) + by_kind.get("wait", 0.0)


def load_reefer_factors(path: Path | None = None) -> dict[str, ReeferFactor]:
    path = path or DATA_DIR / "reefer_factors.csv"
    with open(path, encoding="utf-8") as f:
        return {
            row["id"]: ReeferFactor(
                id=row["id"],
                g_co2e_per_tonne_hour=float(row["g_co2e_per_tonne_hour"]),
                scope=row["scope"],
                source=row["source"],
                is_verified=row["is_verified"].strip().lower() == "yes",
                notes=row["notes"],
            )
            for row in csv.DictReader(f)
        }


def calculate_reefer(
    timeline: Timeline,
    tonnage: float,
    factor_id: str = DEFAULT_FACTOR_ID,
    factors: dict[str, ReeferFactor] | None = None,
) -> ReeferEmission:
    """Refrigeration emissions over a route's whole elapsed time."""
    factors = factors if factors is not None else load_reefer_factors()
    factor = factors.get(factor_id)
    if factor is None:
        raise ReeferFactorError(
            f"no reefer factor {factor_id!r}; known: {', '.join(sorted(factors))}"
        )
    if tonnage <= 0:
        raise ValueError(f"tonnage must be positive, got {tonnage}")

    emission = ReeferEmission(
        factor=factor, tonnage=tonnage, hours_by_kind=dict(timeline.hours_by_kind)
    )
    if not factor.is_verified:
        emission.warnings.append(
            f"reefer overhead is derived, not published: {factor.source}. "
            "It rests on a tonnes-per-TEU conversion, an assumed ship speed, and the "
            "assumption that the unit's draw transfers between modes."
        )
    if timeline.any_estimated:
        emission.warnings.append(
            "Refrigeration is billed against the journey's clock, so it inherits every "
            "estimate in that clock."
        )
    return emission
