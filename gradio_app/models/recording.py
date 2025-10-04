"""Pydantic models describing timelapse configuration and repository metadata.

These schemas sit at the seam between the Gradio UI and the core timelapse
runner.  Planner components operate exclusively on the strongly-typed models
defined here and call :func:`to_session_config` / :func:`from_session_config`
when handing data to ``app.timelapse_session`` or reading stored run metadata
from the repository layer.  Adding new fields therefore requires updating both
the models and the conversion helpers to keep the UI and execution pipeline in
sync.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field, root_validator, validator


class CameraSettings(BaseModel):
    """Camera configuration forwarded to :class:`~gradio_app.services.camera_adapter.CameraAdapter`."""

    # ``overrides`` mirrors ``CameraAdapter.apply_settings`` keyword arguments so
    # the Gradio planner can safely persist advanced options without depending on
    # the concrete camera implementation.
    model_substring: Optional[str] = Field(
        None,
        description=(
            "Optional substring used to auto-select the camera when multiple devices"
            " are present."
        ),
    )
    overrides: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional CameraAdapter.apply_settings overrides keyed by property name.",
    )

    @classmethod
    def from_session_config(cls, cfg: Dict[str, Any]) -> "CameraSettings":
        """Build settings from a raw session configuration mapping."""

        cfg = cfg.copy()
        model_substring = cfg.pop("model_substring", None)
        return cls(model_substring=model_substring, overrides=cfg)

    def to_session_config(self) -> Dict[str, Any]:
        """Convert the model into the structure expected by ``TimelapseSession``."""

        cfg = dict(self.overrides)
        if self.model_substring:
            cfg["model_substring"] = self.model_substring
        return cfg


class TripodSerialSettings(BaseModel):
    """Serial connection parameters for the tripod controller."""

    port: str = Field(..., description="Serial port path or identifier (e.g. /dev/ttyUSB0).")
    baudrate: int = Field(9600, description="Baud rate used for the serial link.")


class TripodSettings(BaseModel):
    """Tripod controller configuration forwarded to :class:`~gradio_app.services.tripod_adapter.TripodAdapter`."""

    # Serial/microstep fields are surfaced individually for validation whereas
    # ``options`` carries any controller-specific key/value pairs that the UI
    # does not reason about directly.
    serial: Optional[TripodSerialSettings] = Field(
        None, description="Optional serial connection settings for motor drivers."
    )
    microstep: Optional[int] = Field(
        None,
        ge=1,
        description="Microstepping factor applied to the stepper drivers (e.g. 16).",
    )
    options: Dict[str, Any] = Field(
        default_factory=dict,
        description="Vendor-specific tripod controller options not modelled explicitly.",
    )

    @classmethod
    def from_session_config(cls, cfg: Dict[str, Any]) -> "TripodSettings":
        """Create settings from a mapping consumed by ``TimelapseSession``."""

        cfg = cfg.copy()
        serial_cfg = cfg.pop("serial", None)
        microstep = cfg.pop("microstep", None)
        return cls(
            serial=TripodSerialSettings(**serial_cfg) if isinstance(serial_cfg, dict) else None,
            microstep=int(microstep) if microstep is not None else None,
            options=cfg,
        )

    def to_session_config(self) -> Dict[str, Any]:
        """Render the configuration mapping expected by ``TripodAdapter``."""

        cfg: Dict[str, Any] = dict(self.options)
        if self.serial:
            cfg["serial"] = self.serial.dict(exclude_none=True)
        if self.microstep is not None:
            cfg["microstep"] = self.microstep
        return cfg


class GimbalAngles(BaseModel):
    """Absolute gimbal orientation used for start/target definitions."""

    pan: float = Field(..., description="Pan angle in degrees.")
    tilt: float = Field(..., description="Tilt angle in degrees.")


class TimelapsePlan(BaseModel):
    """Full description of a timelapse capture plan."""

    name: Optional[str] = Field(
        None, description="Human-friendly identifier used only in the UI layer."
    )
    description: Optional[str] = Field(None, description="Optional planner notes.")
    total_frames: int = Field(
        ..., ge=2, description="Total number of frames to capture during the session."
    )
    interval_s: float = Field(
        ..., gt=0, description="Seconds between capture iterations including motion time."
    )
    settle_time_s: float = Field(
        ..., ge=0, description="Minimum time for the tripod to settle after each move."
    )
    start: GimbalAngles = Field(..., description="Initial gimbal orientation in degrees.")
    target: GimbalAngles = Field(..., description="Final gimbal orientation in degrees.")
    output_dir: str = Field(..., description="Directory where frames and artefacts are written.")
    render_video: bool = Field(
        True, description="Render a timelapse.mp4 clip once capture completes."
    )
    video_fps: Optional[int] = Field(
        30, gt=0, description="Frames-per-second for rendered video when enabled."
    )
    ffmpeg_extra: Optional[str] = Field(
        None, description="Additional command-line flags passed to ffmpeg during rendering."
    )

    @root_validator
    def _validate_video_requirements(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        render_video = values.get("render_video", True)
        if render_video and values.get("video_fps") is None:
            raise ValueError("video_fps must be provided when render_video is enabled")
        if not render_video:
            values["video_fps"] = None
        return values

    @property
    def duration_seconds(self) -> float:
        """Approximate capture duration ignoring setup/teardown overhead."""

        return self.total_frames * self.interval_s

    @classmethod
    def from_session_config(
        cls,
        cfg: Dict[str, Any],
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> "TimelapsePlan":
        """Construct a plan model from a timelapse session dictionary."""

        cfg = cfg.copy()
        video_fps = cfg.get("video_fps")
        if video_fps is not None:
            video_fps = int(video_fps)

        return cls(
            name=name,
            description=description,
            total_frames=int(cfg["total_frames"]),
            interval_s=float(cfg["interval_s"]),
            settle_time_s=float(cfg["settle_time_s"]),
            start=GimbalAngles(**cfg["start"]),
            target=GimbalAngles(**cfg["target"]),
            output_dir=str(cfg["output_dir"]),
            render_video=bool(cfg.get("render_video", True)),
            video_fps=video_fps,
            ffmpeg_extra=cfg.get("ffmpeg_extra"),
        )

    def to_session_config(self) -> Dict[str, Any]:
        """Convert the plan into the mapping consumed by ``TimelapseSession``."""

        cfg: Dict[str, Any] = {
            "total_frames": self.total_frames,
            "interval_s": self.interval_s,
            "settle_time_s": self.settle_time_s,
            "start": self.start.dict(),
            "target": self.target.dict(),
            "output_dir": self.output_dir,
            "render_video": self.render_video,
        }
        if self.render_video:
            cfg["video_fps"] = self.video_fps
        if self.ffmpeg_extra:
            cfg["ffmpeg_extra"] = self.ffmpeg_extra
        return cfg


class RecordingSettings(BaseModel):
    """Aggregated settings required to start a timelapse recording."""

    # This schema mirrors the nested structure consumed by ``TimelapseSession``.
    # Planner views work with this model exclusively before serialising the
    # configuration payload through :meth:`to_session_config` for execution.
    camera: CameraSettings = Field(..., description="Camera configuration block.")
    tripod: TripodSettings = Field(..., description="Tripod configuration block.")
    plan: TimelapsePlan = Field(..., description="Timelapse plan definition.")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when the recording was authored in the planner UI.",
    )
    session_id: Optional[str] = Field(
        None, description="Optional identifier allocated once the job is scheduled."
    )
    tags: List[str] = Field(
        default_factory=list,
        description="User-defined labels that help organise recordings in the UI.",
    )
    notes: Optional[str] = Field(None, description="Additional free-form operator notes.")

    @validator("tags", pre=True)
    def _ensure_list(cls, value: Optional[Iterable[str]]) -> List[str]:
        if value is None:
            return []
        return [str(item) for item in value]

    @classmethod
    def from_session_config(
        cls,
        cfg: Dict[str, Any],
        *,
        session_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[Iterable[str]] = None,
        notes: Optional[str] = None,
    ) -> "RecordingSettings":
        """Create a structured model from a raw ``TimelapseSession`` config mapping."""

        tag_list = list(tags) if tags is not None else []

        return cls(
            camera=CameraSettings.from_session_config(cfg.get("camera", {})),
            tripod=TripodSettings.from_session_config(cfg.get("tripod", {})),
            plan=TimelapsePlan.from_session_config(
                cfg.get("timelapse", {}), name=name, description=description
            ),
            created_at=created_at or datetime.utcnow(),
            session_id=session_id,
            tags=tag_list,
            notes=notes,
        )

    def to_session_config(self) -> Dict[str, Any]:
        """Render the configuration mapping expected by ``TimelapseSession``."""

        return {
            "camera": self.camera.to_session_config(),
            "tripod": self.tripod.to_session_config(),
            "timelapse": self.plan.to_session_config(),
        }


class RecordingAssetType(str, Enum):
    """Enumeration of supported recording asset media types."""

    FRAME = "frame"
    VIDEO = "video"
    METADATA = "metadata"
    OTHER = "other"


class RecordingAsset(BaseModel):
    """Metadata describing an artefact associated with a recording."""

    path: str = Field(..., description="Absolute or repository-relative asset path.")
    kind: RecordingAssetType = Field(
        RecordingAssetType.OTHER, description="Category of the artefact for UI grouping."
    )
    size_bytes: Optional[int] = Field(
        None, description="Approximate asset size, if known, to support UI previews."
    )
    content_type: Optional[str] = Field(
        None, description="MIME-like indicator used for download actions."
    )
    label: Optional[str] = Field(
        None, description="UI label describing the asset (e.g. 'Rendered video')."
    )


class RecordingSummary(BaseModel):
    """Top-level metadata describing a captured timelapse session."""

    session_id: str = Field(..., description="Stable identifier for the stored recording.")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when the session completed or was archived.",
    )
    total_frames: int = Field(
        ..., ge=0, description="Number of frames captured and stored for the session."
    )
    output_dir: str = Field(
        ..., description="Root directory where frames and artefacts reside on disk."
    )
    plan_name: Optional[str] = Field(
        None, description="Original planner name to display in the recordings list."
    )
    duration_s: Optional[float] = Field(
        None, description="Approximate recording duration in seconds, if known."
    )
    video_path: Optional[str] = Field(
        None, description="Path to the rendered video artefact, when available."
    )
    assets: List[RecordingAsset] = Field(
        default_factory=list,
        description="Collection of artefacts (frames, video, metadata) for the session.",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="User-defined labels copied from the originating recording request.",
    )
    notes: Optional[str] = Field(None, description="Free-form operator notes about the session.")

    @validator("assets", pre=True)
    def _ensure_asset_list(cls, value: Optional[Iterable[RecordingAsset]]) -> List[RecordingAsset]:
        if value is None:
            return []
        return list(value)

