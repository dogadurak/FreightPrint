import pytest

from app.core.risk import assess_route, load_risk_zones
from app.core.route import Leg, RouteAlternative

# Inside the southern Red Sea zone (32-52E, 12-20N) only: east of 43E it would also
# enter the Gulf of Aden zone, which is what the overlap test below uses.
THROUGH_RED_SEA = ((34.0, 18.0), (42.0, 18.0))
# Crosses the corner the southern Red Sea and Gulf of Aden zones share.
THROUGH_OVERLAP = ((44.0, 14.0), (50.0, 14.0))
# The same latitude but west of the zone, in the Atlantic.
CLEAR_OF_ZONES = ((-20.0, 14.0), (-10.0, 14.0))


def _sea_leg(geometry=(), passages=(), distance_km=1000.0):
    return Leg(
        mode="sea",
        from_name="a",
        to_name="b",
        distance_km=distance_km,
        geometry=geometry,
        passages=passages,
    )


def _route(*legs):
    return RouteAlternative(legs=list(legs), label="test")


def test_every_zone_declares_where_it_came_from():
    """A zone without provenance cannot be re-checked when the JWC reissues its list."""
    zones = load_risk_zones()

    assert zones
    for zone in zones:
        assert zone.source
        assert zone.valid_from
        assert zone.geometry.is_valid


def test_a_track_through_a_listed_area_is_reported_with_its_length():
    risk = assess_route(_route(_sea_leg(geometry=THROUGH_RED_SEA)))

    assert risk.is_exposed
    assert risk.zone_names == ["Güney Kızıldeniz ve Aden Körfezi"]
    # 8 degrees of longitude at 18N is roughly 847 km.
    assert 800 < risk.distance_in_zones_km < 890


def test_a_track_clear_of_every_zone_reports_no_exposure():
    risk = assess_route(_route(_sea_leg(geometry=CLEAR_OF_ZONES)))

    assert not risk.is_exposed
    assert risk.distance_in_zones_km == 0
    assert risk.zone_names == []


def test_a_sea_leg_without_a_track_is_unassessed_rather_than_assumed_safe():
    """Reporting an untracked leg as clear would be a guess dressed as a result."""
    risk = assess_route(_route(_sea_leg(geometry=(), distance_km=2500)))

    assert not risk.is_exposed
    assert risk.untracked_sea_km == 2500


def test_road_legs_are_not_tested_against_maritime_zones():
    road = Leg(mode="road", from_name="a", to_name="b", distance_km=500, geometry=THROUGH_RED_SEA)

    risk = assess_route(_route(road))

    assert not risk.is_exposed
    assert risk.untracked_sea_km == 0


def test_chokepoints_are_collected_from_the_legs():
    route = _route(
        _sea_leg(passages=("suez", "babalmandab")),
        _sea_leg(passages=("gibraltar", "suez")),
    )

    assert assess_route(route).passages == ["babalmandab", "gibraltar", "suez"]


def test_distance_in_a_zone_adds_up_across_legs():
    route = _route(_sea_leg(geometry=THROUGH_RED_SEA), _sea_leg(geometry=THROUGH_RED_SEA))
    single = assess_route(_route(_sea_leg(geometry=THROUGH_RED_SEA)))

    assert assess_route(route).distance_in_zones_km == pytest.approx(
        single.distance_in_zones_km * 2
    )


def test_overlapping_zones_do_not_double_count_the_exposure():
    """Listed areas overlap; summing per-zone lengths would report a longer exposure
    than the voyage itself has."""
    risk = assess_route(_route(_sea_leg(geometry=THROUGH_OVERLAP)))
    per_zone_total = sum(crossing.distance_km for crossing in risk.crossings)

    assert len(risk.crossings) == 2
    assert per_zone_total == pytest.approx(risk.distance_in_zones_km * 2)
    assert risk.distance_in_zones_km == pytest.approx(per_zone_total / 2)


# Independently known positions, used to check the zones rather than to build them.
CHOKEPOINT_POSITIONS = {
    "babalmandab": (43.35, 12.58),
    "suez": (32.55, 30.02),
    "ormuz": (56.50, 26.57),
    "bosporus": (29.06, 41.12),
    "gibraltar": (-5.60, 35.95),
    "panama": (-79.68, 9.08),
    "malacca": (100.75, 2.50),
}


def test_a_zone_contains_every_chokepoint_it_claims():
    """The polygons are hand-drawn rectangles, so their claims have to be checkable.

    This caught two wrong ones: the southern Red Sea zone stops at 20N and was claiming
    the Suez Canal at 30N, and the Black Sea zone was claiming a Bosporus that lies
    outside it.
    """
    from shapely.geometry import Point

    for zone in load_risk_zones():
        for chokepoint in zone.chokepoints:
            assert chokepoint in CHOKEPOINT_POSITIONS, f"{zone.id} claims unknown {chokepoint}"
            assert zone.geometry.contains(Point(*CHOKEPOINT_POSITIONS[chokepoint])), (
                f"{zone.id} claims {chokepoint} but its polygon does not contain it"
            )


def test_the_zones_agree_with_searoutes_own_chokepoint_labels():
    """Two independent sources cross-checking: searoute labels its network's edges, the
    zones are drawn from a published list. A voyage searoute says transits a chokepoint
    must cross a zone that claims it."""
    from app.core.sea import sea_route

    track = sea_route((121.80, 31.23), (4.13, 51.95))
    leg = Leg(
        mode="sea", from_name="Shanghai", to_name="Rotterdam", distance_km=track.distance_km,
        geometry=tuple(map(tuple, track.geometry)), passages=tuple(track.passages),
    )
    risk = assess_route(_route(leg))
    claimed = {c for crossing in risk.crossings for c in crossing.zone.chokepoints}

    assert "babalmandab" in risk.passages
    assert claimed & set(risk.passages), (
        f"route transits {risk.passages} but the zones it crosses claim {claimed}"
    )
