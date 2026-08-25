"""Holding this engine's assumptions against somebody else's observations.

The point of this module is that it can disagree. A benchmark built to produce agreement
would tell you nothing, so most of these tests are about the comparison staying honest
when it does not match — and about never quietly substituting a number for a missing one.
"""

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
