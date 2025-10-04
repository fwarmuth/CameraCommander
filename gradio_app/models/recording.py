"""Pydantic models describing timelapse configuration placeholders."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class CameraSettings(BaseModel):
    """Placeholder camera settings model."""

    iso: Optional[int] = Field(None, description="Camera ISO value")
    shutter_speed: Optional[str] = Field(None, description="Camera shutter speed")
    aperture: Optional[float] = Field(None, description="Camera aperture value")


class TripodSettings(BaseModel):
    """Placeholder tripod settings model."""

    pan_speed: Optional[float] = Field(None, description="Tripod pan speed")
    tilt_speed: Optional[float] = Field(None, description="Tripod tilt speed")


class TimelapsePlan(BaseModel):
    """Placeholder plan describing a timelapse run."""

    name: str = Field(..., description="Human-friendly plan name")
    duration_seconds: Optional[int] = Field(None, description="Planned duration")
    frame_interval_seconds: Optional[float] = Field(
        None, description="Interval between frames"
    )
    camera: CameraSettings = Field(default_factory=CameraSettings)
    tripod: TripodSettings = Field(default_factory=TripodSettings)


class RecordingSettings(BaseModel):
    """Placeholder for a timelapse recording request."""

    plan: TimelapsePlan
    created_at: datetime = Field(default_factory=datetime.utcnow)
    extra: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True
