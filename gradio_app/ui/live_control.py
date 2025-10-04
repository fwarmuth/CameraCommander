"""Live control tab providing direct camera and tripod interaction."""

from __future__ import annotations

import functools
import logging
from typing import Dict, Iterable, List, Tuple

import gradio as gr

from ..state import AppState
from .utils import (
    format_hardware_error,
    hardware_access_blocked_message,
    unwrap_app_state,
)

logger = logging.getLogger(__name__)

_LIVE_VIEW_INTERVAL_SECONDS = 0.75
_FOCUS_PRESETS: List[Tuple[str, str, int]] = [
    ("Focus Near (Fine)", "near", 1),
    ("Focus Near (Coarse)", "near", 3),
    ("Focus Far (Fine)", "far", 1),
    ("Focus Far (Coarse)", "far", 3),
]
_TRIPOD_JOG_PRESETS: List[Tuple[str, float, float]] = [
    ("Pan Left", -1.5, 0.0),
    ("Pan Right", 1.5, 0.0),
    ("Tilt Up", 0.0, 1.5),
    ("Tilt Down", 0.0, -1.5),
]
_CAMERA_SETTING_FIELDS: List[Tuple[str, str]] = [
    ("main.capturesettings.iso", "ISO"),
    ("main.capturesettings.f-number", "Aperture"),
    ("main.capturesettings.shutterspeed", "Shutter Speed"),
    ("main.imgsettings.whitebalance", "White Balance"),
]


def render_tab(shared_app_state: gr.State) -> gr.Blocks:
    """Render the Live Control tab contents."""

    with gr.Blocks() as tab:
        live_view_active = gr.State(False)

        gr.Markdown("## Live Control")
        lock_banner = gr.Markdown("", elem_classes=["lock-banner"])
        status_message = gr.Markdown("", elem_classes=["status-message"])

        with gr.Row():
            live_view = gr.Image(
                label="Live View",
                interactive=False,
                show_label=True,
                elem_id="live-view-frame",
            )
            with gr.Column():
                with gr.Row():
                    start_live = gr.Button("Start Live View", variant="primary")
                    stop_live = gr.Button("Stop Live View", variant="secondary")
                gr.Markdown("### Focus Nudges")
                focus_buttons: List[gr.Button] = []
                for label, _, _ in _FOCUS_PRESETS:
                    focus_buttons.append(gr.Button(label))

        gr.Markdown("### Camera Settings")
        setting_components: List[gr.Dropdown] = []
        setting_by_key: Dict[str, gr.Dropdown] = {}
        with gr.Row():
            for key, label in _CAMERA_SETTING_FIELDS[:2]:
                control = gr.Dropdown(label=label, choices=[], interactive=False)
                setting_by_key[key] = control
                setting_components.append(control)
        with gr.Row():
            for key, label in _CAMERA_SETTING_FIELDS[2:]:
                control = gr.Dropdown(label=label, choices=[], interactive=False)
                setting_by_key[key] = control
                setting_components.append(control)

        gr.Markdown("### Tripod Jog")
        tripod_buttons: List[gr.Button] = []
        with gr.Row():
            for label, _, _ in _TRIPOD_JOG_PRESETS[:2]:
                tripod_buttons.append(gr.Button(label))
        with gr.Row():
            for label, _, _ in _TRIPOD_JOG_PRESETS[2:]:
                tripod_buttons.append(gr.Button(label))
        stop_tripod = gr.Button("Stop Tripod", variant="secondary")

        # Live view controls -------------------------------------------------
        start_live.click(
            _start_live_view,
            inputs=[shared_app_state, live_view_active],
            outputs=[live_view_active, live_view, status_message],
        )
        stop_live.click(
            _stop_live_view,
            inputs=[live_view_active],
            outputs=[live_view_active, status_message],
        )
        tab.load(
            _refresh_live_view,
            inputs=[shared_app_state, live_view_active],
            outputs=[live_view, live_view_active, status_message],
            every=_LIVE_VIEW_INTERVAL_SECONDS,
        )

        # Focus controls -----------------------------------------------------
        for button, (_, direction, step) in zip(focus_buttons, _FOCUS_PRESETS):
            button.click(
                functools.partial(_nudge_focus, direction=direction, step=step),
                inputs=[shared_app_state],
                outputs=status_message,
            )

        # Camera settings ----------------------------------------------------
        tab.load(
            _load_camera_settings,
            inputs=[shared_app_state],
            outputs=[*setting_components, status_message],
        )
        for key, label in _CAMERA_SETTING_FIELDS:
            control = setting_by_key[key]
            control.change(
                functools.partial(_update_camera_setting, setting=key, label=label),
                inputs=[shared_app_state, control],
                outputs=status_message,
            )

        # Tripod controls ----------------------------------------------------
        for button, (_, pan, tilt) in zip(tripod_buttons, _TRIPOD_JOG_PRESETS):
            button.click(
                functools.partial(_jog_tripod, pan=pan, tilt=tilt),
                inputs=[shared_app_state],
                outputs=status_message,
            )
        stop_tripod.click(
            _stop_tripod,
            inputs=[shared_app_state],
            outputs=status_message,
        )

        # Hardware lock observer --------------------------------------------
        tab.load(
            _observe_hardware_lock,
            inputs=[shared_app_state],
            outputs=[
                start_live,
                stop_live,
                *focus_buttons,
                *setting_components,
                *tripod_buttons,
                stop_tripod,
                lock_banner,
            ],
            stream=True,
        )

    return tab


