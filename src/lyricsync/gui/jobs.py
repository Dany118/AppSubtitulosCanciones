"""Background jobs for work that takes too long to answer a request with.

Syncing a track can take minutes on the GPU, so the UI starts a job, gets an
id back, and polls it for progress. Jobs run one item at a time in a worker
thread and check for cancellation between items.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

# How many finished jobs to keep around for the UI to read back.
MAX_RETAINED = 20


@dataclass
class Job:
    id: str
    kind: str
    total: int
    done: int = 0
    status: str = "running"  # running | finished | cancelled | failed
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    current: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "total": self.total,
            "done": self.done,
            "status": self.status,
            "error": self.error,
            "events": list(self.events),
            "current": self.current,
            "elapsed": round((self.finished_at or time.time()) - self.started_at, 1),
        }

    @property
    def running(self) -> bool:
        return self.status == "running"


class JobRunner:
    """Runs one job at a time per runner instance, in a worker thread."""

    def __init__(self, max_retained: int = MAX_RETAINED) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()
        self._max_retained = max_retained

    def submit(
        self,
        kind: str,
        items: Sequence[Any],
        worker: Callable[[Any], dict[str, Any]],
    ) -> Job:
        """Start ``worker`` over ``items`` and return the job immediately."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, total=len(items))
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._prune()

        thread = threading.Thread(
            target=self._run, args=(job, list(items), worker), daemon=True
        )
        thread.start()
        return job

    def _run(self, job: Job, items: list[Any], worker: Callable[[Any], dict[str, Any]]) -> None:
        try:
            for item in items:
                if job.id in self._cancelled:
                    with self._lock:
                        job.status = "cancelled"
                        job.finished_at = time.time()
                        job.current = ""
                    return

                with self._lock:
                    job.current = str(item)

                try:
                    event = worker(item)
                except Exception as exc:  # noqa: BLE001 - one bad item must not kill the job
                    event = {"item": str(item), "status": "failed", "message": str(exc)}

                with self._lock:
                    job.events.append(event)
                    job.done += 1
                    job.current = ""
        except Exception:  # noqa: BLE001 - defensive: the thread must never die silently
            with self._lock:
                job.status = "failed"
                job.error = traceback.format_exc(limit=3)
                job.finished_at = time.time()
            return

        with self._lock:
            if job.status == "running":
                job.status = "finished"
            job.finished_at = time.time()
            job.current = ""

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Ask a job to stop after the item it is currently working on."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or not job.running:
                return False
            self._cancelled.add(job_id)
            return True

    def active(self) -> Job | None:
        with self._lock:
            for job_id in reversed(self._order):
                job = self._jobs[job_id]
                if job.running:
                    return job
            return None

    def _prune(self) -> None:
        """Drop the oldest finished jobs, keeping every running one."""
        while len(self._order) > self._max_retained:
            for index, job_id in enumerate(self._order):
                if not self._jobs[job_id].running:
                    self._order.pop(index)
                    self._jobs.pop(job_id, None)
                    self._cancelled.discard(job_id)
                    break
            else:
                return
