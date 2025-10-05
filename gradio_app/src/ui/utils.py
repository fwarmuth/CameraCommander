"""Utility helpers shared across Gradio UI modules."""

from __future__ import annotations

import logging
from typing import Any

from services import CameraAdapterError, ServiceError, TripodAdapterError
from state import AppState, AppStateHandle

logger = logging.getLogger(__name__)


_HARDWARE_ERRORS = (ServiceError, CameraAdapterError, TripodAdapterError)


def unwrap_app_state(value: Any) -> AppState:
    """Return the :class:`AppState` stored in *value*.

    Gradio passes the raw value of ``gr.State`` instances into callbacks.  This
    helper performs a defensive type assertion so callback code can operate on
    ``AppState`` directly.
    """

    if isinstance(value, AppState):
        return value
    if isinstance(value, AppStateHandle):
        return value.state
    if value is None:
        return AppState.current()
    raise RuntimeError("Expected AppState in shared state.")


def format_hardware_error(context: str, exc: BaseException) -> str:
    """Return a user-friendly message for hardware access failures."""

    if isinstance(exc, _HARDWARE_ERRORS):
        details = getattr(exc, "code", exc.__class__.__name__)
        logger.warning("%s: %s (%s)", context, exc, details)
        return f"{context}: {exc}"

    logger.exception("Unexpected error during %s", context, exc_info=exc)
    return f"{context}: unexpected error ({exc})."


def hardware_access_blocked_message() -> str:
    """Message displayed when controls are disabled by an active job."""

    return "Hardware is currently reserved by an active timelapse job."
