"""High-level Gradio layout placeholder."""

from __future__ import annotations

import gradio as gr

from state import AppState, AppStateHandle
from . import live_control, planner, session_monitor


def build_application(app_state: AppState) -> gr.Blocks:
    """Construct the top-level Gradio Blocks application.

    This placeholder sets up the tab structure without wiring any functionality.
    """

    with gr.Blocks(title="CameraCommander") as demo:
        shared_state = gr.State(AppStateHandle(app_state))
        active_job_state = gr.State(value=None)

        gr.Markdown("# CameraCommander Gradio Application (Work in Progress)")
        with gr.Tabs():
            with gr.Tab("Live Control"):
                live_control.render_tab(shared_state)
            with gr.Tab("Timelapse Planner"):
                planner.render_tab(shared_state)
            with gr.Tab("Active Session"):
                session_monitor.render_tab(shared_state, active_job_state)
    return demo
