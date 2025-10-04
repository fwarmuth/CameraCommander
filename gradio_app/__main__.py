"""Entry point for launching the Gradio-first application."""

from __future__ import annotations

import asyncio


async def _async_main() -> None:
    """Placeholder async entry point for the upcoming Gradio application."""
    # NOTE: This placeholder will be replaced with the real app bootstrap logic.
    raise NotImplementedError(
        "Gradio application bootstrap has not been implemented yet."
    )


def main() -> None:
    """Run the asynchronous main entry point."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
