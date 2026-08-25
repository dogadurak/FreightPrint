"""Indicative freight rates, and the warning that has to travel with them.

These three numbers used to sit inline in an API handler under a comment saying "for
demo", and they decided which alternative got the "cheapest" badge. A recommendation
resting on invisible assumptions is worse than no recommendation, because the badge
reads as a finding.
"""

import pytest

from app.core.rates import (
    FreightRate,
    RateNotFoundError,
    estimate_freight_cost,
    load_freight_rates,
)
from app.core.route import Leg, RouteAlternative


def _route(*legs):
    return RouteAlternative(
        legs=[Leg(mode=m, from_name="a", to_name="b", distance_km=km) for m, km in legs],
        label="t",
    )


def test_every_mode_the_engine_routes_has_a_rate():
    """A missing row does not make a leg free; it makes the comparison wrong."""
    rates = load_freight_rates()

    assert {"road", "sea", "rail"} <= set(rates)
    assert all(rate.eur_per_km > 0 for rate in rates.values())


def test_the_rates_admit_they_are_not_a_quotation():
    """There is no published table to cite for freight. Saying so in the data is the
    difference between an estimate and a claim."""
    for rate in load_freight_rates().values():
        assert not rate.is_verified, f"{rate.mode} claims to be verified; against what?"
        assert rate.source and rate.notes


def test_a_cost_carries_its_own_caveat():
    cost = estimate_freight_cost(_route(("road", 100), ("sea", 1000)))

    assert cost.is_indicative
    assert cost.warnings, "an unverified rate priced a route and said nothing"
    assert "gösterge" in cost.warnings[0]


def test_cost_is_the_sum_of_its_modes():
    rates = load_freight_rates()
    route = _route(("road", 100), ("sea", 1000), ("rail", 500))

    cost = estimate_freight_cost(route)

    assert cost.eur == pytest.approx(
        100 * rates["road"].eur_per_km
        + 1000 * rates["sea"].eur_per_km
        + 500 * rates["rail"].eur_per_km
    )
    assert sum(cost.by_mode.values()) == pytest.approx(cost.eur)


def test_a_mode_with_no_rate_is_refused_rather_than_charged_nothing():
    """Silently charging nothing for a leg would hand its route the cheapest badge on
    the strength of a missing row — the failure would look like a finding."""
    rates = {"road": FreightRate("road", 1.2, "indicative", False, "s", "n")}

    with pytest.raises(RateNotFoundError, match="rail"):
        estimate_freight_cost(_route(("road", 100), ("rail", 500)), rates=rates)


def test_a_verified_table_would_drop_the_caveat():
    """The warning follows the data, not the code: entering a carrier's contracted
    rates should make the figure a real one."""
    rates = {"road": FreightRate("road", 1.05, "contract", True, "carrier tariff", "")}

    cost = estimate_freight_cost(_route(("road", 200)), rates=rates)

    assert not cost.is_indicative
    assert cost.warnings == []
    assert cost.eur == pytest.approx(210.0)
