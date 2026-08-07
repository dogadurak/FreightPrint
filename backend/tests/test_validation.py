"""Validation against the 34 real shipments in the customer reports.

The dataset holds real customer data and is kept out of the repository, so these tests
skip when it is absent rather than failing a clean checkout.
"""

import pytest

from app.core.geocode import normalise_country
from app.core.validation import (
    DEFAULT_DATASET,
    compare_all_road_baseline,
    compare_emissions,
    load_validation_dataset,
    mean_absolute_percentage_error,
)

pytestmark = pytest.mark.skipif(
    not DEFAULT_DATASET.exists(), reason="validation dataset not present locally"
)

EXPECTED_ROWS = 34
REFERENCE_ROAD_FACTOR = 0.121


@pytest.fixture(scope="module")
def records():
    return load_validation_dataset()


def test_dataset_parses_completely(records):
    assert len(records) == EXPECTED_ROWS
    assert all(record.tonnage > 0 for record in records)
    assert all(record.all_road_km > 0 for record in records)


def test_country_spellings_are_normalised(records):
    """The source mixes ISO codes, English names and Turkish spellings."""
    countries = {record.origin_country for record in records} | {
        record.destination_country for record in records
    }

    assert "TURKEY" not in countries and "Turkey" not in countries
    assert "TR" in countries
    assert all(len(country) == 2 for country in countries), sorted(countries)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("TURKEY", "TR"), ("Turkey", "TR"), ("Poland", "PL"), ("Bosna", "BA"), ("GR", "GR")],
)
def test_country_aliases_resolve(raw, expected):
    assert normalise_country(raw) == expected


def test_all_road_baseline_matches_every_reported_row(records):
    """Every row reports an all-road figure, and our formula must reproduce all of them."""
    comparisons = compare_all_road_baseline(records)

    assert len(comparisons) == EXPECTED_ROWS
    assert all(comparison.matches for comparison in comparisons)


def test_multimodal_emissions_match_within_one_percent(records):
    """The brief's Faz 3 target: same distances and factors must reproduce the report."""
    comparisons = compare_emissions(records)
    matching = [comparison for comparison in comparisons if comparison.matches]

    assert len(comparisons) == 22
    assert len(matching) == 19


def test_the_three_outliers_differ_only_in_road_distance(records):
    """Sea and rail reproduce exactly, so the deviation sits in the road legs alone."""
    outliers = [c for c in compare_emissions(records) if not c.matches]

    assert len(outliers) == 3
    for comparison in outliers:
        implied_km = comparison.implied_road_km(REFERENCE_ROAD_FACTOR)
        assert 0 < implied_km < comparison.record.road_km
        assert comparison.difference_kg > 0


def test_matching_rows_reproduce_almost_exactly(records):
    """A near-zero error on the matching rows is what proves the arithmetic itself."""
    matching = [c for c in compare_emissions(records) if c.matches]

    assert mean_absolute_percentage_error(matching) < 1e-9
