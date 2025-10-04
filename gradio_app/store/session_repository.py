"""Recording repository placeholder."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from ..models import RecordingAsset, RecordingSettings, RecordingSummary


DEFAULT_REPOSITORY_ROOT = Path.home() / ".cameracommander" / "recordings"


@dataclass
class StoredSession:
    """Represents a stored timelapse session on disk."""

    summary: RecordingSummary
    base_path: Path
    settings: Optional[RecordingSettings] = None
    assets: Sequence[RecordingAsset] = field(default_factory=tuple)

    @property
    def session_id(self) -> str:
        """Expose the session identifier for convenience."""

        return self.summary.session_id


class SessionRepository:
    """Placeholder repository for storing and retrieving session metadata."""

    def __init__(self, base_path: Path = DEFAULT_REPOSITORY_ROOT) -> None:
        self._base_path = base_path
        self._base_path.mkdir(parents=True, exist_ok=True)

    def register_session(self, session: StoredSession) -> None:
        """Persist metadata for a completed timelapse session."""

        raise NotImplementedError("Session registration is not implemented yet.")

    def list_sessions(self) -> Iterable[StoredSession]:
        """Return all known sessions."""

        raise NotImplementedError("Listing stored sessions is not implemented yet.")

    def get_session(self, session_id: str) -> Optional[StoredSession]:
        """Retrieve a specific session by identifier."""

        raise NotImplementedError("Fetching a stored session is not implemented yet.")

    def delete_session(self, session_id: str) -> None:
        """Remove a session and its assets from disk."""

        raise NotImplementedError("Deleting a stored session is not implemented yet.")
