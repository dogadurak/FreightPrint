import json
import time
from pathlib import Path

import requests

from .network import DATA_DIR

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy: identify the caller and stay under one request per second.
USER_AGENT = "FreightPrint/0.1 (multimodal freight carbon research)"
MIN_REQUEST_INTERVAL_S = 1.1
REQUEST_TIMEOUT_S = 30

CACHE_PATH = DATA_DIR / "geocode_cache.json"

# The validation dataset mixes ISO codes, English names and Turkish spellings.
COUNTRY_ALIASES = {
    "turkey": "TR",
    "greece": "GR",
    "italy": "IT",
    "poland": "PL",
    "romania": "RO",
    "bulgaria": "BG",
    "serbia": "RS",
    "kosovo": "XK",
    "macedonia": "MK",
    "montenegro": "ME",
    "bosna": "BA",
    "bosnia": "BA",
}


class GeocodingError(RuntimeError):
    pass


def normalise_country(value: str) -> str:
    """Map the dataset's mixed country spellings onto ISO 3166-1 alpha-2 codes."""
    cleaned = value.strip()
    return COUNTRY_ALIASES.get(cleaned.lower(), cleaned.upper())


def _load_cache() -> dict[str, list[float] | None]:
    if not CACHE_PATH.exists():
        return {}
    with open(CACHE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_cache(cache: dict) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)


def geocode(place: str, country: str, cache: dict | None = None) -> tuple[float, float] | None:
    """Resolve a place to (lon, lat), or None when Nominatim has no match.

    Results are cached on disk, misses included, so a rerun does not re-query the
    public service for places it already knows are unresolvable.
    """
    country_code = normalise_country(country)
    key = f"{country_code}|{place.strip().upper()}"

    owns_cache = cache is None
    cache = _load_cache() if owns_cache else cache
    if key in cache:
        hit = cache[key]
        return tuple(hit) if hit else None

    response = requests.get(
        NOMINATIM_URL,
        params={"q": place, "countrycodes": country_code.lower(), "format": "json", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    results = response.json()
    time.sleep(MIN_REQUEST_INTERVAL_S)

    coords = [float(results[0]["lon"]), float(results[0]["lat"])] if results else None
    cache[key] = coords
    if owns_cache:
        _save_cache(cache)
    return tuple(coords) if coords else None


def geocode_all(places: list[tuple[str, str]]) -> dict[tuple[str, str], tuple[float, float] | None]:
    """Geocode many (place, country) pairs, writing the cache once at the end."""
    cache = _load_cache()
    resolved = {pair: geocode(pair[0], pair[1], cache=cache) for pair in places}
    _save_cache(cache)
    return resolved
