import argparse
import sys

import requests

from .core.emissions import (
    DEFAULT_FACTOR_SET,
    FactorNotFoundError,
    calculate_shipment,
    load_emission_factors,
    load_tree_factors,
    lowest_emission_first,
    tree_equivalent,
)
from .core.road import RoadRoutingError
from .core.route import find_route_alternatives
from .core.uncertainty import load_band, round_to_significant, simulate_emission_range


def _parse_point(value: str) -> tuple[float, float]:
    try:
        lon, lat = (float(part) for part in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected 'lon,lat', got {value!r}") from None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise argparse.ArgumentTypeError(f"{value!r} is not a valid lon,lat pair")
    return (lon, lat)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FreightPrint multimodal route and carbon engine")
    # Not required at the parser level: --list-fuels answers a question about the factor
    # file and has no route to ask about. main() enforces them for every other run.
    parser.add_argument("--origin", type=_parse_point, help="lon,lat")
    parser.add_argument("--destination", type=_parse_point, help="lon,lat")
    parser.add_argument("--origin-name", default="origin")
    parser.add_argument("--destination-name", default="destination")
    parser.add_argument("--tonnage", type=float, default=24.0)
    parser.add_argument("--scope", default="TTW", choices=["TTW", "WTW"])
    parser.add_argument(
        "--factor-set",
        # From the engine rather than spelled again here: two copies of a default are two
        # places to forget, and this one had already drifted from the engine's.
        default=DEFAULT_FACTOR_SET,
        help=f"which factor set to price with, e.g. glec or reference "
             f"(default: {DEFAULT_FACTOR_SET})",
    )
    parser.add_argument(
        "--fuel",
        default=None,
        help="road fuel type, e.g. diesel_b5, hvo_uco, electric_tr. Omit for the "
             "set's default. --list-fuels prints what the chosen set offers.",
    )
    parser.add_argument(
        "--list-fuels",
        action="store_true",
        help="print the road fuels the chosen factor set can price, and exit",
    )
    parser.add_argument(
        "--load-factor",
        type=float,
        default=None,
        help="0-1 vehicle utilisation; defaults to each factor's published basis",
    )
    parser.add_argument(
        "--empty-return",
        type=float,
        default=None,
        help="empty return share 0-1; defaults to each factor's published basis",
    )
    parser.add_argument(
        "--load-uncertainty",
        type=float,
        default=0.0,
        help="how far below --load-factor utilisation may fall, 0-1",
    )
    parser.add_argument(
        # Omitted means the per-mode table, not a flat figure. Defaulting to 0.05 here
        # silently overrode data/distance_uncertainty.csv, which is the only place that
        # knows road is measured and rail is not.
        "--distance-uncertainty", type=float, default=None,
        help="relative distance error 0-1; omit for the per-mode table "
             "(data/distance_uncertainty.csv)",
    )
    parser.add_argument("--alternatives", type=int, default=3)
    parser.add_argument(
        "--compare-computed",
        action="store_true",
        help="also compute sea-leg distances with searoute for comparison",
    )
    return parser


def _print_route(shipment, route, tree_factors) -> None:
    print(f"\n=== {shipment.label} - {route.total_distance_km:,.0f} km")

    for leg in shipment.legs:
        connection = f"{leg.mode:<5} {leg.from_name} -> {leg.to_name}"
        # Rounded the same way the total below is, and the same way the CSV report and
        # the API round every figure they publish. Printing a leg at full precision
        # under a total cut to three significant figures made an all-road route — one
        # leg, one total — show 4,527 above 4,530 and claim more precision for the part
        # than for the whole.
        co2 = round_to_significant(leg.co2_kg)
        print(f"    {connection:<44} {leg.distance_km:>8,.0f} km {co2:>10,.0f} kg CO2")

    total = round_to_significant(shipment.total_co2_kg)
    print(f"    {'TOPLAM':<44} {route.total_distance_km:>8,.0f} km {total:>10,.0f} kg CO2")

    if shipment.saving_co2_kg is not None and not route.is_all_road:
        baseline = round_to_significant(shipment.all_road_co2_kg)
        saving = round_to_significant(shipment.saving_co2_kg)
        print(f"    {'tam karayolu senaryosu':<44} {'':>11} {baseline:>10,.0f} kg CO2")
        print(f"    {'tasarruf':<44} {'':>11} {saving:>10,.0f} kg CO2")
        for species, count in tree_equivalent(shipment.saving_co2_kg, tree_factors).items():
            print(f"      {species:<42} {'':>11} {count:>10,.0f} agac/yil")


def _print_fuels(factor_set: str) -> None:
    """List what the chosen set can price, so the names never have to be guessed.

    They are not guessable: the rows are `diesel_b5` and `electric_tr`, so a reasonable
    guess at `diesel` or `electric` is an error.
    """
    factors = load_emission_factors()
    road = [f for f in factors if f.factor_set == factor_set and f.mode == "road"]
    if not road:
        sys.exit(f"'{factor_set}' setinde karayolu yakiti yok")

    print(f"{factor_set} setinin karayolu yakitlari:\n")
    for fuel_type in sorted({f.fuel_type for f in road}):
        same = [f for f in road if f.fuel_type == fuel_type]
        scopes = ", ".join(f"{f.scope} {f.value}" for f in sorted(same, key=lambda x: x.scope))
        marks = []
        if any(f.is_default for f in same):
            marks.append("varsayilan")
        if not all(f.is_verified for f in same):
            marks.append("turetme")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        print(f"  {fuel_type:<15} {scopes}{suffix}")


def main() -> None:
    args = _build_parser().parse_args()

    if args.list_fuels:
        _print_fuels(args.factor_set)
        return
    if args.origin is None or args.destination is None:
        sys.exit("--origin ve --destination gerekli (yalnız --list-fuels için değil)")

    try:
        routes = find_route_alternatives(
            origin=args.origin,
            destination=args.destination,
            origin_name=args.origin_name,
            destination_name=args.destination_name,
            compare_computed_distances=args.compare_computed,
        )
    except RoadRoutingError as error:
        sys.exit(f"Rota bulunamadi: {error}")
    except requests.RequestException as error:
        sys.exit(f"Karayolu rotalama servisine ulasilamadi: {error}")

    try:
        # Price the point estimate at the middle of the same band the range explores.
        band = load_band(args.load_factor, args.load_uncertainty) if args.load_factor else None
        expected_load = sum(band) / 2 if band else None

        shipments = calculate_shipment(
            routes,
            tonnage=args.tonnage,
            scope=args.scope,
            road_fuel_type=args.fuel,
            factor_set=args.factor_set,
            load_factor=expected_load,
            empty_return_share=args.empty_return,
        )
    except FactorNotFoundError as error:
        sys.exit(f"Emisyon faktoru bulunamadi: {error}")
    except ValueError as error:
        sys.exit(f"Gecersiz parametre: {error}")

    tree_factors = load_tree_factors()
    sources = {
        f.source
        for f in load_emission_factors()
        if f.factor_set == args.factor_set and f.scope == args.scope and f.is_verified
    }

    doluluk = f"{band[0]:.2f}-{band[1]:.2f}" if band else "fakt. yayin bazi"
    bos = f"{args.empty_return:g}" if args.empty_return is not None else "fakt. yayin bazi"
    print(f"Sevkiyat: {args.tonnage:g} ton | doluluk: {doluluk} | bos donus: {bos}")
    print(f"Faktor seti: {args.factor_set} | kapsam: {args.scope} | "
          f"kaynak: {'; '.join(sorted(sources)) or 'DOGRULANMAMIS'}")

    for route, shipment in lowest_emission_first(routes, shipments, limit=args.alternatives):
        _print_route(shipment, route, tree_factors)

        try:
            emission_range = simulate_emission_range(
                route,
                tonnage=args.tonnage,
                scope=args.scope,
                road_fuel_type=args.fuel,
                factor_set=args.factor_set,
                load_factor=args.load_factor,
                load_uncertainty=args.load_uncertainty,
                distance_uncertainty=args.distance_uncertainty,
                empty_return_share=args.empty_return,
                seed=0,
            )
        except ValueError as error:
            sys.exit(f"Gecersiz parametre: {error}")

        low, high = emission_range.rounded()
        confidence = f"%{emission_range.confidence * 100:g} guven"
        print(f"    belirsizlik araligi ({confidence}): {low:,.0f} - {high:,.0f} kg CO2")

    warnings = {warning for shipment in shipments for warning in shipment.warnings}
    for warning in sorted(warnings):
        print(f"\n! {warning}")


if __name__ == "__main__":
    main()