async def _start_live_view(
    app_state_value: AppState,
    live_view_enabled: bool,
) -> Tuple[bool, object, str]:
    app_state = unwrap_app_state(app_state_value)
    is_running = bool(live_view_enabled)

    with AppState.use(app_state):
        if await app_state.jobs.has_active_job():
            return False, gr.update(), hardware_access_blocked_message()

        if is_running:
            return True, gr.update(), "Live view is already running."

        try:
            frame = await app_state.resources.capture_preview()
        except Exception as exc:  # pragma: no cover - relies on hardware
            message = format_hardware_error("Live view unavailable", exc)
            return False, gr.update(value=None), message

    return True, gr.update(value=frame), "Live view started."


async def _stop_live_view(live_view_enabled: bool) -> Tuple[bool, str]:
    if not live_view_enabled:
        return False, "Live view already stopped."
    return False, "Live view stopped."


async def _refresh_live_view(
    app_state_value: AppState,
    live_view_enabled: bool,
) -> Tuple[object, bool, object | str]:
    if not live_view_enabled:
        return gr.update(), False, gr.update()

    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        if await app_state.jobs.has_active_job():
            return gr.update(), False, hardware_access_blocked_message()
        try:
            frame = await app_state.resources.capture_preview()
        except Exception as exc:  # pragma: no cover - relies on hardware
            message = format_hardware_error("Live view failed", exc)
            return gr.update(value=None), False, message

    return gr.update(value=frame), True, gr.update()


async def _nudge_focus(
    app_state_value: AppState,
    *,
    direction: str,
    step: int,
) -> str:
    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        if await app_state.jobs.has_active_job():
            return hardware_access_blocked_message()
        try:
            await app_state.resources.drive_focus(
                direction=direction,
                step_size=step,
                live_view=True,
            )
        except Exception as exc:  # pragma: no cover - relies on hardware
            return format_hardware_error("Focus nudge failed", exc)

    return f"Focus moved {direction} (step {step})."


async def _load_camera_settings(app_state_value: AppState) -> Tuple[object, ...]:
    app_state = unwrap_app_state(app_state_value)
    updates: List[object] = []

    with AppState.use(app_state):
        if await app_state.jobs.has_active_job():
            for _ in _CAMERA_SETTING_FIELDS:
                updates.append(gr.update(choices=[], value=None, interactive=False))
            updates.append(hardware_access_blocked_message())
            return tuple(updates)
        try:
            snapshot = await app_state.resources.camera_settings_snapshot(
                include_metadata=True
            )
        except Exception as exc:  # pragma: no cover - relies on hardware
            message = format_hardware_error("Failed loading camera settings", exc)
            for _ in _CAMERA_SETTING_FIELDS:
                updates.append(gr.update(choices=[], value=None, interactive=False))
            updates.append(message)
            return tuple(updates)

    for key, _ in _CAMERA_SETTING_FIELDS:
        details = snapshot.get(key)
        if not details:
            updates.append(gr.update(choices=[], value=None, interactive=False))
            continue
        choices: Iterable[str] = details.get("choices") or []
        current = details.get("current")
        updates.append(
            gr.update(
                choices=list(choices),
                value=current,
                interactive=bool(list(choices)),
            )
        )

    updates.append(gr.update())
    return tuple(updates)


async def _update_camera_setting(
    app_state_value: AppState,
    selected_value: str | None,
    *,
    setting: str,
    label: str,
) -> str | object:
    if selected_value is None:
        return gr.update()

    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        if await app_state.jobs.has_active_job():
            return hardware_access_blocked_message()
        try:
            await app_state.resources.update_camera_settings(
                {setting: selected_value},
                step_policy="snap",
            )
        except Exception as exc:  # pragma: no cover - relies on hardware
            return format_hardware_error(f"Failed updating {label}", exc)

    return f"{label} set to {selected_value}."


async def _jog_tripod(
    app_state_value: AppState,
    *,
    pan: float,
    tilt: float,
) -> str:
    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        if await app_state.jobs.has_active_job():
            return hardware_access_blocked_message()
        try:
            await app_state.resources.move_tripod(pan_deg=pan, tilt_deg=tilt)
        except Exception as exc:  # pragma: no cover - relies on hardware
            return format_hardware_error("Tripod jog failed", exc)

    return f"Tripod jogged Δpan={pan:+.1f}°, Δtilt={tilt:+.1f}°."


async def _stop_tripod(app_state_value: AppState) -> str:
    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        if await app_state.jobs.has_active_job():
            return hardware_access_blocked_message()
        try:
            await app_state.resources.stop_tripod()
        except Exception as exc:  # pragma: no cover - relies on hardware
            return format_hardware_error("Tripod stop failed", exc)

    return "Tripod stop command sent."


async def _observe_hardware_lock(app_state_value: AppState):
    app_state = unwrap_app_state(app_state_value)

    with AppState.use(app_state):
        try:
            async for locked in app_state.subscribe_hardware_lock():
                shared_update = gr.update(interactive=not locked)
                updates = [shared_update, shared_update]
                updates.extend([shared_update] * len(_FOCUS_PRESETS))
                updates.extend([shared_update] * len(_CAMERA_SETTING_FIELDS))
                updates.extend([shared_update] * len(_TRIPOD_JOG_PRESETS))
                updates.append(shared_update)

                message = hardware_access_blocked_message() if locked else ""

                yield (
                    *updates,
                    message,
                )
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Hardware lock observer failed", exc_info=exc)
            shared_update = gr.update()
            updates = [shared_update, shared_update]
            updates.extend([shared_update] * len(_FOCUS_PRESETS))
            updates.extend([shared_update] * len(_CAMERA_SETTING_FIELDS))
            updates.extend([shared_update] * len(_TRIPOD_JOG_PRESETS))
            updates.append(shared_update)
            message = format_hardware_error("Hardware lock monitor failed", exc)
            yield (
                *updates,
                message,
            )
