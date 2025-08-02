"""
timelapse_session.py – Orchestrate a complete timelapse video-capture workflow
using CameraWrapper and TripodController.

Tested with Python 3.11+.  Requires:
    pyyaml      (for YAML config files)
    piexif      (optional – EXIF embedding, falls back to CSV)
    ffmpeg      (on PATH – video rendering)

"""

from __future__ import annotations

import csv
import json
import logging
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

try:
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    yaml = None  # noqa: N816 – allow camel

try:
    import piexif  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    piexif = None  # noqa: N816 – allow camel

# Local project imports
from camerawrapper import CameraWrapper, CameraError
from tripodwrapper import TripodController

__all__ = ["TimelapseSession", "TimelapseError"]

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger.addHandler(_console_handler)


class TimelapseError(RuntimeError):
    """Raised for predictable timelapse-specific failures."""


class TimelapseSession:
    """
    High-level controller for an entire timelapse capture session.

    Parameters
    ----------
    config
        Either a mapping object (**dict**), a **str/Path** to a YAML/JSON file,
        or a mapping-like object.  It must contain the keys *camera*, *tripod*,
        and *timelapse* as described in the project specification.

    Notes
    -----
    * All heavy operations are protected so that *KeyboardInterrupt* leads to a
      clean shutdown with partially-captured material left intact.
    * The class is reusable from a CLI, REPL, or REST handler – no globals.
    """

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #
    def __init__(self, config: Dict[str, Any] | str | Path):
        self._cfg = self._load_config(config)
        self._validate_config(self._cfg)

        self.camera: Optional[CameraWrapper] = None
        self.tripod: Optional[TripodController] = None

        self._pan_step: float = 0.0
        self._tilt_step: float = 0.0
        self._metadata_csv: Optional[csv.DictWriter] = None
        self._metadata_file_handle: Optional[Any] = None  # file object
        self._stop_now: bool = False

        # Convenience shortcuts
        self._tl: SimpleNamespace = SimpleNamespace(**self._cfg["timelapse"])

        self.output_dir: Path = Path(self._tl.output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.video_path: Path = self.output_dir / "timelapse.mp4"

        # Register SIGINT handler for graceful aborts
        signal.signal(signal.SIGINT, self._sigint_handler)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def prepare(self) -> None:
        """
        Initialise camera & tripod, apply settings, pre-compute motion steps,
        and ensure sufficient disk space.
        """
        logger.info("Preparing timelapse session")

        # ---- camera ------------------------------------------------------
        self.camera = self._init_camera(self._cfg["camera"])

        # ---- tripod ------------------------------------------------------
        self.tripod = TripodController(self._cfg["tripod"])
        self._home_and_goto_start()

        # ---- motion math -------------------------------------------------
        f_total = self._tl.total_frames
        if f_total < 2:
            raise TimelapseError("total_frames must be ≥ 2")
        self._pan_step = (self._tl.target["pan"] - self._tl.start["pan"]) / (f_total - 1)
        self._tilt_step = (self._tl.target["tilt"] - self._tl.start["tilt"]) / (f_total - 1)
        logger.info("Per-frame step Δpan=%.6f°, Δtilt=%.6f°", self._pan_step, self._tilt_step)

        # ---- disk space check -------------------------------------------
        self._check_disk_space()

        # ---- metadata sink ----------------------------------------------
        self._open_metadata_sink()

    def run(self, progress_cb: Callable[[int, int], None] | None = None) -> Path:
        """
        Execute the main capture → move loop.  Returns the rendered video Path.

        Parameters
        ----------
        progress_cb
            Optional callable ``progress_cb(done, total)`` invoked after each
            completed frame (1-based *done*).
        """
        if self.camera is None or self.tripod is None:
            self.prepare()

        logger.info("Starting capture loop (%s frames)", self._tl.total_frames)
        try:
            for idx in range(self._tl.total_frames):
                if self._stop_now:
                    logger.warning("Capture aborted by user")
                    break

                iter_start = time.monotonic()
                self.capture_frame(idx)

                if progress_cb:
                    progress_cb(idx + 1, self._tl.total_frames)

                # Last frame – no move / wait needed
                if idx == self._tl.total_frames - 1:
                    break

                # Move to next position & wait for movement to finish
                self.tripod.move_blocking(pan_deg=self._pan_step, tilt_deg=self._tilt_step)

                # Timing cadence --------------------------------------------------
                elapsed = time.monotonic() - iter_start
                remaining = self._tl.interval_s - elapsed
                sleep_time = max(self._tl.settle_time_s, remaining)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    logger.debug("Frame cadence over-run by %.3fs", -sleep_time)
        finally:
            # Always clean up hardware
            try:
                if self.tripod:
                    self.tripod.enable_drivers(False)
                    self.tripod.close()
            except Exception as exc:  # pragma: no cover
                logger.warning("Tripod cleanup failed: %s", exc)
            try:
                if self.camera:
                    self.camera.__exit__(None, None, None)
            except Exception as exc:  # pragma: no cover
                logger.warning("Camera cleanup failed: %s", exc)
            self._close_metadata_sink()

        # ---- video render --------------------------------------------------
        return self.finalize_video()

    # ------------------------------------------------------------------ #
    # Helpers – frame capture & metadata
    # ------------------------------------------------------------------ #
    def capture_frame(self, idx: int) -> Path:
        """
        Capture and persist a single image *idx* (0-based).  Returns the Path.
        """
        if self.camera is None:
            raise TimelapseError("Session not prepared – camera unavailable")

        filename = f"frame_{idx:04d}.jpg"
        path = self.output_dir / filename
        logger.debug("Capturing %s", filename)

        # Capture
        try:
            img_path = self.camera.capture_image(dest=path)
        except CameraError as exc:
            raise TimelapseError(f"Camera capture failed: {exc}") from exc

        # Metadata
        pan, tilt = self.tripod.position if self.tripod else (None, None)
        self.write_metadata(idx, pan, tilt, img_path)

        return img_path

    def write_metadata(self, idx: int, pan: float, tilt: float, img_path: Path) -> None:
        """
        Persist pan/tilt metadata.  Prefer EXIF; fallback to CSV.
        """
        success = False
        if piexif:
            try:
                exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
                # UserComment tag (37510) expects UTF-8 bytes with a prefix
                comment = json.dumps({"frame": idx, "pan_deg": pan, "tilt_deg": tilt})
                exif_dict["Exif"][piexif.ExifIFD.UserComment] = b"UTF8\x00\x00\x00" + comment.encode()
                piexif.insert(piexif.dump(exif_dict), str(img_path))
                success = True
            except Exception as exc:  # pragma: no cover
                logger.warning("EXIF embed failed for %s: %s", img_path.name, exc)

        if not success:
            if self._metadata_csv is None:  # pragma: no cover
                self._open_metadata_sink()  # ensure writer initialised
            self._metadata_csv.writerow(
                {"frame": idx, "filename": img_path.name, "pan_deg": pan, "tilt_deg": tilt}
            )

    # ------------------------------------------------------------------ #
    # Helpers – video render
    # ------------------------------------------------------------------ #
    def finalize_video(self) -> Path:
        """
        Render *timelapse.mp4* using ffmpeg.  Returns the Path.
        """
        logger.info("Rendering video with ffmpeg")

        cmd: list[str] = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(self._tl.video_fps),
            "-i",
            "frame_%04d.jpg",
        ]
        if getattr(self._tl, "ffmpeg_extra", None):
            cmd += shlex.split(self._tl.ffmpeg_extra)
        cmd.append(str(self.video_path))

        try:
            subprocess.run(cmd, cwd=self.output_dir, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="ignore") if exc.stderr else "<no stderr>"
            raise TimelapseError(f"ffmpeg failed: {stderr.strip()}") from exc

        logger.info("Video written to %s", self.video_path)
        return self.video_path

    # ------------------------------------------------------------------ #
    # Internal helpers – camera initialisation
    # ------------------------------------------------------------------ #
    def _init_camera(self, cam_cfg: Dict[str, Any]) -> CameraWrapper:
        """
        Initialise CameraWrapper and apply settings from *cam_cfg*.
        """
        # Camera selection ------------------------------------------------
        cam_cfg = cam_cfg.copy()
        model_sub = cam_cfg.pop("model_substring", None)
        if model_sub is not None:
            camera = CameraWrapper.select_camera(model_sub)
        else:
            discovered = CameraWrapper.discover_cameras()
            if not discovered:
                raise TimelapseError("No USB camera detected")
            model, port = discovered[0].rsplit(" (", 1)
            port = port.rstrip(")")
            camera = CameraWrapper(model, port)

        # Apply remaining settings verbatim
        if cam_cfg:
            logger.info("Applying %s camera settings", len(cam_cfg))
            camera.apply_settings(cam_cfg)

        return camera

    # ------------------------------------------------------------------ #
    # Internal helpers – tripod start position
    # ------------------------------------------------------------------ #
    def _home_and_goto_start(self) -> None:
        """
        Bring tripod to a known start position defined in config.
        """
        if self.tripod is None:
            raise TimelapseError("Tripod not initialised")

        # Home logic would be device-specific; we assume manual / pre-homed.
        self.tripod.reset_position()
        start_pan = self._tl.start["pan"]
        start_tilt = self._tl.start["tilt"]
        logger.info("Moving tripod to start position pan=%.2f°, tilt=%.2f°", start_pan, start_tilt)
        self.tripod.move_blocking(pan_deg=start_pan, tilt_deg=start_tilt)
        self.tripod.enable_drivers(True)

    # ------------------------------------------------------------------ #
    # Internal helpers – config loading & validation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_config(src: Dict[str, Any] | str | Path) -> Dict[str, Any]:
        if isinstance(src, dict):
            return src
        path = Path(src).expanduser().resolve()
        if not path.is_file():
            raise TimelapseError(f"Config file not found: {path}")
        text = path.read_text()

        # Detect format
        try:
            return yaml.safe_load(text) if yaml else json.loads(text)
        except Exception as exc:
            raise TimelapseError(f"Cannot parse config {path}: {exc}") from exc

    @staticmethod
    def _validate_config(cfg: Dict[str, Any]) -> None:
        for section in ("camera", "tripod", "timelapse"):
            if section not in cfg:
                raise TimelapseError(f"Missing required '{section}' section")

        tl = cfg["timelapse"]
        required = {
            "total_frames": int,
            "interval_s": (int, float),
            "settle_time_s": (int, float),
            "start": dict,
            "target": dict,
            "output_dir": str,
            "video_fps": int,
        }
        for key, typ in required.items():
            if key not in tl or not isinstance(tl[key], typ):
                raise TimelapseError(f"timelapse.{key} must be of type {typ}")

        for angle_key in ("pan", "tilt"):
            if angle_key not in tl["start"] or angle_key not in tl["target"]:
                raise TimelapseError(f"start/target must contain '{angle_key}'")

        if tl["interval_s"] < tl["settle_time_s"]:
            raise TimelapseError("interval_s must be ≥ settle_time_s")

    # ------------------------------------------------------------------ #
    # Internal helpers – disk space
    # ------------------------------------------------------------------ #
    def _check_disk_space(self) -> None:
        usage = shutil.disk_usage(self.output_dir)
        # Very rough estimate – assume 20 MB per frame JPEG
        need_bytes = self._tl.total_frames * 20_000_000
        if usage.free < need_bytes:
            raise TimelapseError(
                f"Insufficient disk space: need ≈{need_bytes/1e9:.1f} GB, "
                f"have {usage.free/1e9:.1f} GB on {self.output_dir.drive or self.output_dir.root}"
            )
        logger.info("Disk space OK – %.1f GB free", usage.free / 1e9)

    # ------------------------------------------------------------------ #
    # Internal helpers – metadata sink (CSV fallback)
    # ------------------------------------------------------------------ #
    def _open_metadata_sink(self) -> None:
        """
        Open metadata.csv if EXIF injection is not available.
        """
        if piexif:
            return  # not needed
        meta_path = self.output_dir / "metadata.csv"
        is_new = not meta_path.exists()
        self._metadata_file_handle = meta_path.open("a", newline="", encoding="utf-8")
        self._metadata_csv = csv.DictWriter(
            self._metadata_file_handle,
            fieldnames=["frame", "filename", "pan_deg", "tilt_deg"],
        )
        if is_new:
            self._metadata_csv.writeheader()

    def _close_metadata_sink(self) -> None:
        if self._metadata_file_handle:
            self._metadata_file_handle.close()
            self._metadata_file_handle = None
            self._metadata_csv = None

    # ------------------------------------------------------------------ #
    # Signal handling
    # ------------------------------------------------------------------ #
    def _sigint_handler(self, signum, frame):  # noqa: D401 – simple name
        logger.warning("SIGINT received – finishing current operation then aborting")
        self._stop_now = True
