"""Turning a customer's address column into coordinates, and being polite about it.

Coverage measured this module at 50% — the half that talks to Nominatim was the untested
half, which is also the half that can get a deployed instance blocked. Nominatim's usage
policy is one request a second from an identified caller, and a tool that breaks it does
not fail loudly; it just stops being served one day.
"""

import threading
import time

import pytest
import requests

# Bound before any fixture can stub `time.sleep` out, for the one test that needs a real
# window inside a request rather than an instant one.
REAL_SLEEP = time.sleep

from app.core import geocode as geocode_module
from app.core.geocode import (
    Candidate,
    GeocodingBusy,
    GeocodingError,
    _cache_key,
    geocode,
    geocode_all,
    looks_like_postal_code,
    normalise_country,
    rate_limited,
    search,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def idle_throttle(monkeypatch):
    """Start each test with the throttle idle and its sleep instant but recorded."""
    monkeypatch.setattr(geocode_module, "_earliest_next_request", 0.0)
    slept = []
    monkeypatch.setattr(geocode_module.time, "sleep", slept.append)
    return slept


@pytest.mark.parametrize(
    ("given", "expected"),
    [("turkey", "TR"), ("Turkey", "TR"), ("  bosnia ", "BA"), ("de", "DE"), ("It", "IT")],
)
def test_country_spellings_land_on_one_iso_code(given, expected):
    assert normalise_country(given) == expected


def test_an_unknown_country_is_refused_rather_than_guessed():
    """Guessing here puts a shipment in the wrong country and says nothing."""
    with pytest.raises(GeocodingError, match="COUNTRY_ALIASES"):
        normalise_country("Ruritania")


def test_turkish_capitals_do_not_split_one_city_across_two_cache_keys():
    """Upper-casing a dotted capital leaves it distinct from a plain I, so the same
    place would be looked up, paid for and stored twice."""
    assert _cache_key("İzmir", "TR") == _cache_key("izmir", "TR")
    assert _cache_key("Şanlıurfa", "TR") == _cache_key("sanliurfa", "TR")


@pytest.mark.parametrize("place", ["34500", "TR-34500", "1 Mayis"])
def test_anything_carrying_a_digit_is_treated_as_a_postal_code(place):
    assert looks_like_postal_code(place)


def test_a_postal_code_goes_to_the_postal_field_not_free_text(monkeypatch):
    """Free-text search returns a same-named village hundreds of kilometres away and
    reports no error at all."""
    sent = {}

    def fake_get(url, params=None, **kwargs):
        sent.update(params)
        return FakeResponse([{"lon": "29.0", "lat": "41.0"}])

    monkeypatch.setattr(geocode_module.requests, "get", fake_get)

    geocode("34500", "TR", cache={})

    assert sent["postalcode"] == "34500" and "q" not in sent


def test_a_place_name_goes_to_free_text(monkeypatch):
    sent = {}

    def fake_get(url, params=None, **kwargs):
        sent.update(params)
        return FakeResponse([{"lon": "29.0", "lat": "41.0"}])

    monkeypatch.setattr(geocode_module.requests, "get", fake_get)

    assert geocode("Gebze", "TR", cache={}) == (29.0, 41.0)
    assert sent["q"] == "Gebze" and "postalcode" not in sent


def test_a_miss_is_remembered_so_it_is_not_paid_for_twice(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append(params)
        return FakeResponse([])

    monkeypatch.setattr(geocode_module.requests, "get", fake_get)
    cache = {}

    assert geocode("Nowhere", "TR", cache=cache) is None
    assert geocode("Nowhere", "TR", cache=cache) is None

    assert len(calls) == 1, "an unresolvable place was queried twice"


def test_the_caller_identifies_itself(monkeypatch):
    """The policy asks for a real User-Agent; the default one gets you blocked."""
    headers = {}

    def fake_get(url, params=None, headers=None, **kwargs):
        headers_seen.update(headers or {})
        return FakeResponse([])

    headers_seen = headers
    monkeypatch.setattr(geocode_module.requests, "get", fake_get)

    geocode("Gebze", "TR", cache={})

    assert "FreightPrint" in headers_seen["User-Agent"]


def test_search_returns_every_candidate_rather_than_the_first(monkeypatch):
    """Silently taking the top hit is how a destination ends up in the wrong province."""
    monkeypatch.setattr(
        geocode_module.requests, "get",
        lambda *a, **k: FakeResponse([
            {"display_name": "Karaman, Karaman", "lon": "33.2", "lat": "37.2",
             "type": "city", "importance": 0.6},
            {"display_name": "Karaman, Bursa", "lon": "29.1", "lat": "40.2",
             "type": "village", "importance": 0.3},
        ]),
    )

    found = search("Karaman", country="TR")

    assert len(found) == 2
    assert all(isinstance(c, Candidate) for c in found)
    assert found[0].kind == "city" and found[1].kind == "village"


def test_the_throttle_waits_before_the_request_not_after(idle_throttle, monkeypatch):
    """Sleeping after the response charges this caller for a courtesy owed to the next
    one, so an idle service could never answer in under a second."""
    monkeypatch.setattr(geocode_module.requests, "get", lambda *a, **k: FakeResponse([]))

    search("Gebze")

    assert idle_throttle == [], "an idle geocoder made the first caller wait"

    search("Gebze")

    assert idle_throttle and idle_throttle[0] > 0, "the second caller did not wait at all"


def test_a_failed_request_still_spaces_the_next_one(idle_throttle, monkeypatch):
    """The old sleep sat after raise_for_status, so precisely the run that was erroring
    hammered the service unthrottled."""
    monkeypatch.setattr(
        geocode_module.requests, "get", lambda *a, **k: FakeResponse([], status=500)
    )

    with pytest.raises(requests.HTTPError):
        search("Gebze")

    assert geocode_module._earliest_next_request > time.monotonic(), (
        "a failing request left the throttle open"
    )


def test_concurrent_callers_take_turns(monkeypatch):
    """One request a second is a limit on the service, not on each thread separately."""
    overlapping = []
    concurrent = 0
    guard = threading.Lock()

    def fake_get(*args, **kwargs):
        nonlocal concurrent
        with guard:
            concurrent += 1
            overlapping.append(concurrent)
        # The real sleep, captured before the fixture stubbed it out: without a genuine
        # window inside the request the threads never get a chance to overlap and the
        # test would pass with no throttle at all.
        REAL_SLEEP(0.02)
        with guard:
            concurrent -= 1
        return FakeResponse([])

    monkeypatch.setattr(geocode_module.requests, "get", fake_get)
    monkeypatch.setattr(geocode_module, "MIN_REQUEST_INTERVAL_S", 0.0)

    threads = [threading.Thread(target=lambda: search("Gebze")) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(overlapping) == 4, "not every caller reached the service"
    assert max(overlapping) == 1, "two callers were inside Nominatim at once"


def test_a_queue_that_would_stall_the_app_is_refused_instead(monkeypatch):
    """The throttle serialises, so a burst of autocompletes would otherwise park a
    threadpool worker each and starve the routing endpoints that share the pool."""
    monkeypatch.setattr(geocode_module, "_waiting", threading.BoundedSemaphore(1))
    held = threading.Event()
    release = threading.Event()

    def slow_get(*args, **kwargs):
        held.set()
        release.wait(timeout=5)
        return FakeResponse([])

    monkeypatch.setattr(geocode_module.requests, "get", slow_get)
    worker = threading.Thread(target=lambda: search("Gebze"))
    worker.start()
    try:
        assert held.wait(timeout=5)
        with pytest.raises(GeocodingBusy, match="already queued"):
            search("Gebze")
    finally:
        release.set()
        worker.join()


def test_geocoding_many_places_writes_the_cache_once(monkeypatch, tmp_path):
    writes = []
    monkeypatch.setattr(geocode_module, "CACHE_PATH", tmp_path / "geocode_cache.json")
    monkeypatch.setattr(geocode_module, "_load_cache", lambda: {})
    monkeypatch.setattr(geocode_module, "_save_cache", lambda cache: writes.append(dict(cache)))
    monkeypatch.setattr(
        geocode_module.requests, "get",
        lambda *a, **k: FakeResponse([{"lon": "29.0", "lat": "41.0"}]),
    )

    resolved = geocode_all([("Gebze", "TR"), ("Izmit", "TR")])

    assert len(resolved) == 2
    assert len(writes) == 1, "the cache file was rewritten per place"


def test_the_busy_signal_is_a_geocoding_error_so_callers_need_not_know_about_it():
    assert issubclass(GeocodingBusy, GeocodingError)


def test_rate_limited_releases_its_slot_even_when_the_body_raises(idle_throttle):
    """A leaked slot would shrink the waiting room by one on every error until nothing
    could be geocoded at all."""
    for _ in range(geocode_module.MAX_WAITING + 2):
        with pytest.raises(RuntimeError):
            with rate_limited():
                raise RuntimeError("boom")

    with rate_limited():
        pass
