"""Filesystem-backed repository for timelapse schedule drafts."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Set

from pydantic import ValidationError

from models import RecordingSettings

DEFAULT_SCHEDULE_ROOT = Path.home() / ".config" / "cameracommander" / "schedules"

logger = logging.getLogger(__name__)


@dataclass
class StoredSchedule:
    """Represents a persisted timelapse schedule file."""

    schedule_id: str
    settings: RecordingSettings
    path: Path

    @property
    def name(self) -> Optional[str]:
        """Expose the human-friendly plan name for convenience."""

        return self.settings.plan.name


class ScheduleRepository:
    """Simple JSON-backed storage for :class:`RecordingSettings` drafts."""

    def __init__(self, base_path: Path = DEFAULT_SCHEDULE_ROOT) -> None:
        self._base_path = base_path.expanduser().resolve()
        self._base_path.mkdir(parents=True, exist_ok=True)
        self._change_listeners: Set[Callable[[], None]] = set()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def save_schedule(
        self, settings: RecordingSettings, *, schedule_id: Optional[str] = None
    ) -> StoredSchedule:
        """Persist *settings* under a stable identifier and return the stored entry."""

        if schedule_id is None:
            schedule_id = self._generate_schedule_id(settings)
        schedule_id = schedule_id.strip()
        if not schedule_id:
            raise ValueError("schedule_id must be a non-empty string")

        payload = settings.copy(update={"session_id": schedule_id})
        path = self._path_for(schedule_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")

        stored = StoredSchedule(schedule_id=schedule_id, settings=payload, path=path)
        self._notify_changed()
        logger.info("Saved timelapse schedule %s -> %s", schedule_id, path)
        return stored

    def list_schedules(self) -> List[StoredSchedule]:
        """Return all known schedules sorted by ``created_at`` descending."""

        entries: List[StoredSchedule] = []
        for candidate in sorted(self._base_path.glob("*.json")):
            loaded = self._load_schedule(candidate)
            if loaded is not None:
                entries.append(loaded)

        entries.sort(key=_sort_key_created_at, reverse=True)
        return entries

    def get_schedule(self, schedule_id: str) -> Optional[StoredSchedule]:
        """Return the stored schedule for ``schedule_id`` if the file exists."""

        path = self._path_for(schedule_id)
        if not path.exists():
            return None
        return self._load_schedule(path)

    def delete_schedule(self, schedule_id: str) -> bool:
        """Remove the stored schedule for ``schedule_id``.

        Returns ``True`` when the file existed and was deleted.
        """

        path = self._path_for(schedule_id)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Failed to delete schedule %s: %s", schedule_id, exc)
            return False

        self._notify_changed()
        logger.info("Deleted timelapse schedule %s", schedule_id)
        return True

    # ------------------------------------------------------------------
    # Change listeners
    # ------------------------------------------------------------------
    def add_change_listener(self, callback: Callable[[], None]) -> None:
        self._change_listeners.add(callback)

    def remove_change_listener(self, callback: Callable[[], None]) -> None:
        self._change_listeners.discard(callback)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _generate_schedule_id(self, settings: RecordingSettings) -> str:
        plan = settings.plan
        slug_base = _slugify(plan.name or "schedule")
        utc_stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        unique = uuid.uuid4().hex[:8]
        candidate = f"{utc_stamp}-{slug_base}-{unique}"

        # Collisions are unlikely, but guard to keep IDs stable.
        while self._path_for(candidate).exists():
            unique = uuid.uuid4().hex[:8]
            candidate = f"{utc_stamp}-{slug_base}-{unique}"
        return candidate

    def _path_for(self, schedule_id: str) -> Path:
        filename = f"{schedule_id}.json"
        return self._base_path / filename

    def _load_schedule(self, path: Path) -> Optional[StoredSchedule]:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Unable to read schedule %s: %s", path, exc)
            return None

        try:
            data = json.loads(raw)
            settings = RecordingSettings.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("Skipping invalid schedule %s: %s", path, exc)
            return None

        schedule_id = path.stem
        if settings.session_id != schedule_id:
            settings = settings.copy(update={"session_id": schedule_id})

        return StoredSchedule(schedule_id=schedule_id, settings=settings, path=path)

    def _notify_changed(self) -> None:
        for callback in list(self._change_listeners):
            try:
                callback()
            except Exception:  # pragma: no cover - defensive guard
                logger.exception("Schedule change listener failed")


def _slugify(value: str) -> str:
    """Return a filesystem-friendly slug based on *value*."""

    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    slug = "-".join(filter(None, slug.split("-")))
    return slug or "schedule"


def _sort_key_created_at(entry: StoredSchedule) -> float:
    created_at = entry.settings.created_at
    if isinstance(created_at, datetime):
        return created_at.timestamp()
    try:
        return datetime.fromisoformat(str(created_at)).timestamp()
    except ValueError:
        return 0.0
