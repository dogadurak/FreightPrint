"""Validation against the 34 real shipments in the customer reports.

The dataset holds real customer data and is kept out of the repository, so these tests
skip when it is absent rather than failing a clean checkout.
"""

import pytest

from app.core.geocode import GeocodingError, looks_like_postal_code, normalise_country
from app.core.validation import (
    DEFAULT_DATASET,
    EmissionComparison,
    ShipmentRecord,
    compare_all_road_baseline,
    compare_emissions,
    load_validation_dataset,
    mean_absolute_percentage_error,
)

EXPECTED_ROWS = 34
REFERENCE_ROAD_FACTOR = 0.121


@pytest.fixture(scope="module")
def records():
    if not DEFAULT_DATASET.exists():
        pytest.skip("validation dataset not present locally")
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


def test_unknown_country_name_is_refused_rather_than_upper_cased():
    """Upper-casing an unlisted name yields a bogus country code and a silent wrong hit."""
    with pytest.raises(GeocodingError):
        normalise_country("Netherlands")


@pytest.mark.parametrize("place", ["34940", "SW1A 2AA", "00-001", "20121"])
def test_postal_codes_are_detected(place):
    assert looks_like_postal_code(place)


@pytest.mark.parametrize("place", ["Trieste", "Duisburg", "Ostrava", "Wels"])
def test_city_names_are_not_treated_as_postal_codes(place):
    assert not looks_like_postal_code(place)


def test_mape_counts_exact_matches_instead_of_dropping_them():
    """Filtering zeros would average only the failures and flatter the result."""
    record = _synthetic_record()
    exact = EmissionComparison(record, reported_co2_kg=100.0, recalculated_co2_kg=100.0)
    off_by_ten = EmissionComparison(record, reported_co2_kg=100.0, recalculated_co2_kg=110.0)

    assert mean_absolute_percentage_error([exact, off_by_ten]) == pytest.approx(0.05)

    with pytest.raises(ValueError):
        mean_absolute_percentage_error([])


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


def _synthetic_record(**overrides) -> ShipmentRecord:
    defaults = dict(
        source_report="synthetic",
        origin_country="TR",
        origin_city="a",
        destination_country="DE",
        destination_city="b",
        service_route="",
        tonnage=10.0,
        pre_carriage_road_km=100.0,
        sea_km=1000.0,
        rail_km=0.0,
        post_carriage_road_km=0.0,
        all_road_km=2000.0,
        reported_total_co2_kg=180.5,
        reported_all_road_co2_kg=0.0,
        reported_saving_co2_kg=0.0,
    )
    return ShipmentRecord(**(defaults | overrides))


def test_implied_road_km_inverts_the_emission_formula():
    """Pin the derivation itself: halving it must fail here, not pass silently."""
    # 50 km of road (50 * 10 * 0.121 = 60.5) plus 1000 km of sea (1000 * 10 * 0.012 = 120).
    record = _synthetic_record()
    comparison = EmissionComparison(
        record=record,
        reported_co2_kg=record.reported_total_co2_kg,
        recalculated_co2_kg=100 * 10 * REFERENCE_ROAD_FACTOR + 1000 * 10 * 0.012,
    )

    assert comparison.implied_road_km(REFERENCE_ROAD_FACTOR) == pytest.approx(50.0)


def test_the_three_outliers_report_less_co2_than_their_own_distances_imply(records):
    """What the data shows: each reports a total below what its own km columns produce.

    Attributing that gap to the road legs is an interpretation, not something these
    numbers can prove on their own, so this asserts only the observable shortfall.
    """
    outliers = [c for c in compare_emissions(records) if not c.matches]

    assert len(outliers) == 3
    for comparison in outliers:
        assert comparison.difference_kg > 0
        assert 0 < comparison.implied_road_km(REFERENCE_ROAD_FACTOR) < comparison.record.road_km


def test_matching_rows_reproduce_almost_exactly(records):
    """A near-zero error on the matching rows is what proves the arithmetic itself."""
    matching = [c for c in compare_emissions(records) if c.matches]

    assert mean_absolute_percentage_error(matching) < 1e-9
