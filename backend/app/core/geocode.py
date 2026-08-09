import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .network import CACHE_DIR

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy: identify the caller and stay under one request per second.
USER_AGENT = "FreightPrint/0.1 (multimodal freight carbon research)"
MIN_REQUEST_INTERVAL_S = 1.1
REQUEST_TIMEOUT_S = 30

CACHE_PATH = CACHE_DIR / "geocode_cache.json"

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


# Upper-casing a Turkish dotted capital leaves it distinct from a plain I, which would
# file the same city under two cache keys.
CASE_FOLD = str.maketrans({"İ": "I", "ı": "i", "Ş": "S", "ş": "s", "Ğ": "G", "ğ": "g"})


class GeocodingError(RuntimeError):
    pass


def normalise_country(value: str) -> str:
    """Map the dataset's mixed country spellings onto ISO 3166-1 alpha-2 codes."""
    cleaned = value.strip()
    if alias := COUNTRY_ALIASES.get(cleaned.lower()):
        return alias
    if len(cleaned) != 2:
        raise GeocodingError(
            f"unknown country {value!r}; add it to COUNTRY_ALIASES rather than guessing"
        )
    return cleaned.upper()


def _cache_key(place: str, country_code: str) -> str:
    return f"{country_code}|{place.strip().translate(CASE_FOLD).upper()}"


def looks_like_postal_code(place: str) -> bool:
    """Postal codes must go to Nominatim's `postalcode` field, not its free-text search.

    Free-text search happily returns a same-named village hundreds of kilometres from
    the town a postal code actually denotes, and does so without any error.
    """
    return any(character.isdigit() for character in place)


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
    key = _cache_key(place, country_code)

    owns_cache = cache is None
    cache = _load_cache() if owns_cache else cache
    if key in cache:
        hit = cache[key]
        return tuple(hit) if hit else None

    query = {"postalcode" if looks_like_postal_code(place) else "q": place.strip()}
    response = requests.get(
        NOMINATIM_URL,
        params={
            **query,
            "countrycodes": country_code.lower(),
            "format": "json",
            "limit": 1,
        },
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


@dataclass(frozen=True)
class Candidate:
    """One place a search could have meant."""

    name: str
    lon: float
    lat: float
    kind: str
    importance: float


def search(query: str, country: str | None = None, limit: int = 5) -> list[Candidate]:
    """Return the places a name could mean, most likely first — never just the first.

    Silently taking the top hit is how a destination ends up in the wrong province: the
    validation set has a name matching several settlements whose readings differ by
    seven percentage points of route distance. The caller has to choose.
    """
    params = {
        "q": query.strip(),
        "format": "json",
        "limit": max(1, min(limit, 10)),
        "addressdetails": 1,
    }
    if country:
        params["countrycodes"] = normalise_country(country).lower()

    response = requests.get(
        NOMINATIM_URL, params=params, headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    results = response.json()
    time.sleep(MIN_REQUEST_INTERVAL_S)

    return [
        Candidate(
            name=hit.get("display_name", query),
            lon=float(hit["lon"]),
            lat=float(hit["lat"]),
            kind=hit.get("type", ""),
            importance=float(hit.get("importance", 0)),
        )
        for hit in results
    ]


def geocode_all(places: list[tuple[str, str]]) -> dict[tuple[str, str], tuple[float, float] | None]:
    """Geocode many (place, country) pairs, writing the cache once at the end."""
    cache = _load_cache()
    resolved = {pair: geocode(pair[0], pair[1], cache=cache) for pair in places}
    _save_cache(cache)
    return resolved
