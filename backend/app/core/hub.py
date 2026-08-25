"""Where a consolidation hub belongs, and what opening one would be worth.

This is the question the empty-running module leads to. That one says vehicles pile up
at one end of a corridor; this one asks whether there is a point on the map that would
stop them travelling half-loaded in the first place.

**The objective is vehicle-kilometres, not tonne-kilometres, and that choice is the
whole model.** On tonne-kilometres a hub can never help: going via anywhere is at least
as far as going direct, so the triangle inequality makes every hub a loss and the
optimiser would open none. The benefit of consolidation is not a shorter path. It is
that eight part-loads heading the same way stop being eight vehicles and become three —
the trunk leg is *shared*, and only a model that counts vehicles can see it.

So the cost of a plan is:

    collection   ceil(tonnes / capacity) vehicles from each origin to its hub
    trunk        ceil(total tonnes / capacity) vehicles from the hub onward, once
    direct       ceil(tonnes / capacity) vehicles for anything not worth consolidating

The ceilings are what make this an integer program rather than arithmetic, and they are
also where the saving comes from, so they are not smoothed away. CBC solves it exactly
at this size; `is_optimal` says whether it did, and a heuristic answer is never
presented as a proven one.

**What it cannot see.** Consolidation needs the shipments to be at the hub at the same
time, and a shipment file carries no dates — the same gap the backhaul module has. So
the honest reading of every figure here is conditional: *if these loads could be held
to travel together, a hub here would be worth this much*. On a lane running weekly that
is a reasonable if; on one shipment a quarter it is not, and nothing in the data can
tell the two apart. `notes` says so, and `shipments_per_hub` is there to be looked at
before anyone believes the number.
"""

from dataclasses import dataclass, field
from math import ceil

import pulp
import requests

from . import route as routing
from .emissions import DEFAULT_FACTOR_SET, DEFAULT_SCOPE, find_factor, load_emission_factors
from .network import haversine_km, load_terminals
from .report import ShipmentRow
from .road import RoadRoutingError

# A 40-tonne artic's payload, the vehicle the GLEC road factor describes. Consolidation
# is a question about filling this, so it is the unit the whole model counts in.
VEHICLE_CAPACITY_TONNES = 24.0

# Solving is cheap; building the distance matrix is not, and it is quadratic in the
# candidates. Beyond this the caller is told to narrow the search rather than left
# waiting on a request that will not finish.
MAX_SHIPMENTS = 200
MAX_CANDIDATES = 40


class HubModelError(ValueError):
    pass


@dataclass(frozen=True)
class Site:
    """A place a hub could go."""

    id: str
    name: str
    point: tuple[float, float]


@dataclass
class Assignment:
    reference: str
    lane: str
    tonnes: float
    hub_id: str | None
    hub_name: str
    direct_vehicle_km: float
    collection_vehicle_km: float

    @property
    def is_consolidated(self) -> bool:
        return self.hub_id is not None


@dataclass
class HubPlan:
    opened: list[Site] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    direct_vehicle_km: float = 0.0
    planned_vehicle_km: float = 0.0
    is_optimal: bool = False
    capacity_tonnes: float = VEHICLE_CAPACITY_TONNES
    co2_per_vehicle_km: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def saved_vehicle_km(self) -> float:
        return self.direct_vehicle_km - self.planned_vehicle_km

    @property
    def saved_share(self) -> float:
        return self.saved_vehicle_km / self.direct_vehicle_km if self.direct_vehicle_km else 0.0

    @property
    def saved_co2_kg(self) -> float | None:
        """Only where a factor was available; never a zero standing in for unknown."""
        if self.co2_per_vehicle_km is None:
            return None
        return self.saved_vehicle_km * self.co2_per_vehicle_km

    @property
    def shipments_per_hub(self) -> dict[str, int]:
        """How much traffic each opened hub is carrying the argument on.

        A hub justified by two shipments is a hub justified by nothing, and the total
        saving does not show that on its own.
        """
        counts: dict[str, int] = {}
        for item in self.assignments:
            if item.hub_id:
                counts[item.hub_id] = counts.get(item.hub_id, 0) + 1
        return counts


