"""Async-aware timelapse session controller for the Gradio service layer.

This module mirrors the legacy timelapse implementation but replaces blocking
waits with cooperative cancellation checks so it can be orchestrated from
asyncio-based code. The heavy lifting still happens on a worker thread – this
module merely exposes callbacks that allow the
:class:`~gradio_app.services.timelapse_runner.TimelapseJobRunner` to surface
progress and lifecycle events back to the UI.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

try:  # pragma: no cover - imported module is optional at runtime
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - defensive
    yaml = None  # noqa: N816 – allow camel-case alias

try:  # pragma: no cover - imported module is optional at runtime
    import piexif  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - defensive
    piexif = None  # noqa: N816 – allow camel-case alias

from logging_utils import ensure_trace_level
from tqdm import tqdm

from .camera_adapter import CameraAdapter, CameraAdapterError
from .tripod_adapter import TripodAdapter, TripodAdapterError

__all__ = ["TimelapseSession", "TimelapseError"]

ensure_trace_level()

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]
EventCallback = Callable[[str, Dict[str, Any]], None]


class TimelapseError(RuntimeError):
    """Raised for predictable timelapse-specific failures."""


class TimelapseSession:
    """High-level controller for a single timelapse capture session."""

    def __init__(
        self,
        config: Dict[str, Any] | str | Path,
        *,
        camera_factory: Optional[Callable[[], CameraAdapter]] = None,
        tripod_factory: Optional[Callable[[], TripodAdapter]] = None,
    ) -> None:
        logger.debug("Loading timelapse configuration from %s", config)
        self._cfg = self._load_config(config)
        self._validate_config(self._cfg)
        logger.debug("Configuration validated successfully")

        self.camera: Optional[CameraAdapter] = None
        self.tripod: Optional[TripodAdapter] = None

        self._camera_factory = camera_factory
        self._tripod_factory = tripod_factory

        self._metadata_csv: Optional[csv.DictWriter] = None
        self._metadata_file_handle: Optional[Any] = None
        self._stop_event = threading.Event()

        tl_cfg = self._cfg["timelapse"].copy()
        tl_cfg.setdefault("render_video", True)
        self._tl: SimpleNamespace = SimpleNamespace(**tl_cfg)

        self.output_dir: Path = Path(self._tl.output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.video_path: Path = self.output_dir / "timelapse.mp4"

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._sigint_handler)

    def prepare(self) -> None:
        """Initialise camera & tripod, apply settings, and prime metadata sinks."""

        logger.info("Preparing timelapse session")

        logger.info("Initialising camera and tripod adapters")
        self.camera = self._init_camera(
            self._cfg["camera"], factory=self._camera_factory
        )
        self.tripod = self._init_tripod(
            self._cfg["tripod"], factory=self._tripod_factory
        )
        self._home_and_goto_start()

        logger.info(
            "Hardware prepared – output directory %s", self.output_dir
        )

        f_total = self._tl.total_frames
        if f_total < 2:
            raise TimelapseError("total_frames must be ≥ 2")
        pan_step = (self._tl.target["pan"] - self._tl.start["pan"]) / (f_total - 1)
        tilt_step = (self._tl.target["tilt"] - self._tl.start["tilt"]) / (f_total - 1)
        logger.info("Per-frame step Δpan=%.6f°, Δtilt=%.6f°", pan_step, tilt_step)

        self._check_disk_space()
        self._open_metadata_sink()
        print(
            f"[timelapse] Session prepared: {self._tl.total_frames} frames -> {self.output_dir}."
        )

    def run(
        self,
        progress_cb: ProgressCallback | None = None,
        *,
        event_cb: EventCallback | None = None,
    ) -> Path | None:
        """Execute the main capture loop cooperatively checking for cancellation."""

        if self.camera is None or self.tripod is None:
            self.prepare()

        self._emit_event("started", event_cb, {"total_frames": self._tl.total_frames})
        logger.info("Starting capture loop (%s frames)", self._tl.total_frames)
        print(
            f"[timelapse] Capture started for {self._tl.total_frames} frames."
        )

        start_pan = self._tl.start["pan"]
        start_tilt = self._tl.start["tilt"]
        pan_step = (self._tl.target["pan"] - start_pan) / (self._tl.total_frames - 1)
        tilt_step = (self._tl.target["tilt"] - start_tilt) / (self._tl.total_frames - 1)

        try:
            for idx in range(self._tl.total_frames):
                if self._stop_event.is_set():
                    logger.warning("Capture aborted by user request")
                    break

                iter_start = time.monotonic()
                self.capture_frame(idx)
                logger.trace("Frame %s/%s captured", idx + 1, self._tl.total_frames)

                if progress_cb:
                    progress_cb(idx + 1, self._tl.total_frames)

                if idx == self._tl.total_frames - 1:
                    break

                next_pan = start_pan + (idx + 1) * pan_step
                next_tilt = start_tilt + (idx + 1) * tilt_step
                logger.debug(
                    "Moving tripod to next frame pan=%.4f°, tilt=%.4f°",
                    next_pan,
                    next_tilt,
                )
                self.tripod.move_to_blocking(pan_deg=next_pan, tilt_deg=next_tilt)

                elapsed = time.monotonic() - iter_start
                remaining = self._tl.interval_s - elapsed
                sleep_time = max(self._tl.settle_time_s, remaining)
                logger.trace(
                    "Frame %s dwell %.2fs (elapsed %.2fs, remaining %.2fs)",
                    idx + 1,
                    sleep_time,
                    elapsed,
                    remaining,
                )
                self._wait_with_cancellation(sleep_time)
        except Exception as exc:
            self._emit_event("failed", event_cb, {"error": str(exc)})
            raise
        else:
            if not self._stop_event.is_set():
                self._emit_event("completed", event_cb, {"total_frames": self._tl.total_frames})
                print("[timelapse] Capture loop completed.")
        finally:
            self._teardown_hardware()

        if self._stop_event.is_set():
            logger.info("Skipping video rendering due to cancellation")
            self._close_metadata_sink()
            print("[timelapse] Capture cancelled before rendering.")
            return None

        if getattr(self._tl, "render_video", True):
            video_path = self.finalize_video()
        else:
            logger.info("Skipping video rendering per configuration")
            video_path = None

        frames_dir = self.output_dir.resolve()
        logger.info("Frames at %s", frames_dir)
        print(f"[timelapse] Frames stored at {frames_dir}.")

        fps = getattr(self._tl, "video_fps", 30)
        cmd = ["ffmpeg", "-framerate", str(fps), "-i", "frame_%04d.jpg"]
        if getattr(self._tl, "ffmpeg_extra", None):
            cmd += shlex.split(self._tl.ffmpeg_extra)
        cmd.append("timelapse.mp4")
        logger.info("Render video with: %s", " ".join(cmd))

        user = os.environ.get("USER", "<user>")
        host = socket.gethostname()
        logger.info(
            "Download frames with: rsync -avP %s@%s:%s/ ./",
            user,
            host,
            frames_dir,
        )
        print("[timelapse] Download hint emitted to logs.")

        return video_path

    def request_stop(self) -> None:
        """Signal the session to finish the current operation then abort."""

        self._stop_event.set()

    @property
    def stop_requested(self) -> bool:
        """Return ``True`` if cooperative cancellation has been requested."""

        return self._stop_event.is_set()

    def capture_frame(self, idx: int) -> Path:
        """Capture and persist a single image *idx* (0-based)."""

        if self.camera is None:
            raise TimelapseError("Session not prepared – camera unavailable")

        filename = f"frame_{idx:04d}.jpg"
        path = self.output_dir / filename
        logger.debug("Capturing %s", filename)

        try:
            img_path = self.camera.capture_image_no_af(dest=path)
        except CameraAdapterError as exc:
            raise TimelapseError(f"Camera capture failed: {exc}") from exc

        pan, tilt = self.tripod.position if self.tripod else (None, None)
        self.write_metadata(idx, pan, tilt, img_path)

        return img_path

    def write_metadata(self, idx: int, pan: float, tilt: float, img_path: Path) -> None:
        """Persist pan/tilt metadata. Prefer EXIF; fallback to CSV."""

        success = False
        if piexif:
            try:
                exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
                comment = json.dumps({"frame": idx, "pan_deg": pan, "tilt_deg": tilt})
                exif_dict["Exif"][piexif.ExifIFD.UserComment] = b"UTF8\x00\x00\x00" + comment.encode()
                piexif.insert(piexif.dump(exif_dict), str(img_path))
                success = True
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.warning("EXIF embed failed for %s: %s", img_path.name, exc)

        if not success:
            if self._metadata_csv is None:  # pragma: no cover - defensive guard
                self._open_metadata_sink()
            self._metadata_csv.writerow(
                {"frame": idx, "filename": img_path.name, "pan_deg": pan, "tilt_deg": tilt}
            )

    def finalize_video(self) -> Path:
        """Render *timelapse.mp4* using ffmpeg. Returns the Path."""

        logger.info("Rendering video with ffmpeg")
        print(f"[timelapse] Rendering video to {self.video_path}.")

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
            proc = subprocess.Popen(
                cmd,
                cwd=self.output_dir,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
            )
        except OSError as exc:
            raise TimelapseError(f"ffmpeg failed: {exc}") from exc

        assert proc.stderr is not None  # for type checkers
        stderr_lines: list[str] = []
        with tqdm(total=self._tl.total_frames, unit="frame") as bar:
            for line in proc.stderr:
                stderr_lines.append(line)
                match = re.search(r"frame=\s*(\d+)", line)
                if match:
                    frame = int(match.group(1))
                    bar.update(frame - bar.n)
                if self._stop_event.is_set():
                    proc.terminate()
                    proc.wait(timeout=5)
                    raise TimelapseError("Video rendering aborted by user request")
        ret = proc.wait()
        if ret != 0:
            stderr = "".join(stderr_lines).strip() or "<no stderr>"
            raise TimelapseError(f"ffmpeg failed: {stderr}")

        logger.info("Video written to %s", self.video_path)
        print(f"[timelapse] Video available at {self.video_path}.")
        return self.video_path

    def _init_camera(
        self,
        cam_cfg: Dict[str, Any],
        *,
        factory: Optional[Callable[[], CameraAdapter]] = None,
    ) -> CameraAdapter:
        """Initialise :class:`CameraAdapter` and apply settings from *cam_cfg*."""

        cam_cfg = cam_cfg.copy()
        model_sub = cam_cfg.pop("model_substring", None)
        try:
            if factory is not None:
                camera = factory()
            elif model_sub is not None:
                camera = CameraAdapter.select_camera(model_sub)
            else:
                camera = CameraAdapter.autodetect()
        except CameraAdapterError as exc:
            raise TimelapseError(f"Camera initialisation failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive guard
            raise TimelapseError(f"Camera factory failed: {exc}") from exc

        if cam_cfg:
            logger.info("Applying %s camera settings", len(cam_cfg))
            logger.debug("Camera settings detail: %s", cam_cfg)
            try:
                camera.apply_settings(cam_cfg)
            except CameraAdapterError as exc:
                raise TimelapseError(f"Camera configuration failed: {exc}") from exc

        return camera

    def _init_tripod(
        self,
        tripod_cfg: Dict[str, Any],
        *,
        factory: Optional[Callable[[], TripodAdapter]] = None,
    ) -> TripodAdapter:
        """Initialise :class:`TripodAdapter` from configuration."""

        if factory is not None:
            try:
                return factory()
            except TripodAdapterError as exc:
                raise TimelapseError(f"Tripod initialisation failed: {exc}") from exc
            except Exception as exc:  # pragma: no cover - defensive guard
                raise TimelapseError(f"Tripod factory failed: {exc}") from exc

        try:
            return TripodAdapter(tripod_cfg)
        except TripodAdapterError as exc:
            raise TimelapseError(f"Tripod initialisation failed: {exc}") from exc

    def _home_and_goto_start(self) -> None:
        """Bring tripod to a known start position defined in config."""

        if self.tripod is None:
            raise TimelapseError("Tripod not initialised")

        logger.info("Resetting tripod position")
        self.tripod.reset_position()
        start_pan = self._tl.start["pan"]
        start_tilt = self._tl.start["tilt"]
        logger.info("Moving tripod to start position pan=%.2f°, tilt=%.2f°", start_pan, start_tilt)
        self.tripod.move_to_blocking(pan_deg=start_pan, tilt_deg=start_tilt)
        self.tripod.enable_drivers(True)
        logger.info("Tripod ready at start position")

    @staticmethod
    def _load_config(src: Dict[str, Any] | str | Path) -> Dict[str, Any]:
        if isinstance(src, dict):
            return src
        path = Path(src).expanduser().resolve()
        if not path.is_file():
            raise TimelapseError(f"Config file not found: {path}")
        text = path.read_text()

        try:
            return yaml.safe_load(text) if yaml else json.loads(text)
        except Exception as exc:  # pragma: no cover - defensive guard
            raise TimelapseError(f"Cannot parse config {path}: {exc}") from exc

    @staticmethod
    def _validate_config(cfg: Dict[str, Any]) -> None:
        for section in ("camera", "tripod", "timelapse"):
            if section not in cfg:
                raise TimelapseError(f"Missing required '{section}' section")

        tl = cfg["timelapse"]
        render_video = tl.get("render_video", True)
        if "render_video" in tl and not isinstance(render_video, bool):
            raise TimelapseError("timelapse.render_video must be of type bool")
        required = {
            "total_frames": int,
            "interval_s": (int, float),
            "settle_time_s": (int, float),
            "start": dict,
            "target": dict,
            "output_dir": str,
        }
        if render_video:
            required["video_fps"] = int
        for key, typ in required.items():
            if key not in tl or not isinstance(tl[key], typ):
                raise TimelapseError(f"timelapse.{key} must be of type {typ}")

        for angle_key in ("pan", "tilt"):
            if angle_key not in tl["start"] or angle_key not in tl["target"]:
                raise TimelapseError(f"start/target must contain '{angle_key}'")

        if tl["interval_s"] < tl["settle_time_s"]:
            raise TimelapseError("interval_s must be ≥ settle_time_s")

    def _check_disk_space(self) -> None:
        logger.debug("Checking disk space for %s", self.output_dir)
        usage = shutil.disk_usage(self.output_dir)
        need_bytes = self._tl.total_frames * 20_000_000
        if usage.free < need_bytes:
            raise TimelapseError(
                f"Insufficient disk space: need ≈{need_bytes/1e9:.1f} GB, "
                f"have {usage.free/1e9:.1f} GB on {self.output_dir.drive or self.output_dir.root}"
            )
        logger.info("Disk space OK – %.1f GB free", usage.free / 1e9)

    def _open_metadata_sink(self) -> None:
        if piexif:
            logger.debug("piexif available – EXIF metadata will be embedded")
            return
        meta_path = self.output_dir / "metadata.csv"
        is_new = not meta_path.exists()
        self._metadata_file_handle = meta_path.open("a", newline="", encoding="utf-8")
        self._metadata_csv = csv.DictWriter(
            self._metadata_file_handle,
            fieldnames=["frame", "filename", "pan_deg", "tilt_deg"],
        )
        if is_new:
            self._metadata_csv.writeheader()
            logger.debug("Created new metadata.csv with header")
        logger.info("Metadata CSV sink opened at %s", meta_path)

    def _close_metadata_sink(self) -> None:
        if self._metadata_file_handle:
            self._metadata_file_handle.close()
            self._metadata_file_handle = None
            self._metadata_csv = None
            logger.info("Metadata sink closed")

    def _teardown_hardware(self) -> None:
        logger.info("Tearing down hardware state")
        try:
            if self.tripod:
                self.tripod.enable_drivers(False)
                self.tripod.close()
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("Tripod cleanup failed: %s", exc)
        try:
            if self.camera:
                self.camera.close()
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("Camera cleanup failed: %s", exc)
        self._close_metadata_sink()

    def _wait_with_cancellation(self, duration: float) -> None:
        if duration <= 0:
            return
        deadline = time.monotonic() + duration
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.25, remaining))

    def _emit_event(
        self,
        name: str,
        event_cb: EventCallback | None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        if event_cb is None:
            logger.trace("Event %s ignored (no callback)", name)
            return
        logger.debug("Emitting event %s with payload %s", name, payload)
        event_cb(name, payload or {})

    def _sigint_handler(self, signum, frame) -> None:  # noqa: D401 - inherited signature
        logger.warning("SIGINT received – finishing current operation then aborting")
        self._stop_event.set()

