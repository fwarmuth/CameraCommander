"""Timelapse planner UI wired to the async job runner."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import timedelta
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
from services.timelapse_runner import TimelapseJob
from state import AppState
from .paths import default_plan_output_dir
from .utils import hardware_access_blocked_message, log_button_click, unwrap_app_state

logger = logging.getLogger(__name__)


def render_tab(
    shared_app_state: gr.State,
    active_job_state: Optional[gr.State] = None,
    clone_request_state: Optional[gr.State] = None,
) -> gr.Blocks:
    """Render the Timelapse Planner tab contents."""

    if active_job_state is None:
        active_job_state = gr.State(value=None)
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
            with gr.Column():
                gr.Markdown("### Job Overview")
                job_id_display = gr.Textbox(
                    label="Current Job ID",
                    interactive=False,
                    placeholder="No active job",
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
        # Read/write timeout inputs help operators understand why long tripod moves
        # keep the UI busy – larger values mean "wait this many seconds before
        # declaring the controller unresponsive".
        with gr.Row():
            tripod_timeout = gr.Number(
                label="Read Timeout (seconds)",
                precision=2,
                value=10.0,
            )
            tripod_write_timeout = gr.Number(
                label="Write Timeout (seconds)",
                precision=2,
                value=0.5,
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

        submit_button = gr.Button("Schedule Timelapse", variant="primary")
        lock_banner = gr.Markdown("", elem_classes=["lock-banner"])

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

        preset_outputs: List[gr.components.Component] = [
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
            tripod_timeout,
            tripod_write_timeout,
            tripod_microstep,
            tripod_options,
            tags_box,
            notes_box,
            plan_summary,
            status_message,
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
            active_job_state,
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
            tripod_timeout,
            tripod_write_timeout,
            tripod_microstep,
            tripod_options,
            tags_box,
            notes_box,
        ]

        submit_button.click(
            log_button_click(
                "Schedule Timelapse",
                _schedule_timelapse,
                logger=logger,
            ),
            inputs=submission_inputs,
            outputs=[status_message, active_job_state, job_id_display, plan_summary],
        )

        tab.load(
            _observe_hardware_lock,
            inputs=[shared_app_state],
            outputs=[submit_button, lock_banner],
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
    plan = settings.plan
    serial = settings.tripod.serial

    auto_default = default_plan_output_dir(plan.name).as_posix()
    plan_output = (plan.output_dir or "").strip() or auto_default
    plan_summary = _summarise_plan(plan.copy(update={"output_dir": plan_output}))
    auto_state_value = auto_default
    custom_state_value = plan_output != auto_default

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
        plan.render_video,
        plan.video_fps or 30,
        plan.ffmpeg_extra or "",
        settings.camera.model_substring or "",
        _format_json(settings.camera.overrides),
        serial.port if serial else "",
        serial.baudrate if serial else 9600,
        serial.timeout if serial and serial.timeout is not None else 10.0,
        serial.write_timeout if serial and serial.write_timeout is not None else 0.5,
        settings.tripod.microstep,
        _format_json(settings.tripod.options),
        ", ".join(settings.tags),
        settings.notes or "",
        plan_summary,
        f"Cloned settings from session {session_id}.",
        auto_state_value,
        custom_state_value,
    ]

    return tuple(payload)


def _empty_preset_payload(status: str) -> List[Any]:
    # 27 component updates mirroring preset_outputs order.
    empty_values: List[Any] = [gr.update() for _ in range(23)]
    empty_values.append("")  # plan_summary
    empty_values.append(status)
    auto_default = default_plan_output_dir(None).as_posix()
    empty_values.append(auto_default)
    empty_values.append(False)
    return empty_values


def _resolve_session_id(request: Optional[Any]) -> Optional[str]:
    if isinstance(request, str):
        return request or None
    if isinstance(request, dict):
        candidate = request.get("session_id")
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


async def _observe_hardware_lock(app_state_value: Any) -> AsyncIterator[Tuple[Any, Any]]:
    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        async for locked in app_state.subscribe_hardware_lock():
            if locked:
                yield (
                    gr.update(interactive=False),
                    gr.update(value=hardware_access_blocked_message()),
                )
            else:
                yield (
                    gr.update(interactive=True),
                    gr.update(value=""),
                )


async def _schedule_timelapse(
    app_state_value: Any,
    current_job_id: Optional[str],
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
    tripod_timeout: Optional[float],
    tripod_write_timeout: Optional[float],
    tripod_microstep: Optional[float],
    tripod_options: Optional[str],
    tags: Optional[str],
    notes: Optional[str],
) -> Tuple[Any, Optional[str], Any, Any]:
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
            tripod_timeout,
            tripod_write_timeout,
            tripod_microstep,
            tripod_options,
            tags,
            notes,
        )
    except ValidationError as exc:
        return (
            gr.update(value=f"❌ Validation failed:\n````\n{exc}\n````"),
            current_job_id,
            gr.update(),
            gr.update(value=""),
        )
    except ValueError as exc:
        return (
            gr.update(value=f"❌ {exc}"),
            current_job_id,
            gr.update(),
            gr.update(value=""),
        )

    with AppState.use(app_state):
        if await app_state.jobs.has_active_job():
            return (
                gr.update(
                    value="⚠️ Cannot schedule a new job while another session is active."
                ),
                current_job_id,
                gr.update(),
                gr.update(value=_summarise_plan(settings.plan)),
            )

        app_state.set_tripod_settings(settings.tripod)

        job_id = uuid.uuid4().hex
        settings = settings.copy(update={"session_id": job_id})
        job = TimelapseJob(
            job_id=job_id,
            settings=settings.to_session_config(),
            recording=settings,
        )
        await app_state.start_job(job)

    message = _success_message(settings.plan, job_id)
    summary = _summarise_plan(settings.plan)
    return (
        gr.update(value=message),
        job_id,
        gr.update(value=job_id),
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
    tripod_timeout: Optional[float],
    tripod_write_timeout: Optional[float],
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
            timeout=float(tripod_timeout) if tripod_timeout is not None else None,
            write_timeout=(
                float(tripod_write_timeout) if tripod_write_timeout is not None else None
            ),
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


def _success_message(plan: TimelapsePlan, job_id: str) -> str:
    name = plan.name or "Untitled plan"
    duration_min = plan.duration_seconds / 60.0
    return (
        f"✅ Scheduled **{name}** as job `{job_id}`."
        f" Estimated capture time ≈ {duration_min:.1f} minutes."
    )
