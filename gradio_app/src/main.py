"""Entry point for launching the Gradio-first application."""

from __future__ import annotations

import asyncio

from state import AppState
import ui

_SERVER_NAME = "0.0.0.0"
_SERVER_PORT = 8000


async def _async_main() -> None:
    """Bootstrap and launch the Gradio application."""

    app_state = AppState()
    app = ui.build_application(app_state)

    # Enable background processing and block until the server stops.
    app.queue()

    try:
        await asyncio.to_thread(
            app.launch,
            server_name=_SERVER_NAME,
            server_port=_SERVER_PORT,
            show_api=False,
        )
    finally:
        await app_state.shutdown()


def main() -> None:
    """Run the asynchronous main entry point."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