def candidate_sites(shipments: list[ShipmentRow], include_origins: bool = True) -> list[Site]:
    """Where a hub could go: the network's terminals, and optionally the origins.

    Terminals because they exist and are already connected; origins because a hub is
    often best placed where the freight already is, and excluding them would rule out
    the answer a planner would reach for first.
    """
    sites = [
        Site(id=terminal.id, name=terminal.name, point=terminal.coords)
        for terminal in load_terminals().values()
    ]
    if include_origins:
        seen = {site.point for site in sites}
        for shipment in shipments:
            if shipment.origin not in seen:
                seen.add(shipment.origin)
                sites.append(
                    Site(id=f"origin:{shipment.origin_name}",
                         name=shipment.origin_name, point=shipment.origin)
                )
    return sites


def _solver():
    """The first solver this installation has, quietly.

    Naming one couples the module to a PuLP version; `listSolvers` is the question
    actually being asked. An install with none is a broken install and says so here
    rather than at the bottom of a stack trace.
    """
    available = pulp.listSolvers(onlyAvailable=True)
    if not available:
        raise HubModelError(
            "kurulu bir MIP çözücüsü yok; `pip install pulp[cbc]` gerekiyor"
        )
    return pulp.getSolver(available[0], msg=False)


def _vehicles(tonnes: float, capacity: float) -> int:
    return max(1, ceil(tonnes / capacity))


def _road_km(origin, destination) -> float:
    """Road distance, falling back to a straight line rather than failing the plan.

    A missing road answer would otherwise drop a candidate silently, which changes the
    optimum without saying so. The straight line under-reads, so it makes a site look
    slightly better than it is — recorded in `notes` rather than hidden.
    """
    try:
        return routing.road_route(origin, destination).distance_km
    except (RoadRoutingError, requests.RequestException):
        return haversine_km(origin, destination)


