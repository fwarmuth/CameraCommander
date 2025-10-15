"""Timelapse planner UI for authoring reusable schedules."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import gradio as gr
from pydantic import ValidationError

from models import (
    CameraSettings,
    RecordingSettings,
    TimelapsePlan,
    TripodSerialSettings,
    TripodSettings,
)
from state import AppState
from store.schedule_repository import StoredSchedule
from .paths import default_plan_output_dir
from .utils import log_button_click, unwrap_app_state

logger = logging.getLogger(__name__)


def render_tab(
    shared_app_state: gr.State,
    clone_request_state: Optional[gr.State] = None,
) -> gr.Blocks:
    """Render the Timelapse Planner tab contents."""

    if clone_request_state is None:
        clone_request_state = gr.State(value=None)

    initial_auto_output = default_plan_output_dir(None)
    auto_output_dir_state = gr.State(value=initial_auto_output.as_posix())
    output_dir_custom_state = gr.State(value=False)

    with gr.Blocks() as tab:
        gr.Markdown("## Timelapse Planner")
        status_message = gr.Markdown("", elem_classes=["status-message"])

        with gr.Row():
            with gr.Column():
                gr.Markdown("### Presets")
                preset_dropdown = gr.Dropdown(
                    label="Clone from Previous Session",
                    choices=[],
                    value=None,
                    allow_custom_value=False,
                )
                refresh_presets = gr.Button("Refresh Presets", variant="secondary")
                apply_preset = gr.Button("Clone Selected Preset", variant="secondary")

                gr.Markdown("### Saved Schedules")
                saved_schedule_dropdown = gr.Dropdown(
                    label="Load Saved Schedule",
                    choices=[],
                    value=None,
                    allow_custom_value=False,
                )
                refresh_saved_schedules = gr.Button(
                    "Refresh Saved Schedules", variant="secondary"
                )
                load_saved_schedule = gr.Button(
                    "Load Selected Schedule", variant="secondary"
                )
            with gr.Column():
                gr.Markdown("### Schedule Overview")
                schedule_id_display = gr.Textbox(
                    label="Active Schedule ID",
                    interactive=False,
                    placeholder="No loaded schedules",
                )
                plan_summary = gr.Markdown("", elem_classes=["plan-summary"])

        gr.Markdown("### Timelapse Plan")
        with gr.Row():
            plan_name = gr.Textbox(label="Plan Name", placeholder="Sunset Ridge", lines=1)
            plan_description = gr.Textbox(
                label="Description",
                placeholder="Optional operator notes for this recording.",
                lines=1,
            )
        with gr.Row():
            total_frames = gr.Number(label="Total Frames", precision=0, value=240)
            interval_s = gr.Number(label="Interval (seconds)", precision=2, value=10.0)
            settle_time_s = gr.Number(label="Settle Time (seconds)", precision=2, value=2.0)
        with gr.Row():
            start_pan = gr.Number(label="Start Pan (°)", precision=3, value=0.0)
            start_tilt = gr.Number(label="Start Tilt (°)", precision=3, value=0.0)
            target_pan = gr.Number(label="Target Pan (°)", precision=3, value=15.0)
            target_tilt = gr.Number(label="Target Tilt (°)", precision=3, value=5.0)
        with gr.Row():
            output_dir = gr.Textbox(
                label="Output Directory",
                placeholder="/data/timelapse_runs/sunset",  # noqa: E501
                value=initial_auto_output.as_posix(),
            )
        with gr.Row():
            render_video = gr.Checkbox(label="Render Video", value=True)
            video_fps = gr.Number(label="Video FPS", precision=0, value=30)
            ffmpeg_extra = gr.Textbox(
                label="FFmpeg Extra Flags",
                placeholder="-vf eq=contrast=1.2",  # noqa: E501
            )

        gr.Markdown("### Camera Settings")
        with gr.Row():
            camera_model = gr.Textbox(
                label="Model Substring",
                placeholder="ILCE-7M3",
            )
            camera_overrides = gr.Textbox(
                label="Overrides (JSON)",
                placeholder='{"main.capturesettings.iso": "400"}',
            )

        gr.Markdown("### Tripod Settings")
        with gr.Row():
            tripod_port = gr.Textbox(label="Serial Port", value="/dev/ttyUSB0")
            tripod_baud = gr.Number(label="Baud Rate", precision=0, value=9600)
            tripod_microstep = gr.Number(
                label="Microstep",
                precision=0,
                value=8,
            )
        with gr.Row():
            tripod_options = gr.Textbox(
                label="Controller Options (JSON)",
                placeholder='{"driver": "DRV8825"}',
            )

        gr.Markdown("### Session Metadata")
        with gr.Row():
            tags_box = gr.Textbox(
                label="Tags",
                placeholder="sunset, test, ridge",
                lines=1,
            )
            notes_box = gr.Textbox(
                label="Notes",
                placeholder="Weather looking clear – remember ND filter.",
                lines=2,
            )

        save_button = gr.Button("Save Timelapse Schedule", variant="primary")

        # Preset helpers -----------------------------------------------------
        tab.load(
            _load_preset_choices,
            inputs=[shared_app_state],
            outputs=[preset_dropdown],
        )
        refresh_presets.click(
            log_button_click(
                "Refresh Presets",
                _load_preset_choices,
                logger=logger,
            ),
            inputs=[shared_app_state],
            outputs=[preset_dropdown],
        )

        form_components: List[gr.components.Component] = [
            plan_name,
            plan_description,
            total_frames,
            interval_s,
            settle_time_s,
            start_pan,
            start_tilt,
            target_pan,
            target_tilt,
            output_dir,
            render_video,
            video_fps,
            ffmpeg_extra,
            camera_model,
            camera_overrides,
            tripod_port,
            tripod_baud,
            tripod_microstep,
            tripod_options,
            tags_box,
            notes_box,
        ]

        preset_outputs: List[gr.components.Component] = [
            *form_components,
            plan_summary,
            status_message,
            schedule_id_display,
            auto_output_dir_state,
            output_dir_custom_state,
        ]

        apply_preset.click(
            log_button_click(
                "Clone Selected Preset",
                _clone_preset,
                logger=logger,
            ),
            inputs=[shared_app_state, preset_dropdown],
            outputs=preset_outputs,
        )

        clone_request_state.change(
            _clone_preset,
            inputs=[shared_app_state, clone_request_state],
            outputs=preset_outputs,
        )

        tab.load(
            _load_saved_schedule_choices,
            inputs=[shared_app_state],
            outputs=[saved_schedule_dropdown],
        )
        tab.load(
            _observe_saved_schedule_updates,
            inputs=[shared_app_state],
            outputs=[saved_schedule_dropdown],
        )
        refresh_saved_schedules.click(
            log_button_click(
                "Refresh Saved Schedules",
                _load_saved_schedule_choices,
                logger=logger,
            ),
            inputs=[shared_app_state],
            outputs=[saved_schedule_dropdown],
        )
        load_saved_schedule.click(
            log_button_click(
                "Load Selected Schedule",
                _load_saved_schedule,
                logger=logger,
            ),
            inputs=[shared_app_state, saved_schedule_dropdown],
            outputs=preset_outputs,
        )

        # Inter-field behaviour ---------------------------------------------
        plan_name.change(
            _sync_output_dir_with_plan,
            inputs=[plan_name, auto_output_dir_state, output_dir_custom_state],
            outputs=[output_dir, auto_output_dir_state],
        )

        output_dir.change(
            _track_output_dir_customisation,
            inputs=[output_dir, auto_output_dir_state],
            outputs=[output_dir_custom_state],
        )

        render_video.change(
            _toggle_video_options,
            inputs=[render_video],
            outputs=[video_fps],
        )

        # Job submission -----------------------------------------------------
        submission_inputs: List[gr.components.Component] = [
            shared_app_state,
            plan_name,
            plan_description,
            total_frames,
            interval_s,
            settle_time_s,
            start_pan,
            start_tilt,
            target_pan,
            target_tilt,
            output_dir,
            render_video,
            video_fps,
            ffmpeg_extra,
            camera_model,
            camera_overrides,
            tripod_port,
            tripod_baud,
            tripod_microstep,
            tripod_options,
            tags_box,
            notes_box,
        ]

        save_button.click(
            log_button_click(
                "Save Timelapse Schedule",
                _save_schedule,
                logger=logger,
            ),
            inputs=submission_inputs,
            outputs=[status_message, schedule_id_display, plan_summary],
        )

    return tab


async def _load_preset_choices(app_state_value: Any) -> Any:
    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        sessions = await asyncio.to_thread(lambda: list(app_state.sessions.list_sessions()))

    choices: List[Tuple[str, str]] = []
    for session in sessions:
        label = session.summary.plan_name or session.summary.session_id
        timestamp = session.summary.created_at.strftime("%Y-%m-%d %H:%M")
        display = f"{label} — {timestamp} ({session.summary.session_id})"
        choices.append((display, session.summary.session_id))

    return gr.update(choices=choices, value=None)


async def _load_saved_schedule_choices(app_state_value: Any) -> Any:
    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        schedules = await asyncio.to_thread(app_state.schedules.list_schedules)

    choices = [_format_saved_schedule_label(entry) for entry in schedules]
    return gr.update(choices=choices, value=None)


async def _observe_saved_schedule_updates(
    app_state_value: Any,
) -> AsyncIterator[Any]:
    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        async for _ in app_state.subscribe_schedules():
            schedules = await asyncio.to_thread(app_state.schedules.list_schedules)
            choices = [_format_saved_schedule_label(entry) for entry in schedules]
            yield gr.update(choices=choices)


async def _clone_preset(app_state_value: Any, request: Optional[Any]) -> Tuple[Any, ...]:
    status: str

    session_id = _resolve_session_id(request)

    if not session_id:
        status = "Select a preset session to clone settings."
        return (*_empty_preset_payload(status),)

    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        stored = await asyncio.to_thread(app_state.sessions.get_session, session_id)

    if stored is None or stored.settings is None:
        status = "Preset does not include saved planner settings."
        return (*_empty_preset_payload(status),)

    settings = stored.settings
    with AppState.use(app_state):
        app_state.set_tripod_settings(settings.tripod)

    return _settings_to_form_payload(
        settings,
        f"Cloned settings from session {session_id}.",
        schedule_id=None,
    )


def _empty_preset_payload(status: str) -> List[Any]:
    # Matches the ordering defined in ``preset_outputs``.
    empty_values: List[Any] = [gr.update() for _ in range(21)]
    empty_values.append("")  # plan_summary
    empty_values.append(status)
    empty_values.append("")  # schedule_id_display
    auto_default = default_plan_output_dir(None).as_posix()
    empty_values.append(auto_default)  # auto_output_dir_state
    empty_values.append(False)  # output_dir_custom_state
    return empty_values


def _settings_to_form_payload(
    settings: RecordingSettings,
    status: str,
    *,
    schedule_id: Optional[str],
) -> Tuple[Any, ...]:
    plan = settings.plan
    serial = settings.tripod.serial

    auto_default = default_plan_output_dir(plan.name).as_posix()
    plan_output = (plan.output_dir or "").strip() or auto_default
    summary = _summarise_plan(plan.copy(update={"output_dir": plan_output}))
    custom_state = plan_output != auto_default

    payload: List[Any] = [
        plan.name or "",
        plan.description or "",
        plan.total_frames,
        float(plan.interval_s),
        float(plan.settle_time_s),
        float(plan.start.pan),
        float(plan.start.tilt),
        float(plan.target.pan),
        float(plan.target.tilt),
        plan_output,
        bool(plan.render_video),
        plan.video_fps or 30,
        plan.ffmpeg_extra or "",
        settings.camera.model_substring or "",
        _format_json(settings.camera.overrides),
        serial.port if serial else "",
        serial.baudrate if serial else 9600,
        settings.tripod.microstep,
        _format_json(settings.tripod.options),
        ", ".join(settings.tags),
        settings.notes or "",
        summary,
        status,
        schedule_id or "",
        auto_default,
        custom_state,
    ]

    return tuple(payload)


async def _load_saved_schedule(
    app_state_value: Any,
    schedule_id: Optional[str],
) -> Tuple[Any, ...]:
    if not schedule_id:
        status = "Select a saved schedule to load."
        return (*_empty_preset_payload(status),)

    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        stored = await asyncio.to_thread(app_state.schedules.get_schedule, schedule_id)

    if stored is None:
        status = f"Schedule `{schedule_id}` could not be found."
        return (*_empty_preset_payload(status),)

    settings = stored.settings
    with AppState.use(app_state):
        app_state.set_tripod_settings(settings.tripod)

    return _settings_to_form_payload(
        settings,
        f"Loaded schedule `{schedule_id}`.",
        schedule_id=schedule_id,
    )


def _resolve_session_id(request: Optional[Any]) -> Optional[str]:
    if isinstance(request, str):
        return request or None
    if isinstance(request, dict):
        candidate = request.get("session_id")
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


async def _save_schedule(
    app_state_value: Any,
    plan_name: Optional[str],
    plan_description: Optional[str],
    total_frames: Optional[float],
    interval_s: Optional[float],
    settle_time_s: Optional[float],
    start_pan: Optional[float],
    start_tilt: Optional[float],
    target_pan: Optional[float],
    target_tilt: Optional[float],
    output_dir: Optional[str],
    render_video: bool,
    video_fps: Optional[float],
    ffmpeg_extra: Optional[str],
    camera_model: Optional[str],
    camera_overrides: Optional[str],
    tripod_port: Optional[str],
    tripod_baud: Optional[float],
    tripod_microstep: Optional[float],
    tripod_options: Optional[str],
    tags: Optional[str],
    notes: Optional[str],
) -> Tuple[Any, Any, Any]:
    app_state = unwrap_app_state(app_state_value)

    try:
        settings = _build_recording_settings(
            plan_name,
            plan_description,
            total_frames,
            interval_s,
            settle_time_s,
            start_pan,
            start_tilt,
            target_pan,
            target_tilt,
            output_dir,
            render_video,
            video_fps,
            ffmpeg_extra,
            camera_model,
            camera_overrides,
            tripod_port,
            tripod_baud,
            tripod_microstep,
            tripod_options,
            tags,
            notes,
        )
    except ValidationError as exc:
        return (
            gr.update(value=f"❌ Validation failed:\n````\n{exc}\n````"),
            gr.update(value=""),
            gr.update(value=""),
        )
    except ValueError as exc:
        return (
            gr.update(value=f"❌ {exc}"),
            gr.update(value=""),
            gr.update(value=""),
        )

    with AppState.use(app_state):
        app_state.set_tripod_settings(settings.tripod)
        stored = await asyncio.to_thread(app_state.schedules.save_schedule, settings)

    summary = _summarise_plan(stored.settings.plan)
    message = _schedule_saved_message(stored.schedule_id, stored.path)
    return (
        gr.update(value=message),
        gr.update(value=stored.schedule_id),
        gr.update(value=summary),
    )


def _build_recording_settings(
    plan_name: Optional[str],
    plan_description: Optional[str],
    total_frames: Optional[float],
    interval_s: Optional[float],
    settle_time_s: Optional[float],
    start_pan: Optional[float],
    start_tilt: Optional[float],
    target_pan: Optional[float],
    target_tilt: Optional[float],
    output_dir: Optional[str],
    render_video: bool,
    video_fps: Optional[float],
    ffmpeg_extra: Optional[str],
    camera_model: Optional[str],
    camera_overrides: Optional[str],
    tripod_port: Optional[str],
    tripod_baud: Optional[float],
    tripod_microstep: Optional[float],
    tripod_options: Optional[str],
    tags: Optional[str],
    notes: Optional[str],
) -> RecordingSettings:
    if total_frames is None or total_frames < 2:
        raise ValueError("Total frames must be at least 2.")
    if interval_s is None or interval_s <= 0:
        raise ValueError("Interval must be positive.")
    if settle_time_s is None or settle_time_s < 0:
        raise ValueError("Settle time cannot be negative.")
    fallback_slug = None
    if output_dir and output_dir.strip():
        fallback_slug = Path(output_dir.strip()).name
    auto_output_dir = default_plan_output_dir(plan_name, fallback_slug=fallback_slug)
    resolved_output_dir = (
        output_dir.strip() if output_dir and output_dir.strip() else auto_output_dir.as_posix()
    )

    camera = CameraSettings(
        model_substring=camera_model or None,
        overrides=_parse_json_object(camera_overrides, field="Camera overrides"),
    )

    serial_settings = None
    if tripod_port:
        serial_settings = TripodSerialSettings(
            port=tripod_port,
            baudrate=int(tripod_baud) if tripod_baud else 9600,
        )

    tripod = TripodSettings(
        serial=serial_settings,
        microstep=int(tripod_microstep) if tripod_microstep else None,
        options=_parse_json_object(tripod_options, field="Tripod options"),
    )

    plan = TimelapsePlan(
        name=plan_name or None,
        description=plan_description or None,
        total_frames=int(total_frames),
        interval_s=float(interval_s),
        settle_time_s=float(settle_time_s),
        start={"pan": float(start_pan or 0.0), "tilt": float(start_tilt or 0.0)},
        target={"pan": float(target_pan or 0.0), "tilt": float(target_tilt or 0.0)},
        output_dir=resolved_output_dir,
        render_video=bool(render_video),
        video_fps=int(video_fps) if render_video and video_fps else None,
        ffmpeg_extra=(ffmpeg_extra or None),
    )

    tag_list = _parse_tags(tags)

    return RecordingSettings(
        camera=camera,
        tripod=tripod,
        plan=plan,
        tags=tag_list,
        notes=notes or None,
    )


def _parse_json_object(raw: Optional[str], *, field: str) -> Dict[str, Any]:
    if raw is None:
        return {}
    text = raw.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{field} must be a JSON object.")
    return data


def _parse_tags(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    parts = [item.strip() for item in raw.replace("\n", ",").split(",")]
    return [item for item in parts if item]


def _sync_output_dir_with_plan(
    plan_name: Optional[str],
    auto_output_dir: Optional[str],
    is_custom: bool,
) -> Tuple[Any, str]:
    fallback_slug = None
    if auto_output_dir:
        fallback_slug = Path(auto_output_dir).name
    new_auto_output = default_plan_output_dir(plan_name, fallback_slug=fallback_slug).as_posix()
    if is_custom:
        return (gr.update(), new_auto_output)
    return (gr.update(value=new_auto_output), new_auto_output)


def _track_output_dir_customisation(
    current_value: Optional[str],
    auto_output_dir: Optional[str],
) -> bool:
    current = (current_value or "").strip()
    auto = (auto_output_dir or "").strip()
    if not current:
        return False
    if not auto:
        return False
    return current != auto


def _toggle_video_options(render_video: bool) -> Any:
    return gr.update(interactive=render_video)


def _format_json(payload: Dict[str, Any]) -> str:
    if not payload:
        return ""
    return json.dumps(payload, indent=2, sort_keys=True)


def _format_saved_schedule_label(entry: StoredSchedule) -> Tuple[str, str]:
    plan_name = entry.settings.plan.name or "Untitled"
    timestamp = _format_schedule_timestamp(entry.settings.created_at)
    label = f"{plan_name} — {timestamp} ({entry.schedule_id})"
    return label, entry.schedule_id


def _format_schedule_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        try:
            return value.astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value.strftime("%Y-%m-%d %H:%M")
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _summarise_plan(plan: TimelapsePlan) -> str:
    duration = timedelta(seconds=plan.duration_seconds)
    hours, remainder = divmod(duration.total_seconds(), 3600)
    minutes, seconds = divmod(remainder, 60)
    duration_str = (
        f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
        if hours
        else f"{int(minutes)}m {int(seconds)}s"
    )
    end_frames = plan.total_frames
    return (
        f"**Plan Summary**\n"
        f"- Frames: {end_frames}\n"
        f"- Interval: {plan.interval_s:.2f}s\n"
        f"- Duration: {duration_str}\n"
        f"- Output: `{plan.output_dir}`"
    )


def _schedule_saved_message(schedule_id: str, path: Path) -> str:
    return (
        f"✅ Saved schedule `{schedule_id}`."
        f" Stored at `{path}`."
    )
