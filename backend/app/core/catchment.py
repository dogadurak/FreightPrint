"""Which terminal serves where, measured by road rather than by straight line.

A terminal's catchment is usually drawn as a circle, which is wrong wherever geography
is: the Sea of Marmara, the Alps and the Bosphorus all put places close on a map hours
apart by road. This asks OSRM for the actual driving time from every terminal to every
point of a grid and keeps the nearest one.

The result is a **grid of samples, not a boundary**. Between two samples the answer is
unknown, and the map draws squares at the spacing that was measured rather than a smooth
polygon that would imply a precision nobody computed. Coarsening the grid makes it
cheaper and less certain, and the spacing travels with the answer so the caller can see
which they got.
"""

import math
import os
from dataclasses import dataclass, field

from .cache import DiskCache
from .network import Terminal, load_terminals
from .road import MAX_TABLE_COORDINATES, RoadRoutingError, table_durations

# One degree of latitude is ~111 km, so 1.0 samples about every 111 km north-south and
# less east-west as you go north. Coarse by default: a finer grid is a linear cost in
# OSRM requests and this is a planning view, not a routing answer.
DEFAULT_SPACING_DEG = 1.0
MIN_SPACING_DEG = 0.25

# Beyond this a terminal is not meaningfully "serving" a place, and colouring the whole
# map by whichever terminal is least far away would suggest otherwise.
DEFAULT_MAX_DURATION_H = 8.0

# The pilot corridor and its hinterland: Ireland to the Caucasus, Sicily to Denmark.
DEFAULT_BOUNDS = (-10.0, 35.0, 45.0, 58.0)

MAX_GRID_POINTS = int(os.environ.get("CATCHMENT_MAX_POINTS", "4000"))


@dataclass(frozen=True)
class CatchmentCell:
    lon: float
    lat: float
    terminal_id: str
    duration_h: float


@dataclass
class Catchment:
    cells: list[CatchmentCell]
    spacing_deg: float
    bounds: tuple[float, float, float, float]
    max_duration_h: float
    sampled: int
    unreachable: int
    notes: list[str] = field(default_factory=list)

    @property
    def terminal_ids(self) -> list[str]:
        return sorted({cell.terminal_id for cell in self.cells})

    def cells_by_terminal(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for cell in self.cells:
            counts[cell.terminal_id] = counts.get(cell.terminal_id, 0) + 1
        return counts


def grid_points(
    bounds: tuple[float, float, float, float], spacing_deg: float
) -> list[tuple[float, float]]:
    """Sample points across a bounding box, inclusive of both edges."""
    if spacing_deg < MIN_SPACING_DEG:
        raise ValueError(f"spacing {spacing_deg} is finer than the {MIN_SPACING_DEG} floor")
    west, south, east, north = bounds
    if east <= west or north <= south:
        raise ValueError(f"bounds {bounds} are empty or inverted")

    columns = int(math.floor((east - west) / spacing_deg)) + 1
    rows = int(math.floor((north - south) / spacing_deg)) + 1
    if columns * rows > MAX_GRID_POINTS:
        raise ValueError(
            f"{columns * rows:,} points exceeds the {MAX_GRID_POINTS:,} cap; "
            "coarsen the spacing or shrink the bounds"
        )
    return [
        (round(west + x * spacing_deg, 6), round(south + y * spacing_deg, 6))
        for y in range(rows)
        for x in range(columns)
    ]


def _batches(points: list[tuple[float, float]], size: int):
    for start in range(0, len(points), size):
        yield points[start : start + size]


def build_catchment(
    terminals: dict[str, Terminal] | None = None,
    bounds: tuple[float, float, float, float] = DEFAULT_BOUNDS,
    spacing_deg: float = DEFAULT_SPACING_DEG,
    max_duration_h: float = DEFAULT_MAX_DURATION_H,
    connected_only: bool = True,
    cache: DiskCache | None = None,
) -> Catchment:
    """Assign each grid point to the terminal that reaches it fastest by road.

    Terminals no service calls at are excluded by default. You can drive to Ambarlı, so
    a nearest-terminal map hands it a catchment; nothing sails from it, so that
    catchment is a place you can deliver to and then be stuck. Pass
    `connected_only=False` to map physical proximity regardless of service.
    """
    terminals = terminals if terminals is not None else load_terminals()
    if not terminals:
        raise ValueError("no terminals to build a catchment from")

    excluded: list[str] = []
    if connected_only:
        # Read the legs directly rather than through build_network: that validates the
        # whole service file against the terminals it is given, which is right for
        # routing and wrong here, where a caller may legitimately ask about a subset.
        from .network import load_service_legs

        served = {end for leg in load_service_legs() for end in (leg["from_terminal"], leg["to_terminal"])}
        servable = {tid: t for tid, t in terminals.items() if tid in served}
        excluded = sorted(set(terminals) - set(servable))
        if not servable:
            raise ValueError("no terminal has a service leg; nothing can be a catchment")
        terminals = servable

    ordered = sorted(terminals.values(), key=lambda t: t.id)
    sources = [t.coords for t in ordered]

    # Every source occupies a slot in the same request, so what is left is what the
    # destinations may use.
    per_batch = MAX_TABLE_COORDINATES - len(sources)
    if per_batch < 1:
        raise RoadRoutingError(
            f"{len(sources)} terminals fill the {MAX_TABLE_COORDINATES}-coordinate "
            "table limit on their own; raise OSRM_MAX_TABLE_SIZE on a self-hosted OSRM"
        )

    points = grid_points(bounds, spacing_deg)
    cache = cache if cache is not None else DiskCache()

    cells: list[CatchmentCell] = []
    unreachable = 0
    for batch in _batches(points, per_batch):
        key = (
            f"catch1|{','.join(t.id for t in ordered)}|{max_duration_h}|"
            f"{';'.join(f'{lon},{lat}' for lon, lat in batch)}"
        )
        durations = cache.get_or_compute(key, lambda: table_durations(sources, batch))

        for column, (lon, lat) in enumerate(batch):
            best_duration, best_index = None, None
            for index in range(len(sources)):
                value = durations[index][column]
                # None is "no route found", not "far away"; a point in open water has to
                # drop out rather than be handed to whichever terminal scored least bad.
                if value is None:
                    continue
                if best_duration is None or value < best_duration:
                    best_duration, best_index = value, index

            if best_duration is None or best_duration > max_duration_h:
                unreachable += 1
                continue
            cells.append(
                CatchmentCell(
                    lon=lon,
                    lat=lat,
                    terminal_id=ordered[best_index].id,
                    duration_h=round(best_duration, 2),
                )
            )

    notes = [
        f"{spacing_deg}° aralıklı örnekleme — hücreler ölçülen noktayı temsil eder, "
        "aralarındaki sınır hesaplanmadı.",
        f"{max_duration_h:.0f} saatten uzak noktalar hiçbir terminale atanmadı.",
    ]
    if unreachable:
        notes.append(
            f"{unreachable} nokta karayoluyla ulaşılamadı veya sınırın dışında kaldı "
            "(deniz, ada, menzil dışı)."
        )
    if excluded:
        notes.append(
            f"Hiçbir servisin uğramadığı terminal(ler) dışarıda bırakıldı: "
            f"{', '.join(excluded)}. Oraya sürebilirsiniz ama gemiye binemezsiniz."
        )

    return Catchment(
        cells=cells,
        spacing_deg=spacing_deg,
        bounds=bounds,
        max_duration_h=max_duration_h,
        sampled=len(points),
        unreachable=unreachable,
        notes=notes,
    )
