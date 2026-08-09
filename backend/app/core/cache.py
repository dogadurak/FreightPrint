import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .network import CACHE_DIR

DEFAULT_CACHE_PATH = CACHE_DIR / "route_cache.sqlite"

_local = threading.local()


class DiskCache:
    """A tiny key-value cache that survives process restarts.

    An in-process cache is empty on every start and is not shared between web workers,
    so each restart would replay every OSRM call and hand the user another cold wait.
    SQLite keeps this to one file with no service to run.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_CACHE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # SQLite connections are not shareable across threads, and a web server has many.
        connections = getattr(_local, "connections", None)
        if connections is None:
            connections = _local.connections = {}
        connection = connections.get(self.path)
        if connection is None:
            connection = connections[self.path] = sqlite3.connect(self.path, timeout=30)
        with connection:
            yield connection

    def get(self, key: str) -> Any | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM entries WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key: str, value: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO entries (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> Any:
        """Return the cached value, computing and storing it on a miss.

        Only successful results are stored: a failed lookup must be retried later, not
        remembered as an answer.
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        value = compute()
        self.set(key, value)
        return value

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM entries")
