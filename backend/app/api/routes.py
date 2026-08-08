import requests
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from ..core.emissions import (
    FactorNotFoundError,
    calculate_route_emission,
    calculate_shipment,
    load_emission_factors,
    load_tree_factors,
    lowest_emission_first,
    tree_equivalent,
)
from ..core.cost import CostInputError, calculate_ets, compare_reroute
from ..core.geocode import GeocodingError, search
from ..core.jobs import DEFAULT_CONCURRENCY, registry
from ..core.network import build_network, load_terminals
from ..core.report import ReportInputError, build_report, parse_shipments, report_to_csv
from ..core.risk import assess_route, load_risk_zones
from ..core.reefer import ReeferFactorError, calculate_reefer
from ..core.schedule import build_timeline
from ..core.road import RoadRoutingError
from ..core.route import Leg, RouteAlternative, find_route_alternatives
from ..core.sea import BLOCKABLE_PASSAGES, DEFAULT_RESTRICTIONS, SeaRoutingError, sea_route
from ..core.uncertainty import load_band, round_to_significant, simulate_emission_range
from .schemas import (
    AlternativeOut,
    CompareRequest,
    CompareResponse,
    EtsCostOut,
    EtsLegOut,
    FactorSetOut,
    JobOut,
    PlaceOut,
    LegOut,
    RangeOut,
    ReeferOut,
    RouteRequest,
    RouteResponse,
    RouteRiskOut,
    SailingOut,
    ScheduleStepOut,
    ScenarioOut,
    ScenarioTotalOut,
    TerminalOut,
    TimelineOut,
    ZoneCrossingOut,
)

router = APIRouter(prefix="/api")

FACTOR_SET_DESCRIPTIONS = {
    "reference": "Customer report's own values, for comparison only",
    "glec": "GLEC Framework, unaccompanied ro-ro (trailer only)",
    "glec_accompanied": "GLEC Framework, accompanied ro-ro (tractor and driver travel)",
    "glec_freight_average": "GLEC Framework, ro-ro freight-only fleet average",
    "placeholder": "Unverified values; not for reporting",
}


@router.get("/terminals", response_model=list[TerminalOut])
def list_terminals() -> list[TerminalOut]:
    terminals = load_terminals()
    graph = build_network(terminals)
    return [
        TerminalOut(
            id=terminal.id,
            name=terminal.name,
            country=terminal.country,
            type=terminal.type,
            lon=terminal.coords[0],
            lat=terminal.coords[1],
            is_connected=graph.degree(terminal.id) > 0,
        )
        for terminal in terminals.values()
    ]


@router.get("/factor-sets", response_model=list[FactorSetOut])
def list_factor_sets() -> list[FactorSetOut]:
    """What the caller may price with, and what each choice implies for the sea leg."""
    factors = load_emission_factors()
    names = sorted({factor.factor_set for factor in factors})

    sets = []
    for name in names:
        rows = [f for f in factors if f.factor_set == name]
        sea = {f.scope: f.value for f in rows if f.mode == "sea"}
        sets.append(
            FactorSetOut(
                name=name,
                scopes=sorted({f.scope for f in rows}),
                sea_factor_by_scope=sea,
                source=sorted({f.source for f in rows})[0],
                is_verified=all(f.is_verified for f in rows),
                description=FACTOR_SET_DESCRIPTIONS.get(name, ""),
            )
        )
    return sets


@router.get("/risk-zones")
def risk_zones() -> dict:
    """The listed areas as GeoJSON, so the map can show where the exposure is.

    Served rather than bundled into the front end: the zones are reissued whenever the
    JWC updates its list, and a copy in the browser would drift from the one the
    intersection actually uses.
    """
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": zone.id,
                    "name": zone.name,
                    "zone_type": zone.zone_type,
                    "source": zone.source,
                    "valid_from": zone.valid_from,
                    "chokepoints": list(zone.chokepoints),
                },
                "geometry": zone.geometry.__geo_interface__,
            }
            for zone in load_risk_zones()
        ],
    }


