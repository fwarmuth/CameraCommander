"""Filesystem-backed repository for completed timelapse sessions."""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Set

from pydantic import ValidationError

from models import RecordingAsset, RecordingAssetType, RecordingSettings, RecordingSummary


DEFAULT_REPOSITORY_ROOT = Path.home() / ".cameracommander" / "recordings"


logger = logging.getLogger(__name__)


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
    """Filesystem-backed repository for ``TimelapseSession`` artefacts."""

    METADATA_FILENAME = "metadata.json"
    SETTINGS_FILENAME = "settings.json"
    OUTPUT_SUBDIR = Path("output")

    def __init__(self, base_path: Path = DEFAULT_REPOSITORY_ROOT) -> None:
        self._base_path = base_path
        self._base_path.mkdir(parents=True, exist_ok=True)
        self._change_listeners: Set[Callable[[], None]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def register_session(self, session: StoredSession) -> StoredSession:
        """Persist *session* and return the stored representation.

        ``StoredSession.summary.output_dir`` is treated as the source directory
        for assets produced by the timelapse runner. All artefacts are copied or
        linked into the repository under ``session.summary.session_id``.
        """

        session_id = session.summary.session_id
        destination_root = self._base_path / session_id
        if destination_root.exists():
            raise FileExistsError(f"Session '{session_id}' already registered")

        source_root = Path(session.summary.output_dir).expanduser().resolve()
        destination_output_rel = self.OUTPUT_SUBDIR
        destination_output = destination_root / destination_output_rel

        destination_root.mkdir(parents=True, exist_ok=True)

        if source_root.exists():
            try:
                if source_root.is_dir():
                    self._link_or_copy_directory(source_root, destination_output)
                else:
                    destination_output.parent.mkdir(parents=True, exist_ok=True)
                    self._link_or_copy_file(source_root, destination_output)
            except Exception:
                shutil.rmtree(destination_root, ignore_errors=True)
                raise
        else:
            destination_output.mkdir(parents=True, exist_ok=True)

        source_assets = tuple(session.assets or session.summary.assets)
        if not source_assets:
            source_assets = (
                RecordingAsset(
                    path=".",
                    kind=RecordingAssetType.FRAME,
                    label="Capture output",
                    content_type="inode/directory",
                ),
            )

        relative_assets: list[RecordingAsset] = []
        absolute_assets: list[RecordingAsset] = []

        for asset in source_assets:
            src_path = Path(asset.path)
            if not src_path.is_absolute():
                src_path = source_root / src_path
            src_path = src_path.resolve()

            if not src_path.exists():
                shutil.rmtree(destination_root, ignore_errors=True)
                raise FileNotFoundError(f"Asset not found for session '{session_id}': {src_path}")

            try:
                rel_within_source = src_path.relative_to(source_root)
                rel_dest = destination_output_rel / rel_within_source
                dest_path = destination_output / rel_within_source
            except ValueError:
                rel_within_source = Path(src_path.name)
                rel_dest = rel_within_source
                dest_path = destination_root / rel_within_source
                if not dest_path.exists():
                    if src_path.is_dir():
                        self._link_or_copy_directory(src_path, dest_path)
                    else:
                        self._link_or_copy_file(src_path, dest_path)

            size_bytes = None
            if dest_path.exists() and dest_path.is_file():
                try:
                    size_bytes = dest_path.stat().st_size
                except OSError:
                    size_bytes = None

            asset_payload = asset.dict()
            asset_payload["path"] = rel_dest.as_posix()
            if size_bytes is not None:
                asset_payload["size_bytes"] = size_bytes

            rel_asset = RecordingAsset(**asset_payload)
            abs_asset = rel_asset.copy(update={"path": str(dest_path.resolve())})

            relative_assets.append(rel_asset)
            absolute_assets.append(abs_asset)

        video_path_rel = None
        video_path_abs = None
        for asset in relative_assets:
            if asset.kind == RecordingAssetType.VIDEO:
                video_path_rel = asset.path
                break
        for asset in absolute_assets:
            if asset.kind == RecordingAssetType.VIDEO:
                video_path_abs = asset.path
                break

        summary_relative = session.summary.copy(
            update={
                "output_dir": destination_output_rel.as_posix(),
                "video_path": video_path_rel,
                "assets": relative_assets,
            }
        )
        summary_absolute = summary_relative.copy(
            update={
                "output_dir": str(destination_output.resolve()),
                "video_path": video_path_abs,
                "assets": absolute_assets,
            }
        )

        metadata_path = destination_root / self.METADATA_FILENAME
        metadata_path.write_text(summary_relative.json(indent=2), encoding="utf-8")

        stored_settings: Optional[RecordingSettings] = None
        if session.settings is not None:
            stored_settings = session.settings.copy(update={"session_id": session_id})
            settings_path = destination_root / self.SETTINGS_FILENAME
            settings_path.write_text(stored_settings.json(indent=2), encoding="utf-8")

        stored_session = StoredSession(
            summary=summary_absolute,
            base_path=destination_root,
            settings=stored_settings,
            assets=tuple(absolute_assets),
        )

        self._notify_changed()

        return stored_session

    def list_sessions(self) -> Iterable[StoredSession]:
        """Return all known sessions sorted by ``created_at`` descending."""

        sessions: list[StoredSession] = []
        for entry in sorted(self._base_path.iterdir()):
            if not entry.is_dir():
                continue
            stored = self._load_session(entry)
            if stored is not None:
                sessions.append(stored)

        return sorted(sessions, key=lambda item: item.summary.created_at, reverse=True)

    def get_session(self, session_id: str) -> Optional[StoredSession]:
        """Retrieve a specific session by identifier."""

        path = self._base_path / session_id
        if not path.exists() or not path.is_dir():
            return None
        return self._load_session(path)

    def delete_session(self, session_id: str) -> None:
        """Remove a session and its assets from disk."""

        path = self._base_path / session_id
        if not path.exists():
            raise FileNotFoundError(f"Session '{session_id}' does not exist")
        if not path.is_dir():
            raise RuntimeError(f"Session path '{path}' is not a directory")

        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeError(f"Failed to delete session '{session_id}': {exc}") from exc

        self._notify_changed()

    def ingest_completed_session(
        self,
        *,
        settings: RecordingSettings,
        output_dir: Path,
        session_id: Optional[str] = None,
        video_path: Optional[Path] = None,
        completed_at: Optional[datetime] = None,
    ) -> StoredSession:
        """Register a freshly finished session given runner outputs.

        This helper inspects ``output_dir`` for common artefacts generated by the
        timelapse runner, constructs a :class:`StoredSession`, and persists it via
        :meth:`register_session`.
        """

        session_id = session_id or settings.session_id
        if not session_id:
            raise ValueError("A session_id must be provided or embedded in settings")

        output_dir = Path(output_dir).expanduser().resolve()
        if not output_dir.exists():
            raise FileNotFoundError(f"Output directory does not exist: {output_dir}")

        created_at = completed_at or datetime.utcnow()

        assets: list[RecordingAsset] = [
            RecordingAsset(
                path=".",
                kind=RecordingAssetType.FRAME,
                label="Capture output",
                content_type="inode/directory",
            )
        ]

        candidate_video = video_path or (output_dir / "timelapse.mp4")
        video_real: Optional[Path]
        if candidate_video is None:
            video_real = None
        else:
            video_real = Path(candidate_video)
            if not video_real.is_absolute():
                video_real = output_dir / video_real
            if not video_real.exists():
                video_real = None

        if video_real is not None:
            try:
                size = video_real.stat().st_size
            except OSError:
                size = None
            rel_path: str
            try:
                rel_path = str(video_real.relative_to(output_dir))
            except ValueError:
                rel_path = video_real.name
            assets.append(
                RecordingAsset(
                    path=rel_path,
                    kind=RecordingAssetType.VIDEO,
                    size_bytes=size,
                    content_type="video/mp4",
                    label="Rendered video",
                )
            )

        metadata_csv = output_dir / "metadata.csv"
        if metadata_csv.exists():
            try:
                size = metadata_csv.stat().st_size
            except OSError:
                size = None
            assets.append(
                RecordingAsset(
                    path=str(metadata_csv.relative_to(output_dir)),
                    kind=RecordingAssetType.METADATA,
                    size_bytes=size,
                    content_type="text/csv",
                    label="Frame metadata",
                )
            )

        summary = RecordingSummary(
            session_id=session_id,
            created_at=created_at,
            total_frames=settings.plan.total_frames,
            output_dir=str(output_dir),
            plan_name=settings.plan.name,
            duration_s=settings.plan.duration_seconds,
            video_path=str(video_real) if video_real is not None else None,
            assets=assets,
            tags=settings.tags,
            notes=settings.notes,
        )

        stored = StoredSession(
            summary=summary,
            base_path=self._base_path / session_id,
            settings=settings,
            assets=tuple(assets),
        )

        return self.register_session(stored)

    def add_change_listener(self, listener: Callable[[], None]) -> None:
        """Register *listener* to be notified when sessions mutate."""

        self._change_listeners.add(listener)

    def remove_change_listener(self, listener: Callable[[], None]) -> None:
        """Remove a previously registered session change *listener*."""

        self._change_listeners.discard(listener)

    def _notify_changed(self) -> None:
        listeners = tuple(self._change_listeners)
        for listener in listeners:
            try:
                listener()
            except Exception:  # pragma: no cover - defensive guard
                logger.exception("Session change listener failed")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _load_session(self, path: Path) -> Optional[StoredSession]:
        metadata_path = path / self.METADATA_FILENAME
        if not metadata_path.is_file():
            logger.warning("Skipping session at %s – metadata.json missing", path)
            return None

        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read metadata for %s: %s", path, exc)
            return None

        assets_payload = raw.get("assets", [])
        for asset in assets_payload:
            if "path" in asset:
                asset["path"] = str((path / asset["path"]).resolve())

        output_rel = raw.get("output_dir", self.OUTPUT_SUBDIR.as_posix())
        raw["output_dir"] = str((path / output_rel).resolve())

        video_path = raw.get("video_path")
        if video_path:
            raw["video_path"] = str((path / video_path).resolve())

        try:
            summary = RecordingSummary.parse_obj(raw)
        except ValidationError as exc:
            logger.warning("Invalid metadata for %s: %s", path, exc)
            return None

        settings_path = path / self.SETTINGS_FILENAME
        settings: Optional[RecordingSettings] = None
        if settings_path.is_file():
            try:
                settings = RecordingSettings.parse_raw(settings_path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, json.JSONDecodeError) as exc:
                logger.warning("Failed to load settings for %s: %s", path, exc)
                settings = None

        return StoredSession(summary=summary, base_path=path, settings=settings, assets=tuple(summary.assets))

    @staticmethod
    def _link_or_copy_file(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)

    @staticmethod
    def _link_or_copy_directory(source: Path, destination: Path) -> None:
        if destination.exists():
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
            else:
                shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(source, destination, target_is_directory=True)
        except OSError:
            shutil.copytree(source, destination)
