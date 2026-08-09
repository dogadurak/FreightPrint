"""The playable journey. What matters is that it stays honest while it moves.

The animation is persuasive in a way a table is not, so the things it must not do are
the things a viewer would never catch: emit carbon while parked, skip the waiting, or
pair a leg's figures with another leg's clock.
"""

import pytest

from app.core.emissions import calculate_route_emission
from app.core.playback import PlaybackMismatch, build_playback
from app.core.reefer import calculate_reefer
from app.core.route import Leg, RouteAlternative
from app.core.schedule import ScheduleStep, Timeline, build_timeline

TRACK = ((29.0, 40.0), (29.5, 41.0), (30.0, 42.0))


def _route(*legs):
    return RouteAlternative(legs=list(legs), label="test")


def _road(distance_km=100.0, ferry_km=0.0, geometry=TRACK):
    return Leg(
        mode="road", from_name="a", to_name="b", distance_km=distance_km,
        ferry_km=ferry_km, geometry=geometry,
    )


def _priced(route, **kwargs):
    return calculate_route_emission(route, 24.0, scope="WTW", factor_set="glec", **kwargs)


def test_the_clock_and_the_carbon_come_from_the_same_legs():
    route = _route(_road(), _road(200.0))
    shipment = _priced(route)
    timeline = build_timeline(route)

    playback = build_playback(route, shipment, timeline)

    assert playback.total_co2_kg == pytest.approx(shipment.total_co2_kg)
    assert playback.total_hours == pytest.approx(timeline.total_hours)


def test_a_ferry_split_does_not_shift_every_leg_by_one():
    """A road leg carrying a ferry prices as two legs but keeps one slot on the clock.
    Matching them up by mode is what once gave a 40 km ferry a 2,500 km leg's duration."""
    route = _route(_road(500.0, ferry_km=40.0), _road(200.0))
    shipment = _priced(route)

    playback = build_playback(route, shipment, build_timeline(route))

    assert playback.total_co2_kg == pytest.approx(shipment.total_co2_kg)
    transit = [s for s in playback.segments if s.kind == "transit"]
    assert len(transit) == len(route.legs)


def test_a_changed_expansion_rule_fails_loudly():
    """The pairing is by construction, and the construction is checked. Silently
    mis-pairing every leg is the failure this guards."""
    route = _route(_road())
    shipment = _priced(route)
    shipment.legs.append(shipment.legs[0])  # as if the expansion had split something

    with pytest.raises(PlaybackMismatch, match="expansion rule"):
        build_playback(route, shipment, build_timeline(route))


def test_a_changed_timeline_shape_fails_loudly():
    route = _route(_road(), _road())
    shipment = _priced(route)
    timeline = build_timeline(route)
    timeline.steps = [s for s in timeline.steps if s.kind != "transit"]

    with pytest.raises(PlaybackMismatch, match="transit steps"):
        build_playback(route, shipment, timeline)


def test_nothing_burns_fuel_while_parked():
    """A truck at a terminal is not emitting. Spreading the total evenly over elapsed
    time would put carbon on the clock during an eighteen-hour handling stop."""
    route = _route(
        Leg(mode="road", from_name="a", to_name="pendik", distance_km=50.0,
            from_id="pendik", geometry=TRACK),
        Leg(mode="sea", from_name="pendik", to_name="trieste", distance_km=2500.0,
            from_id="pendik", to_id="trieste", geometry=TRACK),
    )
    playback = build_playback(route, _priced(route), build_timeline(route))

    stationary = [s for s in playback.segments if s.kind != "transit"]
    assert stationary, "this route should have a handover"
    assert all(s.co2_kg == 0 for s in stationary)


def test_refrigeration_keeps_running_when_the_wheels_stop():
    """The reason the reefer figure is charged by the hour: the box is still plugged in
    while it waits for a sailing, and a viewer watching the counter should see that."""
    route = _route(
        Leg(mode="road", from_name="a", to_name="pendik", distance_km=50.0,
            from_id="pendik", geometry=TRACK),
        Leg(mode="sea", from_name="pendik", to_name="trieste", distance_km=2500.0,
            from_id="pendik", to_id="trieste", geometry=TRACK),
    )
    timeline = build_timeline(route)
    playback = build_playback(route, _priced(route), timeline, calculate_reefer(timeline, 24.0))

    stationary = [s for s in playback.segments if s.kind != "transit"]
    assert all(s.reefer_co2_kg > 0 for s in stationary)
    assert playback.total_reefer_co2_kg > 0


def test_the_waiting_is_shown_rather_than_skipped():
    route = _route(
        Leg(mode="road", from_name="a", to_name="pendik", distance_km=50.0,
            from_id="pendik", geometry=TRACK),
        Leg(mode="sea", from_name="pendik", to_name="trieste", distance_km=2500.0,
            from_id="pendik", to_id="trieste", geometry=TRACK),
    )
    playback = build_playback(route, _priced(route), build_timeline(route))

    assert playback.stationary_hours > 0
    assert playback.stationary_hours < playback.total_hours


def test_a_stationary_segment_holds_its_position():
    """It has to be somewhere, and that somewhere is where the last leg ended — not
    the origin, and not nowhere."""
    route = _route(
        Leg(mode="road", from_name="a", to_name="pendik", distance_km=50.0,
            from_id="pendik", geometry=TRACK),
        Leg(mode="sea", from_name="pendik", to_name="trieste", distance_km=2500.0,
            from_id="pendik", to_id="trieste", geometry=TRACK),
    )
    playback = build_playback(route, _priced(route), build_timeline(route))

    for segment in playback.segments:
        if segment.kind != "transit":
            assert len(segment.geometry) == 1
            assert segment.geometry[0] == list(TRACK[-1])


def test_a_leg_with_no_computed_track_still_has_somewhere_to_travel():
    """Rail carries no geometry — the distances come from a reference table. Without a
    schematic line the marker would have nowhere to go between two terminals."""
    route = _route(
        Leg(mode="rail", from_name="Trieste", to_name="Koln", distance_km=950.0,
            from_id="trieste", to_id="koln"),
    )
    playback = build_playback(route, _priced(route), build_timeline(route))

    rail = next(s for s in playback.segments if s.kind == "transit")
    assert len(rail.geometry) == 2
    assert rail.track_is_indicative, "a schematic line must not pass as a surveyed one"
    # The departure wait comes first here; it must still be somewhere at hour zero.
    assert playback.segments[0].geometry, "the journey starts nowhere"


def test_segments_run_back_to_back_without_gaps():
    """A viewer scrubbing the clock must land in a segment at every hour."""
    route = _route(
        Leg(mode="road", from_name="a", to_name="pendik", distance_km=50.0,
            from_id="pendik", geometry=TRACK),
        Leg(mode="sea", from_name="pendik", to_name="trieste", distance_km=2500.0,
            from_id="pendik", to_id="trieste", geometry=TRACK),
    )
    playback = build_playback(route, _priced(route), build_timeline(route))

    assert playback.segments[0].start_h == 0
    for earlier, later in zip(playback.segments, playback.segments[1:]):
        assert earlier.end_h == pytest.approx(later.start_h)
    assert playback.segments[-1].end_h == pytest.approx(playback.total_hours)
