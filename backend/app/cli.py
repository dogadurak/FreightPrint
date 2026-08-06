import argparse
import sys

import requests

from .core.road import RoadRoutingError
from .core.route import find_route_alternatives


def _parse_point(value: str) -> tuple[float, float]:
    try:
        lon, lat = (float(part) for part in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected 'lon,lat', got {value!r}") from None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise argparse.ArgumentTypeError(f"{value!r} is not a valid lon,lat pair")
    return (lon, lat)


def main() -> None:
    parser = argparse.ArgumentParser(description="FreightPrint multimodal route engine")
    parser.add_argument("--origin", required=True, type=_parse_point, help="lon,lat")
    parser.add_argument("--destination", required=True, type=_parse_point, help="lon,lat")
    parser.add_argument("--origin-name", default="origin")
    parser.add_argument("--destination-name", default="destination")
    parser.add_argument("--alternatives", type=int, default=3)
    parser.add_argument(
        "--compare-computed",
        action="store_true",
        help="also compute sea-leg distances with searoute for comparison",
    )
    args = parser.parse_args()

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

    for route in routes:
        print(f"\n=== {route.label} — {route.total_distance_km:,.0f} km total")
        for mode, km in sorted(route.distance_by_mode.items()):
            print(f"    {mode:<5} {km:>8,.0f} km")
        print("    legs:")
        for leg in route.legs:
            computed = ""
            if leg.ref_distance_km and leg.computed_distance_km:
                deviation = (
                    (leg.computed_distance_km - leg.ref_distance_km) / leg.ref_distance_km * 100
                )
                computed = f"  (searoute: {leg.computed_distance_km:,.0f} km, {deviation:+.1f}%)"
            print(
                f"      {leg.mode:<5} {leg.from_name} -> {leg.to_name}: "
                f"{leg.distance_km:,.0f} km{computed}"
            )
        for note in route.notes:
            print(f"    ! {note}")


if __name__ == "__main__":
    main()
