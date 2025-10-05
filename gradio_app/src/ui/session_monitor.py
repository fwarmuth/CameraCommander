"""Active Session monitor UI bound to job runner updates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional, Tuple

import gradio as gr

from models import TimelapsePlan
from services.timelapse_runner import TimelapseJob, TimelapseJobStatus
from state import AppState
from .utils import unwrap_app_state

def render_tab(shared_app_state: gr.State, active_job_state: Optional[gr.State] = None) -> gr.Blocks:
    """Render the Active Session monitoring tab."""

    if active_job_state is None:
        active_job_state = gr.State(value=None)

    with gr.Blocks() as tab:
        gr.Markdown("## Active Session")

        job_start_state = gr.State(value=None)

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
        status_message = gr.Markdown("Awaiting new timelapse job.")
        cancel_button = gr.Button("Cancel Active Job", variant="stop", interactive=False)

        cancel_button.click(
            _cancel_job,
            inputs=[shared_app_state, active_job_state],
            outputs=[status_message],
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
                status_message,
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