def _leg_out(leg) -> LegOut:
    """Duration and geometry come straight off the priced leg, not matched back to the
    route: a ferry split makes two priced legs from one route leg, and re-pairing them
    by mode handed the ferry the sea leg's figures."""
    return LegOut(
        mode=leg.mode,
        from_name=leg.from_name,
        to_name=leg.to_name,
        distance_km=round(leg.distance_km, 1),
        co2_kg=round_to_significant(leg.co2_kg),
        duration_h=round(leg.duration_h, 2) if leg.duration_h else None,
        factor_value=leg.factor.value,
        factor_source=leg.factor.source,
        geometry=[list(point) for point in leg.geometry],
        track_is_indicative=leg.track_is_indicative,
    )


def _risk_out(route) -> RouteRiskOut:
    risk = assess_route(route)
    return RouteRiskOut(
        is_exposed=risk.is_exposed,
        distance_in_zones_km=round(risk.distance_in_zones_km, 1),
        zones=[
            ZoneCrossingOut(
                id=crossing.zone.id,
                name=crossing.zone.name,
                source=crossing.zone.source,
                distance_km=round(crossing.distance_km, 1),
            )
            for crossing in risk.crossings
        ],
        passages=risk.passages,
        untracked_sea_km=round(risk.untracked_sea_km, 1),
    )


def _timeline_out(route) -> TimelineOut:
    timeline = build_timeline(route)
    return TimelineOut(
        total_hours=round(timeline.total_hours, 1),
        total_days=round(timeline.total_days, 2),
        hours_by_kind={k: round(v, 1) for k, v in timeline.hours_by_kind.items()},
        steps=[
            ScheduleStepOut(
                kind=step.kind, mode=step.mode, label=step.label,
                hours=round(step.hours, 1), start_h=round(step.start_h, 1),
                is_estimated=step.is_estimated,
            )
            for step in timeline.steps
        ],
        notes=timeline.notes,
    )


def _reefer_out(route, request: RouteRequest) -> ReeferOut | None:
    """Refrigeration for one route, or nothing when the cargo is not refrigerated.

    Costs no routing call: it reads the timeline the route already carries.
    """
    if not request.is_reefer:
        return None
    try:
        emission = calculate_reefer(build_timeline(route), tonnage=request.tonnage)
    except (ReeferFactorError, ValueError):
        return None
    return ReeferOut(
        co2_kg=round_to_significant(emission.co2_kg),
        stationary_co2_kg=round_to_significant(emission.stationary_co2_kg),
        co2_by_kind={k: round_to_significant(v) for k, v in emission.co2_by_kind.items()},
        hours=round(emission.total_hours, 1),
        source=emission.factor.source,
        is_verified=emission.factor.is_verified,
        warnings=emission.warnings,
    )


def _leg_countries(route, terminals) -> list[tuple[str | None, str | None]]:
    """Country pair per priced leg, in the order `expand_route_legs` produces them.

    A ferry inside a road leg becomes a second, sea-mode leg, so the list is built the
    same way the pricing is rather than from `route.legs` directly.
    """
    pairs: list[tuple[str | None, str | None]] = []
    country = lambda node: terminals[node].country if node in terminals else None
    for leg in route.legs:
        if leg.mode == "road" and leg.ferry_km > 0:
            pairs.append((country(leg.from_id), country(leg.to_id)))
        pairs.append((country(leg.from_id), country(leg.to_id)))
    return pairs


def _ets_out(shipment, route, terminals, request: RouteRequest) -> EtsCostOut | None:
    try:
        cost = calculate_ets(
            shipment,
            _leg_countries(route, terminals),
            carbon_price_eur=request.carbon_price_eur,
            year=request.ets_year,
        )
    except CostInputError:
        return None
    return EtsCostOut(
        carbon_price_eur=cost.carbon_price_eur,
        year=cost.year,
        covered_tonnes=round(cost.covered_tonnes, 3),
        cost_eur=round(cost.cost_eur, 2),
        legs=[
            EtsLegOut(
                from_name=leg.from_name,
                to_name=leg.to_name,
                co2_kg=round_to_significant(leg.co2_kg),
                coverage_share=leg.coverage_share,
                cost_eur=round(leg.cost_eur, 2),
            )
            for leg in cost.legs
        ],
        notes=cost.notes,
    )


