"""Entry point for launching the Gradio-first application."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Iterable, Optional

from logging_utils import configure_logging
from models import TripodSerialSettings, TripodSettings
from state import AppState
import ui

_SERVER_NAME = "0.0.0.0"
_SERVER_PORT = 8000


_DEFAULT_TRIPOD_SETTINGS = TripodSettings(
    serial=TripodSerialSettings(
        port="/dev/ttyUSB0",
        baudrate=9600,
        # Give the adapter plenty of time (10 s) to hear back from long-running
        # moves before it assumes the firmware has hung.
        timeout=10.0,
        # Writes normally finish instantly, so we only budget half a second
        # before declaring the port wedged.
        write_timeout=0.5,
    ),
    microstep=16,
)

def _parse_args(argv: Optional[Iterable[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the CameraCommander Gradio UI")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity (-vvvv is the most verbose).",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


async def _async_main() -> None:
    """Bootstrap and launch the Gradio application."""

    app_state = AppState(tripod_settings=_DEFAULT_TRIPOD_SETTINGS)
    logger = logging.getLogger(__name__)

    logger.info("Initialising Gradio application")
    app = ui.build_application(app_state)
    print("[app] Gradio application initialised.")

    # Enable background processing and block until the server stops.
    app.queue()

    logger.info("Launching Gradio server on %s:%s", _SERVER_NAME, _SERVER_PORT)
    print(f"[app] Launching UI at http://{_SERVER_NAME}:{_SERVER_PORT}.")

    try:
        await asyncio.to_thread(
            app.launch,
            server_name=_SERVER_NAME,
            server_port=_SERVER_PORT,
            show_api=False,
        )
    finally:
        logger.info("Shutting down application state")
        print("[app] Shutting down application state.")
        await app_state.shutdown()


def main(argv: Optional[Iterable[str]] = None) -> None:
    """Run the asynchronous main entry point."""

    args = _parse_args(argv)
    configure_logging(args.verbose)
    logging.getLogger(__name__).debug(
        "Logging configured with verbosity=%s", args.verbose
    )
    asyncio.run(_async_main())


if __name__ == "__main__":
    main(sys.argv[1:])
