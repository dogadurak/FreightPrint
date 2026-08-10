import pytest
import requests

from app.core import road, sea
from app.core.cache import DiskCache

# Captured before anything can replace it, so a test marked `network` can be handed the
# real client back rather than a guard some earlier fixture happened to leave behind.
REAL_REQUEST = requests.sessions.Session.request


def _refuse(self, method, url, *args, **kwargs):
    raise AssertionError(
        f"test tried to reach the network: {method} {url}. "
        "Mock the client, or mark the test with @pytest.mark.network."
    )


@pytest.fixture(autouse=True, scope="session")
def guard_the_whole_session(tmp_path_factory):
    """Block the network and divert the caches before any fixture of any scope runs.

    pytest sets fixtures up widest scope first, so a module- or session-scoped fixture
    is built **before** the function-scoped guards below and cannot be protected by
    them. That is not hypothetical: a module-scoped fixture that routed the pilot
    corridor slipped past the per-test guard entirely, called the public OSRM demo
    server on every run, and wrote the answers into the real route cache — the two
    things these fixtures exist to prevent. It passed because a previous real call had
    already populated that cache, so the suite looked hermetic and was not.

    This one is session-scoped, so nothing is set up before it.
    """
    cache = DiskCache(tmp_path_factory.mktemp("session-cache") / "route_cache.sqlite")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(requests.sessions.Session, "request", _refuse)
        patch.setattr(road, "_disk_cache", cache)
        patch.setattr(sea, "_disk_cache", cache)
        yield


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    """Fail any test that reaches the network instead of letting it reach OSRM.

    A test that quietly makes a real request passes on a developer's machine and then
    fails in CI, or worse, passes there too and hides that its mock stopped being used.
    Mark a test `@pytest.mark.network` to opt out; nothing does today.
    """
    if request.node.get_closest_marker("network"):
        # The session guard is already in place, so opting out means putting the real
        # client back rather than simply declining to install the block.
        monkeypatch.setattr(requests.sessions.Session, "request", REAL_REQUEST)
        return

    monkeypatch.setattr(requests.sessions.Session, "request", _refuse)


@pytest.fixture(autouse=True)
def isolated_disk_cache(tmp_path, monkeypatch):
    """Point the on-disk route caches at throwaway files for every test.

    Without this, a mocked OSRM answer is written to the real cache: it pollutes live
    data with fixtures, and the next run serves that fixture back before the mock is
    ever consulted, so the test would keep passing against broken parsing code. Sea
    routes are isolated for the same reason, and so a test run cannot quietly reshape
    the cache the application uses.

    Per test rather than per session, so one test's mocked answer cannot be served to
    another; the session guard above only ensures the *real* cache is never the target.
    """
    cache = DiskCache(tmp_path / "route_cache.sqlite")
    monkeypatch.setattr(road, "_disk_cache", cache)
    monkeypatch.setattr(sea, "_disk_cache", cache)
    road.road_route.cache_clear()
    sea.sea_route.cache_clear()
    yield
    road.road_route.cache_clear()
    sea.sea_route.cache_clear()
