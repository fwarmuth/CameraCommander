"""
CameraWrapper – a high-level, self-healing wrapper around libgphoto2.

Key design references
---------------------
- python-gphoto2 examples for basic libgphoto2 calls :contentReference[oaicite:0]{index=0}
- Enumerating cameras with gp_camera_autodetect :contentReference[oaicite:1]{index=1}
- Walking the configuration tree to list/modify settings :contentReference[oaicite:2]{index=2}
- Idiomatic binding ideas from gphoto2-cffi :contentReference[oaicite:3]{index=3}
- Recovering from “Could not open camera” errors :contentReference[oaicite:4]{index=4}
- Interpreting battery-level widgets :contentReference[oaicite:5]{index=5}
- Resetting a mis-behaving USB device with *usbreset* :contentReference[oaicite:6]{index=6}
- Port close / camera exit best practice :contentReference[oaicite:7]{index=7}
- Non-blocking capture patterns used for webcam mode :contentReference[oaicite:8]{index=8}
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import gphoto2 as gp

__all__ = ["CameraWrapper", "CameraError"]

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class CameraError(RuntimeError):
    """Raised for any camera-related failure."""


class CameraWrapper:
    """
    High-level façade over libgphoto2 / python-gphoto2.

    Features
    --------
    * Discover and select USB cameras
    * Read / modify configuration
    * Blocking and threaded (non-blocking) image capture
    * Battery-level query
    * Automatic self-healing reconnect with optional USB reset

    Notes
    -----
    * All public APIs raise `CameraError` on failure.
    * Methods that perform I/O are routed through `_with_reconnect`
      so a transient failure triggers close → reopen → usbreset retry logic.
    """

    #: How many consecutive failures trigger escalation
    _MAX_RETRIES: int = 3

    def __init__(self, model: str, port_path: str):
        self._model: str = model
        self._port_path: str = port_path  # e.g. "usb:001,004"
        self._context: gp.Context = gp.Context()
        self._camera: Optional[gp.Camera] = None
        self._open_camera()

    # --------------------------------------------------------------------- #
    # Static helpers – enumeration & selection
    # --------------------------------------------------------------------- #
    @staticmethod
    def discover_cameras() -> List[str]:
        """
        Return list of detected camera model strings.

        Example result: ["Canon EOS R (usb:001,004)", "Nikon D750 (usb:001,006)"]
        """
        context = gp.Context()
        camera_list = gp.check_result(gp.gp_camera_autodetect(context))
        models: List[str] = []
        for i in range(camera_list.count()):
            name, addr = camera_list.get_name(i), camera_list.get_value(i)
            models.append(f"{name} ({addr})")
        return models

    @classmethod
    def select_camera(cls, model_substring: str) -> "CameraWrapper":
        """
        Convenience constructor that picks a unique camera whose *model_substring*
        (case-insensitive) appears in its model string, otherwise raises.
        """
        matches = [
            m for m in cls.discover_cameras()
            if model_substring.lower() in m.lower()
        ]
        if not matches:
            raise CameraError(f"No camera matches '{model_substring}'.")
        if len(matches) > 1:
            raise CameraError(
                f"Multiple cameras match '{model_substring}': {matches}. "
                "Specify a more unique substring."
            )
        # Split "<model> (<port>)"
        model, port = re.match(r"^(.+?) \((.+)\)$", matches[0]).groups()  # type: ignore
        return cls(model, port)
    
    # ------------------------------------------------------------------ #
    #  Connection helpers – final, working version                       #
    # ------------------------------------------------------------------ #
    def _open_camera(self) -> None:
        """(Re)open the underlying gp.Camera handle using high-level helpers."""
        logger.debug("Opening camera %s on %s", self._model, self._port_path)
    
        # --- abilities list --------------------------------------------------
        ab_list = gp.CameraAbilitiesList()
        ab_list.load()                                                # GP checks inside
        idx = ab_list.lookup_model(self._model)
        if idx < 0:
            raise CameraError(f"Model '{self._model}' not found.")
        abilities = ab_list.get_abilities(idx)
    
        # --- port info list --------------------------------------------------
        pi_list = gp.PortInfoList()
        pi_list.load()
        pidx = pi_list.lookup_path(self._port_path)
        if pidx < 0:
            raise CameraError(f"Port '{self._port_path}' not found.")
        port_info = pi_list.get_info(pidx)
    
        # --- bind + init -----------------------------------------------------
        self._camera = gp.Camera()
        self._camera.set_abilities(abilities)
        self._camera.set_port_info(port_info)
    
        try:
            self._camera.init()                                       # ← no check_result
        except gp.GPhoto2Error as exc:
            raise CameraError(f"Could not initialise camera: {exc}") from exc
    
    def _close_camera(self) -> None:
        """Gracefully close an open camera handle."""
        if self._camera is None:
            return
        try:
            self._camera.exit()                                       # ← no check_result
        except gp.GPhoto2Error:
            pass
        finally:
            self._camera = None

    def _usb_reset(self) -> None:
        """
        Last-ditch effort: invoke *usbreset* on the camera's bus:device
        (requires CAP_SYS_ADMIN or sudo). :contentReference[oaicite:9]{index=9}
        """
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

    # --------------------------------------------------------------------- #
    # Decorator – retry with reconnect logic
    # --------------------------------------------------------------------- #
    def _with_reconnect(self, fn: Callable[..., Any], *args, **kwargs):
        """Execute *fn* with retries and automatic reconnect / reset."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except (gp.GPhoto2Error, CameraError, OSError) as exc:
                last_exc = exc
                logger.warning("Attempt %d/%d failed: %s", attempt, self._MAX_RETRIES, exc)
                # Close then reopen; escalate to usbreset on final retry
                self._close_camera()
                time.sleep(0.5)
                self._usb_reset()
                time.sleep(0.5)
                try:
                    self._open_camera()
                except CameraError as exc2:
                    logger.error("Re-open failed: %s", exc2)
                    # Continue to next loop – usbreset will happen on final
        raise CameraError(f"Operation failed after retries: {last_exc!s}")

    # --------------------------------------------------------------------- #
    # Configuration helpers
    # --------------------------------------------------------------------- #
    def _get_config_root(self) -> gp.CameraWidget:
        return gp.check_result(gp.gp_camera_get_config(self._camera, self._context))

    @staticmethod
    def _flatten_widget(widget: gp.CameraWidget, prefix: str = "") -> Dict[str, gp.CameraWidget]:
        """
        Recursively flatten the config tree, returning {full_name: widget}.
        """
        mp: Dict[str, gp.CameraWidget] = {}
        name = widget.get_name()
        full_name = f"{prefix}.{name}" if prefix else name
        mp[full_name] = widget
        for i in range(widget.count_children()):
            child = widget.get_child(i)
            mp.update(CameraWrapper._flatten_widget(child, full_name))
        return mp

    def query_settings(self) -> Dict[str, Dict[str, Any]]:
        """
        Safely walk the entire configuration tree and return

            {full_path: {"current": value_or_None,
                         "choices": [...]/None,
                         "type": "RADIO" | "TEXT" | ...}}

        Widgets that are purely structural (WINDOW / SECTION) are omitted.
        BUTTON widgets are reported with current=None and choices=None.
        """
        VALUE_TYPES = {
            gp.GP_WIDGET_TEXT,
            gp.GP_WIDGET_RANGE,
            gp.GP_WIDGET_TOGGLE,
            gp.GP_WIDGET_RADIO,
            gp.GP_WIDGET_MENU,
            gp.GP_WIDGET_DATE,
        }
        CONTAINER_TYPES = {gp.GP_WIDGET_WINDOW, gp.GP_WIDGET_SECTION}

        TYPE_NAMES = {
            gp.GP_WIDGET_WINDOW:  "WINDOW",
            gp.GP_WIDGET_SECTION: "SECTION",
            gp.GP_WIDGET_TEXT:    "TEXT",
            gp.GP_WIDGET_RANGE:   "RANGE",
            gp.GP_WIDGET_TOGGLE:  "TOGGLE",
            gp.GP_WIDGET_RADIO:   "RADIO",
            gp.GP_WIDGET_MENU:    "MENU",
            gp.GP_WIDGET_BUTTON:  "BUTTON",
            gp.GP_WIDGET_DATE:    "DATE",
        }

        def _inner() -> Dict[str, Dict[str, Any]]:
            root = self._get_config_root()
            flat = self._flatten_widget(root)
            out: Dict[str, Dict[str, Any]] = {}

            for path, widget in flat.items():
                wtype = widget.get_type()

                # Skip pure containers – they have no value.
                if wtype in CONTAINER_TYPES:
                    continue

                entry: Dict[str, Any] = {"type": TYPE_NAMES.get(wtype, "UNKNOWN"),
                                         "current": None,
                                         "choices": None}

                # Try to read current value if the type supports it
                if wtype in VALUE_TYPES:
                    try:
                        entry["current"] = widget.get_value()
                    except gp.GPhoto2Error:
                        # Some drivers still return BAD_PARAMETERS – ignore
                        pass

                # Enumerate choices for RADIO / MENU widgets
                if wtype in (gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
                    entry["choices"] = [
                        widget.get_choice(i) for i in range(widget.count_choices())
                    ]

                out[path] = entry
            return out

        return self._with_reconnect(_inner)


    def get_current_settings(self) -> Dict[str, Any]:
        """Return {setting_name: current_value} without choice metadata."""
        settings = self.query_settings()
        return {k: v["current"] for k, v in settings.items()}

    def apply_settings(self, new_settings: Dict[str, Any]) -> None:
        """
        Atomically set multiple configuration values.

        Validation: fails fast if any key unknown or value invalid.
        """
        def _inner():
            root = self._get_config_root()
            flat = self._flatten_widget(root)
            # Validate
            for key, value in new_settings.items():
                if key not in flat:
                    raise CameraError(f"Unknown setting '{key}'.")
                widget = flat[key]
                wtype = widget.get_type()
                if wtype in (gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
                    valid = [widget.get_choice(i) for i in range(widget.count_choices())]
                    if str(value) not in valid:
                        raise CameraError(f"Invalid value '{value}' for {key}; choices={valid}.")
            # Apply
            for key, value in new_settings.items():
                flat[key].set_value(str(value))
            # Commit
            gp.check_result(gp.gp_camera_set_config(self._camera, root, self._context))

        self._with_reconnect(_inner)

    # --------------------------------------------------------------------- #
    # Capture
    # --------------------------------------------------------------------- #
    def capture_image(self, dest: Optional[Path] = None) -> Path:
        """
        Blocking capture. Returns the saved image Path.
        When dest is a file it is used as output, when dest is a dir create it
        and use a generic name using the current timestamp to avoid overriding.
        """

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
        """
        Non-blocking capture. Executes *callback(path)* in a background thread.
        """

        def _worker():
            try:
                img = self.capture_image(dest=dest)
                callback(img)
            except Exception as exc:
                logger.error("Async capture failed: %s", exc)

        threading.Thread(target=_worker, daemon=True).start()

    # --------------------------------------------------------------------- #
    # Battery
    # --------------------------------------------------------------------- #
    def get_battery_level(self) -> int:
        """
        Return battery percent (0-100). If camera exposes enumerated levels 0-3,
        heuristically convert to 0,33,66,100%. :contentReference[oaicite:10]{index=10}
        """
        val = self.query_settings().get("batterylevel", {}).get("current")
        if val is None:
            raise CameraError("Camera does not expose 'batterylevel'.")
        try:
            # Common case: integer percent already
            return int(val)
        except ValueError:
            # Fallback mapping
            mapping = {"0": 0, "1": 33, "2": 66, "3": 100}
            return mapping.get(str(val), -1)

    # --------------------------------------------------------------------- #
    # Context manager helpers
    # --------------------------------------------------------------------- #
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._close_camera()
