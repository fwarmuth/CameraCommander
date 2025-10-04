"""Application state container for the Gradio UI layer.

``AppState`` centralizes shared dependencies (repositories, job runners, and
async coordination helpers) so UI callbacks can stay import-light.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Dict, Iterable, Iterator, Optional, Set

from .services import AsyncResourceManager, TimelapseJobRunner
from .services.timelapse_runner import TimelapseJob, TimelapseJobStatus
from .store.session_repository import SessionRepository

_APP_STATE: ContextVar["AppState"] = ContextVar("app_state")
_JOB_STREAM_SENTINEL = object()
_SESSION_STREAM_SENTINEL = object()


@dataclass
class AppState:
    """Centralized application state for the Gradio-driven application."""

    # Core services exposed to the UI through ``AppState.current``.
    resources: AsyncResourceManager = field(default_factory=AsyncResourceManager)
    jobs: TimelapseJobRunner = field(default_factory=TimelapseJobRunner)
    sessions: SessionRepository = field(default_factory=SessionRepository)

    library_selected_session: Optional[str] = field(default=None, init=False)

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _shutdown_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _job_listeners: Dict[str, Set[asyncio.Queue[TimelapseJob | object]]] = field(
        default_factory=lambda: defaultdict(set), init=False
    )
    _background_tasks: Set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _hardware_lock_listeners: Set[asyncio.Queue[bool | object]] = field(
        default_factory=set, init=False
    )
    _session_listeners: Set[asyncio.Queue[int | object]] = field(
        default_factory=set, init=False
    )
    _session_version: int = field(default=0, init=False)
    _session_repo_listener: Optional[Callable[[], None]] = field(
        default=None, init=False, repr=False
    )
    _loop: Optional[asyncio.AbstractEventLoop] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - fallback for non-running loop
            self._loop = asyncio.get_event_loop()

        listener = self._on_session_repository_changed
        self.sessions.add_change_listener(listener)
        self._session_repo_listener = listener

    # ------------------------------------------------------------------
    # Dependency injection helpers
    # ------------------------------------------------------------------
    @classmethod
    def current(cls) -> "AppState":
        """Return the ``AppState`` bound to the active context."""

        try:
            return _APP_STATE.get()
        except LookupError as exc:  # pragma: no cover - defensive guard
            raise RuntimeError(
                "AppState is not bound to the current context. Did you call ``AppState.use``?"
            ) from exc

    @classmethod
    @contextmanager
    def use(cls, state: "AppState") -> Iterator["AppState"]:
        """Bind *state* to the current context for the duration of the block."""

        token = _APP_STATE.set(state)
        try:
            yield state
        finally:
            _APP_STATE.reset(token)

    # ------------------------------------------------------------------
    # Job subscription and dispatch
    # ------------------------------------------------------------------
    async def start_job(self, job: TimelapseJob) -> None:
        """Register *job*, notify listeners, and spawn an update task."""

        await self.jobs.start_job(job)
        await self._dispatch_job_update(job)
        self._spawn_job_observer(job.job_id)

    async def publish_job_update(self, job: TimelapseJob) -> None:
        """Fan out *job* updates and close the stream on terminal states."""

        await self._dispatch_job_update(job)
        if job.status in {
            TimelapseJobStatus.COMPLETED,
            TimelapseJobStatus.FAILED,
            TimelapseJobStatus.CANCELLED,
        }:
            await self._dispatch_job_closed(job.job_id)

    async def subscribe_job(self, job_id: str) -> AsyncIterator[TimelapseJob]:
        """Yield updates for ``job_id`` until the associated queue closes."""

        queue: asyncio.Queue[TimelapseJob | object] = asyncio.Queue()

        async with self._lock:
            listeners = self._job_listeners[job_id]
            listeners.add(queue)

        # Immediately deliver the latest snapshot, if present.
        snapshot = await self.jobs.get_job(job_id)
        if snapshot is not None:
            queue.put_nowait(snapshot)

        try:
            while True:
                update = await queue.get()
                if update is _JOB_STREAM_SENTINEL:
                    break
                yield update  # type: ignore[misc]
        finally:
            await self._remove_job_listener(job_id, queue)

    async def subscribe_hardware_lock(self) -> AsyncIterator[bool]:
        """Yield ``True`` while timelapse jobs hold exclusive hardware access."""

        queue: asyncio.Queue[bool | object] = asyncio.Queue()

        async with self._lock:
            self._hardware_lock_listeners.add(queue)

        queue.put_nowait(await self.jobs.has_active_job())

        try:
            while True:
                update = await queue.get()
                if update is _JOB_STREAM_SENTINEL:
                    break
                yield bool(update)
        finally:
            async with self._lock:
                self._hardware_lock_listeners.discard(queue)

    async def subscribe_sessions(self) -> AsyncIterator[int]:
        """Yield version counters whenever the session repository changes."""

        queue: asyncio.Queue[int | object] = asyncio.Queue()

        async with self._lock:
            self._session_listeners.add(queue)

        try:
            while True:
                update = await queue.get()
                if update is _SESSION_STREAM_SENTINEL:
                    break
                if isinstance(update, int):
                    yield update
        finally:
            async with self._lock:
                self._session_listeners.discard(queue)

    async def _dispatch_job_update(self, job: TimelapseJob) -> None:
        async with self._lock:
            listeners: Iterable[asyncio.Queue[TimelapseJob | object]] = tuple(
                self._job_listeners.get(job.job_id, set())
            )

        for queue in listeners:
            queue.put_nowait(job)

        await self._notify_hardware_lock()

    async def _dispatch_job_closed(self, job_id: str) -> None:
        job = await self.jobs.get_job(job_id)
        if job is not None and job.status not in {
            TimelapseJobStatus.COMPLETED,
            TimelapseJobStatus.FAILED,
            TimelapseJobStatus.CANCELLED,
        } and not self._shutdown_event.is_set():
            return

        async with self._lock:
            listeners = tuple(self._job_listeners.get(job_id, set()))

        for queue in listeners:
            queue.put_nowait(_JOB_STREAM_SENTINEL)

        await self._notify_hardware_lock()

    async def _notify_hardware_lock(self) -> None:
        locked = await self.jobs.has_active_job()

        async with self._lock:
            listeners = tuple(self._hardware_lock_listeners)

        for queue in listeners:
            queue.put_nowait(locked)

    def _on_session_repository_changed(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():  # pragma: no cover - defensive guard
            return
        loop.call_soon_threadsafe(self._notify_session_change)

    def _notify_session_change(self) -> None:
        self._session_version += 1

        listeners: Iterable[asyncio.Queue[int | object]]
        listeners = tuple(self._session_listeners)

        for queue in listeners:
            queue.put_nowait(self._session_version)

    async def _remove_job_listener(
        self, job_id: str, queue: asyncio.Queue[TimelapseJob | object]
    ) -> None:
        async with self._lock:
            listeners = self._job_listeners.get(job_id)
            if listeners is None:
                return
            listeners.discard(queue)
            if not listeners:
                self._job_listeners.pop(job_id, None)

    def _spawn_job_observer(self, job_id: str) -> None:
        async def _observer() -> None:
            try:
                async for update in self.jobs.iter_updates(job_id):
                    await self._dispatch_job_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive guard
                await self._handle_job_exception(job_id, exc)
            finally:
                await self._dispatch_job_closed(job_id)

        task = asyncio.create_task(_observer())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _handle_job_exception(self, job_id: str, exc: BaseException) -> None:
        job = await self.jobs.get_job(job_id)
        if job is None:
            return
        job.status = TimelapseJobStatus.FAILED
        job.message = str(exc)
        await self._dispatch_job_update(job)

    # ------------------------------------------------------------------
    # Shutdown lifecycle
    # ------------------------------------------------------------------
    async def shutdown(self) -> None:
        """Cancel background tasks, close queues, and stop downstream services."""

        if self._shutdown_event.is_set():
            return

        self._shutdown_event.set()

        if self._session_repo_listener is not None:
            self.sessions.remove_change_listener(self._session_repo_listener)
            self._session_repo_listener = None

        # Cancel any observer tasks that may still be running.
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        # Close all listener queues.
        async with self._lock:
            listeners = [
                queue
                for queues in self._job_listeners.values()
                for queue in queues
            ]
            self._job_listeners.clear()
            lock_listeners = tuple(self._hardware_lock_listeners)
            self._hardware_lock_listeners.clear()
            session_listeners = tuple(self._session_listeners)
            self._session_listeners.clear()

        for queue in listeners:
            queue.put_nowait(_JOB_STREAM_SENTINEL)

        for queue in lock_listeners:
            queue.put_nowait(_JOB_STREAM_SENTINEL)

        for queue in session_listeners:
            queue.put_nowait(_SESSION_STREAM_SENTINEL)

        await self.jobs.shutdown()
        await self.resources.shutdown()


def get_app_state() -> AppState:
    """Convenience accessor mirroring :meth:`AppState.current`."""

    return AppState.current()
