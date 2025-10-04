"""Placeholder components for the Active Session tab."""

from __future__ import annotations

import gradio as gr


def render_tab(_app_state: gr.State) -> gr.Blocks:
    """Render the Active Session monitoring tab."""

    with gr.Blocks() as tab:
        gr.Markdown("## Active Session")
        gr.Markdown(
            "Progress indicators and cancellation controls will appear here."
        )
    return tab
