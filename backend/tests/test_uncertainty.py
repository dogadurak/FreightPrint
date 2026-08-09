"""Per-mode distance uncertainty.

One band across every leg made the all-road baseline and the multimodal option look
equally well founded, when road distance is computed independently and the sea leg comes
from a reference table. The comparison is between those two, so the difference in how
much each is trusted is part of the answer.
"""

from app.core.uncertainty import simulate_emission_range
from tests.test_emissions import _leg, _route




def test_each_mode_carries_its_own_measured_spread():
    """One band across every leg is false confidence where it is least deserved. Road
    distance is computed independently and lands within 1.9%; the sea leg comes from a
    reference table nobody has recomputed. They must not claim equal certainty."""
    from app.core.uncertainty import load_distance_uncertainty

    spreads = load_distance_uncertainty()

    assert spreads["road"].relative < spreads["sea"].relative
    assert spreads["road"].is_measured
    assert not spreads["sea"].is_measured, "the sea figure rests on a single crossing"


def test_a_road_only_route_gets_a_narrower_band_than_one_crossing_water():
    road_only = _route("road", [_leg("road", 1000)])
    with_sea = _route("mixed", [_leg("road", 500), _leg("sea", 500)])

    dry = simulate_emission_range(road_only, 24.0, factor_set="glec", scope="WTW", seed=0)
    wet = simulate_emission_range(with_sea, 24.0, factor_set="glec", scope="WTW", seed=0)

    width = lambda r: (r.high_co2_kg - r.low_co2_kg) / r.median_co2_kg
    assert width(dry) < width(wet)


def test_an_explicit_figure_still_overrides_every_leg():
    """Callers that want one number for the whole route keep that option."""
    route = _route("mixed", [_leg("road", 500), _leg("sea", 500)])

    narrow = simulate_emission_range(
        route, 24.0, distance_uncertainty=0.001, factor_set="glec", scope="WTW", seed=0
    )
    wide = simulate_emission_range(
        route, 24.0, distance_uncertainty=0.30, factor_set="glec", scope="WTW", seed=0
    )

    width = lambda r: (r.high_co2_kg - r.low_co2_kg) / r.median_co2_kg
    assert width(narrow) < width(wide) / 10


def test_the_shipped_spreads_are_plausible_rather_than_zero():
    """A zero would mean a distance is known exactly, which none of them is."""
    from app.core.uncertainty import load_distance_uncertainty

    for mode, spread in load_distance_uncertainty().items():
        assert 0 < spread.relative < 0.5, f"{mode} spread {spread.relative} is not credible"
        assert spread.basis, f"{mode} does not say what it was measured against"
        assert spread.notes


def test_an_uncharacterised_mode_is_not_treated_as_certain(monkeypatch):
    """A mode nobody has measured is the last one that should look exact."""
    from app.core import uncertainty as uncertainty_module

    known = uncertainty_module.load_distance_uncertainty()
    monkeypatch.setattr(
        uncertainty_module, "load_distance_uncertainty",
        lambda *a, **k: {"road": known["road"]},
    )
    route = _route("mixed", [_leg("sea", 1000)])

    result = simulate_emission_range(route, 24.0, factor_set="glec", scope="WTW", seed=0)

    assert result.high_co2_kg > result.low_co2_kg, "an unlisted mode collapsed to certainty"
