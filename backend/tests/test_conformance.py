"""The ISO 14083 self-assessment. Its worth is in the gaps it names.

A conformance tool that passes everything it happens to implement is worse than none,
because it launders a figure. So most of these tests are about the checks that must
never quietly start passing.
"""

import pytest

from app.core.conformance import DATA_QUALITY, assess


def test_a_tank_to_wheel_figure_is_not_reportable():
    """ISO 14083 quantifies well-to-wheel. TTW omits fuel production entirely, which for
    electricity is the whole of it."""
    result = assess("glec", "TTW")

    assert result.verdict == "not-reportable"
    scope = next(c for c in result.checks if c.id == "scope")
    assert scope.status == "missing" and scope.is_blocking


def test_well_to_wheel_clears_the_blocking_check():
    result = assess("glec", "WTW")

    assert result.verdict != "not-reportable"
    assert next(c for c in result.checks if c.id == "scope").status == "met"


def test_hub_emissions_are_reported_absent_rather_than_scoped_out():
    """Nothing here computes terminal or warehouse energy. Dropping the requirement
    because we do not meet it is how a tool starts lying."""
    for scope in ("TTW", "WTW"):
        hub = next(c for c in assess("glec", scope).checks if c.id == "hub_emissions")
        assert hub.status == "missing"
        assert hub.gap, "a missing requirement has to say what would close it"


def test_the_absence_of_primary_data_is_stated():
    """Every factor here is a published default. The standard ranks operator-measured
    fuel above all of them, and no amount of engineering on this side changes that."""
    primary = next(c for c in assess("glec", "WTW").checks if c.id == "primary_data")

    assert primary.status == "missing"
    assert "taşıyıcı" in primary.gap.lower()


def test_data_quality_can_never_reach_the_measured_tiers():
    """Four and five both require the operator's own measurements. A score that could
    reach them from published defaults would be meaningless."""
    for factor_set, scope in (("glec", "WTW"), ("reference", "TTW")):
        assert assess(factor_set, scope).data_quality <= 3
    assert set(DATA_QUALITY) == {1, 2, 3, 4, 5}


def test_only_the_factors_a_report_would_use_are_judged():
    """A report priced on diesel must not be marked down because the same set also
    carries derived HVO rows it never touched."""
    diesel = assess("glec", "WTW")
    hvo = assess("glec", "WTW", road_fuel_type="hvo_uco")

    assert diesel.data_quality == 3
    assert hvo.data_quality < diesel.data_quality, "a derived factor must cost quality"


def test_a_factor_set_without_empty_running_is_blocked():
    """ISO requires empty running in the basis. The comparison set does not carry it."""
    empty = next(c for c in assess("reference", "TTW").checks if c.id == "empty_running")

    assert empty.status == "missing" and empty.is_blocking


def test_every_check_names_the_clause_it_comes_from():
    """So a reader can go and disagree with us."""
    for check in assess("glec", "WTW").checks:
        assert check.clause.startswith("ISO 14083")
        assert check.requirement and check.evidence
        if check.status != "met":
            assert check.gap or check.status == "partial"


def test_the_assessment_says_it_is_not_a_certification():
    result = assess("glec", "WTW")

    assert any("belgelendirme değildir" in note for note in result.notes)


def test_an_unpriceable_basis_is_refused_rather_than_scored():
    with pytest.raises(ValueError, match="no factors"):
        assess("reference", "WTW")
