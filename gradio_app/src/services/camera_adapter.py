"""Camera interaction helpers tailored for the Gradio application layer.

The adapter mirrors the legacy camera wrapper behaviour but trims CLI oriented
helpers so it can be reused safely from async Gradio callbacks.
"""

from __future__ import annotations

import datetime as _dt
import io
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import gphoto2 as gp

__all__ = [
    "CameraAdapter",
    "CameraAdapterError",
]

logger = logging.getLogger(__name__)


class CameraAdapterError(RuntimeError):
    """Structured exception surfaced to the Gradio layer."""

    def __init__(self, message: str, *, code: str = "camera_error", details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


# ---------------------------------------------------------------------------
# Widget helpers copied from ``app/src/camera_utils.py`` to keep the adapter
# self-contained within ``gradio_app``.
# ---------------------------------------------------------------------------

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
    gp.GP_WIDGET_WINDOW: "WINDOW",
    gp.GP_WIDGET_SECTION: "SECTION",
    gp.GP_WIDGET_TEXT: "TEXT",
    gp.GP_WIDGET_RANGE: "RANGE",
    gp.GP_WIDGET_TOGGLE: "TOGGLE",
    gp.GP_WIDGET_RADIO: "RADIO",
    gp.GP_WIDGET_MENU: "MENU",
    gp.GP_WIDGET_BUTTON: "BUTTON",
    gp.GP_WIDGET_DATE: "DATE",
}


# Known exposure-related config paths surfaced in the live control UI.
EXPOSURE_SETTING_KEYS = (
    "main.imgsettings.iso",
    "main.capturesettings.aperture",
    "main.capturesettings.shutterspeed",
    "main.imgsettings.whitebalance",
)


def flatten_widget(widget: gp.CameraWidget, prefix: str = "") -> Dict[str, gp.CameraWidget]:
    mapping: Dict[str, gp.CameraWidget] = {}
    name = widget.get_name()
    full_name = f"{prefix}.{name}" if prefix else name
    mapping[full_name] = widget
    for idx in range(widget.count_children()):
        mapping.update(flatten_widget(widget.get_child(idx), full_name))
    return mapping


def choices(widget: gp.CameraWidget) -> List[str]:
    return _safe_widget_choices(widget)


def _safe_widget_value(widget: gp.CameraWidget) -> Optional[str]:
    try:
        raw = widget.get_value()
    except gp.GPhoto2Error:
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            return raw.decode(errors="ignore")
        except Exception:
            return None
    return str(raw)


def _safe_widget_choices(widget: gp.CameraWidget) -> List[str]:
    try:
        count = widget.count_choices()
    except gp.GPhoto2Error:
        return []
    options: List[str] = []
    for idx in range(count):
        try:
            choice = widget.get_choice(idx)
        except gp.GPhoto2Error:
            continue
        if isinstance(choice, bytes):
            try:
                choice = choice.decode(errors="ignore")
            except Exception:
                continue
        options.append(str(choice))
    return options


def _range_to_choices(widget: gp.CameraWidget) -> List[str]:
    try:
        vmin, vmax, step = widget.get_range()
    except gp.GPhoto2Error:
        return []
    if step <= 0:
        return []
    count = int(round((vmax - vmin) / step)) + 1
    values: List[str] = []
    for index in range(max(count, 0)):
        raw = vmin + index * step
        if float(raw).is_integer():
            values.append(str(int(raw)))
        else:
            values.append(f"{raw:g}")
    return values


_BOOL_TRUE = {"1", "true", "on", "yes", "enabled"}
_BOOL_FALSE = {"0", "false", "off", "no", "disabled"}


