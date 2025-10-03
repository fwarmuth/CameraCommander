"""Async resource manager used by the Gradio UI layer.

The original prototype kept a handful of convenience coroutines in the Gradio
app module itself.  They were difficult to test and made it impossible to share
state between different UI entrypoints.  The :class:`AsyncResourceManager`
introduced here centralises access to the heavyweight camera and tripod
wrappers.  Both of those objects interact with hardware and therefore must be
initialised lazily and protected by ``asyncio.Lock`` instances so the UI can
run safely in an asynchronous environment.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
from PIL import Image

try:  # pragma: no cover - optional dependency during tests
    from camerawrapper import CameraError, CameraWrapper  # type: ignore
except Exception:  # pragma: no cover - fallback for environments without gphoto2
    CameraWrapper = None  # type: ignore[assignment]

    class CameraError(RuntimeError):
        """Fallback error when the camera stack is unavailable."""


try:  # pragma: no cover - optional dependency during tests
    from tripodwrapper import TripodController  # type: ignore
except Exception:  # pragma: no cover - fallback when serial support is missing
    TripodController = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

__all__ = ["AsyncResourceManager", "UIResourceError"]


class UIResourceError(RuntimeError):
    """Errors that should be surfaced to the UI in a human friendly manner."""


@dataclass(slots=True)
class _CameraConfig:
    """Internal representation of the camera configuration."""

    raw: Dict[str, Any]
    start_settings: Dict[str, Any]
    enable_live_view_on_start: bool
    live_view_setting: str


@dataclass(slots=True)
class _TripodConfig:
    """Internal representation of the tripod configuration."""

    raw: Dict[str, Any]


class AsyncResourceManager:
    """Coordinate access to hardware resources for the Gradio UI.

    Parameters
    ----------
    camera_config:
        Dictionary describing how to instantiate :class:`CameraWrapper`.  The
        default ``_default_camera_factory`` understands the following keys:

        ``model_substring``
            Passed to :meth:`CameraWrapper.select_camera` to pick the first
            matching camera.
        ``model`` and ``port``
            If both keys are supplied the wrapper is constructed directly via
            ``CameraWrapper(model, port)``.
    camera_start_settings:
        Optional dictionary of settings to apply once the camera is ready.
    enable_live_view_on_start:
        When ``True`` the manager enables ``main.actions.viewfinder`` during
        :meth:`init` and disables it again in :meth:`shutdown`.
    live_view_setting:
        Configuration key used for toggling live view (defaults to
        ``"main.actions.viewfinder"``).
    tripod_config:
        Dictionary forwarded to :class:`TripodController` when the tripod is
        first requested.
    camera_factory / tripod_factory:
        Optional callables that receive the respective configuration dictionaries
        and return initialised hardware wrappers.  They can be used during
        testing to provide fakes.
    """

    def __init__(
        self,
        *,
        camera_config: Optional[Dict[str, Any]] = None,
        camera_start_settings: Optional[Dict[str, Any]] = None,
        enable_live_view_on_start: bool = True,
        live_view_setting: str = "main.actions.viewfinder",
        tripod_config: Optional[Dict[str, Any]] = None,
        camera_factory: Optional[Callable[[Dict[str, Any]], CameraWrapper]] = None,
        tripod_factory: Optional[Callable[[Dict[str, Any]], TripodController]] = None,
    ) -> None:
        self._camera_cfg = _CameraConfig(
            raw=dict(camera_config or {}),
            start_settings=dict(camera_start_settings or {}),
            enable_live_view_on_start=enable_live_view_on_start,
            live_view_setting=live_view_setting,
        )
        self._tripod_cfg = (
            _TripodConfig(raw=dict(tripod_config or {})) if tripod_config is not None else None
        )

        self._camera_factory = camera_factory or self._default_camera_factory
        self._tripod_factory = tripod_factory or self._default_tripod_factory

        self._camera: Optional[CameraWrapper] = None
        self._tripod: Optional[TripodController] = None

        # Locks protecting lifecycle and operational access.
        self._camera_init_lock = asyncio.Lock()
        self._camera_lock = asyncio.Lock()
        self._tripod_init_lock = asyncio.Lock()
        self._tripod_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------
    async def init(self) -> None:
        """Prepare hardware for the UI session."""

        # Apply requested startup settings lazily.  If camera creation fails we
        # surface the error but do not crash initialisation completely – the UI
        # can still start without hardware.
        settings: Dict[str, Any] = {}
        if self._camera_cfg.start_settings:
            settings.update(self._camera_cfg.start_settings)
        if self._camera_cfg.enable_live_view_on_start and self._camera_cfg.live_view_setting:
            settings.setdefault(self._camera_cfg.live_view_setting, 1)

        if settings:
            try:
                await self.apply_camera_settings(settings)
            except UIResourceError as exc:
                logger.warning("Camera initialisation skipped: %s", exc)

    async def shutdown(self) -> None:
        """Release hardware resources gracefully."""

        async with self._camera_lock:
            if self._camera is not None:
                if self._camera_cfg.live_view_setting:
                    try:
                        await asyncio.to_thread(
                            self._camera.apply_settings,
                            {self._camera_cfg.live_view_setting: 0},
                        )
                    except Exception:  # pragma: no cover - best-effort shutdown
                        logger.exception("Failed to disable live view during shutdown")
                try:
                    await asyncio.to_thread(self._camera.__exit__, None, None, None)
                except Exception:  # pragma: no cover - best-effort shutdown
                    logger.exception("Camera shutdown raised an exception")
                finally:
                    self._camera = None

        async with self._tripod_lock:
            if self._tripod is not None:
                try:
                    await asyncio.to_thread(self._tripod.close)
                except Exception:  # pragma: no cover - best-effort shutdown
                    logger.exception("Tripod shutdown raised an exception")
                finally:
                    self._tripod = None

    # ------------------------------------------------------------------
    # Camera helpers
    # ------------------------------------------------------------------
    async def get_live_view_frame(
        self, crop_state: Optional[Dict[str, Any]] = None
    ) -> np.ndarray:
        """Return the latest live view frame as a NumPy array.

        ``crop_state`` can contain ``{"center": (x, y), "size": int}`` to zoom
        into a portion of the live view feed – the same structure that the
        Gradio UI produces.
        """

        async with self._camera_lock:
            camera = await self._ensure_camera()
            try:
                buffer = await asyncio.to_thread(camera.capture_preview)
            except CameraError as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError(f"Failed to capture live view: {exc}") from exc
            except Exception as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError("Failed to capture live view.") from exc

        if isinstance(buffer, io.BytesIO):
            data = buffer.getvalue()
        else:  # fallback – CameraWrapper historically returned BytesIO
            data = bytes(buffer)

        try:
            with Image.open(io.BytesIO(data)) as pil_image:
                pil_image = pil_image.convert("RGB")
                original_size = pil_image.size

                if crop_state and {"center", "size"}.issubset(crop_state):
                    center_x, center_y = crop_state["center"]
                    size = int(crop_state["size"])
                    left = max(0, int(center_x) - size // 2)
                    top = max(0, int(center_y) - size // 2)
                    right = min(original_size[0], int(center_x) + size // 2)
                    bottom = min(original_size[1], int(center_y) + size // 2)
                    if left < right and top < bottom:
                        pil_image = pil_image.crop((left, top, right, bottom)).resize(
                            original_size, Image.NEAREST
                        )

                frame = np.array(pil_image)
        except UIResourceError:
            raise
        except Exception as exc:  # pragma: no cover - depends on camera payload
            raise UIResourceError("Camera returned an unreadable preview frame.") from exc

        return frame

    async def apply_camera_settings(self, settings: Dict[str, Any]) -> str:
        """Apply the provided settings to the camera."""

        if not settings:
            return "No settings updated."

        async with self._camera_lock:
            camera = await self._ensure_camera()
            try:
                await asyncio.to_thread(camera.apply_settings, settings)
            except CameraError as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError(f"Failed to apply camera settings: {exc}") from exc
            except Exception as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError("Failed to apply camera settings.") from exc
        return "Settings applied."

    async def nudge_focus(self, direction: str, step_size: int, *, live_view: bool = True) -> str:
        """Move the focus motor a single step."""

        async with self._camera_lock:
            camera = await self._ensure_camera()
            try:
                await asyncio.to_thread(
                    camera.focus_step, direction, int(step_size), live_view=live_view
                )
            except CameraError as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError(f"Failed to adjust focus: {exc}") from exc
            except Exception as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError("Failed to adjust focus.") from exc
        return f"Focus adjusted: {direction} ({int(step_size)})"

    # ------------------------------------------------------------------
    # Tripod helpers
    # ------------------------------------------------------------------
    async def move_tripod(
        self,
        *,
        pan: Optional[float] = None,
        tilt: Optional[float] = None,
        relative: bool = False,
        timeout: Optional[float] = None,
    ) -> str:
        """Move the tripod head either relatively or absolutely."""

        async with self._tripod_lock:
            controller = await self._ensure_tripod()
            try:
                if relative:
                    await asyncio.to_thread(
                        controller.move_blocking,
                        float(pan or 0.0),
                        float(tilt or 0.0),
                        timeout=timeout,
                    )
                else:
                    await asyncio.to_thread(
                        controller.move_to_blocking,
                        None if pan is None else float(pan),
                        None if tilt is None else float(tilt),
                        timeout=timeout,
                    )
            except Exception as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError(f"Failed to move tripod: {exc}") from exc
        return "Tripod moved."

    async def enable_tripod_drivers(self, enable: bool) -> str:
        """Enable or disable the tripod's stepper drivers."""

        async with self._tripod_lock:
            controller = await self._ensure_tripod()
            try:
                await asyncio.to_thread(controller.enable_drivers, bool(enable))
            except Exception as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError(f"Failed to update tripod drivers: {exc}") from exc
        state = "enabled" if enable else "disabled"
        return f"Tripod drivers {state}."

    async def tripod_status(self) -> Dict[str, Any]:
        """Return the latest tripod status dictionary."""

        async with self._tripod_lock:
            controller = await self._ensure_tripod()
            try:
                pan, tilt, drivers = await asyncio.to_thread(controller.status)
            except Exception as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError(f"Failed to query tripod status: {exc}") from exc
        return {"pan": pan, "tilt": tilt, "drivers_enabled": drivers}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _ensure_camera(self) -> CameraWrapper:
        if CameraWrapper is None:  # pragma: no cover - depends on environment
            raise UIResourceError(
                "Camera support is unavailable – ensure libgphoto2 is installed."
            )

        if self._camera is not None:
            return self._camera

        async with self._camera_init_lock:
            if self._camera is not None:
                return self._camera

            try:
                camera = await asyncio.to_thread(self._camera_factory, self._camera_cfg.raw)
            except UIResourceError:
                raise
            except Exception as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError(f"Failed to initialise camera: {exc}") from exc

            self._camera = camera
            return camera

    async def _ensure_tripod(self) -> TripodController:
        if TripodController is None:  # pragma: no cover - depends on environment
            raise UIResourceError("Tripod support is unavailable – install pyserial.")
        if self._tripod_cfg is None:
            raise UIResourceError("Tripod is not configured.")
        if self._tripod is not None:
            return self._tripod

        async with self._tripod_init_lock:
            if self._tripod is not None:
                return self._tripod

            try:
                tripod = await asyncio.to_thread(self._tripod_factory, self._tripod_cfg.raw)
            except UIResourceError:
                raise
            except Exception as exc:  # pragma: no cover - hardware dependent
                raise UIResourceError(f"Failed to initialise tripod: {exc}") from exc

            self._tripod = tripod
            return tripod

    @staticmethod
    def _default_camera_factory(config: Dict[str, Any]) -> CameraWrapper:
        if CameraWrapper is None:  # pragma: no cover - depends on environment
            raise UIResourceError(
                "Camera support is unavailable – ensure libgphoto2 is installed."
            )

        if "model" in config and "port" in config:
            return CameraWrapper(str(config["model"]), str(config["port"]))

        selector = (
            config.get("model_substring")
            or config.get("selector")
            or config.get("match")
            or config.get("model_hint")
        )
        if selector:
            return CameraWrapper.select_camera(str(selector))

        raise UIResourceError(
            "Camera configuration must define either 'model'/'port' or 'model_substring'."
        )

    @staticmethod
    def _default_tripod_factory(config: Dict[str, Any]) -> TripodController:
        if TripodController is None:  # pragma: no cover - depends on environment
            raise UIResourceError("Tripod support is unavailable – install pyserial.")
        if not config:
            raise UIResourceError("Tripod configuration is empty.")
        return TripodController(config)
