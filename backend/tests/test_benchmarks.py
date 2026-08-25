"""Holding this engine's assumptions against somebody else's observations.

The point of this module is that it can disagree. A benchmark built to produce agreement
would tell you nothing, so most of these tests are about the comparison staying honest
when it does not match — and about never quietly substituting a number for a missing one.
"""

import json
from pathlib import Path

import pytest

from app.core.benchmarks import (
    DEFAULT_REFERENCE_GEO,
    BenchmarkUnavailable,
    check_empty_running_assumptions,
    load_empty_running,
    observed,
)


def test_the_observations_are_a_downloaded_survey_not_a_typed_table():
    """Every row is a country-year Eurostat published. If this file ever grows a value
    somebody entered by hand, the whole exercise stops meaning anything."""
    rows = load_empty_running()

    assert len(rows) > 50, "the survey should cover many country-years"
    assert {row.year for row in rows} >= {2023, 2024}
    for row in rows:
        assert 0 < row.empty_share < 1, f"{row.geo} {row.year}: implausible share"
        assert row.empty_mio_vkm < row.total_mio_vkm


def test_international_haulage_runs_fuller_than_national():
    """Long-haul is planned and local distribution is not, so the international rate is
    consistently lower. It is the right comparison for a Turkey-to-Germany corridor, and
    using the overall rate would judge it against the wrong traffic."""
    eu = observed(DEFAULT_REFERENCE_GEO)

    assert eu.intl_empty_share is not None
    assert eu.intl_empty_share < eu.empty_share
    assert eu.relevant_share == eu.intl_empty_share
    assert eu.basis == "uluslararası"


def test_a_country_without_the_split_falls_back_to_its_overall_rate():
    """Reported rather than invented: some countries publish a total and no breakdown."""
    without = [row for row in load_empty_running() if row.intl_empty_share is None]
    if not without:
        pytest.skip("every country in this download reports the international split")

    row = without[0]
    assert row.relevant_share == row.empty_share
    assert row.basis == "toplam"


def test_the_most_recent_year_is_the_default():
    rows = [row for row in load_empty_running() if row.geo == DEFAULT_REFERENCE_GEO]

    assert observed(DEFAULT_REFERENCE_GEO).year == max(row.year for row in rows)


def test_turkey_has_no_observation_and_that_is_said_rather_than_substituted():
    """The corridor's Turkish end genuinely has no survey behind it. "We have no
    observation for Turkey" and "Turkey looks like the EU" are different statements and
    only one of them is true."""
    with pytest.raises(BenchmarkUnavailable, match="TR"):
        observed("TR")


def test_a_year_that_was_not_downloaded_says_which_ones_were():
    with pytest.raises(BenchmarkUnavailable, match="1999"):
        observed(DEFAULT_REFERENCE_GEO, year=1999)


def test_the_glec_road_factor_assumes_more_empty_running_than_europe_reports():
    """The finding this module exists to produce, and it is a disagreement.

    GLEC builds 30% empty return into every road figure this engine emits. Eurostat
    observes about 12% on international EU haulage. That is not GLEC being wrong — its
    figure describes the fleet it was measured on, which includes national work where
    empty running is genuinely higher — but it does mean the factor's basis is not this
    corridor's traffic, and nobody here had ever checked.
    """
    from app.core.emissions import find_factor, load_emission_factors

    glec = find_factor(load_emission_factors(), "road", scope="WTW", factor_set="glec")
    report = check_empty_running_assumptions(factor_empty_share=glec.basis_empty_share)

    comparison = report.comparisons[0]
    assert comparison.ours == pytest.approx(0.30)
    assert comparison.observed_value < 0.20
    assert comparison.verdict == "above"
    assert comparison.ratio > 2


def test_a_comparison_names_the_survey_it_used():
    """A benchmark without its source is an assertion."""
    report = check_empty_running_assumptions(factor_empty_share=0.30)

    assert "Eurostat" in report.comparisons[0].observed_source
    assert str(observed().year) in report.comparisons[0].observed_source


def test_the_verdict_is_three_valued_rather_than_pass_fail():
    """These assumptions are not meant to equal the observation — one is a published
    fleet average, the other an upper bound by construction. What matters is which side
    of it you are on."""
    reference = observed().relevant_share

    assert check_empty_running_assumptions(factor_empty_share=reference * 3).comparisons[0].verdict == "above"
    assert check_empty_running_assumptions(factor_empty_share=reference / 3).comparisons[0].verdict == "below"
    assert check_empty_running_assumptions(factor_empty_share=reference).comparisons[0].verdict == "near"


def test_both_limits_of_the_comparison_travel_with_it():
    report = check_empty_running_assumptions(factor_empty_share=0.30)
    notes = " ".join(report.notes)

    assert "indirilmiştir, üretilmemiştir" in notes
    assert "Türkiye" in notes, "the missing half of the corridor is not admitted"


