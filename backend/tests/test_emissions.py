import pytest

from app.core.emissions import (
    calculate_route_emission,
    calculate_shipment,
    effective_factor_value,
    find_factor,
    load_emission_factors,
    load_tree_factors,
    tree_equivalent,
)
from app.core.route import Leg, RouteAlternative
from app.core.uncertainty import round_to_significant, simulate_emission_range

REFERENCE_ROAD_FACTOR = 0.121
REFERENCE_SEA_FACTOR = 0.012
REFERENCE_RAIL_FACTOR = 0.016


def _leg(mode, distance_km, ferry_km=0.0):
    return Leg(
        mode=mode,
        from_name="a",
        to_name="b",
        distance_km=distance_km,
        ferry_km=ferry_km,
    )


def _route(label, legs):
    return RouteAlternative(legs=legs, label=label)


def test_reference_factors_match_the_source_report():
    factors = load_emission_factors()

    assert find_factor(factors, "road").value == REFERENCE_ROAD_FACTOR
    assert find_factor(factors, "sea").value == REFERENCE_SEA_FACTOR
    assert find_factor(factors, "rail").value == REFERENCE_RAIL_FACTOR


def test_every_factor_declares_scope_source_and_year():
    for factor in load_emission_factors():
        assert factor.scope in {"TTW", "WTW"}
        assert factor.source
        assert factor.year > 2000


def test_leg_emission_is_distance_times_tonnage_times_factor():
    route = _route("test", [_leg("road", 100)])
    shipment = calculate_route_emission(route, tonnage=24)

    assert shipment.total_co2_kg == pytest.approx(100 * 24 * REFERENCE_ROAD_FACTOR)


def test_saving_is_all_road_minus_multimodal():
    all_road = _route("all-road", [_leg("road", 3425)])
    multimodal = _route("via sea", [_leg("road", 60), _leg("sea", 2500), _leg("rail", 990)])

    results = calculate_shipment([all_road, multimodal], tonnage=24)
    baseline, alternative = results

    assert baseline.all_road_co2_kg == pytest.approx(baseline.total_co2_kg)
    assert alternative.saving_co2_kg == pytest.approx(
        baseline.total_co2_kg - alternative.total_co2_kg
    )
    assert alternative.saving_co2_kg > 0


def test_ferry_km_inside_a_road_leg_is_charged_at_the_sea_factor():
    route = _route("crete", [_leg("road", 643, ferry_km=137)])
    shipment = calculate_route_emission(route, tonnage=24)

    expected = 24 * (506 * REFERENCE_ROAD_FACTOR + 137 * REFERENCE_SEA_FACTOR)
    assert shipment.total_co2_kg == pytest.approx(expected)
    assert shipment.co2_by_mode.keys() == {"road", "sea"}
    assert any("ferry" in warning for warning in shipment.warnings)


def test_unverified_factor_raises_a_warning_instead_of_passing_silently():
    route = _route("hvo", [_leg("road", 100)])
    shipment = calculate_route_emission(route, tonnage=24, road_fuel_type="hvo")

    assert any("unverified factor" in warning for warning in shipment.warnings)


def test_partial_load_raises_emissions_per_ton_km():
    factor = find_factor(load_emission_factors(), "road")

    assert effective_factor_value(factor, load_factor=0.5) == pytest.approx(factor.value * 2)
    assert effective_factor_value(factor, empty_return_share=1.0) == pytest.approx(factor.value * 2)


@pytest.mark.parametrize("load_factor", [0, -0.5, 1.5])
def test_impossible_load_factor_is_rejected(load_factor):
    factor = find_factor(load_emission_factors(), "road")

    with pytest.raises(ValueError):
        effective_factor_value(factor, load_factor=load_factor)


def test_tree_equivalent_uses_the_configured_species_factors():
    species = load_tree_factors()
    trees = tree_equivalent(7717.68, species)

    assert trees["average_tree"] == pytest.approx(7717.68 / 22.5)
    assert trees["pinus_brutia"] == pytest.approx(7717.68 / 411.4)


def test_tree_equivalent_is_zero_when_the_alternative_emits_more():
    assert set(tree_equivalent(-500, load_tree_factors()).values()) == {0.0}


def test_uncertainty_range_brackets_the_point_estimate():
    """A reported range that excludes the number printed beside it reads as a bug."""
    route = _route("via sea", [_leg("road", 60), _leg("sea", 2500), _leg("rail", 990)])
    point = calculate_route_emission(route, tonnage=24).total_co2_kg

    result = simulate_emission_range(route, tonnage=24, seed=0)

    assert result.low_co2_kg < point < result.high_co2_kg


def test_load_uncertainty_widens_the_range_upwards_only():
    """Load factor cannot exceed capacity, so the band can only add emissions."""
    route = _route("via sea", [_leg("road", 60), _leg("sea", 2500)])
    point = calculate_route_emission(route, tonnage=24).total_co2_kg

    result = simulate_emission_range(route, tonnage=24, load_uncertainty=0.2, seed=0)

    assert result.high_co2_kg > point
    assert result.median_co2_kg > point


def test_uncertainty_range_is_reproducible_with_a_seed():
    route = _route("via sea", [_leg("road", 60), _leg("sea", 2500)])
    first = simulate_emission_range(route, tonnage=24, seed=7)
    second = simulate_emission_range(route, tonnage=24, seed=7)

    assert first.low_co2_kg == second.low_co2_kg


def test_reported_range_drops_digits_the_inputs_do_not_justify():
    assert round_to_significant(502.67999999) == 503.0
    assert round_to_significant(1283.4, digits=3) == 1280.0
    assert round_to_significant(0.0) == 0.0
