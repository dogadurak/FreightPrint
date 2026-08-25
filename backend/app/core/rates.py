"""Indicative freight rates, and a loud warning that they are not a quotation.

These existed as three numbers written inline in an API handler — 1.2, 0.3 and 0.5 euro
a kilometre, under a comment saying "for demo" — and they decided which alternative got
the "cheapest" badge. A recommendation with no basis is worse than no recommendation,
because the badge reads as a finding.

They are still estimates. What changed is that they now behave like every other number
in this engine: they live in a data file, they carry `is_verified=no`, they say what
they rest on, and anything that prices with them is told so and passes the warning on.

The honest position on freight rates is that there is no published table to cite. Real
prices are contracted per lane, move with fuel, season, capacity and the availability of
a return load, and vary by a factor of several between shippers on the same corridor.
So the rate a carrier actually pays should be entered rather than inferred, and
`load_freight_rates` exists to be overridden. Until it is, the badge means "cheapest on
indicative rates", which is a far weaker claim than "cheapest" and has to be shown as
such.
"""

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .network import DATA_DIR


class RateNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class FreightRate:
    mode: str
    eur_per_km: float
    basis: str
    is_verified: bool
    source: str
    notes: str


@lru_cache(maxsize=1)
def load_freight_rates(path: Path | None = None) -> dict[str, FreightRate]:
    path = path or DATA_DIR / "freight_rates.csv"
    with open(path, encoding="utf-8") as f:
        return {
            row["mode"]: FreightRate(
                mode=row["mode"],
                eur_per_km=float(row["eur_per_km"]),
                basis=row["basis"],
                is_verified=row["is_verified"].strip().lower() == "yes",
                source=row["source"],
                notes=row["notes"],
            )
            for row in csv.DictReader(f)
        }


@dataclass
class FreightCost:
    """What a route would cost to move, and how much that figure can be trusted."""

    eur: float
    by_mode: dict[str, float]
    is_indicative: bool
    warnings: list[str]


def estimate_freight_cost(route, rates: dict[str, FreightRate] | None = None) -> FreightCost:
    """Price a route's distance at the rate table, mode by mode.

    A mode with no rate is an error rather than a free leg: silently charging nothing
    for a rail leg would hand the intermodal option the "cheapest" badge on the strength
    of a missing row.
    """
    rates = rates if rates is not None else load_freight_rates()

    by_mode: dict[str, float] = {}
    for mode, distance_km in route.distance_by_mode.items():
        rate = rates.get(mode)
        if rate is None:
            raise RateNotFoundError(f"no freight rate for mode={mode}; add it to freight_rates.csv")
        by_mode[mode] = distance_km * rate.eur_per_km

    used = [rates[mode] for mode in by_mode]
    unverified = [rate for rate in used if not rate.is_verified]
    warnings = []
    if unverified:
        warnings.append(
            "Navlun maliyeti gösterge niteliğindedir; yayımlanmış bir tarifeye değil, "
            f"kaba büyüklüklere dayanır ({', '.join(sorted(r.mode for r in unverified))}). "
            "Gerçek navlun sözleşmeye, hatta ve doluluğa göre kat kat değişir."
        )

    return FreightCost(
        eur=sum(by_mode.values()),
        by_mode=by_mode,
        is_indicative=bool(unverified),
        warnings=warnings,
    )