def _price_scenario(routes, request: RouteRequest, scenario, factors, terminals) -> ScenarioOut:
    """Reprice already-routed alternatives. No OSRM call, so this is effectively free.

    A scenario that cannot be priced is returned carrying its error rather than
    dropped: the dashboard offers it as a choice, so it has to say why it is empty.
    """
    verified = [
        f for f in factors if f.factor_set == scenario.factor_set and f.scope == scenario.scope
    ]
    common = dict(
        tonnage=request.tonnage,
        scope=scenario.scope,
        road_fuel_type=request.road_fuel_type,
        factor_set=scenario.factor_set,
    )
    try:
        band = (
            load_band(request.load_factor, request.load_uncertainty)
            if request.load_factor is not None
            else None
        )
        priced = calculate_shipment(
            routes,
            load_factor=sum(band) / 2 if band else None,
            empty_return_share=request.empty_return_share,
            **common,
        )
    except (FactorNotFoundError, ValueError) as error:
        return ScenarioOut(
            factor_set=scenario.factor_set,
            scope=scenario.scope,
            sources=[],
            is_verified=False,
            totals=[],
            error=str(error),
        )

    totals = []
    for route, shipment in lowest_emission_first(routes, priced, limit=request.max_alternatives):
        emission_range = None
        try:
            simulated = simulate_emission_range(
                route,
                load_factor=request.load_factor,
                load_uncertainty=request.load_uncertainty,
                distance_uncertainty=request.distance_uncertainty,
                empty_return_share=request.empty_return_share,
                seed=0,
                **common,
            )
            low, high = simulated.rounded()
            emission_range = RangeOut(
                low_co2_kg=low, high_co2_kg=high, confidence=simulated.confidence
            )
        except (FactorNotFoundError, ValueError):
            emission_range = None

        # Refrigeration rides on the clock, not the factor set, so it is identical
        # across scenarios — but it is repeated here so each scenario's total stands
        # on its own without the caller having to join it back to the alternative.
        reefer = _reefer_out(route, request)
        totals.append(
            ScenarioTotalOut(
                label=shipment.label,
                is_all_road=route.is_all_road,
                total_co2_kg=round_to_significant(shipment.total_co2_kg),
                co2_by_mode={k: round_to_significant(v) for k, v in shipment.co2_by_mode.items()},
                saving_co2_kg=(
                    round_to_significant(shipment.saving_co2_kg)
                    if shipment.saving_co2_kg is not None
                    else None
                ),
                emission_range=emission_range,
                # Allowance cost follows the emissions, so it belongs to the scenario;
                # risk follows the track and is reported once on the alternative.
                ets=_ets_out(shipment, route, terminals, request),
                reefer=reefer,
                total_with_reefer_co2_kg=(
                    round_to_significant(shipment.total_co2_kg + reefer.co2_kg)
                    if reefer
                    else None
                ),
            )
        )

    return ScenarioOut(
        factor_set=scenario.factor_set,
        scope=scenario.scope,
        sources=sorted({f.source for f in verified if f.is_verified}),
        is_verified=bool(verified) and all(f.is_verified for f in verified),
        totals=totals,
        warnings=sorted({w for shipment in priced for w in shipment.warnings}),
    )