def _to_bool_like(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
    raise ValueError(f"Expected a boolean-like value, got {value!r}.")


def _to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise ValueError(f"Expected a numeric value, got {value!r}.") from exc
    raise ValueError(f"Expected a numeric value, got {value!r}.")


def _aligns_to_step(value: float, vmin: float, step: float, tol: float = 1e-9) -> bool:
    if step <= 0:
        return True
    ratio = (value - vmin) / step
    return abs(ratio - round(ratio)) <= tol * max(1.0, abs(ratio))


def _snap_to_step(value: float, vmin: float, vmax: float, step: float) -> float:
    if step <= 0:
        return float(min(max(value, vmin), vmax))
    rounded = round((value - vmin) / step)
    snapped = vmin + rounded * step
    return float(min(max(snapped, vmin), vmax))


def _to_unix_timestamp(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, _dt.datetime):
        return int(value.timestamp())
    if isinstance(value, _dt.date):
        dt = _dt.datetime(value.year, value.month, value.day)
        return int(dt.timestamp())
    if isinstance(value, str):
        stripped = value.strip()
        try:
            dt = _dt.datetime.fromisoformat(stripped)
            return int(dt.timestamp())
        except ValueError:
            try:
                return int(float(stripped))
            except ValueError as exc:  # pragma: no cover - defensive guard
                raise ValueError(f"Expected ISO8601 string or unix timestamp, got {value!r}.") from exc
    raise ValueError(f"Expected ISO8601 string or unix timestamp, got {value!r}.")


def _coerce_choice(value: Any, valid: Iterable[str]) -> str:
    valid_list = list(valid)
    if isinstance(value, str):
        if value in valid_list:
            return value
        raise ValueError(f"Invalid option {value!r}; choices={valid_list}.")
    if isinstance(value, bool):
        options = set(valid_list)
        if {"On", "Off"} <= options:
            return "On" if value else "Off"
        if {"1", "0"} <= options:
            return "1" if value else "0"
    if isinstance(value, (int, float)):
        as_int = str(int(value))
        as_float = str(value)
        if as_int in valid_list:
            return as_int
        if as_float in valid_list:
            return as_float
    raise ValueError(f"Unsupported value {value!r}; choices={valid_list}.")


def _normalize_for_widget(
    widget: gp.CameraWidget,
    raw: Any,
    *,
    step_policy: str = "strict",
) -> Any:
    wtype = widget.get_type()

    if wtype == gp.GP_WIDGET_TOGGLE:
        return _to_bool_like(raw)
    if wtype in (gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
        return _coerce_choice(raw, choices(widget))
    if wtype == gp.GP_WIDGET_RANGE:
        vmin, vmax, step = widget.get_range()
        numeric = _to_float(raw)
        if step_policy == "snap":
            numeric = _snap_to_step(numeric, vmin, vmax, step)
        if not (vmin <= numeric <= vmax):
            raise ValueError(f"{numeric} not within range [{vmin}, {vmax}].")
        if step_policy == "strict" and not _aligns_to_step(numeric, vmin, step):
            raise ValueError(f"{numeric} does not align to step {step} from {vmin}.")
        if float(numeric).is_integer() and float(vmin).is_integer() and float(step).is_integer():
            return int(numeric)
        return float(numeric)
    if wtype == gp.GP_WIDGET_TEXT:
        if raw is None:
            raise ValueError("Text widgets do not accept None.")
        return str(raw)
    if wtype == gp.GP_WIDGET_DATE:
        return _to_unix_timestamp(raw)
    return raw


class CameraAdapter:
    """High-level, self-healing façade around python-gphoto2."""

    _MAX_RETRIES = 3

    def __init__(self, model: str, port_path: str) -> None:
        self._model = model
        self._port_path = port_path
        self._context: gp.Context = gp.Context()
        self._camera: Optional[gp.Camera] = None
        self._open_camera()

    # ------------------------------------------------------------------
    # Discovery helpers
    # ------------------------------------------------------------------
    @staticmethod
    def discover_cameras() -> List[str]:
        context = gp.Context()
        camera_list = gp.check_result(gp.gp_camera_autodetect(context))
        discovered: List[str] = []
        for idx in range(camera_list.count()):
            name = camera_list.get_name(idx)
            addr = camera_list.get_value(idx)
            discovered.append(f"{name} ({addr})")
        return discovered

    @classmethod
    def select_camera(cls, model_substring: str) -> "CameraAdapter":
        matches = [entry for entry in cls.discover_cameras() if model_substring.lower() in entry.lower()]
        if not matches:
            raise CameraAdapterError(
                f"No camera matches '{model_substring}'.",
                code="camera_not_found",
                details={"query": model_substring},
            )
        if len(matches) > 1:
            raise CameraAdapterError(
                "Multiple cameras matched substring.",
                code="camera_ambiguous",
                details={"query": model_substring, "matches": matches},
            )
        model, port = re.match(r"^(.+?) \((.+)\)$", matches[0]).groups()  # type: ignore[union-attr]
        return cls(model, port)

    @classmethod
    def autodetect(cls) -> "CameraAdapter":
        discovered = cls.discover_cameras()
        if not discovered:
            raise CameraAdapterError("No USB camera detected.", code="camera_not_found")
        model, port = re.match(r"^(.+?) \((.+)\)$", discovered[0]).groups()  # type: ignore[union-attr]
        return cls(model, port)

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    def _open_camera(self) -> None:
        logger.debug("Opening camera %s on %s", self._model, self._port_path)
        abilities_list = gp.CameraAbilitiesList()
        abilities_list.load()
        idx = abilities_list.lookup_model(self._model)
        if idx < 0:
            raise CameraAdapterError(
                f"Model '{self._model}' not found.",
                code="camera_model_unknown",
            )
        abilities = abilities_list.get_abilities(idx)

        port_list = gp.PortInfoList()
        port_list.load()
        port_idx = port_list.lookup_path(self._port_path)
        if port_idx < 0:
            raise CameraAdapterError(
                f"Port '{self._port_path}' not found.",
                code="camera_port_unknown",
            )
        port_info = port_list.get_info(port_idx)

        self._camera = gp.Camera()
        self._camera.set_abilities(abilities)
        self._camera.set_port_info(port_info)

        try:
            self._camera.init()
        except gp.GPhoto2Error as exc:  # pragma: no cover - depends on hardware
            raise CameraAdapterError(
                f"Could not initialise camera: {exc}",
                code="camera_init_failed",
            ) from exc

    def _close_camera(self) -> None:
        if self._camera is None:
            return
        try:
            self._camera.exit()
        except gp.GPhoto2Error:  # pragma: no cover - best effort cleanup
            pass
        finally:
            self._camera = None

    def _usb_reset(self) -> None:
        match = re.match(r"usb:(\d+),(\d+)", self._port_path)
        if not match:
            logger.error("Cannot parse USB address from %s", self._port_path)
            return
        bus, device = match.groups()
        cmd = ["sudo", "usbreset", f"{bus}/{device}"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except subprocess.SubprocessError as exc:  # pragma: no cover - defensive guard
            logger.error("usbreset failed: %s", exc)
            return
        if result.stdout:
            logger.warning("usbreset stdout: %s", result.stdout.strip())
        if result.stderr:
            logger.warning("usbreset stderr: %s", result.stderr.strip())
        if result.returncode:
            logger.warning("usbreset returned %s", result.returncode)

    def _with_reconnect(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except (gp.GPhoto2Error, CameraAdapterError, OSError) as exc:
                last_exc = exc
                logger.warning("Camera call failed (attempt %s/%s): %s", attempt, self._MAX_RETRIES, exc)
                time.sleep(1)
                if attempt >= 2:
                    self._close_camera()
                if attempt >= 3:
                    self._usb_reset()
                try:
                    self._open_camera()
                except CameraAdapterError as reopen_exc:
                    logger.error("Re-open failed: %s", reopen_exc)
        raise CameraAdapterError(
            f"Camera operation failed after retries: {last_exc}",
            code="camera_operation_failed",
        )

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    def _get_config_root(self) -> gp.CameraWidget:
        if self._camera is None:
            raise CameraAdapterError("Camera connection is closed.", code="camera_not_ready")
        return gp.check_result(gp.gp_camera_get_config(self._camera, self._context))

    def get_setting_options(self, setting_keys: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        def _inner() -> Dict[str, Dict[str, Any]]:
            root = self._get_config_root()
            flattened = flatten_widget(root)
            payload: Dict[str, Dict[str, Any]] = {}
            for key in setting_keys:
                widget = flattened.get(key)
                if widget is None:
                    continue
                wtype = widget.get_type()
                entry: Dict[str, Any] = {
                    "type": TYPE_NAMES.get(wtype, "UNKNOWN"),
                    "current": _safe_widget_value(widget),
                    "choices": [],
                }
                if wtype in (gp.GP_WIDGET_MENU, gp.GP_WIDGET_RADIO):
                    entry["choices"] = _safe_widget_choices(widget)
                elif wtype == gp.GP_WIDGET_TOGGLE:
                    entry["choices"] = ["On", "Off"]
                elif wtype == gp.GP_WIDGET_RANGE:
                    entry["choices"] = _range_to_choices(widget)
                payload[key] = entry
            return payload

        return self._with_reconnect(_inner)

    def get_exposure_options(self) -> Dict[str, Dict[str, Any]]:
        return self.get_setting_options(EXPOSURE_SETTING_KEYS)

    def query_settings(self) -> Dict[str, Dict[str, Any]]:
        def _inner() -> Dict[str, Dict[str, Any]]:
            root = self._get_config_root()
            flattened = flatten_widget(root)
            payload: Dict[str, Dict[str, Any]] = {}
            for path, widget in flattened.items():
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
                        entry["current"] = None
                if wtype in (gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
                    entry["choices"] = choices(widget)
                payload[path] = entry
            return payload

        return self._with_reconnect(_inner)

    def get_current_settings(self) -> Dict[str, Any]:
        settings = self.query_settings()
        return {path: info["current"] for path, info in settings.items()}

    def apply_settings(self, new_settings: Dict[str, Any], *, step_policy: str = "strict") -> None:
        if step_policy not in {"strict", "snap"}:
            raise CameraAdapterError("step_policy must be 'strict' or 'snap'.", code="invalid_arguments")

        def _inner() -> None:
            root = self._get_config_root()
            flattened = flatten_widget(root)
            normalized: Dict[str, Any] = {}
            for key, raw_value in new_settings.items():
                widget = flattened.get(key)
                if widget is None:
                    raise CameraAdapterError(
                        f"Unknown setting '{key}'.",
                        code="camera_setting_unknown",
                    )
                try:
                    normalized[key] = _normalize_for_widget(widget, raw_value, step_policy=step_policy)
                except ValueError as exc:
                    raise CameraAdapterError(
                        f"Failed validating '{key}': {exc}",
                        code="camera_setting_invalid",
                        details={"setting": key},
                    ) from exc
            for key, value in normalized.items():
                try:
                    flattened[key].set_value(value)
                except Exception as exc:  # pragma: no cover - depends on driver
                    raise CameraAdapterError(
                        f"Setting '{key}' failed: {exc}",
                        code="camera_setting_apply_failed",
                        details={"setting": key},
                    ) from exc
            gp.check_result(gp.gp_camera_set_config(self._camera, root, self._context))
            logger.debug("Applied settings: %s", normalized)

        self._with_reconnect(_inner)

    def focus_step(self, direction: str = "near", step_size: int = 1, *, live_view: bool = False) -> None:
        if direction not in {"near", "far"}:
            raise CameraAdapterError("direction must be 'near' or 'far'.", code="invalid_arguments")
        if step_size not in {1, 2, 3}:
            raise CameraAdapterError("step_size must be 1, 2 or 3.", code="invalid_arguments")

        current = self.get_current_settings()
        if current.get("main.capturesettings.continuousaf") != "Off":
            logger.debug("Turning off continuous AF")
            self.apply_settings({"main.capturesettings.continuousaf": "Off"})
        if current.get("main.actions.viewfinder") != 1:
            logger.debug("Enabling viewfinder")
            self.apply_settings({"main.actions.viewfinder": 1})
        self.apply_settings({"main.actions.manualfocusdrive": f"{direction.capitalize()} {step_size}"})
        if not live_view:
            self.apply_settings({"main.actions.viewfinder": 0})

    # ------------------------------------------------------------------
    # Capture helpers
    # ------------------------------------------------------------------
    def capture_preview(self) -> io.BytesIO:
        def _inner() -> io.BytesIO:
            if self._camera is None:
                raise CameraAdapterError("Camera connection is closed.", code="camera_not_ready")
            camera_file = gp.check_result(gp.gp_camera_capture_preview(self._camera))
            data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))
            return io.BytesIO(data)

        return self._with_reconnect(_inner)

    def capture_image(self, dest: Optional[Path] = None) -> Path:
        def _inner() -> Path:
            if self._camera is None:
                raise CameraAdapterError("Camera connection is closed.", code="camera_not_ready")
            file_path = gp.check_result(
                gp.gp_camera_capture(self._camera, gp.GP_CAPTURE_IMAGE, self._context)
            )
            camera_file = gp.check_result(
                gp.gp_camera_file_get(
                    self._camera,
                    file_path.folder,
                    file_path.name,
                    gp.GP_FILE_TYPE_NORMAL,
                )
            )
            data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))
            if dest and dest.suffix:
                output_path = dest
            else:
                output_dir = dest or Path(tempfile.gettempdir())
                timestamp = int(time.time())
                extension = Path(file_path.name).suffix
                output_path = output_dir / f"capture_{timestamp}{extension}"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(data)
            gp.check_result(
                gp.gp_camera_file_delete(
                    self._camera,
                    file_path.folder,
                    file_path.name,
                    self._context,
                )
            )
            return output_path

        return self._with_reconnect(_inner)

    def capture_image_no_af(self, dest: Optional[Path] = None, *, timeout_ms: int = 5_000) -> Path:
        """Capture a still without driving the autofocus motor."""

        if timeout_ms <= 0:
            raise CameraAdapterError("timeout_ms must be positive.", code="invalid_arguments")

        eos_remote_release = "main.actions.eosremoterelease"

        def _inner() -> Path:
            if self._camera is None:
                raise CameraAdapterError("Camera connection is closed.", code="camera_not_ready")

            # Trigger shutter without autofocus -------------------------------------------------
            self.apply_settings({eos_remote_release: "Immediate"})

            # Wait until the camera confirms which file was produced ----------------------------
            event_type, event_data = gp.check_result(
                gp.gp_camera_wait_for_event(self._camera, timeout_ms, self._context)
            )
            while event_type != gp.GP_EVENT_FILE_ADDED:
                event_type, event_data = gp.check_result(
                    gp.gp_camera_wait_for_event(self._camera, timeout_ms, self._context)
                )

            file_path = event_data

            camera_file = gp.check_result(
                gp.gp_camera_file_get(
                    self._camera,
                    file_path.folder,
                    file_path.name,
                    gp.GP_FILE_TYPE_NORMAL,
                )
            )
            data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))

            if dest and dest.suffix:
                output_path = dest
            else:
                output_dir = dest or Path(tempfile.gettempdir())
                timestamp = int(time.time())
                extension = Path(file_path.name).suffix
                output_path = output_dir / f"capture_{timestamp}{extension}"

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(data)

            gp.check_result(
                gp.gp_camera_file_delete(
                    self._camera,
                    file_path.folder,
                    file_path.name,
                    self._context,
                )
            )

            # Reset shutter button state -------------------------------------------------------
            self.apply_settings({eos_remote_release: "Release Full"})

            return output_path

        return self._with_reconnect(_inner)

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------
    def get_battery_level(self) -> int:
        value = self.get_current_settings().get("batterylevel")
        if value is None:
            raise CameraAdapterError("Camera does not expose 'batterylevel'.", code="camera_setting_missing")
        try:
            return int(value)
        except (TypeError, ValueError):
            mapping = {"0": 0, "1": 33, "2": 66, "3": 100}
            return mapping.get(str(value), -1)

    # ------------------------------------------------------------------
    # Context manager helpers
    # ------------------------------------------------------------------
    def close(self) -> None:
        self._close_camera()

    def __enter__(self) -> "CameraAdapter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._close_camera()
