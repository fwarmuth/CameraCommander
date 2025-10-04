"""High-level Gradio layout placeholder."""

from __future__ import annotations

import gradio as gr

from ..state import AppState
from . import library, live_control, planner, session_monitor


def build_application(app_state: AppState) -> gr.Blocks:
    """Construct the top-level Gradio Blocks application.

    This placeholder sets up the tab structure without wiring any functionality.
    """

    with gr.Blocks(title="CameraCommander") as demo:
        shared_state = gr.State(app_state)
        active_job_state = gr.State(value=None)
        planner_clone_state = gr.State(value=None)

        gr.Markdown("# CameraCommander Gradio Application (Work in Progress)")
        with gr.TabbedInterface(
            [
                live_control.render_tab(shared_state),
                planner.render_tab(shared_state, active_job_state, planner_clone_state),
                session_monitor.render_tab(shared_state, active_job_state),
                library.render_tab(shared_state, planner_clone_state),
            ],
            [
                "Live Control",
                "Timelapse Planner",
                "Active Session",
                "Library",
            ],
        ):
            pass
    return demo