@router.post("/routes", response_model=RouteResponse)
def calculate_routes(request: RouteRequest) -> RouteResponse:
    try:
        routes = find_route_alternatives(
            origin=request.origin.as_tuple(),
            destination=request.destination.as_tuple(),
            origin_name=request.origin_name,
            destination_name=request.destination_name,
        )
    except RoadRoutingError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except requests.RequestException as error:
        raise HTTPException(status_code=503, detail=f"road routing unavailable: {error}") from error

    try:
        band = (
            load_band(request.load_factor, request.load_uncertainty)
            if request.load_factor is not None
            else None
        )
        expected_load = sum(band) / 2 if band else None

        shipments = calculate_shipment(
            routes,
            tonnage=request.tonnage,
            scope=request.scope,
            road_fuel_type=request.road_fuel_type,
            factor_set=request.factor_set,
            load_factor=expected_load,
            empty_return_share=request.empty_return_share,
        )
    except FactorNotFoundError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    tree_factors = load_tree_factors()
    terminals = load_terminals()
    ranked = lowest_emission_first(routes, shipments, limit=request.max_alternatives)

    alternatives = []
    for route, shipment in ranked:
        legs = [_leg_out(leg) for leg in shipment.legs]

        emission_range = None
        try:
            simulated = simulate_emission_range(
                route,
                tonnage=request.tonnage,
                scope=request.scope,
                road_fuel_type=request.road_fuel_type,
                factor_set=request.factor_set,
                load_factor=request.load_factor,
                load_uncertainty=request.load_uncertainty,
                distance_uncertainty=request.distance_uncertainty,
                empty_return_share=request.empty_return_share,
                seed=0,
            )
            low, high = simulated.rounded()
            emission_range = RangeOut(
                low_co2_kg=low, high_co2_kg=high, confidence=simulated.confidence
            )
        except (FactorNotFoundError, ValueError):
            emission_range = None

        saving = shipment.saving_co2_kg
        reefer = _reefer_out(route, request)
        alternatives.append(
            AlternativeOut(
                label=shipment.label,
                is_all_road=route.is_all_road,
                total_distance_km=round(route.total_distance_km, 1),
                total_co2_kg=round_to_significant(shipment.total_co2_kg),
                distance_by_mode={k: round(v, 1) for k, v in route.distance_by_mode.items()},
                co2_by_mode={
                    k: round_to_significant(v) for k, v in shipment.co2_by_mode.items()
                },
                all_road_co2_kg=(
                    round_to_significant(shipment.all_road_co2_kg)
                    if shipment.all_road_co2_kg is not None
                    else None
                ),
                saving_co2_kg=round_to_significant(saving) if saving is not None else None,
                tree_equivalent=(
                    {k: round(v) for k, v in tree_equivalent(saving, tree_factors).items()}
                    if saving is not None and not route.is_all_road
                    else {}
                ),
                legs=legs,
                emission_range=emission_range,
                risk=_risk_out(route),
                timeline=_timeline_out(route),
                reefer=reefer,
                total_with_reefer_co2_kg=(
                    round_to_significant(shipment.total_co2_kg + reefer.co2_kg)
                    if reefer
                    else None
                ),
                notes=route.notes,
            )
        )

    factors = load_emission_factors()
    sources = sorted(
        {
            f.source
            for f in factors
            if f.factor_set == request.factor_set and f.scope == request.scope and f.is_verified
        }
    )
    warnings = sorted({w for shipment in shipments for w in shipment.warnings})

    return RouteResponse(
        factor_set=request.factor_set,
        scope=request.scope,
        tonnage=request.tonnage,
        sources=sources,
        alternatives=alternatives,
        warnings=warnings,
        scenarios=[
            _price_scenario(routes, request, s, factors, terminals) for s in request.scenarios
        ],
    )


MAX_UPLOAD_BYTES = 2_000_000


