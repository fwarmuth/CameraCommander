"""Gradio-first application package for CameraCommander."""

import os
import tempfile
from pathlib import Path


def _configure_tempdir() -> None:
    """Ensure Gradio has somewhere writable for temporary files."""

    override = os.environ.get("CAMERACOMMANDER_TMPDIR")
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates.append(Path.home() / ".cache" / "cameracommander" / "tmp")
    candidates.append(Path.cwd() / ".tmp")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        if os.access(candidate, os.W_OK | os.X_OK):
            location = str(candidate)
            os.environ.setdefault("TMPDIR", location)
            os.environ.setdefault("TEMP", location)
            os.environ.setdefault("TMP", location)
            tempfile.tempdir = location
            return


_configure_tempdir()

from .state import AppState
from .ui import build_application

__all__ = [
    "__version__",
    "AppState",
    "build_application",
]

__version__ = "0.1.0"
