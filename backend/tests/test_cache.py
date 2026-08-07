import pytest

from app.core.cache import DiskCache


@pytest.fixture
def cache(tmp_path):
    return DiskCache(tmp_path / "cache.sqlite")


def test_value_survives_a_new_connection(tmp_path):
    """The point of this cache is outliving the process, not just the call."""
    path = tmp_path / "cache.sqlite"
    DiskCache(path).set("route", {"distance_km": 1628.0})

    assert DiskCache(path).get("route") == {"distance_km": 1628.0}


def test_missing_key_returns_none(cache):
    assert cache.get("never-stored") is None


def test_get_or_compute_runs_once_per_key(cache):
    calls = []

    def compute():
        calls.append(1)
        return {"distance_km": 42.0}

    assert cache.get_or_compute("k", compute) == {"distance_km": 42.0}
    assert cache.get_or_compute("k", compute) == {"distance_km": 42.0}
    assert len(calls) == 1


def test_a_failed_computation_is_not_remembered_as_an_answer(cache):
    """A network failure must be retried later, not cached as the result."""

    def failing():
        raise RuntimeError("OSRM unreachable")

    with pytest.raises(RuntimeError):
        cache.get_or_compute("k", failing)

    assert cache.get("k") is None
    assert cache.get_or_compute("k", lambda: {"distance_km": 1.0}) == {"distance_km": 1.0}


def test_writing_the_same_key_replaces_the_value(cache):
    cache.set("k", {"distance_km": 1.0})
    cache.set("k", {"distance_km": 2.0})

    assert cache.get("k") == {"distance_km": 2.0}


def test_clear_empties_the_cache(cache):
    cache.set("k", {"distance_km": 1.0})
    cache.clear()

    assert cache.get("k") is None
