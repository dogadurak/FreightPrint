"""The journey as something that happens, rather than a column of totals.

A map with a line on it says the same thing whether the crossing took two days or ten.
This lays the route out as a sequence a client can play: where the shipment is at any
hour, how much carbon it has emitted by then, and — the part a static figure hides —
the hours it spends not moving at all. On the pilot corridor a third of the multimodal
disadvantage is handling and waiting for a sailing, and watching the marker sit still
at Trieste for eighteen hours makes that argument better than the number does.

The join between the clock and the priced legs happens **here**, not in the browser.
Pairing a timeline step back to a leg by mode is what once handed a forty-kilometre
ferry the two-and-a-half-thousand-kilometre sea leg's duration. `build_timeline` walks
`route.legs` in order and `expand_route_legs` splits a ferry out of its road leg, so the
two are reconciled by replaying that split rather than by matching, and the reconciliation
is asserted so a change to either breaks loudly.
"""

from dataclasses import dataclass, field, replace

from .emissions import ShipmentEmission
from .reefer import ReeferEmission
from .route import RouteAlternative
from .schedule import Timeline


class PlaybackMismatch(RuntimeError):
    """The clock and the priced legs disagree about what the route is made of."""


@dataclass(frozen=True)
class PlaybackSegment:
    kind: str  # "transit" | "dwell" | "wait"
    mode: str | None
    label: str
    start_h: float
    hours: float
    # Transport carbon emitted during this segment. Zero while stationary: a truck at a
    # terminal is not burning fuel, which is exactly why refrigeration is separate.
    co2_kg: float
    # Refrigeration, which does not stop when the wheels do.
    reefer_co2_kg: float
    # Where the shipment is over this segment. A stationary segment holds one point; a
    # rail leg has no computed track and holds only its endpoints, drawn schematically.
    geometry: list[list[float]] = field(default_factory=list)
    is_estimated: bool = False
    track_is_indicative: bool = False

    @property
    def end_h(self) -> float:
        return self.start_h + self.hours


@dataclass
class Playback:
    segments: list[PlaybackSegment]
    total_hours: float
    total_co2_kg: float
    total_reefer_co2_kg: float

    @property
    def stationary_hours(self) -> float:
        return sum(s.hours for s in self.segments if s.kind != "transit")


def _priced_legs_by_route_leg(
    route: RouteAlternative, shipment: ShipmentEmission
) -> list[list]:
    """Group the priced legs under the route leg each came from.

    `expand_route_legs` emits two priced legs for a road leg carrying a ferry and one
    otherwise. Replaying that rule pairs them by construction; the count is checked so
    a change there cannot silently shift every leg's figures by one.
    """
    grouped, cursor = [], 0
    for leg in route.legs:
        count = 2 if (leg.mode == "road" and leg.ferry_km > 0) else 1
        grouped.append(shipment.legs[cursor : cursor + count])
        cursor += count

    if cursor != len(shipment.legs):
        raise PlaybackMismatch(
            f"{len(route.legs)} route legs expand to {cursor} priced legs but "
            f"{len(shipment.legs)} were priced; the expansion rule has changed"
        )
    return grouped


def _schematic(leg, terminals) -> list[list[float]]:
    """A straight two-point track for a leg whose route was never computed.

    Rail has no geometry at all — the distances come from a reference table and no rail
    network is loaded — so the marker would otherwise have nowhere to travel between
    two terminals. Drawn from the terminal coordinates and flagged, the way the map
    already draws rail as a dashed schematic line rather than pretending it is a survey.
    """
    ends = [terminals.get(leg.from_id), terminals.get(leg.to_id)]
    if all(ends):
        return [list(t.coords) for t in ends]
    return []


def _place_stationary_segments(segments: list[PlaybackSegment]) -> list[PlaybackSegment]:
    """Give every waiting and handling segment a position on the map.

    Normally that is where the previous leg ended. A journey that *starts* by waiting for
    a departure has no previous leg, though, and leaving it without a position would put
    the marker nowhere at hour zero — so those look forward to where the next leg begins.
    """
    placed = list(segments)
    for index, segment in enumerate(placed):
        if segment.kind == "transit" or segment.geometry:
            continue

        before = next(
            (s.geometry[-1] for s in reversed(placed[:index]) if s.geometry), None
        )
        after = next((s.geometry[0] for s in placed[index + 1 :] if s.geometry), None)
        here = before or after
        if here is not None:
            placed[index] = replace(segment, geometry=[list(here)])
    return placed


def build_playback(
    route: RouteAlternative,
    shipment: ShipmentEmission,
    timeline: Timeline,
    reefer: ReeferEmission | None = None,
) -> Playback:
    """Lay the journey out as a playable sequence."""
    from .network import load_terminals

    terminals = load_terminals()
    grouped = _priced_legs_by_route_leg(route, shipment)

    transit_steps = [s for s in timeline.steps if s.kind == "transit"]
    if len(transit_steps) != len(route.legs):
        raise PlaybackMismatch(
            f"{len(transit_steps)} transit steps against {len(route.legs)} route legs; "
            "the timeline no longer emits one per leg"
        )

    # Refrigeration is charged against the clock, so it spreads over elapsed hours
    # rather than over distance — including the hours nothing is moving.
    reefer_per_hour = (
        reefer.co2_kg / timeline.total_hours
        if reefer and timeline.total_hours > 0
        else 0.0
    )

    segments: list[PlaybackSegment] = []
    transit_index = 0
    for step in timeline.steps:
        if step.kind == "transit":
            leg = route.legs[transit_index]
            priced = grouped[transit_index]
            transit_index += 1
            geometry = (
                [list(point) for point in leg.geometry]
                if leg.geometry
                else _schematic(leg, terminals)
            )
            segments.append(
                PlaybackSegment(
                    kind="transit",
                    mode=step.mode,
                    label=step.label,
                    start_h=step.start_h,
                    hours=step.hours,
                    co2_kg=sum(p.co2_kg for p in priced),
                    reefer_co2_kg=reefer_per_hour * step.hours,
                    geometry=geometry,
                    is_estimated=step.is_estimated,
                    # Either searoute's unsailable Corinth shortcut, or a leg with no
                    # computed track at all. Both are drawable; neither is the route taken.
                    track_is_indicative=leg.track_is_indicative or not leg.geometry,
                )
            )
        else:
            # Stationary: the position is filled in below, once both neighbours are known.
            segments.append(
                PlaybackSegment(
                    kind=step.kind,
                    mode=step.mode,
                    label=step.label,
                    start_h=step.start_h,
                    hours=step.hours,
                    co2_kg=0.0,
                    reefer_co2_kg=reefer_per_hour * step.hours,
                    geometry=[],
                    is_estimated=step.is_estimated,
                )
            )

    segments = _place_stationary_segments(segments)

    return Playback(
        segments=segments,
        total_hours=timeline.total_hours,
        total_co2_kg=sum(s.co2_kg for s in segments),
        total_reefer_co2_kg=sum(s.reefer_co2_kg for s in segments),
    )
