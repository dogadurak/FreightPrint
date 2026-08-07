import pytest

from app.core import road, sea
from app.core.cache import DiskCache


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
