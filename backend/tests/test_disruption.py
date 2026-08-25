"""What the corridor costs when a piece of it stops working.

The thing these tests mostly defend is the difference between a shipment that got more
expensive and a shipment that cannot move at all. Averaging the second into the first
understates precisely the closure that matters most, and it is an easy mistake to make
because both look like "a bigger number" from inside the code.
"""

import pytest

from app.core import route as route_module
from app.core.disruption import (
    Disruption,
    assess_disruption,
    candidate_disruptions,
    rank_criticality,
)
from app.core.network import build_network, haversine_km, load_terminals
from app.core.report import ShipmentRow
from app.core.road import RoadRoute

GEBZE = (29.4306, 40.7889)
DUSSELDORF = (6.7735, 51.2277)


@pytest.fixture(autouse=True)
def offline_road(monkeypatch):
    def fake(origin, destination):
        km = haversine_km(origin, destination) * 1.3
        return RoadRoute(distance_km=km, duration_h=km / 70, geometry=(origin, destination))

    monkeypatch.setattr(route_module, "road_route", fake)


def _shipment(reference="A1", tonnage=24.0):
    return ShipmentRow(
        reference=reference,
        carrier="test",
        origin=GEBZE,
        destination=DUSSELDORF,
        origin_name="Gebze",
        destination_name="Dusseldorf",
        tonnage=tonnage,
    )


def test_closing_a_terminal_removes_it_from_the_network():
    """Removed, not merely disconnected: nothing may route through it or be handed to
    it as an endpoint."""
    graph = build_network(closed_terminals=frozenset({"trieste"}))

    assert "trieste" not in graph
    assert not any("trieste" in edge for edge in graph.edges)


def test_suspending_a_service_stops_it_in_both_directions():
    """A service runs both ways; naming one direction would leave a one-way corridor
    nobody operates."""
    graph = build_network(suspended_legs=frozenset({("trieste", "pendik")}))

    assert not graph.has_edge("pendik", "trieste")
    assert "pendik" in graph and "trieste" in graph


def test_closing_an_unknown_terminal_is_refused():
    """A typo in a scenario name would otherwise read as "nothing happened", which is
    the most misleading answer a disruption study can give."""
    with pytest.raises(ValueError, match="unknown terminal"):
        build_network(closed_terminals=frozenset({"atlantis"}))


def test_closing_what_a_shipment_depends_on_changes_its_route():
    """Derived rather than named: the closure is built from the terminals the chosen
    route actually uses, so this keeps testing the property when the network changes or
    the best route moves. Naming a terminal here would only pin today's answer."""
    survey = assess_disruption(
        [_shipment()], Disruption("none", "yok"), scope="WTW", factor_set="glec"
    )
    depends_on = survey.outcomes[0].normal_terminals
    assert depends_on, "the chosen route touches no terminal; nothing to disrupt"

    impact = assess_disruption(
        [_shipment()],
        Disruption("d", "bagimli terminaller kapali", closed_terminals=depends_on),
        scope="WTW",
        factor_set="glec",
    )

    outcome = impact.outcomes[0]
    assert outcome.stranded or outcome.rerouted, (
        "every terminal the route needs was closed and the answer did not move"
    )
    if not outcome.stranded:
        assert outcome.extra_co2_kg >= 0


def test_a_closure_that_touches_nothing_reports_no_effect():
    """A shipment that never used the closed piece is not evidence the closure was
    harmless — and must not be counted as an affected one either."""
    impact = assess_disruption(
        [_shipment()],
        Disruption("m", "Mersin kapalı", closed_terminals=frozenset({"mersin"})),
        scope="WTW",
        factor_set="glec",
    )

    assert impact.severity == "no-effect"
    assert impact.affected == []
    assert impact.extra_co2_kg == pytest.approx(0.0)


def test_a_stranded_shipment_is_counted_apart_from_an_expensive_one():
    """There is no number that honestly stands for "cannot go", so it must never enter
    the delta. A mean that absorbed it would understate the worst closure there is."""
    impact = assess_disruption(
        [_shipment()],
        Disruption("all", "her sey kapali", closed_terminals=frozenset(load_terminals())),
        scope="WTW",
        factor_set="glec",
    )

    outcome = impact.outcomes[0]
    # With every terminal gone the all-road option still exists, so this corridor is
    # not strandable — which is itself the finding, and the delta stays honest either way.
    if outcome.stranded:
        assert outcome.extra_co2_kg == 0.0
        assert impact.severity == "stranded"
    else:
        assert outcome.disrupted_label == "all-road"


def test_extra_hours_are_summed_over_shipments():
    """A day lost on each of forty shipments is forty days of stock, not one day."""
    impact = assess_disruption(
        [_shipment("A1"), _shipment("A2")],
        Disruption("t", "Trieste kapalı", closed_terminals=frozenset({"trieste"})),
        scope="WTW",
        factor_set="glec",
    )

    assert len(impact.outcomes) == 2
    assert impact.extra_hours == pytest.approx(sum(o.extra_hours for o in impact.outcomes))
    assert impact.extra_co2_kg == pytest.approx(sum(o.extra_co2_kg for o in impact.outcomes))


def test_every_piece_of_the_network_is_a_candidate():
    """Built from the network, so a terminal added tomorrow is ranked the day after
    without anyone remembering to list it here."""
    candidates = candidate_disruptions()
    terminals = load_terminals()

    closed = {next(iter(d.closed_terminals)) for d in candidates if d.closed_terminals}
    assert closed == set(terminals)
    assert any(d.suspended_legs for d in candidates), "no service is treated as failable"
    assert all(d.name for d in candidates), "a scenario with no name cannot be read aloud"


