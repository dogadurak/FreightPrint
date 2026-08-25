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
