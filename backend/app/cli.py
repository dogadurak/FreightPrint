import argparse
import sys

import requests

from .core.emissions import calculate_shipment, load_tree_factors, tree_equivalent
from .core.road import RoadRoutingError
from .core.route import find_route_alternatives
from .core.uncertainty import round_to_significant, simulate_emission_range


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
    parser.add_argument("--origin", required=True, type=_parse_point, help="lon,lat")
    parser.add_argument("--destination", required=True, type=_parse_point, help="lon,lat")
    parser.add_argument("--origin-name", default="origin")
    parser.add_argument("--destination-name", default="destination")
    parser.add_argument("--tonnage", type=float, default=24.0)
    parser.add_argument("--scope", default="TTW", choices=["TTW", "WTW"])
    parser.add_argument("--fuel", default=None, help="road fuel type, e.g. diesel, hvo, lng")
    parser.add_argument("--load-factor", type=float, default=1.0, help="0-1 vehicle utilisation")
    parser.add_argument("--empty-return", type=float, default=0.0, help="empty return share, 0-1")
    parser.add_argument(
        "--load-uncertainty",
        type=float,
        default=0.0,
        help="how far below --load-factor utilisation may fall, 0-1",
    )
    parser.add_argument(
        "--distance-uncertainty", type=float, default=0.05, help="relative distance error, 0-1"
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
        print(f"    {connection:<44} {leg.distance_km:>8,.0f} km {leg.co2_kg:>10,.0f} kg CO2")

    print(f"    {'TOPLAM':<44} {route.total_distance_km:>8,.0f} km "
          f"{shipment.total_co2_kg:>10,.0f} kg CO2")

    if shipment.saving_co2_kg is not None and not route.is_all_road:
        print(f"    {'tam karayolu senaryosu':<44} {'':>11} {shipment.all_road_co2_kg:>10,.0f} kg CO2")
        print(f"    {'tasarruf':<44} {'':>11} {shipment.saving_co2_kg:>10,.0f} kg CO2")
        for species, count in tree_equivalent(shipment.saving_co2_kg, tree_factors).items():
            print(f"      {species:<42} {'':>11} {count:>10,.0f} agac/yil")


def main() -> None:
    args = _build_parser().parse_args()

    try:
        routes = find_route_alternatives(
            origin=args.origin,
            destination=args.destination,
            origin_name=args.origin_name,
            destination_name=args.destination_name,
            max_alternatives=args.alternatives,
            compare_computed_distances=args.compare_computed,
        )
    except RoadRoutingError as error:
        sys.exit(f"Rota bulunamadi: {error}")
    except requests.RequestException as error:
        sys.exit(f"Karayolu rotalama servisine ulasilamadi: {error}")

    shipments = calculate_shipment(
        routes,
        tonnage=args.tonnage,
        scope=args.scope,
        road_fuel_type=args.fuel,
        load_factor=args.load_factor,
        empty_return_share=args.empty_return,
    )
    tree_factors = load_tree_factors()

    print(f"Sevkiyat: {args.tonnage:g} ton | kapsam: {args.scope} | "
          f"doluluk: {args.load_factor:g} | bos donus: {args.empty_return:g}")

    for shipment, route in zip(shipments, routes):
        _print_route(shipment, route, tree_factors)

        emission_range = simulate_emission_range(
            route,
            tonnage=args.tonnage,
            scope=args.scope,
            road_fuel_type=args.fuel,
            load_factor=args.load_factor,
            load_uncertainty=args.load_uncertainty,
            distance_uncertainty=args.distance_uncertainty,
            empty_return_share=args.empty_return,
            seed=0,
        )
        low, high = emission_range.rounded()
        confidence = f"%{emission_range.confidence * 100:g} guven"
        print(f"    belirsizlik araligi ({confidence}): {low:,.0f} - {high:,.0f} kg CO2")

    warnings = {warning for shipment in shipments for warning in shipment.warnings}
    for warning in sorted(warnings):
        print(f"\n! {warning}")


if __name__ == "__main__":
    main()
