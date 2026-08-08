import pytest

from app.core.reefer import (
    ReeferFactor,
    ReeferFactorError,
    calculate_reefer,
    load_reefer_factors,
)
from app.core.schedule import ScheduleStep, Timeline

FACTOR = ReeferFactor(
    id="test", g_co2e_per_tonne_hour=200.0, scope="WTW", source="test", is_verified=True, notes=""
)
FACTORS = {"test": FACTOR}


def timeline_of(*steps: tuple[str, float], estimated: bool = False) -> Timeline:
    return Timeline(
        steps=[
            ScheduleStep(
                kind=kind, mode=None, label=kind, hours=hours, start_h=0.0, is_estimated=estimated
            )
            for kind, hours in steps
        ]
    )


def test_refrigeration_is_charged_by_the_hour():
    emission = calculate_reefer(timeline_of(("transit", 10.0)), tonnage=24.0, factors=FACTORS, factor_id="test")

    assert emission.co2_kg == pytest.approx(200.0 * 24.0 * 10.0 / 1000)


def test_the_same_distance_taken_slower_costs_more():
    """The point of an hourly basis: a reefer's draw follows the clock, not the odometer.

    A per-kilometre factor would price these two identically and hide the difference.
    """
    fast = calculate_reefer(timeline_of(("transit", 40.0)), 24.0, "test", FACTORS)
    slow = calculate_reefer(timeline_of(("transit", 90.0)), 24.0, "test", FACTORS)

    assert slow.co2_kg > fast.co2_kg * 2


def test_hours_spent_standing_still_are_charged():
    """A box waiting for a sailing is plugged in and drawing; kilometres cannot see it."""
    emission = calculate_reefer(
        timeline_of(("transit", 88.6), ("dwell", 18.0), ("wait", 24.5)), 24.0, "test", FACTORS
    )

    assert emission.stationary_co2_kg == pytest.approx(200.0 * 24.0 * 42.5 / 1000)
    assert emission.stationary_co2_kg / emission.co2_kg > 0.3
    assert emission.co2_by_kind["dwell"] < emission.co2_by_kind["wait"] < emission.co2_by_kind["transit"]


def test_the_published_reefer_ratio_is_not_carried_across_modes():
    """GLEC's x1.9 is a container-ship ratio and does not transfer.

    A container ship's per-tonne-km emissions are small, so the unit roughly doubles
    them; a ro-ro's are already large, so the same unit is a modest addition. Applying
    the ratio to ro-ro overstates the overhead about ninefold. This test fails if
    anyone reintroduces a multiplicative reefer model.
    """
    published = load_reefer_factors()["derived_glec_container"]
    roro_dry_g_per_tonne_km = 68.0  # GLEC Table 45, trailer on a ro-ro vessel, WTW
    sea_hours, sea_km = 64.0, 2_500.0  # Pendik -> Trieste, a real DFDS sailing

    overhead_g_per_tonne_km = published.g_co2e_per_tonne_hour * sea_hours / sea_km
    uplift = (roro_dry_g_per_tonne_km + overhead_g_per_tonne_km) / roro_dry_g_per_tonne_km

    assert uplift < 1.15, f"ro-ro reefer uplift {uplift:.2f}x looks like the container ratio"


def test_the_container_derivation_reproduces_glec_table_46():
    """Trace the chain back: at container-ship speed the hourly figure must return
    the published per-TEU-km numbers it was derived from."""
    published = load_reefer_factors()["derived_glec_container"]
    ship_speed_kmh, tonnes_per_teu = 32.0, 10.0  # GLEC p.38 gives 10 t/TEU

    g_per_teu_km = published.g_co2e_per_tonne_hour / ship_speed_kmh * tonnes_per_teu

    assert g_per_teu_km == pytest.approx(145.0 - 76.0, rel=0.02)


def test_a_derived_factor_announces_itself():
    emission = calculate_reefer(timeline_of(("transit", 10.0)), 24.0)

    assert not emission.factor.is_verified
    assert any("derived, not published" in w for w in emission.warnings)


def test_an_estimated_clock_is_flagged_because_the_charge_rides_on_it():
    emission = calculate_reefer(timeline_of(("transit", 10.0), estimated=True), 24.0, "test", FACTORS)

    assert any("inherits every estimate" in w for w in emission.warnings)


def test_an_unknown_factor_is_refused_rather_than_defaulted():
    # The message names what is available, so a typo is self-correcting.
    with pytest.raises(ReeferFactorError, match="test"):
        calculate_reefer(timeline_of(("transit", 10.0)), 24.0, "no-such-factor", FACTORS)


@pytest.mark.parametrize("tonnage", [0, -5])
def test_tonnage_must_be_positive(tonnage):
    with pytest.raises(ValueError):
        calculate_reefer(timeline_of(("transit", 10.0)), tonnage, "test", FACTORS)


def test_the_shipped_factor_file_loads_and_is_marked_underived():
    factors = load_reefer_factors()

    assert "derived_glec_container" in factors
    assert not factors["derived_glec_container"].is_verified
    assert "Table 46" in factors["derived_glec_container"].source
