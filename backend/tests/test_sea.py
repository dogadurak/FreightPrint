import pytest



def test_no_route_sails_through_the_corinth_canal():
    """The canal is 21 m wide at the bottom; no ro-ro or container ship transits it.
    searoute's network offered it as a shortcut and its `restrictions` argument cannot
    block it, so the node is removed from the network instead."""
    from app.core.sea import CORINTH_CANAL_BOX, sea_route
    from shapely.geometry import LineString

    # Every pairing that used to take the shortcut: Marmara or Levant to the Adriatic.
    for origin, destination in (
        ((29.25, 40.87), (13.75, 45.65)),   # Pendik -> Trieste
        ((34.64, 36.80), (13.75, 45.65)),   # Mersin -> Trieste
        ((29.25, 40.87), (16.87, 41.14)),   # Pendik -> Bari
    ):
        route = sea_route(origin, destination)
        assert not LineString(route.geometry).intersects(CORINTH_CANAL_BOX), (
            f"{origin} -> {destination} still cuts the isthmus"
        )
        assert route.is_realistic


def test_the_corrected_route_rounds_the_peloponnese():
    """Not merely absent from the canal — it has to go the way a ship actually goes,
    south past Cape Malea and up the Ionian."""
    from app.core.sea import sea_route

    track = sea_route((29.25, 40.87), (13.75, 45.65)).geometry

    south_of_peloponnese = [p for p in track if 21.5 < p[0] < 24.0 and 36.0 < p[1] < 36.8]
    ionian = [p for p in track if 19.0 < p[0] < 21.5 and 37.5 < p[1] < 39.5]
    assert south_of_peloponnese, "does not round the southern capes"
    assert ionian, "does not come up the Ionian"


def test_severing_the_canal_lengthens_the_route_rather_than_breaking_it():
    """Removing a node from a graph can disconnect more than intended. The Gulf of
    Corinth loses its eastern exit, which is correct, but everything else must still
    route — and route longer, because the shortcut is gone."""
    from app.core.sea import sea_route

    corrected = sea_route((29.25, 40.87), (13.75, 45.65))

    assert corrected.distance_km > 2100
    assert corrected.distance_km < 2400, "an implausible detour suggests a broken graph"


@pytest.fixture(scope="module")
def coastline():
    """Natural Earth 10m land, clipped to the corridor and simplified for speed.

    Bundled rather than downloaded so the suite stays hermetic; simplified to about
    400 m, which is coarser than the strait the tolerances below allow for.
    """
    import json
    from pathlib import Path

    from shapely.geometry import shape

    path = Path(__file__).parent / "fixtures" / "coastline.geojson"
    return shape(json.loads(path.read_text(encoding="utf-8"))["geometry"])


def _land_km(track, land) -> float:
    from shapely.geometry import LineString

    return sum(
        LineString([track[i], track[i + 1]]).intersection(land).length * 111
        for i in range(len(track) - 1)
    )


@pytest.mark.parametrize(
    "origin,destination",
    [
        ((29.25, 40.87), (13.75, 45.65)),   # Pendik -> Trieste
        ((34.64, 36.80), (13.75, 45.65)),   # Mersin -> Trieste
        ((29.25, 40.87), (16.87, 41.14)),   # Pendik -> Bari
    ],
)
def test_the_drawn_track_stays_off_the_land(origin, destination, coastline):
    """searoute's edges are straight lines between nodes up to 170 km apart, so several
    of them cut across capes and straits — 29 km of the Pendik-Trieste track ran over
    the Gallipoli peninsula. Waypoint chains replace those edges.

    The tolerance is not zero because searoute's own node at (26.2, 40.1) sits on land
    by about a kilometre and the Dardanelles is only four wide; that residue cannot be
    routed away. It was 55 km before the refinements.
    """
    from app.core.sea import sea_route

    on_land = _land_km(sea_route(origin, destination).geometry, coastline)

    assert on_land < 15, f"{on_land:.1f} km of the track runs over land"


def test_the_refinements_are_what_keeps_it_off_the_land(coastline):
    """Guards against the chains being dropped or silently failing to apply: without
    them the same sailing puts 55 km over Gallipoli and Cape Malea."""
    from app.core.sea import _refinements

    chains = _refinements()
    assert {"dardanelles", "cape_malea"} <= set(chains)
    for name, chain in chains.items():
        assert chain["via"], f"{name} has no waypoints"
        assert len(chain["from"]) == 2 and len(chain["to"]) == 2
