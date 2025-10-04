"""Gradio-first application package for CameraCommander."""

from .state import AppState
from .ui import build_application

__all__ = [
    "__version__",
    "AppState",
    "build_application",
]

__version__ = "0.1.0"
