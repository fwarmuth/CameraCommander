"""Timelapse planner UI wired to the async job runner."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import gradio as gr
from pydantic import ValidationError

from ..models import (
    CameraSettings,
    RecordingSettings,
    TimelapsePlan,
    TripodSerialSettings,
    TripodSettings,
)
from ..services.timelapse_runner import TimelapseJob
from ..state import AppState
from .utils import hardware_access_blocked_message, unwrap_app_state


def render_tab(shared_app_state: gr.State, active_job_state: Optional[gr.State] = None) -> gr.Blocks:
    """Render the Timelapse Planner tab contents."""

    if active_job_state is None:
        active_job_state = gr.State(value=None)

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
            tripod_port = gr.Textbox(label="Serial Port", placeholder="/dev/ttyUSB0")
            tripod_baud = gr.Number(label="Baud Rate", precision=0, value=9600)
            tripod_microstep = gr.Number(
                label="Microstep",
                precision=0,
                value=None,
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
            _load_preset_choices,
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
            tripod_microstep,
            tripod_options,
            tags_box,
            notes_box,
            plan_summary,
            status_message,
        ]

        apply_preset.click(
            _clone_preset,
            inputs=[shared_app_state, preset_dropdown],
            outputs=preset_outputs,
        )

        # Inter-field behaviour ---------------------------------------------
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
            tripod_microstep,
            tripod_options,
            tags_box,
            notes_box,
        ]

        submit_button.click(
            _schedule_timelapse,
            inputs=submission_inputs,
            outputs=[status_message, active_job_state, job_id_display, plan_summary],
        )

        tab.load(
            _observe_hardware_lock,
            inputs=[shared_app_state],
            outputs=[submit_button, lock_banner],
            stream=True,
        )

    return tab


async def _load_preset_choices(app_state_value: Any) -> gr.Dropdown.update:
    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        sessions = await asyncio.to_thread(lambda: list(app_state.sessions.list_sessions()))

    choices: List[Tuple[str, str]] = []
    for session in sessions:
        label = session.summary.plan_name or session.summary.session_id
        timestamp = session.summary.created_at.strftime("%Y-%m-%d %H:%M")
        display = f"{label} — {timestamp} ({session.summary.session_id})"
        choices.append((display, session.summary.session_id))

    return gr.Dropdown.update(choices=choices, value=None)


async def _clone_preset(app_state_value: Any, session_id: Optional[str]) -> Tuple[Any, ...]:
    status: str

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
    plan = settings.plan
    serial = settings.tripod.serial

    plan_summary = _summarise_plan(plan)

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
        plan.output_dir,
        plan.render_video,
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
        plan_summary,
        f"Cloned settings from session {session_id}.",
    ]

    return tuple(payload)


def _empty_preset_payload(status: str) -> List[Any]:
    # 23 component updates mirroring preset_outputs order.
    empty_values: List[Any] = [gr.update() for _ in range(21)]
    empty_values.append("")  # plan_summary
    empty_values.append(status)
    return empty_values


async def _observe_hardware_lock(app_state_value: Any) -> AsyncIterator[Tuple[gr.Update, gr.Update]]:
    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        async for locked in app_state.subscribe_hardware_lock():
            if locked:
                yield (
                    gr.Button.update(interactive=False),
                    gr.Markdown.update(value=hardware_access_blocked_message()),
                )
            else:
                yield (
                    gr.Button.update(interactive=True),
                    gr.Markdown.update(value=""),
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
    tripod_microstep: Optional[float],
    tripod_options: Optional[str],
    tags: Optional[str],
    notes: Optional[str],
) -> Tuple[gr.Update, Optional[str], gr.Update, gr.Update]:
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
            gr.Markdown.update(value=f"❌ Validation failed:\n````\n{exc}\n````"),
            current_job_id,
            gr.Textbox.update(),
            gr.Markdown.update(value=""),
        )
    except ValueError as exc:
        return (
            gr.Markdown.update(value=f"❌ {exc}"),
            current_job_id,
            gr.Textbox.update(),
            gr.Markdown.update(value=""),
        )

    with AppState.use(app_state):
        if await app_state.jobs.has_active_job():
            return (
                gr.Markdown.update(
                    value="⚠️ Cannot schedule a new job while another session is active."
                ),
                current_job_id,
                gr.Textbox.update(),
                gr.Markdown.update(value=_summarise_plan(settings.plan)),
            )

        job_id = uuid.uuid4().hex
        settings = settings.copy(update={"session_id": job_id})
        job = TimelapseJob(job_id=job_id, settings=settings.to_session_config())
        await app_state.start_job(job)

    message = _success_message(settings.plan, job_id)
    summary = _summarise_plan(settings.plan)
    return (
        gr.Markdown.update(value=message),
        job_id,
        gr.Textbox.update(value=job_id),
        gr.Markdown.update(value=summary),
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
    if output_dir is None or not output_dir.strip():
        raise ValueError("Output directory is required.")

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
        output_dir=output_dir.strip(),
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


def _toggle_video_options(render_video: bool) -> gr.Number.update:
    return gr.Number.update(interactive=render_video)


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
