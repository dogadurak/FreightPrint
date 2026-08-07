import pytest

from app.core import road
from app.core.cache import DiskCache


@pytest.fixture(autouse=True)
def isolated_disk_cache(tmp_path, monkeypatch):
    """Point the on-disk route cache at a throwaway file for every test.

    Without this, a mocked OSRM answer is written to the real cache: it pollutes live
    data with fixtures, and the next run serves that fixture back before the mock is
    ever consulted, so the test would keep passing against broken parsing code.
    """
    monkeypatch.setattr(road, "_disk_cache", DiskCache(tmp_path / "route_cache.sqlite"))
    road.road_route.cache_clear()
    yield
    road.road_route.cache_clear()
