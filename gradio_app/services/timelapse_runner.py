"""Timelapse job orchestration placeholders for the Gradio UI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
from typing import AsyncIterator, Dict, Optional


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
    status: TimelapseJobStatus = TimelapseJobStatus.PENDING
    progress: float = 0.0
    message: Optional[str] = None
    output_path: Optional[str] = None


class TimelapseJobRunner:
    """Async stub that mirrors the eventual camera-backed runner API."""

    def __init__(self) -> None:
        self._jobs: Dict[str, TimelapseJob] = {}
        self._lock = asyncio.Lock()

    async def start_job(self, job: TimelapseJob) -> None:
        """Register *job* and transition it to ``RUNNING``."""

        async with self._lock:
            self._jobs[job.job_id] = job
            job.status = TimelapseJobStatus.RUNNING
            job.message = "Timelapse execution placeholder has started."

    async def get_job(self, job_id: str) -> Optional[TimelapseJob]:
        """Return the stored job for ``job_id`` if present."""

        async with self._lock:
            return self._jobs.get(job_id)

    async def iter_updates(self, job_id: str) -> AsyncIterator[TimelapseJob]:
        """Yield placeholder updates for ``job_id``."""

        # Production code will push progress through an async queue.
        job = await self.get_job(job_id)
        if job is None:
            return
        yield job

    async def cancel_job(self, job_id: str) -> None:
        """Mark ``job_id`` as ``CANCELLED`` if it exists."""

        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = TimelapseJobStatus.CANCELLED
            job.message = "Cancellation placeholder executed."

    async def purge_completed(self) -> None:
        """Drop finished jobs from the in-memory cache."""

        async with self._lock:
            to_remove = [job_id for job_id, job in self._jobs.items() if job.status in {
                TimelapseJobStatus.COMPLETED,
                TimelapseJobStatus.FAILED,
                TimelapseJobStatus.CANCELLED,
            }]
            for job_id in to_remove:
                self._jobs.pop(job_id, None)

    async def shutdown(self) -> None:
        """Clear any cached job metadata during teardown."""

        async with self._lock:
            self._jobs.clear()