def test_nothing_asked_for_means_nothing_compared():
    """No silent defaults: a report with no comparisons is empty, not invented."""
    assert check_empty_running_assumptions().comparisons == []


def test_the_worst_gap_is_the_one_furthest_from_the_observation():
    report = check_empty_running_assumptions(
        factor_empty_share=0.30, modelled_empty_share=1.0
    )

    assert report.worst.what.startswith("Boş dönüş modelinin")


# ── weighting the survey by the route's own kilometres ────────────────────────

CORRIDOR_ROADS = json.loads(
    (Path(__file__).parent / "fixtures" / "corridor_roads.json").read_text(encoding="utf-8")
)


@pytest.fixture
def corridor(monkeypatch):
    """The pilot corridor on OSRM's own recorded geometry, so the country split is real."""
    from app.core import route as route_module
    from app.core.network import haversine_km
    from app.core.road import RoadRoute
    from app.core.route import find_route_alternatives

    def replay(origin, destination):
        leg = CORRIDOR_ROADS.get(f"{origin[0]},{origin[1]}|{destination[0]},{destination[1]}")
        if leg is None:
            km = haversine_km(origin, destination) * 1.3
            return RoadRoute(distance_km=km, duration_h=km / 70, geometry=(origin, destination))
        return RoadRoute(
            distance_km=leg["distance_km"], duration_h=leg["duration_h"],
            ferry_km=leg["ferry_km"], geometry=tuple(map(tuple, leg["geometry"])),
        )

    monkeypatch.setattr(route_module, "road_route", replay)
    routes = find_route_alternatives((29.4306, 40.7889), (6.7735, 51.2277))
    return next(route for route in routes if route.is_all_road)


def test_the_rate_is_weighted_by_where_the_route_actually_runs(corridor):
    """The EU-27 average is dominated by the countries that haul the most, not by the
    ones this freight crosses. This corridor runs through Austria and Germany, which are
    among the emptiest, so its own rate sits well above the EU figure."""
    from app.core.benchmarks import corridor_empty_running

    result = corridor_empty_running(corridor)

    assert result.rate > observed().relevant_share, "weighting changed nothing"
    assert min(result.per_country.values()) <= result.rate <= max(result.per_country.values())


def test_the_countries_with_no_survey_are_named_and_measured(corridor):
    """Serbia and Turkey do not report. Naming them is not enough — how much of the
    journey they carry is what decides whether the rate means anything."""
    from app.core.benchmarks import corridor_empty_running

    result = corridor_empty_running(corridor)

    assert set(result.missing) >= {"RS", "TR"}
    assert sum(result.missing.values()) > 700, "the unobserved share looks too small"
    assert result.covered_km + sum(result.missing.values()) == pytest.approx(result.total_km)


def test_the_rate_never_travels_without_its_coverage(corridor):
    """A weighted mean quietly taken over 70% of a journey, presented as the journey's
    rate, is the kind of number that survives right up until somebody checks it."""
    from app.core.benchmarks import corridor_empty_running

    result = corridor_empty_running(corridor)

    assert 0.6 < result.coverage < 0.8
    assert result.is_representative


def test_a_route_through_nothing_observed_is_not_representative():
    """The threshold has to be able to fail, or it is decoration."""
    from app.core.benchmarks import CorridorEmptyRunning

    thin = CorridorEmptyRunning(rate=0.2, covered_km=100, total_km=1000)

    assert not thin.is_representative
    assert thin.coverage == pytest.approx(0.1)


def test_distance_nobody_could_place_is_not_counted_as_observed(corridor):
    """Kilometres that fall in no country polygon are a gap like any other, and folding
    them into the covered share would overstate how much of the route was seen."""
    from app.core.benchmarks import corridor_empty_running

    result = corridor_empty_running(corridor)

    assert "yerleştirilemedi" in result.missing


# ── the sea factor against the ships that actually sail ───────────────────────

def test_the_mrv_derivation_is_real_ships_not_a_typed_table():
    """684 verified ship-years, downloaded from EMSA and derived by script. If this file
    ever grows a hand-entered row the comparison stops meaning anything."""
    from app.core.benchmarks import load_roro_intensity

    ships = load_roro_intensity()

    assert len(ships) > 600
    assert {s.reporting_period for s in ships} >= {2023, 2024, 2025}
    assert all(s.kg_co2_per_tonne_km > 0 for s in ships)
    assert {s.ship_type for s in ships} <= {
        "Ro-ro ship", "Ro-pax ship", "Container/ro-ro cargo ship"
    }, "a vessel that is not a ro-ro got into the comparison"