def plan_hubs(
    shipments: list[ShipmentRow],
    sites: list[Site] | None = None,
    max_hubs: int = 1,
    capacity_tonnes: float = VEHICLE_CAPACITY_TONNES,
    scope: str = DEFAULT_SCOPE,
    factor_set: str = DEFAULT_FACTOR_SET,
) -> HubPlan:
    """Choose where to open hubs so the fleet drives the fewest vehicle-kilometres.

    Every shipment may go direct or through one open hub. The collection leg is priced
    per shipment; the trunk leg is priced once per (hub, destination) pair on the
    combined flow, which is the only place consolidation can pay.
    """
    if not shipments:
        raise HubModelError("plan edilecek sevkiyat yok")
    if len(shipments) > MAX_SHIPMENTS:
        raise HubModelError(
            f"{len(shipments)} sevkiyat, tesis yeri modeli için fazla (en fazla {MAX_SHIPMENTS})"
        )
    if max_hubs < 1:
        raise HubModelError(f"en az bir merkez açılabilmeli, {max_hubs} istendi")

    sites = sites if sites is not None else candidate_sites(shipments)
    if len(sites) > MAX_CANDIDATES:
        raise HubModelError(
            f"{len(sites)} aday nokta, mesafe matrisi için fazla (en fazla {MAX_CANDIDATES})"
        )

    plan = HubPlan(capacity_tonnes=capacity_tonnes)
    destinations = {s.destination for s in shipments}

    # Distances, computed once. Everything below is arithmetic on these.
    to_site = {
        (i, site.id): _road_km(shipment.origin, site.point)
        for i, shipment in enumerate(shipments)
        for site in sites
    }
    trunk = {
        (site.id, destination): _road_km(site.point, destination)
        for site in sites
        for destination in destinations
    }
    direct = {i: _road_km(s.origin, s.destination) for i, s in enumerate(shipments)}

    trips = {i: _vehicles(s.tonnage, capacity_tonnes) for i, s in enumerate(shipments)}
    plan.direct_vehicle_km = sum(trips[i] * direct[i] for i in range(len(shipments)))

    problem = pulp.LpProblem("hub_location", pulp.LpMinimize)
    # `problem.add_variable_dicts` rather than the older `LpVariable.dicts`, which PuLP
    # removes in 4.0.
    open_site = problem.add_variable_dicts("open", [s.id for s in sites], cat="Binary")
    via = problem.add_variable_dicts(
        "via", [(i, s.id) for i in range(len(shipments)) for s in sites], cat="Binary"
    )
    go_direct = problem.add_variable_dicts("direct", range(len(shipments)), cat="Binary")
    # Integer, because half a lorry does not run. This is where the saving lives.
    trunk_vehicles = problem.add_variable_dicts(
        "trunk", [(s.id, d) for s in sites for d in destinations], lowBound=0, cat="Integer"
    )

    problem += (
        pulp.lpSum(via[(i, s.id)] * trips[i] * to_site[(i, s.id)]
                   for i in range(len(shipments)) for s in sites)
        + pulp.lpSum(trunk_vehicles[(s.id, d)] * trunk[(s.id, d)]
                     for s in sites for d in destinations)
        + pulp.lpSum(go_direct[i] * trips[i] * direct[i] for i in range(len(shipments)))
    )

    for i, shipment in enumerate(shipments):
        problem += go_direct[i] + pulp.lpSum(via[(i, s.id)] for s in sites) == 1
        for site in sites:
            problem += via[(i, site.id)] <= open_site[site.id]

    # The ceiling, expressed linearly: enough vehicles to carry the flow assigned. At
    # the optimum this binds, because every extra vehicle costs distance.
    for site in sites:
        for destination in destinations:
            flow = pulp.lpSum(
                via[(i, site.id)] * s.tonnage
                for i, s in enumerate(shipments)
                if s.destination == destination
            )
            problem += capacity_tonnes * trunk_vehicles[(site.id, destination)] >= flow

    problem += pulp.lpSum(open_site[s.id] for s in sites) <= max_hubs

    # Whichever solver this install actually has, rather than naming one: PuLP renames
    # its bundled CBC in 4.0, and a hardcoded name would not fail loudly — it would
    # raise on every request while the tests that pinned it still passed.
    status = problem.solve(_solver())
    plan.is_optimal = pulp.LpStatus[status] == "Optimal"
    if not plan.is_optimal:
        plan.notes.append(
            f"Çözücü en iyi çözümü kanıtlayamadı (durum: {pulp.LpStatus[status]}); "
            "aşağıdaki plan en iyi olmayabilir."
        )
        return plan

    plan.planned_vehicle_km = pulp.value(problem.objective)
    by_id = {site.id: site for site in sites}
    plan.opened = [by_id[s.id] for s in sites if open_site[s.id].value() > 0.5]

    for i, shipment in enumerate(shipments):
        chosen = next((s for s in sites if via[(i, s.id)].value() > 0.5), None)
        plan.assignments.append(
            Assignment(
                reference=shipment.reference,
                lane=f"{shipment.origin_name} → {shipment.destination_name}",
                tonnes=shipment.tonnage,
                hub_id=chosen.id if chosen else None,
                hub_name=chosen.name if chosen else "doğrudan",
                direct_vehicle_km=trips[i] * direct[i],
                collection_vehicle_km=trips[i] * to_site[(i, chosen.id)] if chosen else 0.0,
            )
        )

    try:
        factor = find_factor(
            load_emission_factors(), "road", scope=scope, factor_set=factor_set
        )
        # The factor is per tonne-kilometre at the publisher's own load assumption, so a
        # vehicle-kilometre is that factor times the payload it assumes. Stated rather
        # than folded in: it is the one step here that is not pure geometry.
        plan.co2_per_vehicle_km = factor.value * capacity_tonnes * factor.basis_load_factor
    except LookupError:
        plan.notes.append(
            f"{factor_set}/{scope} için karayolu faktörü bulunamadı; "
            "tasarruf yalnızca araç-km olarak verildi."
        )

    plan.notes.append(
        "Amaç fonksiyonu **araç-kilometredir**, ton-kilometre değil. Ton-km üzerinden bir "
        "merkez asla kazandırmaz — üçgen eşitsizliği gereği her sapma en az doğrudan yol "
        "kadar uzundur. Konsolidasyonun getirisi kısa yol değil, ana bacağın paylaşılmasıdır."
    )
    plan.notes.append(
        "Konsolidasyon, yüklerin merkezde aynı anda bulunmasını gerektirir; sevkiyat "
        "dosyasında tarih yoktur. Bu yüzden buradaki her rakam koşulludur: **yükler birlikte "
        "gidecek şekilde bekletilebilseydi** bir merkez şu kadar değerdi. Haftalık işleyen "
        "bir hatta bu makul bir kabul, çeyrekte bir sevkiyatta değildir."
    )
    plan.notes.append(
        f"Araç kapasitesi {capacity_tonnes:g} ton alındı; tam yük eşiği budur ve "
        "kaç aracın gerektiğini bu belirler."
    )
    return plan
