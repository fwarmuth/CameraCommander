"""Timelapse job orchestration for the Gradio UI."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, AsyncIterator, Callable, Dict, Optional

from logging_utils import ensure_trace_level
from .timelapse_session import TimelapseError, TimelapseSession
from models import RecordingSettings

if TYPE_CHECKING:  # pragma: no cover - type checking helpers
    from .camera_adapter import CameraAdapter
    from .tripod_adapter import TripodAdapter

ensure_trace_level()
logger = logging.getLogger(__name__)


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
    camera_factory: Optional[Callable[[], "CameraAdapter"]] = field(
        default=None, repr=False, compare=False
    )
    tripod_factory: Optional[Callable[[], "TripodAdapter"]] = field(
        default=None, repr=False, compare=False
    )


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

        logger.info("Starting timelapse job %s", job.job_id)
        session = TimelapseSession(
            job.settings,
            camera_factory=job.camera_factory,
            tripod_factory=job.tripod_factory,
        )
        queue: asyncio.Queue[TimelapseJob | object] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _progress(done: int, total: int) -> None:
            def _notify() -> None:
                job.progress = done / total if total else 0.0
                job.message = f"Captured {done}/{total} frames"
                queue.put_nowait(job)
                logger.debug("Job %s progress %s/%s", job.job_id, done, total)

            loop.call_soon_threadsafe(_notify)

        def _event(name: str, payload: Dict[str, object]) -> None:
            def _notify() -> None:
                if name == "started":
                    job.message = "Timelapse capture started"
                    print(f"[timelapse] Job {job.job_id} started.")
                elif name == "completed":
                    job.message = "Capture loop finished"
                    print(f"[timelapse] Job {job.job_id} capture completed.")
                elif name == "failed":
                    error = payload.get("error", "unknown error")
                    job.message = f"Capture failed: {error}"
                    print(f"[timelapse] Job {job.job_id} failed: {error}")
                logger.debug("Job %s event %s payload=%s", job.job_id, name, payload)
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
        print(f"[timelapse] Job {job.job_id} scheduled.")
        logger.info("Job %s scheduled", job.job_id)

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
                logger.error("Job %s failed: %s", job.job_id, exc)
                print(f"[timelapse] Job {job.job_id} failed: {exc}.")
            except Exception as exc:  # pragma: no cover - defensive guard
                job.status = TimelapseJobStatus.FAILED
                job.message = f"Unexpected failure: {exc}"
                job.output_path = None
                queue.put_nowait(job)
                logger.exception("Job %s unexpected failure", job.job_id)
                print(f"[timelapse] Job {job.job_id} failed unexpectedly: {exc}.")
            else:
                if job.status is TimelapseJobStatus.CANCELLED or session.stop_requested:
                    if session.stop_requested and job.status is TimelapseJobStatus.RUNNING:
                        job.status = TimelapseJobStatus.CANCELLED
                    job.message = job.message or "Timelapse cancelled"
                    job.output_path = None
                    logger.info("Job %s cancelled", job.job_id)
                    print(f"[timelapse] Job {job.job_id} cancelled.")
                else:
                    job.status = TimelapseJobStatus.COMPLETED
                    job.progress = 1.0
                    job.output_path = str(result) if result else None
                    job.message = job.message or "Timelapse completed successfully"
                    logger.info(
                        "Job %s completed successfully (output=%s)",
                        job.job_id,
                        job.output_path,
                    )
                    if job.output_path:
                        print(
                            f"[timelapse] Job {job.job_id} completed. Output: {job.output_path}."
                        )
                    else:
                        print(f"[timelapse] Job {job.job_id} completed without video output.")
                queue.put_nowait(job)
            finally:
                queue.put_nowait(_QUEUE_SENTINEL)
                async with self._lock:
                    self._workers.pop(job.job_id, None)
                    self._sessions.pop(job.job_id, None)
                logger.debug("Job %s worker finished", job.job_id)

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
        logger.info("Cancellation requested for job %s", job_id)
        if queue is not None:
            queue.put_nowait(job)
        if session is not None:
            session.request_stop()
        if worker is not None:
            await worker

    async def has_active_job(self) -> bool:
        """Return ``True`` if any job currently holds hardware access."""

        async with self._lock:
            active = any(
                job.status not in {
                    TimelapseJobStatus.COMPLETED,
                    TimelapseJobStatus.FAILED,
                    TimelapseJobStatus.CANCELLED,
                }
                for job in self._jobs.values()
            )
        logger.debug("Active job check -> %s", active)
        return active

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
        logger.info("Shutdown requested – %s sessions notified", len(sessions))
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

        async with self._lock:
            self._workers.clear()
            self._sessions.clear()
            self._queues.clear()
            self._jobs.clear()
        logger.info("Job runner shutdown complete")