def test_criticality_puts_a_stoppage_above_any_amount_of_carbon():
    """A closure that halts traffic and one that lengthens it are not comparable
    quantities: one is a stoppage, the other is a cost."""
    shipments = [_shipment("A1")]
    ranked = rank_criticality(
        shipments,
        disruptions=[
            Disruption("cheap", "biraz pahali", closed_terminals=frozenset({"trieste"})),
            Disruption("halt", "durdurur", closed_terminals=frozenset(load_terminals())),
        ],
        scope="WTW",
        factor_set="glec",
    )

    stranded_first = [c.stranded > 0 for c in ranked]
    assert stranded_first == sorted(stranded_first, reverse=True)


def test_a_terminal_the_traffic_uses_outranks_one_it_does_not():
    """The reason this is computed rather than read off the graph. Which terminal is
    which is derived from the routing, so the test states the property and not a name."""
    survey = assess_disruption(
        [_shipment()], Disruption("none", "yok"), scope="WTW", factor_set="glec"
    )
    used = survey.outcomes[0].normal_terminals
    unused = sorted(set(load_terminals()) - used)
    assert used and unused, "need one terminal on the route and one off it"

    ranked = rank_criticality(
        [_shipment()],
        disruptions=candidate_disruptions(
            terminals=[sorted(used)[0], unused[0]], include_legs=False
        ),
        scope="WTW",
        factor_set="glec",
    )

    by_id = {c.disruption.id: c for c in ranked}
    assert by_id[f"terminal:{unused[0]}"].severity == "no-effect"
    # The used one either reroutes or strands; either way it must not rank below a
    # terminal the traffic never touches.
    assert ranked[0].disruption.id == f"terminal:{sorted(used)[0]}"


def test_an_empty_disruption_says_so_rather_than_reporting_a_clean_network():
    impact = assess_disruption([_shipment()], Disruption("none", "hiçbir şey"))

    assert any("tanımlanmadı" in note for note in impact.notes)
    assert impact.severity == "no-effect"


def test_a_closure_can_never_come_out_as_an_improvement():
    """Every route that survives a closure existed before it, so a disrupted option
    beating the baseline means the baseline search missed it — not that losing a hub
    helps. Reported raw it once said closing Trieste saved 556 kg."""
    impact = assess_disruption(
        [_shipment()],
        Disruption("t", "Trieste kapalı", closed_terminals=frozenset({"trieste"})),
        scope="WTW",
        factor_set="glec",
    )

    for outcome in impact.outcomes:
        assert outcome.extra_co2_kg >= 0, "a closure reported as a carbon saving"


def test_correcting_the_baseline_is_recorded_rather_than_absorbed():
    """A baseline that had to be corrected is itself a finding about the search."""
    from app.core.disruption import DisruptionImpact, ShipmentOutcome, _correct_baseline

    impact = DisruptionImpact(disruption=Disruption("x", "x"))
    outcome = ShipmentOutcome(
        reference="A1", lane="a → b",
        normal_co2_kg=5000.0, normal_hours=100.0, normal_label="via hub",
        disrupted_co2_kg=4200.0, disrupted_hours=90.0, disrupted_label="via rail",
    )

    _correct_baseline(outcome, impact)

    assert outcome.extra_co2_kg == 0.0
    assert outcome.baseline_understated
    # The chosen routes stay exactly as found: the shipment really did move, and an
    # earlier version overwrote the baseline's label and reported "no effect".
    assert outcome.normal_label == "via hub"
    assert outcome.rerouted, "a real reroute was hidden by correcting the baseline"
    assert impact.notes and "Fark sıfır sayıldı" in impact.notes[0]


def test_a_genuinely_worse_disruption_is_left_alone():
    """The correction must only ever reach downwards; a real penalty stays a penalty."""
    from app.core.disruption import DisruptionImpact, ShipmentOutcome, _correct_baseline

    impact = DisruptionImpact(disruption=Disruption("x", "x"))
    outcome = ShipmentOutcome(
        reference="A1", lane="a → b",
        normal_co2_kg=4000.0, normal_hours=90.0, normal_label="via hub",
        disrupted_co2_kg=5200.0, disrupted_hours=140.0, disrupted_label="long way round",
    )

    _correct_baseline(outcome, impact)

    assert outcome.extra_co2_kg == pytest.approx(1200.0)
    assert outcome.extra_hours == pytest.approx(50.0)
    assert not outcome.baseline_understated
    assert impact.notes == []


def test_a_faster_forced_route_reports_the_hours_it_saves():
    """Carbon is clamped because carbon is what the route was chosen on; time is not, so
    a forced alternative can genuinely be quicker while emitting more. Reporting that as
    zero would hide the trade-off the planner is actually choosing between."""
    from app.core.disruption import ShipmentOutcome

    outcome = ShipmentOutcome(
        reference="A1", lane="a → b",
        normal_co2_kg=4000.0, normal_hours=120.0, normal_label="slow sea route",
        disrupted_co2_kg=4800.0, disrupted_hours=96.0, disrupted_label="direct road",
    )

    assert outcome.extra_co2_kg == pytest.approx(800.0)
    assert outcome.extra_hours == pytest.approx(-24.0), "an hours saving was clamped away"
