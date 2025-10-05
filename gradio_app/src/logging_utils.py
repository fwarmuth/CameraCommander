"""Logging helpers shared across the Gradio application package."""

from __future__ import annotations

import logging

TRACE_LEVEL = 5


def ensure_trace_level() -> None:
    """Ensure a ``TRACE`` logging level is registered on :class:`logging.Logger`."""

    if hasattr(logging.Logger, "trace"):
        return

    logging.addLevelName(TRACE_LEVEL, "TRACE")

    def _trace(self: logging.Logger, message: str, *args: object, **kwargs: object) -> None:
        if self.isEnabledFor(TRACE_LEVEL):
            self._log(TRACE_LEVEL, message, args, **kwargs)  # type: ignore[arg-type]

    logging.Logger.trace = _trace  # type: ignore[attr-defined]


ensure_trace_level()


def configure_logging(verbosity: int) -> None:
    """Initialise the root logger with console output and *verbosity* support."""

    ensure_trace_level()

    if verbosity <= 0:
        level = logging.WARNING
    elif verbosity == 1:
        level = logging.INFO
    elif verbosity == 2:
        level = logging.DEBUG
    elif verbosity == 3:
        level = TRACE_LEVEL
    else:
        level = logging.NOTSET

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    logging.captureWarnings(True)
