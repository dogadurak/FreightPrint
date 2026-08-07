import requests
from fastapi import APIRouter, HTTPException

from ..core.emissions import (
    FactorNotFoundError,
    calculate_shipment,
    load_emission_factors,
    load_tree_factors,
    lowest_emission_first,
    tree_equivalent,
)
from ..core.network import build_network, load_terminals
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


def _leg_out(leg_emission, route_leg) -> LegOut:
    return LegOut(
        mode=leg_emission.mode,
        from_name=leg_emission.from_name,
        to_name=leg_emission.to_name,
        distance_km=round(leg_emission.distance_km, 1),
        co2_kg=round_to_significant(leg_emission.co2_kg),
        duration_h=round(route_leg.duration_h, 2) if route_leg and route_leg.duration_h else None,
        factor_value=leg_emission.factor.value,
        factor_source=leg_emission.factor.source,
        geometry=[list(point) for point in (route_leg.geometry if route_leg else ())],
    )


def _match_route_leg(route, leg_emission, used: set[int]):
    """Pair a priced leg back to the route leg it came from.

    A road leg carrying a ferry becomes two priced legs, so the pairing is by name and
    mode rather than position, and each route leg is only claimed once.
    """
    for index, leg in enumerate(route.legs):
        if index in used:
            continue
        if leg.mode == leg_emission.mode or (leg.mode == "road" and leg.ferry_km > 0):
            used.add(index)
            return leg
    return None


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
        used: set[int] = set()
        legs = [_leg_out(leg, _match_route_leg(route, leg, used)) for leg in shipment.legs]

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
