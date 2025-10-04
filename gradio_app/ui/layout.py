"""High-level Gradio layout placeholder."""

from __future__ import annotations

import gradio as gr

from . import library, live_control, planner, session_monitor


def build_application() -> gr.Blocks:
    """Construct the top-level Gradio Blocks application.

    This placeholder sets up the tab structure without wiring any functionality.
    """

    with gr.Blocks(title="CameraCommander") as demo:
        gr.Markdown("# CameraCommander Gradio Application (Work in Progress)")
        with gr.TabbedInterface(
            [
                live_control.render_tab(),
                planner.render_tab(),
                session_monitor.render_tab(),
                library.render_tab(),
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
