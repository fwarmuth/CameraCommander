"""Placeholder components for the Library tab."""

from __future__ import annotations

import gradio as gr


def render_tab() -> gr.Blocks:
    """Render the Library tab contents."""

    with gr.Blocks() as tab:
        gr.Markdown("## Library")
        gr.Markdown(
            "Completed recordings will be listed here for playback and management."
        )
    return tab