def test_glec_ro_ro_sits_inside_the_fleet_it_claims_to_describe():
    """The result this exercise exists to produce, and it is a pass.

    A fleet average is not meant to equal any one ship; it is meant to be a fair middle.
    GLEC's 0.063 lands inside the interquartile range of the verified EU ro-ro fleet in
    every period published — which is the strongest thing that could honestly be said
    about the number this project's headline rests on.
    """
    from app.core.benchmarks import compare_sea_factor

    for year in (2023, 2024, 2025):
        result = compare_sea_factor(0.063, year=year)
        assert result.verdict == "within", f"{year}: GLEC fell outside the observed fleet"
        assert 0.4 < result.share_below < 0.7, "a fair middle should split the fleet"


def test_the_factor_drifts_as_the_fleet_improves():
    """The observed median fell across the three periods, so a static factor sits
    further above reality each year. That is a finding about the factor's shelf life,
    not about this engine."""
    from app.core.benchmarks import compare_sea_factor

    medians = [compare_sea_factor(0.063, year=y).median for y in (2023, 2024, 2025)]

    assert medians == sorted(medians, reverse=True), "the fleet is not improving"
    assert medians[0] / medians[-1] > 1.1, "the drift is too small to be worth reporting"


def test_the_spread_is_reported_because_no_single_factor_can_express_it():
    """The most important number here and the one an average hides: the middle half of
    the fleet spans a factor of about 2.7, so *any* fleet average is a poor description
    of the particular ship carrying the load."""
    from app.core.benchmarks import compare_sea_factor

    result = compare_sea_factor(0.063)

    assert result.spread > 2, "the fleet looks implausibly uniform"
    assert result.q1 < result.median < result.q3


def test_the_comparison_is_made_on_one_scope_and_says_which():
    """MRV measures fuel burned, so it is tank-to-wake. Holding GLEC's well-to-wake
    0.068 against it would charge the observation for fuel production it never saw."""
    from app.core.benchmarks import MRV_SCOPE, compare_sea_factor

    assert MRV_SCOPE == "TTW"
    notes = " ".join(compare_sea_factor(0.063).notes)
    assert "WTW" in notes and "yakit uretimini olcmez" in notes


def test_the_median_is_used_because_a_few_ships_break_the_mean():
    """Some ships report almost no transport work and come back with intensities two
    orders of magnitude high. One of them can carry a mean on its own."""
    from app.core.benchmarks import compare_sea_factor, load_roro_intensity
    import statistics

    fleet = [s.kg_co2_per_tonne_km for s in load_roro_intensity() if s.reporting_period == 2024]
    result = compare_sea_factor(0.063, year=2024)

    assert result.median == pytest.approx(statistics.median(fleet))
    assert statistics.mean(fleet) > result.median * 1.5, "the outliers are gone; drop this test"


def test_a_period_that_was_not_imported_says_which_ones_were():
    from app.core.benchmarks import compare_sea_factor

    with pytest.raises(BenchmarkUnavailable, match="2010"):
        compare_sea_factor(0.063, year=2010)


def test_the_observed_fleet_contains_no_ro_pax_and_that_is_stated():
    """A limit of the publication, not of this importer.

    The importer accepts ro-pax and container/ro-ro vessels. None survive: MRV measures a
    ro-pax's transport work in passengers, so the tonne-mile column comes back empty for
    the entire class. The observation is therefore pure ro-ro cargo ships, and saying so
    is the difference between a fleet that was filtered and one that was never reported.
    """
    from app.core.benchmarks import compare_sea_factor

    result = compare_sea_factor(0.063)

    assert "Ro-pax ship" not in result.ship_types
    assert sum(result.ship_types.values()) == result.ships
    assert any("ro-pax" in note.lower() for note in result.notes)


def test_the_accompanied_factor_is_compared_but_not_called_a_test():
    """Its traffic — tractor and driver travelling with the load — largely sails ro-pax,
    which this fleet does not contain. The comparison is still the nearest observation
    there is, so it is reported, flagged rather than suppressed."""
    from app.core.benchmarks import compare_sea_factor

    accompanied = compare_sea_factor(0.093, vehicle_type="roro_truck_trailer")
    trailer = compare_sea_factor(0.063, vehicle_type="roro_trailer")

    assert accompanied.is_comparable is False
    assert trailer.is_comparable is True
    assert any("DIKKAT" in note for note in accompanied.notes)
    assert accompanied.ships == trailer.ships, "the flag must not change the fleet"


def test_the_reference_basis_is_below_every_ship_in_the_verified_fleet():
    """The engine's default sea factor comes from the validation dataset's own carbon
    report, and the factor table has always carried a note that it looked closer to a
    container ship than a ro-ro. MRV turns that note into an observation."""
    from app.core.benchmarks import compare_sea_factor
    from app.core.emissions import find_factor, load_emission_factors

    reference = find_factor(load_emission_factors(), "sea", scope="TTW", factor_set="reference")
    result = compare_sea_factor(reference.value, reference.source)

    assert result.verdict == "below"
    assert result.share_below == 0.0
    assert result.ratio < 0.3, "roughly a quarter of the observed median"
