import pytest

from app.core.emissions import (
    calculate_route_emission,
    calculate_shipment,
    effective_factor_value,
    find_factor,
    load_emission_factors,
    load_tree_factors,
    lowest_emission_first,
    tree_equivalent,
)
from app.core.route import Leg, RouteAlternative
from app.core.uncertainty import load_band, round_to_significant, simulate_emission_range

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


def test_glec_factors_match_the_published_tables():
    """Pin the primary-source values so an edit cannot quietly restate the standard."""
    factors = load_emission_factors()
    glec = lambda mode, scope: find_factor(factors, mode, scope=scope, factor_set="glec").value

    assert glec("road", "TTW") == 0.060 and glec("road", "WTW") == 0.075
    assert glec("sea", "TTW") == 0.063 and glec("sea", "WTW") == 0.068
    assert glec("rail", "TTW") == 0.020 and glec("rail", "WTW") == 0.025


def test_every_mode_has_a_wtw_factor_for_iso_14083():
    """ISO 14083 and GLEC report on a well-to-wake basis, so WTW cannot be missing."""
    factors = load_emission_factors()

    for mode in ("road", "sea", "rail"):
        assert find_factor(factors, mode, scope="WTW", factor_set="glec").is_verified


def test_roro_sea_freight_is_not_cheap_like_container_shipping():
    """A ro-ro ship carries the trailer's tare too, so it is nothing like a box boat.

    The reference set prices sea near a container-ship value while the services it
    describes are ro-ro, which is what makes its saving look far larger than it is.
    """
    factors = load_emission_factors()
    reference = find_factor(factors, "sea", factor_set="reference").value
    glec_roro = find_factor(factors, "sea", factor_set="glec").value

    assert glec_roro > reference * 4


def test_glec_pricing_can_turn_a_reported_saving_negative():
    """On a ro-ro corridor the multimodal option need not win, and must not be forced to."""
    multimodal = _route("via roro", [_leg("road", 61), _leg("sea", 2500), _leg("rail", 950)])
    all_road = _route("all-road", [_leg("road", 2515)])

    baseline, alternative = calculate_shipment([all_road, multimodal], 24, factor_set="glec")

    assert alternative.saving_co2_kg < 0
    assert tree_equivalent(alternative.saving_co2_kg, load_tree_factors()) == {
        "average_tree": 0.0,
        "pinus_brutia": 0.0,
    }


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


@pytest.mark.parametrize("empty_return_share", [-0.1, 1.5, 5])
def test_impossible_empty_return_share_is_rejected(empty_return_share):
    factor = find_factor(load_emission_factors(), "road")

    with pytest.raises(ValueError):
        effective_factor_value(factor, empty_return_share=empty_return_share)


def test_lowest_emission_ranks_ahead_of_shorter_distance():
    """A road-heavy leg can be shorter and still dirtier; emissions decide the order."""
    short_but_road_heavy = _route("via road", [_leg("road", 540), _leg("sea", 2500)])
    longer_but_cleaner = _route("via rail", [_leg("rail", 950), _leg("sea", 2500)])
    routes = [short_but_road_heavy, longer_but_cleaner]
    shipments = calculate_shipment(routes, tonnage=24)

    ranked = lowest_emission_first(routes, shipments)

    assert short_but_road_heavy.total_distance_km < longer_but_cleaner.total_distance_km
    assert ranked[0][0] is longer_but_cleaner


def test_trimming_keeps_the_cleanest_option_not_the_shortest():
    """Trimming a distance ranking used to hide the answer the tool exists to give."""
    routes = [
        _route("all-road", [_leg("road", 2515)]),
        _route("shortest", [_leg("road", 540), _leg("sea", 2500)]),
        _route("cleanest", [_leg("rail", 950), _leg("sea", 2500)]),
    ]
    shipments = calculate_shipment(routes, tonnage=24)

    ranked = lowest_emission_first(routes, shipments, limit=1)

    assert len(ranked) == 2
    assert ranked[0][0].is_all_road
    assert ranked[1][0].label == "cleanest"


def test_all_road_baseline_stays_first_even_when_it_emits_most():
    routes = [_route("all-road", [_leg("road", 2515)]), _route("via sea", [_leg("sea", 2500)])]
    shipments = calculate_shipment(routes, tonnage=24)

    ranked = lowest_emission_first(routes, shipments)

    assert ranked[0][0].is_all_road
    assert ranked[0][1].total_co2_kg > ranked[1][1].total_co2_kg


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


@pytest.mark.parametrize("load_uncertainty", [0.0, 0.1, 0.3])
def test_range_brackets_the_point_estimate_priced_at_the_same_band(load_uncertainty):
    """The headline number is priced at the band midpoint, so it must sit inside the band."""
    route = _route("via sea", [_leg("road", 60), _leg("sea", 2500), _leg("rail", 990)])
    low_load, high_load = load_band(1.0, load_uncertainty)
    point = calculate_route_emission(
        route, tonnage=24, load_factor=(low_load + high_load) / 2
    ).total_co2_kg

    result = simulate_emission_range(
        route, tonnage=24, load_uncertainty=load_uncertainty, seed=0
    )

    assert result.low_co2_kg <= point <= result.high_co2_kg


def test_load_band_is_clipped_at_full_capacity():
    assert load_band(1.0, 0.2) == (0.8, 1.0)
    assert load_band(0.9, 0.1) == pytest.approx((0.81, 0.99))


def test_uncertainty_and_point_estimate_agree_on_ferry_distance():
    """Both paths must read the route through the same ferry split, or they disagree."""
    route = _route("crete", [_leg("road", 643, ferry_km=137)])
    point = calculate_route_emission(route, tonnage=24).total_co2_kg

    result = simulate_emission_range(route, tonnage=24, seed=0)

    assert result.low_co2_kg <= point <= result.high_co2_kg


def test_uncertainty_range_is_reproducible_with_a_seed():
    route = _route("via sea", [_leg("road", 60), _leg("sea", 2500)])
    first = simulate_emission_range(route, tonnage=24, seed=7)
    second = simulate_emission_range(route, tonnage=24, seed=7)

    assert first.low_co2_kg == second.low_co2_kg


def test_reported_range_drops_digits_the_inputs_do_not_justify():
    assert round_to_significant(502.67999999) == 503.0
    assert round_to_significant(1283.4, digits=3) == 1280.0
    assert round_to_significant(0.0) == 0.0
