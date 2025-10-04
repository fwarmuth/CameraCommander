"""Placeholder components for the Timelapse Planner tab."""

from __future__ import annotations

import gradio as gr


def render_tab(_app_state: gr.State) -> gr.Blocks:
    """Render the Timelapse Planner tab contents."""

    with gr.Blocks() as tab:
        gr.Markdown("## Timelapse Planner")
        gr.Markdown(
            "This section will collect timelapse parameters and launch new jobs."
        )
    return tab
