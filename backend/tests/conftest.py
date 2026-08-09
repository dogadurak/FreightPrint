import pytest
import requests

from app.core import road, sea
from app.core.cache import DiskCache


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    """Fail any test that reaches the network instead of letting it reach OSRM.

    A test that quietly makes a real request passes on a developer's machine and then
    fails in CI, or worse, passes there too and hides that its mock stopped being used.
    Mark a test `@pytest.mark.network` to opt out; nothing does today.
    """
    if request.node.get_closest_marker("network"):
        return

    def refuse(self, method, url, *args, **kwargs):
        raise AssertionError(
            f"test tried to reach the network: {method} {url}. "
            "Mock the client, or mark the test with @pytest.mark.network."
        )

    monkeypatch.setattr(requests.sessions.Session, "request", refuse)


@pytest.fixture(autouse=True)
def isolated_disk_cache(tmp_path, monkeypatch):
    """Point the on-disk route caches at throwaway files for every test.

    Without this, a mocked OSRM answer is written to the real cache: it pollutes live
    data with fixtures, and the next run serves that fixture back before the mock is
    ever consulted, so the test would keep passing against broken parsing code. Sea
    routes are isolated for the same reason, and so a test run cannot quietly reshape
    the cache the application uses.
    """
    cache = DiskCache(tmp_path / "route_cache.sqlite")
    monkeypatch.setattr(road, "_disk_cache", cache)
    monkeypatch.setattr(sea, "_disk_cache", cache)
    road.road_route.cache_clear()
    sea.sea_route.cache_clear()
    yield
    road.road_route.cache_clear()
    sea.sea_route.cache_clear()
