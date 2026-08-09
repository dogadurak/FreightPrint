"""Background jobs for work too slow to answer inside a request.

A cold shipment costs about six seconds and seven OSRM calls, so a 500-row file is
roughly fifty minutes. No browser or proxy waits that long: the upload has to return a
handle immediately and the caller polls it.

Jobs live in this process only. That is deliberate — the brief keeps the system
stateless and a database for something a user watches for a few minutes would be
storage for its own sake — but it means jobs do not survive a restart and do not exist
for a second worker. Running more than one worker needs a shared store first.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

# Routing is network-bound, so a few at a time is a real speed-up. Kept low on purpose:
# the public OSRM demo rate limits, and hammering it gets the whole run blocked. Raise
# it only against a self-hosted OSRM.
DEFAULT_CONCURRENCY = 4

# Finished jobs are held so a slow client can still collect its file, then dropped.
MAX_RETAINED_JOBS = 50


@dataclass
class Job:
    id: str
    total: int
    status: str = "queued"  # queued | running | done | failed
    done: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Bytes, not text: a report can come back as a spreadsheet or a PDF.
    result: bytes | None = None
    error: str | None = None
    filename: str = "freightprint-rapor.csv"

    @property
    def progress(self) -> float:
        return self.done / self.total if self.total else 0.0


class JobRegistry:
    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="job")

    def submit(self, total: int, work: Callable[[Job], str], filename: str) -> Job:
        """Start `work` in the background. It receives the job so it can report progress."""
        job = Job(id=uuid.uuid4().hex[:12], total=total, filename=filename)
        with self._lock:
            self._jobs[job.id] = job
            self._evict_old()
        self._pool.submit(self._run, job, work)
        return job

    def _run(self, job: Job, work: Callable[[Job], str]) -> None:
        job.status = "running"
        try:
            job.result = work(job)
            job.status = "done"
        except Exception as error:  # noqa: BLE001 - the job's failure is its result
            job.error = str(error)
            job.status = "failed"

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _evict_old(self) -> None:
        """Drop the oldest finished jobs once the registry grows past its cap."""
        if len(self._jobs) <= MAX_RETAINED_JOBS:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j.status in {"done", "failed"}),
            key=lambda j: j.created_at,
        )
        for job in finished[: len(self._jobs) - MAX_RETAINED_JOBS]:
            self._jobs.pop(job.id, None)


_registry: JobRegistry | None = None


def registry() -> JobRegistry:
    global _registry
    if _registry is None:
        _registry = JobRegistry()
    return _registry
