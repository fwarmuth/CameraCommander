"""Application state container placeholder."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .services import AsyncResourceManager, TimelapseJobRunner


@dataclass
class AppState:
    """Centralized application state for the upcoming Gradio UI."""

    resources: AsyncResourceManager = field(default_factory=AsyncResourceManager)
    jobs: TimelapseJobRunner = field(default_factory=TimelapseJobRunner)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def shutdown(self) -> None:
        """Placeholder shutdown hook for cleaning up application state."""

        await self.resources.shutdown()