@router.post("/report", response_class=PlainTextResponse)
def bulk_report(
    file: UploadFile = File(..., description="CSV of shipments"),
    scope: str = Form("TTW"),
    factor_set: str = Form("reference"),
    road_fuel_type: str | None = Form(None),
    load_factor: float | None = Form(None),
    empty_return_share: float | None = Form(None),
) -> PlainTextResponse:
    """Price a file of shipments and hand back the report as a downloadable CSV."""
    if scope not in {"TTW", "WTW"}:
        raise HTTPException(status_code=422, detail=f"scope must be TTW or WTW, got {scope!r}")

    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"file exceeds {MAX_UPLOAD_BYTES} bytes")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=422, detail=f"file must be UTF-8 text: {error}") from error

    try:
        shipments = parse_shipments(content)
    except ReportInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        report = build_report(
            shipments,
            scope=scope,
            factor_set=factor_set,
            road_fuel_type=road_fuel_type,
            load_factor=load_factor,
            empty_return_share=empty_return_share,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    # Every shipment failing means the settings are wrong, not the data.
    if shipments and not report.calculated:
        detail = report.rows[0].status if report.rows else "no shipment could be calculated"
        raise HTTPException(status_code=422, detail=detail)

    return PlainTextResponse(
        content=report_to_csv(report),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="freightprint-report.csv"'},
    )


def _sail(
    label: str,
    request: CompareRequest,
    restrictions: tuple[str, ...],
    factors,
) -> SailingOut:
    """Price one sailing of the voyage under a set of blocked passages."""
    try:
        track = sea_route(
            request.origin.as_tuple(), request.destination.as_tuple(), restrictions=restrictions
        )
    except SeaRoutingError as error:
        empty = RouteRiskOut(is_exposed=False, distance_in_zones_km=0.0)
        return SailingOut(
            label=label, distance_km=0, duration_h=None, co2_kg=0, ets_eur=0,
            risk=empty, unreachable=str(error),
        )

    leg = Leg(
        mode="sea",
        from_name=request.origin_name,
        to_name=request.destination_name,
        distance_km=track.distance_km,
        duration_h=track.duration_h,
        geometry=tuple((point[0], point[1]) for point in track.geometry),
        passages=tuple(track.passages),
    )
    route = RouteAlternative(legs=[leg], label=label)
    shipment = calculate_route_emission(
        route,
        tonnage=request.tonnage,
        scope=request.scope,
        factor_set=request.factor_set,
        factors=factors,
    )
    ets = calculate_ets(
        shipment,
        [(request.origin_country, request.destination_country)],
        carbon_price_eur=request.carbon_price_eur,
        year=request.ets_year,
    )
    return SailingOut(
        label=label,
        distance_km=round(track.distance_km, 1),
        duration_h=round(track.duration_h, 1) if track.duration_h else None,
        co2_kg=round_to_significant(shipment.total_co2_kg),
        ets_eur=round(ets.cost_eur, 2),
        risk=_risk_out(route),
        geometry=[list(point) for point in track.geometry],
    )


@router.post("/compare", response_model=CompareResponse)
def compare_sailings(request: CompareRequest) -> CompareResponse:
    """Compare a direct sailing with one that avoids a chokepoint.

    The surcharge is echoed back rather than derived: what this adds beside it is the
    diversion's cost in distance, time, CO2 and allowances, so the carrier's line item
    has something to be checked against.
    """
    unknown = [p for p in request.avoid if p not in BLOCKABLE_PASSAGES]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"cannot avoid {unknown}; blockable passages: {sorted(BLOCKABLE_PASSAGES)}",
        )

    factors = load_emission_factors()
    try:
        direct = _sail("doğrudan", request, DEFAULT_RESTRICTIONS, factors)
        diverted = _sail(
            "sapma", request, tuple(DEFAULT_RESTRICTIONS) + tuple(request.avoid), factors
        )
    except FactorNotFoundError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except CostInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if direct.unreachable:
        raise HTTPException(status_code=422, detail=direct.unreachable)

    # Some voyages cannot avoid the passage at all — the Black Sea needs the Bosporus.
    # That is an answer, not a failure, so it is returned with the differences left unset.
    if diverted.unreachable:
        return CompareResponse(
            factor_set=request.factor_set,
            scope=request.scope,
            tonnage=request.tonnage,
            avoided=request.avoid,
            direct=direct,
            diverted=diverted,
            surcharge_eur=request.surcharge_eur,
        )

    reroute = compare_reroute(
        direct_distance_km=direct.distance_km,
        direct_duration_h=direct.duration_h,
        direct_co2_kg=direct.co2_kg,
        direct_ets_eur=direct.ets_eur,
        diverted_distance_km=diverted.distance_km,
        diverted_duration_h=diverted.duration_h,
        diverted_co2_kg=diverted.co2_kg,
        diverted_ets_eur=diverted.ets_eur,
        surcharge_eur=request.surcharge_eur,
    )
    return CompareResponse(
        factor_set=request.factor_set,
        scope=request.scope,
        tonnage=request.tonnage,
        avoided=request.avoid,
        direct=direct,
        diverted=diverted,
        extra_distance_km=round(reroute.extra_distance_km, 1),
        extra_duration_h=(
            round(reroute.extra_duration_h, 1) if reroute.extra_duration_h is not None else None
        ),
        extra_co2_kg=round_to_significant(reroute.extra_co2_kg),
        extra_ets_eur=round(reroute.extra_ets_eur, 2),
        surcharge_eur=request.surcharge_eur,
        total_extra_eur=round(reroute.total_eur, 2),
        avoided_zone_km=round(
            direct.risk.distance_in_zones_km - diverted.risk.distance_in_zones_km, 1
        ),
    )


