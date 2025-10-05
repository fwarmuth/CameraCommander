"""Timelapse job orchestration for the Gradio UI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
from typing import AsyncIterator, Dict, Optional

from .timelapse_session import TimelapseError, TimelapseSession
from ..models import RecordingSettings


class TimelapseJobStatus(Enum):
    """Lifecycle states for a timelapse job."""

    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class TimelapseJob:
    """Lightweight job description shared with the UI."""

    job_id: str
    settings: Dict[str, object]
    recording: RecordingSettings
    status: TimelapseJobStatus = TimelapseJobStatus.PENDING
    progress: float = 0.0
    message: Optional[str] = None
    output_path: Optional[str] = None


_QUEUE_SENTINEL: object = object()


class TimelapseJobRunner:
    """Async coordinator that proxies hardware execution on a worker thread."""

    def __init__(self) -> None:
        self._jobs: Dict[str, TimelapseJob] = {}
        self._queues: Dict[str, asyncio.Queue[TimelapseJob | object]] = {}
        self._sessions: Dict[str, TimelapseSession] = {}
        self._workers: Dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start_job(self, job: TimelapseJob) -> None:
        """Register *job*, spawn the session, and publish initial state."""

        session = TimelapseSession(job.settings)
        queue: asyncio.Queue[TimelapseJob | object] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _progress(done: int, total: int) -> None:
            def _notify() -> None:
                job.progress = done / total if total else 0.0
                job.message = f"Captured {done}/{total} frames"
                queue.put_nowait(job)

            loop.call_soon_threadsafe(_notify)

        def _event(name: str, payload: Dict[str, object]) -> None:
            def _notify() -> None:
                if name == "started":
                    job.message = "Timelapse capture started"
                elif name == "completed":
                    job.message = "Capture loop finished"
                elif name == "failed":
                    error = payload.get("error", "unknown error")
                    job.message = f"Capture failed: {error}"
                queue.put_nowait(job)

            loop.call_soon_threadsafe(_notify)

        async with self._lock:
            self._jobs[job.job_id] = job
            self._queues[job.job_id] = queue
            self._sessions[job.job_id] = session

        job.status = TimelapseJobStatus.RUNNING
        job.progress = 0.0
        job.message = "Timelapse job scheduled"
        queue.put_nowait(job)

        async def _worker() -> None:
            try:
                result = await asyncio.to_thread(
                    session.run,
                    progress_cb=_progress,
                    event_cb=_event,
                )
            except TimelapseError as exc:
                job.status = TimelapseJobStatus.FAILED
                job.message = str(exc)
                job.output_path = None
                queue.put_nowait(job)
            except Exception as exc:  # pragma: no cover - defensive guard
                job.status = TimelapseJobStatus.FAILED
                job.message = f"Unexpected failure: {exc}"
                job.output_path = None
                queue.put_nowait(job)
            else:
                if job.status is TimelapseJobStatus.CANCELLED or session.stop_requested:
                    if session.stop_requested and job.status is TimelapseJobStatus.RUNNING:
                        job.status = TimelapseJobStatus.CANCELLED
                    job.message = job.message or "Timelapse cancelled"
                    job.output_path = None
                else:
                    job.status = TimelapseJobStatus.COMPLETED
                    job.progress = 1.0
                    job.output_path = str(result) if result else None
                    job.message = job.message or "Timelapse completed successfully"
                queue.put_nowait(job)
            finally:
                queue.put_nowait(_QUEUE_SENTINEL)
                async with self._lock:
                    self._workers.pop(job.job_id, None)
                    self._sessions.pop(job.job_id, None)

        worker = asyncio.create_task(_worker())
        async with self._lock:
            self._workers[job.job_id] = worker

    async def get_job(self, job_id: str) -> Optional[TimelapseJob]:
        """Return the stored job for ``job_id`` if present."""

        async with self._lock:
            return self._jobs.get(job_id)

    async def iter_updates(self, job_id: str) -> AsyncIterator[TimelapseJob]:
        """Stream updates for ``job_id`` until completion."""

        async with self._lock:
            queue = self._queues.get(job_id)

        if queue is None:
            return

        while True:
            update = await queue.get()
            if update is _QUEUE_SENTINEL:
                break
            yield update  # type: ignore[misc]

    async def cancel_job(self, job_id: str) -> None:
        """Request cancellation and wait for cooperative teardown."""

        async with self._lock:
            job = self._jobs.get(job_id)
            session = self._sessions.get(job_id)
            queue = self._queues.get(job_id)
            worker = self._workers.get(job_id)

        if job is None or job.status in {
            TimelapseJobStatus.COMPLETED,
            TimelapseJobStatus.FAILED,
            TimelapseJobStatus.CANCELLED,
        }:
            return

        job.status = TimelapseJobStatus.CANCELLED
        job.message = "Cancellation requested"
        if queue is not None:
            queue.put_nowait(job)
        if session is not None:
            session.request_stop()
        if worker is not None:
            await worker

    async def has_active_job(self) -> bool:
        """Return ``True`` if any job currently holds hardware access."""

        async with self._lock:
            return any(
                job.status not in {
                    TimelapseJobStatus.COMPLETED,
                    TimelapseJobStatus.FAILED,
                    TimelapseJobStatus.CANCELLED,
                }
                for job in self._jobs.values()
            )

    async def purge_completed(self) -> None:
        """Drop finished jobs from the in-memory cache and queues."""

        async with self._lock:
            to_remove = [
                job_id
                for job_id, job in self._jobs.items()
                if job.status in {
                    TimelapseJobStatus.COMPLETED,
                    TimelapseJobStatus.FAILED,
                    TimelapseJobStatus.CANCELLED,
                }
            ]
            for job_id in to_remove:
                self._jobs.pop(job_id, None)
                self._queues.pop(job_id, None)

    async def shutdown(self) -> None:
        """Cancel running jobs and clean up resources."""

        async with self._lock:
            sessions = list(self._sessions.values())
            workers = list(self._workers.values())

        for session in sessions:
            session.request_stop()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

        async with self._lock:
            self._workers.clear()
            self._sessions.clear()
            self._queues.clear()
            self._jobs.clear()
