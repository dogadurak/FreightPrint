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


# ── the band has to say what it rests on ──────────────────────────────────────

def test_the_per_mode_table_is_the_default_not_a_flat_figure():
    """The defect this section exists to prevent coming back.

    `RouteRequest.distance_uncertainty` defaulted to 0.05, and since the field was a
    plain float it was never None, so `simulate_emission_range` never consulted
    `distance_uncertainty.csv`. Every band the dashboard drew was a flat five per cent on
    every mode — precisely what the README says was wrong in both directions — while the
    README described the per-mode behaviour at length. Built, measured, documented, off.
    """
    from app.api.schemas import RouteRequest

    field = RouteRequest.model_fields["distance_uncertainty"]

    assert field.default is None, "a flat default silently disables the per-mode table"


def test_the_cli_also_defaults_to_the_table():
    """Both entry points had their own copy of the same wrong default."""
    from app.cli import _build_parser

    default = _build_parser().get_default("distance_uncertainty")

    assert default is None


def test_per_mode_narrows_an_all_road_band_and_the_difference_is_large():
    """Road distance is computed here by OSRM and checked against 30 reported ones, so a
    flat 5% is far too wide for it. The README quotes 7.0% against 2.6% on this corridor;
    if that gap ever closes, one of the two numbers has changed and should be re-derived
    rather than restated."""
    route = _route("all-road", [_leg("road", 3425)])

    flat = simulate_emission_range(route, tonnage=24, distance_uncertainty=0.05,
                                   factor_set="glec", seed=0)
    per_mode = simulate_emission_range(route, tonnage=24, distance_uncertainty=None,
                                       factor_set="glec", seed=0)
    width = lambda r: (r.high_co2_kg - r.low_co2_kg) / r.median_co2_kg

    assert width(per_mode) < width(flat) / 2


def test_the_band_reports_which_modes_were_never_measured():
    """Rail's spread has a sample size of zero and is borrowed from sea. A band drawn
    partly from that must say so, or it reads as measured throughout."""
    route = _route("multimodal", [
        _leg("road", 61), _leg("sea", 2500), _leg("rail", 950)])

    result = simulate_emission_range(route, tonnage=24, distance_uncertainty=None,
                                     factor_set="glec", seed=0)

    assert set(result.unmeasured_modes) == {"sea", "rail"}
    assert {b.mode for b in result.bases} == {"road", "sea", "rail"}
    rail = next(b for b in result.bases if b.mode == "rail")
    assert rail.sample_size == 0 and not rail.is_measured


def test_only_the_modes_the_route_uses_are_reported():
    """A road-only shipment has no sea leg, so a sea caveat beside it would be noise."""
    route = _route("road", [_leg("road", 100)])

    result = simulate_emission_range(route, tonnage=24, distance_uncertainty=None,
                                     factor_set="glec", seed=0)

    assert [b.mode for b in result.bases] == ["road"]
    assert result.unmeasured_modes == ()


def test_an_explicit_override_reports_no_table_bases():
    """A caller who passed one figure for everything overrode the table, and listing its
    bases would describe a calculation that did not happen."""
    route = _route("road", [_leg("road", 100)])

    result = simulate_emission_range(route, tonnage=24, distance_uncertainty=0.05,
                                     factor_set="glec", seed=0)

    assert result.bases == ()