# A file this size finishes inside a request; anything larger has to become a job.
SYNCHRONOUS_ROW_LIMIT = 20


def _read_upload(file: UploadFile) -> str:
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"file exceeds {MAX_UPLOAD_BYTES} bytes")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=422, detail=f"file must be UTF-8 text: {error}") from error


def _job_out(job) -> JobOut:
    return JobOut(
        id=job.id, status=job.status, total=job.total, done=job.done,
        progress=round(job.progress, 3), filename=job.filename, error=job.error,
    )


@router.post("/report/jobs", response_model=JobOut, status_code=202)
def start_report_job(
    file: UploadFile = File(..., description="CSV of shipments"),
    scope: str = Form("TTW"),
    factor_set: str = Form("reference"),
    road_fuel_type: str | None = Form(None),
    load_factor: float | None = Form(None),
    empty_return_share: float | None = Form(None),
) -> JobOut:
    """Start a report in the background and hand back a handle to poll.

    A cold shipment costs about six seconds, so a few hundred rows outlives any request
    timeout. The file is parsed here so a malformed upload still fails immediately rather
    than a minute later inside a job.
    """
    if scope not in {"TTW", "WTW"}:
        raise HTTPException(status_code=422, detail=f"scope must be TTW or WTW, got {scope!r}")
    try:
        shipments = parse_shipments(_read_upload(file))
    except ReportInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    def work(job) -> str:
        report = build_report(
            shipments,
            scope=scope,
            factor_set=factor_set,
            road_fuel_type=road_fuel_type,
            load_factor=load_factor,
            empty_return_share=empty_return_share,
            concurrency=DEFAULT_CONCURRENCY,
            on_progress=lambda done: setattr(job, "done", done),
        )
        if not report.calculated:
            raise RuntimeError(report.rows[0].status if report.rows else "nothing calculated")
        return report_to_csv(report)

    return _job_out(registry().submit(len(shipments), work, "freightprint-rapor.csv"))


@router.get("/report/jobs/{job_id}", response_model=JobOut)
def report_job_status(job_id: str) -> JobOut:
    job = registry().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return _job_out(job)


@router.get("/report/jobs/{job_id}/file", response_class=PlainTextResponse)
def report_job_file(job_id: str) -> PlainTextResponse:
    job = registry().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    if job.status == "failed":
        raise HTTPException(status_code=422, detail=job.error or "job failed")
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"job is {job.status}, not done")
    return PlainTextResponse(
        content=job.result,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{job.filename}"'},
    )


@router.get("/places", response_model=list[PlaceOut])
def find_places(q: str, country: str | None = None, limit: int = 5) -> list[PlaceOut]:
    """Places a name could mean, for the caller to choose between.

    Deliberately a list. Resolving a name to one point behind the user's back is how a
    shipment ends up in the wrong province, and the difference does not announce itself:
    two readings of one name in the validation set differ by seven points of route
    distance, and both look perfectly ordinary on screen.
    """
    if not q or not q.strip():
        raise HTTPException(status_code=422, detail="q must not be empty")
    try:
        candidates = search(q, country=country, limit=limit)
    except GeocodingError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except requests.RequestException as error:
        raise HTTPException(
            status_code=503, detail=f"geocoding service unavailable: {error}"
        ) from error
    return [
        PlaceOut(name=c.name, lon=round(c.lon, 5), lat=round(c.lat, 5), kind=c.kind)
        for c in candidates
    ]
