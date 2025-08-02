import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict

import yaml

from camerawrapper import CameraError
from timelapse import TimelapseError, TimelapseSession


def build_settings(
    iso: Any,
    shutter: Any,
    aperture: Any,
    wb: Any,
    serial_port: str,
    microstep: int,
    start_pan: float,
    start_tilt: float,
    end_pan: float,
    end_tilt: float,
    total_frames: int,
    interval: float,
    settle: float,
    output_dir: str,
    video_fps: int,
) -> Dict[str, Any]:
    """Build a settings dictionary used for export and timelapse runs."""
    return {
        "camera": {
            "main.imgsettings.iso": iso,
            "main.capturesettings.shutterspeed": shutter,
            "main.capturesettings.aperture": aperture,
            "main.imgsettings.whitebalance": wb,
        },
        "tripod": {
            "serial": {"port": serial_port, "baudrate": 9600},
            "microstep": microstep,
        },
        "timelapse": {
            "total_frames": int(total_frames),
            "interval_s": float(interval),
            "settle_time_s": float(settle),
            "start": {"pan": float(start_pan), "tilt": float(start_tilt)},
            "target": {"pan": float(end_pan), "tilt": float(end_tilt)},
            "output_dir": output_dir,
            "video_fps": int(video_fps),
        },
    }


async def run_prototype_timelapse(settings: Dict[str, Any], frames: int) -> tuple[list[str], str]:
    """Run a short timelapse using the provided settings."""
    tmp_dir = tempfile.mkdtemp()
    cfg = settings.copy()
    cfg["timelapse"] = cfg["timelapse"].copy()
    cfg["timelapse"]["total_frames"] = int(frames)
    cfg["timelapse"]["output_dir"] = tmp_dir
    try:
        session = TimelapseSession(cfg)
        await asyncio.to_thread(session.prepare)
        await asyncio.to_thread(session.run)
        images = sorted(Path(tmp_dir).glob("frame_*.jpg"))
        return [str(p) for p in images], "Prototype timelapse completed"
    except (TimelapseError, CameraError) as exc:  # pragma: no cover - hardware dependent
        return [], f"Error: {exc}"


def export_settings(data: Dict[str, Any]) -> str:
    """Write settings to a temporary YAML file and return the path."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as fh:
        yaml.safe_dump(data, fh)
        return fh.name
