import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict

import yaml

# Import CameraError if available; provide a fallback so the UI can load
# without the camera stack installed.
try:  # pragma: no cover - environment dependent
    from camerawrapper import CameraError  # type: ignore
except Exception:  # pragma: no cover
    class CameraError(RuntimeError):
        pass

from .camera import close_camera, initialize_camera
from .tripod import close_tripod


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
    render_video: bool,
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
            "render_video": bool(render_video),
            **({"video_fps": int(video_fps)} if render_video else {}),
        },
    }


async def run_prototype_timelapse(settings: Dict[str, Any], frames: int) -> tuple[list[str], str]:
    """Run a short timelapse using the provided settings.

    The settings are written to a temporary YAML file and fed into
    :class:`~timelapse.TimelapseSession` to exercise the same code path used for
    real captures.
    """
    tmp_dir = tempfile.mkdtemp()
    cfg = settings.copy()
    cfg["timelapse"] = cfg["timelapse"].copy()
    cfg["timelapse"]["total_frames"] = int(frames)
    cfg["timelapse"]["output_dir"] = tmp_dir

    # Persist config so TimelapseSession reads it as it would during normal
    # operation.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(cfg, fh)
        cfg_path = fh.name

    # Stop live view and release tripod before constructing a new session
    await close_camera()
    await close_tripod()

    images: list[Path] = []
    status: str

    # Import Timelapse pieces lazily so that missing camera stack doesn't
    # prevent the UI from launching. If unavailable, report a friendly status.
    try:  # pragma: no cover - environment dependent
        from timelapse import TimelapseSession, TimelapseError  # type: ignore
    except Exception as exc:  # pragma: no cover
        status = f"Timelapse support unavailable: {exc}"
        return [], status

    try:
        session = TimelapseSession(cfg_path)
        await asyncio.to_thread(session.prepare)
        await asyncio.to_thread(session.run)
        images = sorted(Path(tmp_dir).glob("frame_*.jpg"))
        status = "Prototype timelapse completed"
    except (TimelapseError, CameraError) as exc:  # pragma: no cover - hardware dependent
        status = f"Error: {exc}"
    finally:
        # Restore camera for continued live view in the UI
        await initialize_camera()

    return [str(p) for p in images], status


def export_settings(data: Dict[str, Any]) -> str:
    """Write settings to a temporary YAML file and return the path."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as fh:
        yaml.safe_dump(data, fh)
        return fh.name
