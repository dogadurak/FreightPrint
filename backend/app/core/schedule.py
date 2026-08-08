"""When a shipment is where, not just how far it went.

The brief puts duration in the product's first sentence and Modül B asks for the
difference in it, but the engine only ever carried distance. Transit time is not
distance over a speed: a truck stops for the driver's legally required rest, a container
waits at a terminal to be lifted, and a weekly service means the box sits until the next
sailing. Those three make up most of a door-to-door time and none of them is mileage.
"""

from dataclasses import dataclass, field

from .route import RouteAlternative

# Motorway running speed for a heavy truck, below the car speed OSRM reports.
TRUCK_SPEED_KMH = 70.0

# Regulation (EC) 561/2006: 9 h driving a day, a 45 min break every 4.5 h, 11 h daily
# rest. A long haul is mostly the rest, so ignoring these understates road time badly.
MAX_DAILY_DRIVING_H = 9.0
BREAK_AFTER_H = 4.5
BREAK_LENGTH_H = 0.75
DAILY_REST_H = 11.0

# Derived from the published ro-ro schedules: Pendik-Trieste 2500 km in 64 h, Yalova-Sète
# 3100 km in 80 h, Pendik-Patras 1450 km in 34 h all sit near this, so a leg without a
# published time is estimated from it rather than invented.
SEA_SPEED_KMH = 39.0
# European intermodal rail, door to door including intermediate stops. No published
# figure was found for these corridors, so every rail time here is an estimate.
RAIL_SPEED_KMH = 40.0

# Hours a unit spends being handled at a terminal, by what changes there.
DWELL_HOURS = {("road", "sea"): 6.0, ("sea", "road"): 6.0,
               ("road", "rail"): 4.0, ("rail", "road"): 4.0,
               ("sea", "rail"): 8.0, ("rail", "sea"): 8.0,
               ("sea", "sea"): 8.0, ("rail", "rail"): 4.0, ("road", "road"): 0.0}
DEFAULT_DWELL_H = 6.0

HOURS_PER_WEEK = 168.0


@dataclass
class ScheduleStep:
    """One block of the timeline: moving, being handled, or waiting for a departure."""

    kind: str  # "transit" | "dwell" | "wait"
    mode: str | None
    label: str
    hours: float
    start_h: float
    is_estimated: bool = False

    @property
    def end_h(self) -> float:
        return self.start_h + self.hours


@dataclass
class Timeline:
    steps: list[ScheduleStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_hours(self) -> float:
        return sum(step.hours for step in self.steps)

    @property
    def total_days(self) -> float:
        return self.total_hours / 24

    @property
    def hours_by_kind(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for step in self.steps:
            totals[step.kind] = totals.get(step.kind, 0.0) + step.hours
        return totals

    @property
    def any_estimated(self) -> bool:
        return any(step.is_estimated for step in self.steps)


def road_elapsed_hours(distance_km: float, speed_kmh: float = TRUCK_SPEED_KMH) -> float:
    """Wall-clock time for a road leg, driver's hours included.

    Driving time alone would put Turkey to Germany inside two days. The breaks and daily
    rests the law requires are most of the difference.
    """
    driving_h = distance_km / speed_kmh
    # Breaks and rests fall due after a completed stint, and the final one ends on
    # arrival rather than in a stop. Pro-rating them instead would charge a two-hour
    # run for part of a break it never has to take.
    breaks = _completed_intervals(driving_h, BREAK_AFTER_H)
    rests = _completed_intervals(driving_h, MAX_DAILY_DRIVING_H)
    return driving_h + breaks * BREAK_LENGTH_H + rests * DAILY_REST_H


def _completed_intervals(driving_h: float, interval_h: float) -> int:
    """How many stops fall due: one after each full interval, none after the last."""
    completed = int(driving_h // interval_h)
    if completed and driving_h % interval_h == 0:
        completed -= 1
    return completed


def expected_wait_hours(frequency_per_week: float | None) -> float:
    """Average wait for the next departure of a service running n times a week.

    Arriving at random against an even schedule, the mean wait is half the gap. Nothing
    is assumed when the frequency is unknown — the wait is left out and flagged instead.
    """
    if not frequency_per_week or frequency_per_week <= 0:
        return 0.0
    return HOURS_PER_WEEK / frequency_per_week / 2


def build_timeline(route: RouteAlternative, services: dict | None = None) -> Timeline:
    """Lay a route out in time: transit, terminal handling, and waiting for departures."""
    from .network import load_service_schedules

    services = services if services is not None else load_service_schedules()
    timeline = Timeline()
    cursor = 0.0
    previous_mode: str | None = None
    estimated_modes: set[str] = set()
    unknown_frequency: list[str] = []

    for leg in route.legs:
        if previous_mode is not None:
            dwell = DWELL_HOURS.get((previous_mode, leg.mode), DEFAULT_DWELL_H)
            if dwell:
                timeline.steps.append(ScheduleStep(
                    kind="dwell", mode=None, label=f"{leg.from_name} aktarma",
                    hours=dwell, start_h=cursor, is_estimated=True,
                ))
                cursor += dwell

        service = services.get((leg.from_id, leg.to_id)) if leg.from_id and leg.to_id else None
        if leg.mode in {"sea", "rail"}:
            wait = expected_wait_hours(service.frequency_per_week if service else None)
            if wait:
                timeline.steps.append(ScheduleStep(
                    kind="wait", mode=leg.mode,
                    label=f"{leg.from_name} kalkış beklemesi",
                    hours=wait, start_h=cursor,
                ))
                cursor += wait
            elif service is None or service.frequency_per_week is None:
                unknown_frequency.append(f"{leg.from_name}->{leg.to_name}")

        hours, estimated = _transit_hours(leg, service)
        if estimated:
            estimated_modes.add(leg.mode)
        timeline.steps.append(ScheduleStep(
            kind="transit", mode=leg.mode,
            label=f"{leg.from_name} → {leg.to_name}",
            hours=hours, start_h=cursor, is_estimated=estimated,
        ))
        cursor += hours
        previous_mode = leg.mode

    if "rail" in estimated_modes:
        timeline.notes.append(
            f"Demiryolu süreleri {RAIL_SPEED_KMH:.0f} km/sa ortalamadan türetildi; "
            "bu koridorlar için yayımlanmış tarife bulunamadı."
        )
    if "sea" in estimated_modes:
        timeline.notes.append(
            f"Bazı deniz bacaklarının süresi {SEA_SPEED_KMH:.0f} km/sa ortalamadan "
            "türetildi (yayımlanmış tarifelerden çıkarıldı)."
        )
    if unknown_frequency:
        timeline.notes.append(
            "Servis sıklığı bilinmediği için şu bacaklarda kalkış beklemesi hesaba "
            f"katılmadı: {', '.join(unknown_frequency)}. Gerçek süre daha uzun olabilir."
        )
    timeline.notes.append(
        "Aktarma süreleri sektör tipik değerleridir, ölçüm değildir. Gümrük ve sınır "
        "bekleme süreleri hiç dâhil değildir."
    )
    return timeline


def _transit_hours(leg, service) -> tuple[float, bool]:
    """Hours for one leg, and whether that figure is an estimate."""
    if leg.mode == "road":
        return road_elapsed_hours(leg.distance_km), True
    if service is not None and service.transit_hours is not None:
        return service.transit_hours, False
    speed = SEA_SPEED_KMH if leg.mode == "sea" else RAIL_SPEED_KMH
    return leg.distance_km / speed, True
