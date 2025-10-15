"""Timelapse manager UI combining schedule control and job monitoring."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, List, Optional, Tuple

import gradio as gr

from models import RecordingSettings, TimelapsePlan
from services.timelapse_runner import TimelapseJob, TimelapseJobStatus
from state import AppState
from store.schedule_repository import StoredSchedule
from .utils import log_button_click, unwrap_app_state

logger = logging.getLogger(__name__)

_NO_SCHEDULES_MESSAGE = "No timelapse schedules saved yet."
_SELECT_SCHEDULE_PROMPT = "Select a schedule to view details."


async def _initialise_schedule_manager(app_state_value: Any) -> Tuple[Any, Any, Optional[str]]:
    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        schedules = await asyncio.to_thread(app_state.schedules.list_schedules)
    return _build_schedule_components(schedules, None)


async def _refresh_schedule_choices(
    app_state_value: Any, selected_schedule: Optional[str]
) -> Tuple[Any, Any, Optional[str]]:
    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        schedules = await asyncio.to_thread(app_state.schedules.list_schedules)
    return _build_schedule_components(schedules, selected_schedule)


async def _select_schedule(
    app_state_value: Any, requested_schedule: Optional[str]
) -> Tuple[Optional[str], Any]:
    if not requested_schedule:
        return None, gr.update(value=_SELECT_SCHEDULE_PROMPT)

    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        stored = await asyncio.to_thread(app_state.schedules.get_schedule, requested_schedule)

    if stored is None:
        message = f"Schedule `{requested_schedule}` no longer exists."
        return None, gr.update(value=message)

    summary = _schedule_summary(stored.settings)
    return stored.schedule_id, gr.update(value=summary)


async def _start_schedule(
    app_state_value: Any,
    selected_schedule: Optional[str],
    current_job_id: Optional[str],
) -> Tuple[Any, Optional[str], Optional[str]]:
    if not selected_schedule:
        return gr.update(value="Select a schedule before starting."), current_job_id, None

    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        has_active = await app_state.jobs.has_active_job()
        if has_active:
            message = "⚠️ Cannot start a new timelapse while another job is active."
            return gr.update(value=message), current_job_id, None

        stored = await asyncio.to_thread(app_state.schedules.get_schedule, selected_schedule)

        if stored is None:
            message = f"Schedule `{selected_schedule}` could not be found."
            return gr.update(value=message), current_job_id, None

        app_state.set_tripod_settings(stored.settings.tripod)

        job_id = uuid.uuid4().hex
        settings = stored.settings.copy(update={"session_id": job_id})
        job = TimelapseJob(
            job_id=job_id,
            settings=settings.to_session_config(),
            recording=settings,
        )
        await app_state.start_job(job)

    plan = settings.plan
    duration_min = plan.duration_seconds / 60.0
    message = (
        f"✅ Started schedule `{selected_schedule}` as job `{job_id}`."
        f" Estimated capture time ≈ {duration_min:.1f} minutes."
    )
    return gr.update(value=message), job_id, _now().isoformat()


async def _observe_schedule_repository(
    app_state_value: Any,
    selected_schedule: Optional[str],
) -> AsyncIterator[Tuple[Any, Any, Optional[str]]]:
    app_state = unwrap_app_state(app_state_value)
    current_selection = selected_schedule

    with AppState.use(app_state):
        async for _ in app_state.subscribe_schedules():
            schedules = await asyncio.to_thread(app_state.schedules.list_schedules)
            dropdown, details, current_selection = _build_schedule_components(
                schedules, current_selection
            )
            yield dropdown, details, current_selection


def _build_schedule_components(
    schedules: List[StoredSchedule], selection: Optional[str]
) -> Tuple[Any, Any, Optional[str]]:
    if schedules:
        valid_ids = {entry.schedule_id for entry in schedules}
        if selection not in valid_ids:
            selection = schedules[0].schedule_id
    else:
        selection = None

    choices = [(_format_schedule_label(entry), entry.schedule_id) for entry in schedules]

    target_settings: Optional[RecordingSettings] = None
    if selection is not None:
        for entry in schedules:
            if entry.schedule_id == selection:
                target_settings = entry.settings
                break

    if not schedules:
        summary = _NO_SCHEDULES_MESSAGE
    else:
        summary = _schedule_summary(target_settings)

    dropdown = gr.update(choices=choices, value=selection)
    details = gr.update(value=summary)
    return dropdown, details, selection


def _schedule_summary(
    settings: Optional[RecordingSettings],
) -> str:
    if settings is None:
        return _SELECT_SCHEDULE_PROMPT

    plan = settings.plan
    duration = timedelta(seconds=plan.duration_seconds)
    hours, remainder = divmod(int(duration.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        duration_str = f"{hours}h {minutes}m {seconds}s"
    else:
        duration_str = f"{minutes}m {seconds}s"

    summary_lines = [
        "**Plan Details**",
        f"- Frames: {plan.total_frames}",
        f"- Interval: {plan.interval_s:.2f}s",
        f"- Duration: {duration_str}",
        f"- Output: `{plan.output_dir}`",
    ]

    metadata_lines: List[str] = []
    camera_match = settings.camera.model_substring
    if camera_match:
        metadata_lines.append(f"- Camera match: {camera_match}")

    serial = settings.tripod.serial
    if serial and getattr(serial, "port", None):
        metadata_lines.append(f"- Tripod port: {serial.port}")

    if settings.tags:
        metadata_lines.append(f"- Tags: {', '.join(settings.tags)}")

    if settings.notes:
        metadata_lines.append(f"- Notes: {settings.notes}")

    if metadata_lines:
        summary_lines.append("")
        summary_lines.append("**Metadata**")
        summary_lines.extend(metadata_lines)

    return "\n".join(summary_lines)


def _format_schedule_label(entry: StoredSchedule) -> str:
    plan_name = entry.settings.plan.name or "Untitled"
    timestamp = _format_schedule_timestamp(entry.settings.created_at)
    return f"{plan_name} — {timestamp} ({entry.schedule_id})"


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

def render_tab(shared_app_state: gr.State, active_job_state: Optional[gr.State] = None) -> gr.Blocks:
    """Render the timelapse manager tab."""

    if active_job_state is None:
        active_job_state = gr.State(value=None)

    selected_schedule_state = gr.State(value=None)
    job_start_state = gr.State(value=None)

    with gr.Blocks() as tab:
        gr.Markdown("## Timelapse Manager")

        with gr.Row():
            with gr.Column(scale=1, min_width=280):
                gr.Markdown("### Saved Schedules")
                schedule_dropdown = gr.Dropdown(
                    label="Schedules",
                    choices=[],
                    value=None,
                    allow_custom_value=False,
                )
                refresh_button = gr.Button("Refresh Schedules", variant="secondary")
                start_button = gr.Button("Start Selected Schedule", variant="primary")
                schedule_details = gr.Markdown(
                    _NO_SCHEDULES_MESSAGE,
                    elem_classes=["plan-summary"],
                )
                schedule_status = gr.Markdown("", elem_classes=["status-message"])

            with gr.Column(scale=2):
                gr.Markdown("### Active Job")
                job_heading = gr.Markdown("### No active session")
                job_details = gr.Markdown("", elem_classes=["plan-summary"])
                progress_slider = gr.Slider(
                    label="Progress",
                    minimum=0,
                    maximum=100,
                    value=0,
                    step=0.1,
                    interactive=False,
                )
                eta_message = gr.Markdown("ETA: --")
                job_status_message = gr.Markdown("Awaiting new timelapse job.")
                cancel_button = gr.Button("Cancel Active Job", variant="stop", interactive=False)

        tab.load(
            _initialise_schedule_manager,
            inputs=[shared_app_state],
            outputs=[schedule_dropdown, schedule_details, selected_schedule_state],
        )

        refresh_button.click(
            log_button_click(
                "Refresh Schedules",
                _refresh_schedule_choices,
                logger=logger,
            ),
            inputs=[shared_app_state, selected_schedule_state],
            outputs=[schedule_dropdown, schedule_details, selected_schedule_state],
        )

        schedule_dropdown.change(
            _select_schedule,
            inputs=[shared_app_state, schedule_dropdown],
            outputs=[selected_schedule_state, schedule_details],
        )

        start_button.click(
            log_button_click(
                "Start Selected Schedule",
                _start_schedule,
                logger=logger,
            ),
            inputs=[shared_app_state, selected_schedule_state, active_job_state],
            outputs=[schedule_status, active_job_state, job_start_state],
        )

        cancel_button.click(
            log_button_click(
                "Cancel Active Job",
                _cancel_job,
                logger=logger,
            ),
            inputs=[shared_app_state, active_job_state],
            outputs=[job_status_message],
        )

        tab.load(
            _observe_schedule_repository,
            inputs=[shared_app_state, selected_schedule_state],
            outputs=[schedule_dropdown, schedule_details, selected_schedule_state],
        )

        tab.load(
            _monitor_active_job,
            inputs=[shared_app_state, active_job_state, job_start_state],
            outputs=[
                active_job_state,
                job_heading,
                job_details,
                progress_slider,
                eta_message,
                job_status_message,
                cancel_button,
                job_start_state,
            ],
        )

    return tab


async def _monitor_active_job(
    app_state_value: Any,
    job_id: Optional[str],
    started_at: Optional[str],
) -> AsyncIterator[Tuple[Optional[str], Any, Any, Any, Any, Any, Any, Optional[str]]]:
    app_state = unwrap_app_state(app_state_value)

    if not job_id:
        yield _idle_payload("No active timelapse job.")
        return

    start_dt = _parse_timestamp(started_at)
    last_job: Optional[TimelapseJob] = None

    with AppState.use(app_state):
        async for job in app_state.subscribe_job(job_id):
            last_job = job

            if job.status is TimelapseJobStatus.RUNNING and start_dt is None:
                start_dt = _now()

            yield _job_payload(job, start_dt)

    if last_job:
        descriptor = last_job.status.name.title()
        detail = last_job.message or descriptor
        final_message = f"{descriptor} (job {last_job.job_id}): {detail}"
    else:
        final_message = "No active timelapse job."
    yield _idle_payload(final_message)


async def _cancel_job(app_state_value: Any, job_id: Optional[str]) -> Any:
    if not job_id:
        return gr.update(value="No active job to cancel.")

    app_state = unwrap_app_state(app_state_value)
    with AppState.use(app_state):
        await app_state.jobs.cancel_job(job_id)

    return gr.update(value=f"Cancellation requested for job `{job_id}`.")


def _job_payload(
    job: TimelapseJob,
    start_dt: Optional[datetime],
) -> Tuple[Optional[str], Any, Any, Any, Any, Any, Any, Optional[str]]:
    plan_summary = _plan_summary(job)
    heading = f"### Job `{job.job_id}` — {job.status.name.title()}"
    eta = _format_eta(job, start_dt)
    progress_percent = max(0.0, min(job.progress, 1.0)) * 100
    cancel_enabled = job.status in {TimelapseJobStatus.PENDING, TimelapseJobStatus.RUNNING}
    status = job.message or "Awaiting runner updates."
    next_start = start_dt.isoformat() if start_dt else None

    return (
        job.job_id,
        gr.update(value=heading),
        gr.update(value=plan_summary),
        gr.update(value=progress_percent),
        gr.update(value=eta),
        gr.update(value=status),
        gr.update(interactive=cancel_enabled),
        next_start,
    )


def _idle_payload(message: str) -> Tuple[Optional[str], Any, Any, Any, Any, Any, Any, Optional[str]]:
    return (
        None,
        gr.update(value="### No active session"),
        gr.update(value=""),
        gr.update(value=0.0),
        gr.update(value="ETA: --"),
        gr.update(value=message),
        gr.update(interactive=False),
        None,
    )


def _plan_summary(job: TimelapseJob) -> str:
    plan = getattr(job, "recording", None)
    if plan is not None:
        try:
            plan_model = plan.plan
        except AttributeError:  # pragma: no cover - defensive guard
            plan_model = None
    else:
        plan_model = None

    if plan_model is None:
        try:
            plan_cfg = job.settings.get("timelapse", {})
            plan_model = TimelapsePlan.from_session_config(plan_cfg)
        except Exception:  # pragma: no cover - defensive guard
            return "Timelapse configuration unavailable."

    duration = timedelta(seconds=plan_model.duration_seconds)
    minutes, seconds = divmod(int(duration.total_seconds()), 60)
    hours, minutes = divmod(minutes, 60)
    duration_str = (
        f"{hours}h {minutes}m {seconds}s"
        if hours
        else f"{minutes}m {seconds}s"
    )

    return (
        f"**Plan Details**\n"
        f"- Frames: {plan_model.total_frames}\n"
        f"- Interval: {plan_model.interval_s:.2f}s\n"
        f"- Duration: {duration_str}\n"
        f"- Output: `{plan_model.output_dir}`"
    )


def _format_eta(job: TimelapseJob, start_dt: Optional[datetime]) -> str:
    if job.status not in {TimelapseJobStatus.RUNNING, TimelapseJobStatus.PENDING}:
        return "ETA: --"

    recording = getattr(job, "recording", None)
    if recording is not None:
        total_frames = recording.plan.total_frames
        interval_s = recording.plan.interval_s
    else:
        plan_cfg = job.settings.get("timelapse", {})
        total_frames = plan_cfg.get("total_frames")
        interval_s = plan_cfg.get("interval_s")
        if not total_frames or not interval_s:
            return "ETA: estimating..."

    total_duration = float(total_frames) * float(interval_s)
    progress = max(0.0, min(job.progress, 1.0))

    if progress <= 0.0:
        remaining = total_duration
    else:
        if start_dt is None:
            start_dt = _now()
        elapsed = (_now() - start_dt).total_seconds()
        remaining = max(elapsed * (1.0 - progress) / progress, 0.0)

    eta_ts = _now() + timedelta(seconds=remaining)
    minutes_remaining = remaining / 60.0
    return (
        f"ETA: {eta_ts.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
        f" — {minutes_remaining:.1f} min remaining"
    )


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)
