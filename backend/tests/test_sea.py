

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
