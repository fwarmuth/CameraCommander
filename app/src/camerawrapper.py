# camera_wrapper.py
"""
CameraWrapper – a high-level, self-healing wrapper around libgphoto2.
(… your header kept as-is …)
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import io
import gphoto2 as gp

from camera_utils import (
    TYPE_NAMES, VALUE_TYPES, CONTAINER_TYPES,
    flatten_widget, normalize_for_widget, choices,
)

__all__ = ["CameraWrapper", "CameraError"]

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class CameraError(RuntimeError):
    """Raised for any camera-related failure."""


class CameraWrapper:
    """
    High-level façade over libgphoto2 / python-gphoto2.
    (… docstring kept …)
    """

    _MAX_RETRIES: int = 3

    def __init__(self, model: str, port_path: str):
        self._model: str = model
        self._port_path: str = port_path  # e.g. "usb:001,004"
        self._context: gp.Context = gp.Context()
        self._camera: Optional[gp.Camera] = None
        self._open_camera()

    # --------------------------- enumeration -------------------------------- #

    @staticmethod
    def discover_cameras() -> List[str]:
        context = gp.Context()
        camera_list = gp.check_result(gp.gp_camera_autodetect(context))
        models: List[str] = []
        for i in range(camera_list.count()):
            name, addr = camera_list.get_name(i), camera_list.get_value(i)
            models.append(f"{name} ({addr})")
        return models

    @classmethod
    def select_camera(cls, model_substring: str) -> "CameraWrapper":
        matches = [m for m in cls.discover_cameras() if model_substring.lower() in m.lower()]
        if not matches:
            raise CameraError(f"No camera matches '{model_substring}'.")
        if len(matches) > 1:
            raise CameraError(
                f"Multiple cameras match '{model_substring}': {matches}. "
                "Specify a more unique substring."
            )
        model, port = re.match(r"^(.+?) \((.+)\)$", matches[0]).groups()  # type: ignore
        return cls(model, port)


    # --------------------------- connection --------------------------------- #

    def _open_camera(self) -> None:
        logger.debug("Opening camera %s on %s", self._model, self._port_path)

        ab_list = gp.CameraAbilitiesList()
        ab_list.load()
        idx = ab_list.lookup_model(self._model)
        if idx < 0:
            raise CameraError(f"Model '{self._model}' not found.")
        abilities = ab_list.get_abilities(idx)

        pi_list = gp.PortInfoList()
        pi_list.load()
        pidx = pi_list.lookup_path(self._port_path)
        if pidx < 0:
            raise CameraError(f"Port '{self._port_path}' not found.")
        port_info = pi_list.get_info(pidx)

        self._camera = gp.Camera()
        self._camera.set_abilities(abilities)
        self._camera.set_port_info(port_info)

        try:
            self._camera.init()
        except gp.GPhoto2Error as exc:
            raise CameraError(f"Could not initialise camera: {exc}") from exc

    def _close_camera(self) -> None:
        if self._camera is None:
            return
        try:
            self._camera.exit()
        except gp.GPhoto2Error:
            pass
        finally:
            self._camera = None

    def _usb_reset(self) -> None:
        m = re.match(r"usb:(\d+),(\d+)", self._port_path)
        if not m:
            logger.error("Cannot parse USB address from %s", self._port_path)
            return
        bus, dev = m.groups()
        cmd = ["sudo", "usbreset", f"{bus}/{dev}"]
        logger.warning("Running USB reset: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.stdout:
                logger.warning(f"Stdout: {result.stdout}")
            if result.stderr:
                logger.error(f"Stderr: {result.stderr}")
            if result.returncode:
                logger.warning(f"returncode: {result.returncode}")
        except subprocess.SubprocessError as exc:
            logger.error("usbreset failed: %s", exc)
    

    # ------------------------- retry decorator ------------------------------ #

    def _with_reconnect(self, fn: Callable[..., Any], *args, **kwargs):
        """Execute *fn* with retries and automatic reconnect / reset."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except (gp.GPhoto2Error, CameraError, OSError) as exc:
                last_exc = exc
                logger.warning("Attempt %d/%d failed: %s", attempt, self._MAX_RETRIES, exc)
                time.sleep(1)

                # Reconnect strategies escalate with attempts
                if attempt >= 2:
                    self._close_camera()
                if attempt >= 3:
                    self._usb_reset()

                try:
                    self._open_camera()
                except CameraError as exc2:
                    logger.error("Re-open failed: %s", exc2)
                    # keep looping; will escalate/exit naturally
        raise CameraError(f"Operation failed after retries: {last_exc!s}")

    # ----------------------------- config ----------------------------------- #

    def _get_config_root(self) -> gp.CameraWidget:
        return gp.check_result(gp.gp_camera_get_config(self._camera, self._context))

    def query_settings(self) -> Dict[str, Dict[str, Any]]:
        """
        Return {full_path: {"current": value_or_None,
                            "choices": [...]/None,
                            "type": "RADIO" | "TEXT" | ...}}
        """
        def _inner() -> Dict[str, Dict[str, Any]]:
            root = self._get_config_root()
            flat = flatten_widget(root)
            out: Dict[str, Dict[str, Any]] = {}

            for path, widget in flat.items():
                wtype = widget.get_type()

                if wtype in CONTAINER_TYPES:
                    continue

                entry: Dict[str, Any] = {
                    "type": TYPE_NAMES.get(wtype, "UNKNOWN"),
                    "current": None,
                    "choices": None,
                }

                if wtype in VALUE_TYPES:
                    try:
                        entry["current"] = widget.get_value()
                    except gp.GPhoto2Error:
                        pass

                if wtype in (gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
                    entry["choices"] = choices(widget)

                out[path] = entry
            return out

        return self._with_reconnect(_inner)

    def get_current_settings(self) -> Dict[str, Any]:
        return {k: v["current"] for k, v in self.query_settings().items()}

    def apply_settings(self, new_settings: Dict[str, Any], *, step_policy: str = "strict") -> None:
        """
        Atomically set multiple configuration values.
        - step_policy: "strict" (reject misaligned RANGE values) or "snap" (round to nearest step)
        """
        if step_policy not in ("strict", "snap"):
            raise CameraError("step_policy must be 'strict' or 'snap'.")

        def _inner():
            root = self._get_config_root()
            flat = flatten_widget(root)

            # Normalize & validate first (fail fast; no partial writes)
            normalized: Dict[str, Any] = {}
            for key, raw in new_settings.items():
                widget = flat.get(key)
                if widget is None:
                    raise CameraError(f"Unknown setting '{key}'.")
                try:
                    normalized[key] = normalize_for_widget(widget, raw, step_policy=step_policy)
                except ValueError as exc:
                    raise CameraError(f"Failed validating '{key}': {exc}") from exc

            # Apply
            for key, value in normalized.items():
                try:
                    flat[key].set_value(value)
                except Exception as exc:
                    raise CameraError(f"Setting '{key}' failed: {exc}") from exc

            # Commit
            gp.check_result(gp.gp_camera_set_config(self._camera, root, self._context))
            logger.debug("Set settings: %s", normalized)

        return self._with_reconnect(_inner)

    def _focus_step(self, direction="near", step_size=1):
        if direction not in ("near", "far"):
            raise ValueError(
                f"direction must be 'near' or 'far', not {direction}"
            )
        if step_size not in (1, 2, 3):
            raise ValueError(
                f"step_size must be 1, 2 or 3, not {step_size}"
            )
        self.apply_settings(
            {"main.actions.manualfocusdrive": f"{direction.capitalize()} {step_size}"}
        )

    def focus_step(self, direction="near", step_size=1, live_view=False):
        # Check if "main.capturesettings.continuousaf" is off
        if self.query_settings()["main.capturesettings.continuousaf"] != "Off":
            logger.debug("Turning off continuous AF")
            self.apply_settings({"main.capturesettings.continuousaf": "Off"})
        # Check if "main.actions.viewfinder" is on
        if self.query_settings()["main.actions.viewfinder"] != 1:
            logger.debug("Turning on viewfinder")
            self.apply_settings({"main.actions.viewfinder": 1})
        self._focus_step(direction, step_size)
        if not live_view:
            self.apply_settings({"main.actions.viewfinder": 0})

    def capture_preview(self) -> io.BytesIO:
        def _inner() -> io.BytesIO:
            camera_file = gp.check_result(gp.gp_camera_capture_preview(self._camera))
            data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))
            return io.BytesIO(data)
        return self._with_reconnect(_inner)

    # ----------------------------- capture ---------------------------------- #

    def capture_image(self, dest: Optional[Path] = None) -> Path:
        def _inner() -> Path:
            file_path_from_camera = gp.check_result(
                gp.gp_camera_capture(self._camera, gp.GP_CAPTURE_IMAGE, self._context)
            )
            camera_file = gp.check_result(
                gp.gp_camera_file_get(
                    self._camera,
                    file_path_from_camera.folder,
                    file_path_from_camera.name,
                    gp.GP_FILE_TYPE_NORMAL,
                )
            )
            data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))

            if dest and dest.suffix:
                output_path = dest
            else:
                output_dir = dest or Path(tempfile.gettempdir())
                timestamp = int(time.time())
                extension = Path(file_path_from_camera.name).suffix
                output_path = output_dir / f"capture_{timestamp}{extension}"

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as fh:
                fh.write(data)

            gp.check_result(
                gp.gp_camera_file_delete(
                    self._camera,
                    file_path_from_camera.folder,
                    file_path_from_camera.name,
                    self._context,
                )
            )
            return output_path

        return self._with_reconnect(_inner)

    def capture_image_async(self, callback: Callable[[Path], None], dest: Optional[Path] = None) -> None:
        def _worker():
            try:
                img = self.capture_image(dest=dest)
                callback(img)
            except Exception as exc:
                logger.error("Async capture failed: %s", exc)

        threading.Thread(target=_worker, daemon=True).start()
    
    def capture_image_no_af(self, dest: Path | None = None,
                            timeout_ms: int = 5_000) -> Path:
        """
        Capture a photo **without driving the AF motor**.
    
        The function:
          1.  Sends the EOS remote-release value «Immediate» ( = full-press, no AF ),
              which the camera interprets as “fire the shutter *now*”.
          2.  Waits for the GP_EVENT_FILE_ADDED event and downloads that file.
          3.  Sends «Release Full» so the shutter button state is reset.
        """
        EOS_REMOTE_RELEASE = "main.actions.eosremoterelease"     # Canon DSLRs / mirrorless
        def _inner() -> Path:
            # ---------- trigger shutter (no-AF) ----------
            # (Optionally be explicit and switch continuous-AF off once, e.g. at start-up)
            # self.apply_settings({CAF: "Off"})
            self.apply_settings({EOS_REMOTE_RELEASE: "Immediate"})
    
            # ---------- wait until the camera tells us which file was created ----------
            # libgphoto2 raises GP_EVENT_FILE_ADDED when exposure finished
            evt_type, evt_data = gp.check_result(
                gp.gp_camera_wait_for_event(self._camera, timeout_ms, self._context)
            )
            while evt_type != gp.GP_EVENT_FILE_ADDED:
                evt_type, evt_data = gp.check_result(
                    gp.gp_camera_wait_for_event(self._camera, timeout_ms, self._context)
                )
            file_path_from_camera = evt_data      # (folder, name)
    
            # ---------- download ----------
            camera_file = gp.check_result(
                gp.gp_camera_file_get(
                    self._camera,
                    file_path_from_camera.folder,
                    file_path_from_camera.name,
                    gp.GP_FILE_TYPE_NORMAL,
                )
            )
            data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))
    
            # build output path exactly like your original helper -------------
            if dest and dest.suffix:                  # caller supplied fixed filename
                output_path = dest
            else:
                output_dir = dest or Path(tempfile.gettempdir())
                timestamp = int(time.time())
                extension = Path(file_path_from_camera.name).suffix
                output_path = output_dir / f"capture_{timestamp}{extension}"
    
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as fh:
                fh.write(data)
    
            # ---------- clean up on camera ----------
            gp.check_result(
                gp.gp_camera_file_delete(
                    self._camera,
                    file_path_from_camera.folder,
                    file_path_from_camera.name,
                    self._context,
                )
            )
    
            # ---------- release the virtual shutter button ----------
            # If you omit this, the next shot may return “Device Busy”
            self.apply_settings({EOS_REMOTE_RELEASE: "Release Full"})
            return output_path
    
        return self._with_reconnect(_inner)

    # ----------------------------- battery ---------------------------------- #

    def get_battery_level(self) -> int:
        """
        Return battery percent (0-100). If camera exposes enumerated levels 0-3,
        heuristically convert to 0,33,66,100%.
        """
        val = self.query_settings().get("batterylevel", {}).get("current")
        if val is None:
            raise CameraError("Camera does not expose 'batterylevel'.")
        try:
            return int(val)
        except (ValueError, TypeError):
            mapping = {"0": 0, "1": 33, "2": 66, "3": 100}
            return mapping.get(str(val), -1)

    # -------------------------- context manager ----------------------------- #

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._close_camera()
