import pytest

from app.core.cost import (
    CostInputError,
    calculate_ets,
    compare_reroute,
    phase_in_share,
    voyage_coverage_share,
)
from app.core.emissions import calculate_route_emission
from app.core.route import Leg, RouteAlternative

CARBON_PRICE = 80.0


def _shipment(legs, scope="TTW"):
    return calculate_route_emission(RouteAlternative(legs=legs, label="t"), tonnage=24, scope=scope)


@pytest.mark.parametrize(
    ("origin", "destination", "expected"),
    [
        ("IT", "FR", 1.0),   # both in the EEA
        ("TR", "IT", 0.5),   # one end outside
        ("IT", "TR", 0.5),   # direction does not matter
        ("TR", "EG", 0.0),   # neither end inside
        ("NO", "IT", 1.0),   # EEA, not EU
        (None, "IT", 0.5),   # unknown end counts as outside
    ],
)
def test_coverage_follows_where_the_voyage_runs(origin, destination, expected):
    assert voyage_coverage_share(origin, destination) == expected


@pytest.mark.parametrize(("year", "expected"), [(2024, 0.40), (2025, 0.70), (2026, 1.0), (2030, 1.0)])
def test_phase_in_reaches_full_coverage_in_2026(year, expected):
    assert phase_in_share(year) == expected


def test_only_sea_legs_are_billed():
    """Road joins ETS2 separately and rail is not in the maritime scheme at all."""
    shipment = _shipment([Leg("road", "a", "b", 500), Leg("rail", "b", "c", 900)])

    cost = calculate_ets(shipment, [("TR", "IT"), ("IT", "DE")], carbon_price_eur=CARBON_PRICE)

    assert cost.legs == []
    assert cost.cost_eur == 0


def test_a_turkey_to_eu_sea_leg_is_billed_at_half():
    shipment = _shipment([Leg("sea", "Pendik", "Trieste", 2500)])
    co2_kg = shipment.total_co2_kg

    cost = calculate_ets(shipment, [("TR", "IT")], carbon_price_eur=CARBON_PRICE, year=2026)

    assert cost.legs[0].coverage_share == 0.5
    assert cost.covered_tonnes == pytest.approx(co2_kg / 1000 * 0.5)
    assert cost.cost_eur == pytest.approx(co2_kg / 1000 * 0.5 * CARBON_PRICE)


def test_an_intra_eu_sea_leg_is_billed_in_full():
    shipment = _shipment([Leg("sea", "Trieste", "Sete", 1500)])

    cost = calculate_ets(shipment, [("IT", "FR")], carbon_price_eur=CARBON_PRICE)

    assert cost.legs[0].coverage_share == 1.0


def test_an_earlier_year_bills_less_for_the_same_voyage():
    shipment = _shipment([Leg("sea", "Pendik", "Trieste", 2500)])
    pairs = [("TR", "IT")]

    in_2024 = calculate_ets(shipment, pairs, carbon_price_eur=CARBON_PRICE, year=2024)
    in_2026 = calculate_ets(shipment, pairs, carbon_price_eur=CARBON_PRICE, year=2026)

    assert in_2024.cost_eur == pytest.approx(in_2026.cost_eur * 0.40)


def test_pricing_wtw_emissions_says_it_overstates_the_bill():
    """The scheme bills tank-to-wake; pricing WTW figures against it is conservative."""
    shipment = _shipment([Leg("sea", "Pendik", "Trieste", 2500)], scope="TTW")

    cost = calculate_ets(shipment, [("TR", "IT")])
    assert cost.notes == []

    shipment.scope = "WTW"
    assert any("overstates" in note for note in calculate_ets(shipment, [("TR", "IT")]).notes)


@pytest.mark.parametrize("price", [-1, -0.01])
def test_a_negative_carbon_price_is_refused(price):
    shipment = _shipment([Leg("sea", "a", "b", 100)])

    with pytest.raises(CostInputError):
        calculate_ets(shipment, [("TR", "IT")], carbon_price_eur=price)


def test_a_mismatched_country_list_is_refused_rather_than_zipped_short():
    """zip() would silently drop legs and quietly under-bill the voyage."""
    shipment = _shipment([Leg("sea", "a", "b", 100), Leg("sea", "b", "c", 100)])

    with pytest.raises(CostInputError):
        calculate_ets(shipment, [("TR", "IT")])


def test_reroute_reports_what_avoiding_the_zone_cost():
    reroute = compare_reroute(
        direct_distance_km=19538, direct_duration_h=659,
        direct_co2_kg=100_000, direct_ets_eur=1000,
        diverted_distance_km=25686, diverted_duration_h=867,
        diverted_co2_kg=131_000, diverted_ets_eur=1310,
        surcharge_eur=4000,
    )

    assert reroute.extra_distance_km == pytest.approx(6148)
    assert reroute.extra_duration_h == pytest.approx(208)
    assert reroute.extra_co2_kg == pytest.approx(31_000)
    assert reroute.total_eur == pytest.approx(310 + 4000)


def test_a_missing_duration_leaves_the_difference_unknown_rather_than_zero():
    reroute = compare_reroute(
        direct_distance_km=1, direct_duration_h=None, direct_co2_kg=1, direct_ets_eur=0,
        diverted_distance_km=2, diverted_duration_h=5, diverted_co2_kg=2, diverted_ets_eur=0,
    )

    assert reroute.extra_duration_h is None
