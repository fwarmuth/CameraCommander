"""Placeholder components for the Live Control tab."""

from __future__ import annotations

import gradio as gr


def render_tab() -> gr.Blocks:
    """Render the Live Control tab contents."""

    with gr.Blocks() as tab:
        gr.Markdown("## Live Control")
        gr.Markdown(
            "This area will provide live view streaming, focus controls, and "
            "camera/tripod adjustments."
        )
    return tab
