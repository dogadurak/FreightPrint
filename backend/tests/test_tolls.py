"""The CO2 component of road tolls, and the countries it deliberately does not price."""

import json
from datetime import date
from pathlib import Path

import pytest

from app.core import route as route_module
from app.core.emissions import calculate_route_emission
from app.core.geography import distance_by_country, road_distance_by_country
from app.core.road import RoadRoute
from app.core.route import Leg, RouteAlternative, find_route_alternatives
from app.core.tolls import CLASS_BASED, estimate_tolls, load_toll_schemes

GEBZE, DUSSELDORF = (29.4306, 40.7889), (6.7735, 51.2277)

# OSRM's own answers for this corridor, captured once and bundled. What these tests are
# really about is where the road goes — which borders it crosses, and how much of the
# run is German — so a straight-line stub would test nothing. Recording the real
# geometry keeps the assertions meaningful and the suite hermetic at the same time.
CORRIDOR_ROADS = json.loads(
    (Path(__file__).parent / "fixtures" / "corridor_roads.json").read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def recorded_roads(monkeypatch):
    def replay(origin, destination):
        key = f"{origin[0]},{origin[1]}|{destination[0]},{destination[1]}"
        leg = CORRIDOR_ROADS.get(key)
        if leg is None:
            raise AssertionError(
                f"no recorded road leg for {key}; re-capture the corridor fixture"
            )
        return RoadRoute(
            distance_km=leg["distance_km"],
            duration_h=leg["duration_h"],
            ferry_km=leg["ferry_km"],
            geometry=tuple(map(tuple, leg["geometry"])),
        )

    monkeypatch.setattr(route_module, "road_route", replay)


@pytest.fixture
def corridor(recorded_roads):
    return find_route_alternatives(GEBZE, DUSSELDORF)


def test_a_track_splits_into_the_countries_it_crosses():
    """A line drawn north-west out of Thrace crosses exactly one border."""
    parts = distance_by_country([[28.5, 41.2], [26.5, 42.0], [24.5, 42.6]])

    assert {p.iso for p in parts} >= {"TR", "BG"}
    assert all(p.distance_km > 0 for p in parts)


def test_the_country_shares_add_up_to_the_leg_s_real_distance(corridor):
    """OSRM's polyline is simplified and about eight percent shorter than the road it
    stands for. Measuring the drawn line would lose two hundred kilometres of a
    Turkey-Germany run, and every toll computed from it with them."""
    route = next(r for r in corridor if r.is_all_road)
    road_km = sum(leg.distance_km for leg in route.legs if leg.mode == "road")

    split = sum(part.distance_km for part in road_distance_by_country(route))

    assert split == pytest.approx(road_km, rel=1e-6)


def test_the_corridor_runs_through_the_balkans(corridor):
    """A sanity check on the geometry rather than on the arithmetic: this route is not
    plausible unless it crosses these."""
    isos = {p.iso for p in road_distance_by_country(next(r for r in corridor if r.is_all_road))}

    assert {"TR", "BG", "RS", "HR", "SI", "AT", "DE"} <= isos


def test_distance_that_falls_in_no_country_is_admitted_not_reassigned():
    """A toll charged on a kilometre nobody can place is worse than an admitted gap."""
    parts = distance_by_country([[-30.0, 40.0], [-25.0, 40.0]])   # mid-Atlantic

    assert parts and parts[0].iso == ""


def test_only_germany_is_priced(corridor):
    """Austria and Czechia charge by CO2 class but publish no euro-per-tonne rate, so
    there is no carbon price to apply. Inventing one would be worse than the gap."""
    route = next(r for r in corridor if r.is_all_road)
    emission = calculate_route_emission(route, 24.0, scope="WTW", factor_set="glec")

    estimate = estimate_tolls(route, emission.co2_by_mode.get("road", 0.0))
    priced = {c.iso for c in estimate.priced_countries}

    assert priced == {"DE"}
    austria = next(c for c in estimate.countries if c.iso == "AT")
    assert not austria.priced and austria.cost_eur == 0
    assert "sınıfına göre" in austria.reason


def test_the_german_rate_is_the_published_one(corridor):
    """200 EUR a tonne, in force since 1 December 2023."""
    scheme = load_toll_schemes()["DE"]
    assert scheme.eur_per_tonne_co2 == 200.0
    assert scheme.in_force == date(2023, 12, 1)

    route = next(r for r in corridor if r.is_all_road)
    emission = calculate_route_emission(route, 24.0, scope="WTW", factor_set="glec")
    estimate = estimate_tolls(route, emission.co2_by_mode.get("road", 0.0))

    germany = next(c for c in estimate.countries if c.iso == "DE")
    assert germany.cost_eur == pytest.approx(germany.co2_kg / 1000 * 200.0)


def test_a_scheme_is_not_charged_before_it_came_into_force(corridor):
    route = next(r for r in corridor if r.is_all_road)
    emission = calculate_route_emission(route, 24.0, scope="WTW", factor_set="glec")

    before = estimate_tolls(route, emission.co2_by_mode.get("road", 0.0), on_date=date(2022, 1, 1))

    assert before.total_eur == 0
    germany = next(c for c in before.countries if c.iso == "DE")
    assert not germany.priced and "yururluge" in germany.reason


def test_avoiding_german_road_kilometres_shows_up_as_money(corridor):
    """The point of the module: the multimodal option loses on carbon under GLEC and
    wins on the German toll, and a carrier sees both."""
    road = next(r for r in corridor if r.is_all_road)
    multimodal = next(r for r in corridor if not r.is_all_road)

    def toll(route):
        emission = calculate_route_emission(route, 24.0, scope="WTW", factor_set="glec")
        return estimate_tolls(route, emission.co2_by_mode.get("road", 0.0)).total_eur

    assert toll(road) > toll(multimodal) * 5


def test_the_estimate_says_it_is_only_the_carbon_component(corridor):
    """Infrastructure, noise and air pollution are the larger part of a German toll and
    none of them follow carbon. Presenting this as "the toll" would overstate nothing
    and understate everything."""
    route = next(r for r in corridor if r.is_all_road)
    emission = calculate_route_emission(route, 24.0, scope="WTW", factor_set="glec")

    notes = " ".join(estimate_tolls(route, emission.co2_by_mode.get("road", 0.0)).notes)

    assert "CO2 bileşeni" in notes
    assert "Altyapı" in notes


def test_a_route_with_no_road_leg_prices_nothing():
    sea_only = RouteAlternative(
        legs=[Leg(mode="sea", from_name="a", to_name="b", distance_km=1000.0)], label="sea"
    )
    estimate = estimate_tolls(sea_only, 0.0)

    assert estimate.total_eur == 0
    assert estimate.countries == []


def test_class_based_schemes_are_named_rather_than_forgotten():
    assert "AT" in CLASS_BASED and "CZ" in CLASS_BASED


def test_a_ferry_inside_a_road_leg_is_not_charged_as_road():
    """OSRM's driving profile routes over ferries and reports the crossing inside the
    road leg's distance. A trailer on a ship is on nobody's road, and the carbon was
    already moved to the sea factor, so charging the toll over those kilometres would
    spread a road leg's carbon across a distance that includes the crossing."""
    route = RouteAlternative(
        legs=[
            Leg(
                mode="road", from_name="Munih", to_name="Hamburg",
                distance_km=1000.0, ferry_km=400.0, duration_h=14.0,
                geometry=((11.58, 48.14), (9.99, 53.55)),
            )
        ],
        label="ferry-bearing",
    )
    emission = calculate_route_emission(route, 24.0, scope="WTW", factor_set="glec")

    estimate = estimate_tolls(route, emission.co2_by_mode.get("road", 0.0))
    charged_km = sum(c.distance_km for c in estimate.countries)

    assert charged_km == pytest.approx(600.0, rel=1e-6), (
        f"the toll charges over {charged_km:.0f} km of a leg with only 600 km of road"
    )
    # And the carbon it spreads is the road carbon, not the ferry's.
    assert estimate.priced_co2_kg + estimate.unpriced_co2_kg == pytest.approx(
        emission.co2_by_mode["road"]
    )
    assert emission.co2_by_mode["sea"] > 0, "the crossing should be priced as sea"
