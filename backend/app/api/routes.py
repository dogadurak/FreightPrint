import requests
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from ..core.emissions import (
    FactorNotFoundError,
    calculate_shipment,
    load_emission_factors,
    load_tree_factors,
    lowest_emission_first,
    tree_equivalent,
)
from ..core.network import build_network, load_terminals
from ..core.report import ReportInputError, build_report, parse_shipments, report_to_csv
from ..core.road import RoadRoutingError
from ..core.route import find_route_alternatives
from ..core.uncertainty import load_band, round_to_significant, simulate_emission_range
from .schemas import (
    AlternativeOut,
    FactorSetOut,
    LegOut,
    RangeOut,
    RouteRequest,
    RouteResponse,
    TerminalOut,
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
