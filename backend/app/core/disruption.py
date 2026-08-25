"""What this network costs when part of it stops working.

Every other module here answers "what does this shipment emit". This one answers the
question a logistics manager actually loses sleep over: **if that terminal closes or
that service stops, what happens to everything I have moving?** Suez in 2021, the Red
Sea from 2024, a strike at a single ro-ro berth — the corridor does not fail gracefully,
it fails at one node, and the cost lands on every shipment routed through it.

The method is deliberately plain, because a plain method is one a carrier can argue
with. Take the demand as it stands, remove one piece of the network, route and price
everything again, and report the difference. No proxy for criticality, no centrality
score standing in for an outcome: the number is the extra carbon, the extra hours and
the extra euros that a closure actually causes across the portfolio.

Two things this is careful about.

**A shipment that can no longer be moved is not a shipment with a higher cost.** It is
counted separately and never averaged into the delta, because a mean that quietly
absorbs the unroutable ones understates exactly the closure that matters most.

**Ranking is by consequence, not by connectivity.** A terminal can sit on many paths and
still be cheap to lose because a near-substitute exists one port along; another can carry
little traffic and be irreplaceable. Betweenness cannot tell those apart and a re-route
can, which is the whole reason this is worth computing rather than reading off the graph.
"""

from dataclasses import dataclass, field
from typing import Callable, Iterable

from .emissions import (
    DEFAULT_FACTOR_SET,
    DEFAULT_SCOPE,
    FactorNotFoundError,
    calculate_shipment,
    lowest_emission_first,
)
from .network import load_service_legs, load_terminals
from .report import ShipmentRow
from .road import RoadRoutingError
from .route import find_route_alternatives
from .schedule import build_timeline


@dataclass(frozen=True)
class Disruption:
    """One thing that has stopped working, named so a result can be read aloud."""

    id: str
    name: str
    closed_terminals: frozenset[str] = frozenset()
    suspended_legs: frozenset[tuple[str, str]] = frozenset()

    @property
    def is_empty(self) -> bool:
        return not (self.closed_terminals or self.suspended_legs)


@dataclass
class ShipmentOutcome:
    """One shipment before and after, or the fact that it can no longer be moved."""

    reference: str
    lane: str
    normal_co2_kg: float
    normal_hours: float
    normal_label: str
    disrupted_co2_kg: float | None = None
    disrupted_hours: float | None = None
    disrupted_label: str = ""
    stranded: bool = False
    # The terminals the undisrupted choice passes through — what this shipment depends on.
    normal_terminals: frozenset[str] = frozenset()
    # True when the disrupted run turned up something the baseline search had missed.
    # The route still changed and still counts as rerouted; only the delta is corrected.
    baseline_understated: bool = False

    @property
    def extra_co2_kg(self) -> float:
        """Never negative: closing part of a network cannot improve it.

        Where the disrupted run beat the baseline, the baseline was under-searched, not
        the closure beneficial — see `_correct_baseline`. Clamping here rather than
        rewriting the chosen route keeps the reroute visible: the shipment really did
        move to a different service, and hiding that would answer the wrong question.
        """
        if self.stranded:
            return 0.0
        return max(0.0, self.disrupted_co2_kg - self.normal_co2_kg)

    @property
    def extra_hours(self) -> float:
        """Signed, unlike the carbon — and the difference is not an oversight.

        The route is chosen by lowest emissions, so with a complete search the disrupted
        best can never emit less than the undisrupted best; a negative there means the
        search was short. Time is not what was minimised, so a forced alternative can
        genuinely be faster while emitting more. Clamping it would hide the trade-off a
        planner is actually choosing between: closing Trieste costs carbon and *saves*
        hours, and both halves of that are the answer.
        """
        if self.stranded:
            return 0.0
        return self.disrupted_hours - self.normal_hours

    @property
    def rerouted(self) -> bool:
        return not self.stranded and self.disrupted_label != self.normal_label


