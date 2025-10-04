"""Async resource coordination for camera and tripod services."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from .camera_adapter import CameraAdapter, CameraAdapterError
from .tripod_adapter import TripodAdapter, TripodAdapterError

__all__ = ["AsyncResourceManager", "ResourceHandles", "ServiceError"]


class ServiceError(RuntimeError):
    """Structured exception surfaced to Gradio callbacks."""

    def __init__(self, message: str, *, code: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class ResourceHandles:
    """Container holding lazily initialised hardware adapters."""

    camera: Optional[CameraAdapter] = None
    tripod: Optional[TripodAdapter] = None


class AsyncResourceManager:
    """Manage shared access to camera and tripod adapters for async code."""

    def __init__(
        self,
        *,
        camera_factory: Optional[Callable[[], CameraAdapter]] = None,
        tripod_factory: Optional[Callable[[], TripodAdapter]] = None,
    ) -> None:
        self._handles = ResourceHandles()
        self._camera_factory = camera_factory or CameraAdapter.autodetect
        self._tripod_factory = tripod_factory

        self._handles_lock = asyncio.Lock()
        self._camera_init_lock = asyncio.Lock()
        self._tripod_init_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Factory configuration
    # ------------------------------------------------------------------
    def configure_camera(self, factory: Callable[[], CameraAdapter]) -> None:
        """Override the camera factory used during lazy initialisation."""

        self._camera_factory = factory
        # Drop cached instance so next access rebuilds it with new factory.
        self._handles.camera = None

    def configure_tripod(self, factory: Callable[[], TripodAdapter]) -> None:
        """Override the tripod factory used during lazy initialisation."""

        self._tripod_factory = factory
        self._handles.tripod = None

    # ------------------------------------------------------------------
    # Lazy acquisition helpers
    # ------------------------------------------------------------------
    async def get_camera(self) -> CameraAdapter:
        async with self._camera_init_lock:
            if self._handles.camera is None:
                factory = self._camera_factory
                try:
                    camera = await asyncio.to_thread(factory)
                except CameraAdapterError:
                    raise
                except Exception as exc:  # pragma: no cover - defensive guard
                    raise ServiceError(
                        "Unexpected camera initialisation failure.",
                        code="camera_initialisation_error",
                    ) from exc
                self._handles.camera = camera
        return self._handles.camera

    async def get_tripod(self) -> TripodAdapter:
        if self._tripod_factory is None:
            raise TripodAdapterError(
                "Tripod has not been configured.",
                code="tripod_not_configured",
            )
        async with self._tripod_init_lock:
            if self._handles.tripod is None:
                try:
                    tripod = await asyncio.to_thread(self._tripod_factory)
                except TripodAdapterError:
                    raise
                except Exception as exc:  # pragma: no cover - defensive guard
                    raise ServiceError(
                        "Unexpected tripod initialisation failure.",
                        code="tripod_initialisation_error",
                    ) from exc
                self._handles.tripod = tripod
        return self._handles.tripod

    # ------------------------------------------------------------------
    # Camera helpers
    # ------------------------------------------------------------------
    async def camera_settings_snapshot(self, *, include_metadata: bool = False) -> Dict[str, Any]:
        method_name = "query_settings" if include_metadata else "get_current_settings"
        return await self._call_camera(method_name)

    async def update_camera_settings(self, new_settings: Dict[str, Any], *, step_policy: str = "strict") -> None:
        await self._call_camera("apply_settings", new_settings, step_policy=step_policy)

    async def drive_focus(self, direction: str = "near", step_size: int = 1, *, live_view: bool = False) -> None:
        await self._call_camera("focus_step", direction, step_size, live_view=live_view)

    async def capture_preview(self) -> bytes:
        preview = await self._call_camera("capture_preview")
        if isinstance(preview, bytes):
            return preview
        if hasattr(preview, "getvalue"):
            return preview.getvalue()  # type: ignore[no-any-return]
        raise ServiceError("Preview object is not bytes-like.", code="camera_preview_error")

    def stream_live_view(self, *, interval: float = 0.5) -> AsyncIterator[bytes]:
        if interval <= 0:
            raise ServiceError("interval must be positive.", code="invalid_arguments")

        async def _generator() -> AsyncIterator[bytes]:
            try:
                while True:
                    frame = await self.capture_preview()
                    yield frame
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

        return _generator()

    # ------------------------------------------------------------------
    # Tripod helpers
    # ------------------------------------------------------------------
    async def tripod_status(self) -> Dict[str, Any]:
        pan, tilt, drivers = await self._call_tripod("status")
        return {"pan": pan, "tilt": tilt, "drivers_enabled": drivers}

    async def move_tripod(self, pan_deg: float = 0.0, tilt_deg: float = 0.0) -> None:
        await self._call_tripod("move", pan_deg, tilt_deg)

    async def move_tripod_to(self, *, pan_deg: Optional[float] = None, tilt_deg: Optional[float] = None) -> None:
        await self._call_tripod("move_to", pan_deg=pan_deg, tilt_deg=tilt_deg)

    async def stop_tripod(self) -> None:
        await self._call_tripod("stop")

    # ------------------------------------------------------------------
    # Shutdown lifecycle
    # ------------------------------------------------------------------
    async def shutdown(self) -> None:
        async with self._handles_lock:
            camera, tripod = self._handles.camera, self._handles.tripod
            self._handles = ResourceHandles()

        tasks: list[Awaitable[Any]] = []
        if camera is not None:
            tasks.append(asyncio.to_thread(camera.close))
        if tripod is not None:
            tasks.append(asyncio.to_thread(tripod.close))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _call_camera(self, method: str, *args: Any, **kwargs: Any) -> Any:
        camera = await self.get_camera()
        func = getattr(camera, method)
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except CameraAdapterError:
            raise
        except Exception as exc:  # pragma: no cover - defensive guard
            raise ServiceError(
                f"Camera operation '{method}' failed.",
                code="camera_operation_error",
                details={"operation": method},
            ) from exc

    async def _call_tripod(self, method: str, *args: Any, **kwargs: Any) -> Any:
        tripod = await self.get_tripod()
        func = getattr(tripod, method)
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except TripodAdapterError:
            raise
        except Exception as exc:  # pragma: no cover - defensive guard
            raise ServiceError(
                f"Tripod operation '{method}' failed.",
                code="tripod_operation_error",
                details={"operation": method},
            ) from exc
