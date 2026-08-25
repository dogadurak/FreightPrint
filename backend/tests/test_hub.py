"""Where a consolidation hub belongs.

The model's one real decision is counting vehicles rather than tonne-kilometres, and
most of these tests exist to hold that line. On tonne-kilometres the triangle inequality
makes every hub a loss and the optimiser opens none — the answer looks defensible and is
simply the wrong question.
"""

import pytest

from app.core import route as route_module
from app.core.hub import (
    VEHICLE_CAPACITY_TONNES,
    HubModelError,
    Site,
    candidate_sites,
    plan_hubs,
)
from app.core.network import haversine_km
from app.core.report import ShipmentRow
from app.core.road import RoadRoute

# Three suppliers clustered near Gebze, all shipping to one German destination.
SUPPLIERS = [
    ((29.43, 40.79), "Gebze"),
    ((29.28, 40.87), "Izmit"),
    ((29.10, 40.98), "Sakarya"),
]
DESTINATION = (6.7735, 51.2277)
HUB_NEAR_SUPPLIERS = Site(id="hub", name="Aday merkez", point=(29.30, 40.88))


@pytest.fixture(autouse=True)
def offline_road(monkeypatch):
    def fake(origin, destination):
        km = haversine_km(origin, destination) * 1.3
        return RoadRoute(distance_km=km, duration_h=km / 70, geometry=(origin, destination))

    monkeypatch.setattr(route_module, "road_route", fake)


def _parts(tonnage=8.0):
    """Part-loads: three of these fit one vehicle, so consolidation has something to do."""
    return [
        ShipmentRow(
            reference=f"P{i}", carrier="t",
            origin=point, destination=DESTINATION,
            origin_name=name, destination_name="Dusseldorf",
            tonnage=tonnage,
        )
        for i, (point, name) in enumerate(SUPPLIERS)
    ]


def test_part_loads_heading_the_same_way_are_worth_consolidating():
    """Three eight-tonne loads are three vehicles direct and one from a hub. That is the
    entire point of the module, and it is invisible on tonne-kilometres."""
    plan = plan_hubs(_parts(), sites=[HUB_NEAR_SUPPLIERS], max_hubs=1)

    assert plan.is_optimal
    assert plan.opened, "no hub opened where three part-loads share a destination"
    assert plan.saved_vehicle_km > 0
    assert all(a.is_consolidated for a in plan.assignments)


def test_full_loads_are_left_alone():
    """A full vehicle has nothing to share. Routing it via a hub is pure detour, and a
    model that still opened one would be optimising the wrong quantity."""
    plan = plan_hubs(_parts(tonnage=VEHICLE_CAPACITY_TONNES), sites=[HUB_NEAR_SUPPLIERS])

    assert plan.is_optimal
    assert not any(a.is_consolidated for a in plan.assignments)
    assert plan.saved_vehicle_km == pytest.approx(0.0)


def test_a_hub_is_never_worse_than_going_direct():
    """Direct is always available, so the optimum cannot cost more than not having the
    hub at all. A negative saving would mean the model forced traffic through it."""
    plan = plan_hubs(_parts(), sites=[HUB_NEAR_SUPPLIERS])

    assert plan.planned_vehicle_km <= plan.direct_vehicle_km + 1e-6
    assert plan.saved_share >= 0


def test_a_detour_hub_is_refused_even_when_it_could_consolidate():
    """Consolidation pays only if the collection legs are short. A "hub" on the far side
    of the destination consolidates just as well and costs more to reach."""
    far = Site(id="far", name="Uzak", point=(2.0, 48.0))   # past the destination

    plan = plan_hubs(_parts(), sites=[far])

    assert plan.is_optimal
    assert not any(a.is_consolidated for a in plan.assignments)


def test_the_optimiser_picks_the_nearer_of_two_hubs():
    near = HUB_NEAR_SUPPLIERS
    far = Site(id="far", name="Uzak", point=(20.0, 45.0))

    plan = plan_hubs(_parts(), sites=[near, far], max_hubs=1)

    assert [s.id for s in plan.opened] == ["hub"]


def test_it_will_not_open_more_hubs_than_asked_for():
    sites = [HUB_NEAR_SUPPLIERS, Site(id="b", name="B", point=(29.0, 41.1))]

    plan = plan_hubs(_parts(), sites=sites, max_hubs=1)

    assert len(plan.opened) <= 1


def test_the_saving_says_how_many_shipments_it_rests_on():
    """A hub justified by two shipments is a hub justified by nothing, and the total
    saving does not show that on its own."""
    plan = plan_hubs(_parts(), sites=[HUB_NEAR_SUPPLIERS])

    assert plan.shipments_per_hub == {"hub": 3}


def test_carbon_comes_from_the_factor_file_or_not_at_all():
    """A vehicle-kilometre becomes carbon through the published factor and the payload it
    assumes. Where no factor matches, the answer is absent rather than zero."""
    plan = plan_hubs(_parts(), sites=[HUB_NEAR_SUPPLIERS], scope="WTW", factor_set="glec")

    assert plan.co2_per_vehicle_km and plan.co2_per_vehicle_km > 0
    assert plan.saved_co2_kg == pytest.approx(
        plan.saved_vehicle_km * plan.co2_per_vehicle_km
    )

    missing = plan_hubs(_parts(), sites=[HUB_NEAR_SUPPLIERS], factor_set="no-such-set")
    assert missing.co2_per_vehicle_km is None
    assert missing.saved_co2_kg is None
    assert any("faktörü bulunamadı" in note for note in missing.notes)


def test_the_objective_is_named_and_so_is_what_it_cannot_see():
    """Both caveats have to travel with the number: the vehicle-kilometre choice, and
    that consolidation needs dates a shipment file does not carry."""
    notes = " ".join(plan_hubs(_parts(), sites=[HUB_NEAR_SUPPLIERS]).notes)

    assert "araç-kilometredir" in notes
    assert "tarih yoktur" in notes


def test_candidate_sites_include_the_network_and_the_freight():
    """A hub is often best where the freight already is, so excluding origins would rule
    out the answer a planner reaches for first."""
    sites = candidate_sites(_parts())
    ids = {site.id for site in sites}

    assert "trieste" in ids, "the network's own terminals are not candidates"
    assert any(site.id.startswith("origin:") for site in sites)


@pytest.mark.parametrize(
    ("shipments", "kwargs", "match"),
    [
        ([], {}, "sevkiyat yok"),
        (None, {"max_hubs": 0}, "en az bir merkez"),
    ],
)
def test_an_impossible_request_is_refused_with_the_reason(shipments, kwargs, match):
    with pytest.raises(HubModelError, match=match):
        plan_hubs(_parts() if shipments is None else shipments,
                  sites=[HUB_NEAR_SUPPLIERS], **kwargs)


def test_too_many_candidates_is_refused_rather_than_left_running():
    """The distance matrix is quadratic in the candidates; the caller is told to narrow
    the search rather than left waiting on a request that will not finish."""
    many = [Site(id=f"s{i}", name=str(i), point=(20.0 + i * 0.1, 45.0)) for i in range(60)]

    with pytest.raises(HubModelError, match="aday nokta"):
        plan_hubs(_parts(), sites=many)