@dataclass
class DisruptionImpact:
    disruption: Disruption
    outcomes: list[ShipmentOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def affected(self) -> list[ShipmentOutcome]:
        """Shipments the closure actually reached — rerouted or stranded.

        A shipment that never touched the closed piece is not evidence that the closure
        was harmless; it is evidence it was irrelevant to that lane. Keeping the two
        apart is what stops a large portfolio diluting a severe local failure to nothing.
        """
        return [o for o in self.outcomes if o.stranded or o.rerouted]

    @property
    def stranded(self) -> list[ShipmentOutcome]:
        return [o for o in self.outcomes if o.stranded]

    @property
    def extra_co2_kg(self) -> float:
        return sum(o.extra_co2_kg for o in self.outcomes)

    @property
    def extra_hours(self) -> float:
        """Summed over shipments: a day lost on each of forty is forty days of stock."""
        return sum(o.extra_hours for o in self.outcomes)

    @property
    def worst(self) -> ShipmentOutcome | None:
        movable = [o for o in self.outcomes if not o.stranded]
        return max(movable, key=lambda o: o.extra_co2_kg) if movable else None

    @property
    def severity(self) -> str:
        """What the closure did, in the terms a plan is written in.

        Stranded traffic outranks any amount of extra carbon: a shipment that cannot
        move is an operational failure, and one that moves expensively is a cost.
        """
        if self.stranded:
            return "stranded"
        if not self.affected:
            return "no-effect"
        return "rerouted"


def _all_candidates() -> int:
    """Let both runs reach every terminal, rather than the nearest few.

    Routing normally considers only the handful of nearest *connected* terminals to each
    endpoint, which keeps an interactive request cheap. That limit is fatal here,
    because it makes the option set depend on which terminals exist: closing Mersin —
    a port this corridor never uses — freed a slot in the nearest-five and let a
    different alternative into the search, so an irrelevant closure reported as a
    reroute. Under-searching the baseline had the same effect in reverse, making a
    closure look like a saving.

    So a disruption study widens the search to the whole network and pays for it. On
    sixteen terminals that is at most thirty-two road lookups per shipment, cached
    thereafter, and this is a batch analysis rather than a keystroke. If the network
    grows to where that hurts, the fix is a cheaper candidate rule that does not depend
    on which nodes are present — not a smaller number here.
    """
    return len(load_terminals())


def _price(routes, shipment, scope, factor_set):
    """The option a shipper would actually take: lowest emissions, with its clock.

    The terminals it passes through come back too. They are what a shipment actually
    depends on, and knowing them turns "this lane is exposed" into "this lane is
    exposed *here*".
    """
    priced = calculate_shipment(
        routes, tonnage=shipment.tonnage, scope=scope, factor_set=factor_set
    )
    route, emission = min(
        lowest_emission_first(routes, priced), key=lambda pair: pair[1].total_co2_kg
    )
    used = frozenset(
        node for leg in route.legs for node in (leg.from_id, leg.to_id)
        if node and not node.startswith("__")
    )
    return emission.total_co2_kg, build_timeline(route).total_hours, emission.label, used


def _correct_baseline(outcome: ShipmentOutcome, impact: DisruptionImpact) -> None:
    """Keep the comparison honest when the disrupted run finds something better.

    **Closing part of a network cannot improve it.** Every route that survives a closure
    was available before it, so a disrupted option beating the undisrupted best does not
    mean the closure helped — it means the baseline search never looked at that option.

    That is not hypothetical here. Route enumeration reaches only the few *nearest
    connected* terminals to each endpoint, so which terminals are candidates depends on
    which ones exist: closing Trieste promoted a Halkalı–Chitila rail path into the
    candidate set, and it priced 556 kg below the "undisrupted best" that had never
    considered it. Reported raw, that would have said losing a hub saves carbon.

    So the delta is floored at zero and the fact is flagged — but the chosen routes are
    left exactly as they were found. An earlier version overwrote the baseline's label
    with the disrupted one, which made a genuine reroute report as "no effect": the
    shipment really had moved off Pendik–Trieste–Köln onto a rail path, and rewriting
    the baseline hid the very thing the study exists to show.
    """
    if outcome.disrupted_co2_kg is None or outcome.disrupted_co2_kg >= outcome.normal_co2_kg:
        return

    outcome.baseline_understated = True
    impact.notes.append(
        f"{outcome.reference}: kesintili aramada {outcome.normal_co2_kg:,.0f} kg'lık "
        f"esastan daha iyi bir seçenek çıktı ({outcome.disrupted_co2_kg:,.0f} kg, "
        f"{outcome.disrupted_label}). Bir kapanma ağı iyileştiremeyeceğine göre bu, "
        f"kapanmanın faydası değil esas aramanın bu seçeneği kaçırdığı anlamına gelir. "
        f"Fark sıfır sayıldı; rota değişimi olduğu gibi raporlanıyor."
    )


def assess_disruption(
    shipments: list[ShipmentRow],
    disruption: Disruption,
    scope: str = DEFAULT_SCOPE,
    factor_set: str = DEFAULT_FACTOR_SET,
    on_progress: Callable[[int], None] | None = None,
) -> DisruptionImpact:
    """Route the demand twice — as it is, and without the disrupted piece."""
    impact = DisruptionImpact(disruption=disruption)
    if disruption.is_empty:
        impact.notes.append("Kesinti tanımlanmadı; ağdan hiçbir parça çıkarılmadı.")

    common = dict(scope=scope, factor_set=factor_set)
    candidates = _all_candidates()
    for done, shipment in enumerate(shipments, start=1):
        try:
            normal = find_route_alternatives(
                origin=shipment.origin,
                destination=shipment.destination,
                origin_name=shipment.origin_name,
                destination_name=shipment.destination_name,
                candidate_terminals=candidates,
            )
            normal_co2, normal_hours, normal_label, used = _price(normal, shipment, **common)
        except (RoadRoutingError, LookupError, ValueError) as error:
            impact.notes.append(f"{shipment.reference} normal koşulda rotalanamadı: {error}")
            if on_progress:
                on_progress(done)
            continue

        outcome = ShipmentOutcome(
            reference=shipment.reference,
            lane=f"{shipment.origin_name} → {shipment.destination_name}",
            normal_co2_kg=normal_co2,
            normal_hours=normal_hours,
            normal_label=normal_label,
            normal_terminals=used,
        )

        try:
            disrupted = find_route_alternatives(
                origin=shipment.origin,
                destination=shipment.destination,
                origin_name=shipment.origin_name,
                destination_name=shipment.destination_name,
                candidate_terminals=candidates,
                closed_terminals=disruption.closed_terminals,
                suspended_legs=disruption.suspended_legs,
            )
            co2, hours, label, _ = _price(disrupted, shipment, **common)
            outcome.disrupted_co2_kg = co2
            outcome.disrupted_hours = hours
            outcome.disrupted_label = label
            _correct_baseline(outcome, impact)
        except (RoadRoutingError, LookupError, ValueError, FactorNotFoundError):
            # No route survives the closure. Recorded as stranded rather than as a very
            # expensive move: there is no number that honestly stands for "cannot go".
            outcome.stranded = True

        impact.outcomes.append(outcome)
        if on_progress:
            on_progress(done)

    return impact


def candidate_disruptions(
    terminals: Iterable[str] | None = None,
    include_legs: bool = True,
) -> list[Disruption]:
    """One disruption per thing that can fail: every terminal, every service.

    Built from the network rather than listed by hand, so a terminal added tomorrow is
    ranked the day after without anyone remembering to add it here.
    """
    all_terminals = load_terminals()
    chosen = list(terminals) if terminals is not None else list(all_terminals)

    disruptions = [
        Disruption(
            id=f"terminal:{terminal_id}",
            name=f"{all_terminals[terminal_id].name} kapalı",
            closed_terminals=frozenset({terminal_id}),
        )
        for terminal_id in chosen
        if terminal_id in all_terminals
    ]

    if include_legs:
        for leg in load_service_legs():
            origin, destination = leg["from_terminal"], leg["to_terminal"]
            disruptions.append(
                Disruption(
                    id=f"leg:{origin}-{destination}",
                    name=(
                        f"{all_terminals[origin].name} – {all_terminals[destination].name} "
                        f"{leg['mode']} servisi durdu"
                    ),
                    suspended_legs=frozenset({(origin, destination)}),
                )
            )
    return disruptions


@dataclass
class Criticality:
    """One piece of the network, ranked by what losing it costs."""

    disruption: Disruption
    stranded: int
    rerouted: int
    extra_co2_kg: float
    extra_hours: float

    @property
    def severity(self) -> str:
        if self.stranded:
            return "stranded"
        return "rerouted" if self.rerouted else "no-effect"


def rank_criticality(
    shipments: list[ShipmentRow],
    disruptions: list[Disruption] | None = None,
    scope: str = DEFAULT_SCOPE,
    factor_set: str = DEFAULT_FACTOR_SET,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Criticality]:
    """Rank the network's pieces by the cost of losing each one.

    Stranded traffic first, then extra carbon. Sorting purely by carbon would put a
    closure that halts six shipments below one that lengthens sixty, and those are not
    comparable quantities — one is a cost, the other is a stoppage.

    This routes the demand once per disruption, which is the expensive part and the
    reason it is a batch job rather than a request.
    """
    disruptions = disruptions if disruptions is not None else candidate_disruptions()

    ranked = []
    for index, disruption in enumerate(disruptions, start=1):
        impact = assess_disruption(shipments, disruption, scope=scope, factor_set=factor_set)
        ranked.append(
            Criticality(
                disruption=disruption,
                stranded=len(impact.stranded),
                rerouted=len([o for o in impact.outcomes if o.rerouted]),
                extra_co2_kg=impact.extra_co2_kg,
                extra_hours=impact.extra_hours,
            )
        )
        if on_progress:
            on_progress(index, len(disruptions))

    return sorted(ranked, key=lambda c: (-c.stranded, -c.extra_co2_kg, -c.extra_hours))
