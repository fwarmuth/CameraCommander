# camera_utils.py
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Tuple

import gphoto2 as gp

# Public constants used by the wrapper
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

# ------------------------ tree helpers ------------------------------------ #

def flatten_widget(widget: gp.CameraWidget, prefix: str = "") -> Dict[str, gp.CameraWidget]:
    """
    Recursively flatten the config tree, returning {full_path: widget}.
    """
    mp: Dict[str, gp.CameraWidget] = {}
    name = widget.get_name()
    full_name = f"{prefix}.{name}" if prefix else name
    mp[full_name] = widget
    for i in range(widget.count_children()):
        child = widget.get_child(i)
        mp.update(flatten_widget(child, full_name))
    return mp

def choices(widget: gp.CameraWidget) -> List[str]:
    return [widget.get_choice(i) for i in range(widget.count_choices())]

# ------------------------ normalization ----------------------------------- #

_BOOL_TRUE = {"1", "true", "on", "yes", "enabled"}
_BOOL_FALSE = {"0", "false", "off", "no", "disabled"}

def to_bool_like(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _BOOL_TRUE:
            return True
        if s in _BOOL_FALSE:
            return False
    raise ValueError(f"Expected a boolean/0/1, got {v!r}.")

def to_number(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            pass
    raise ValueError(f"Expected a number, got {v!r}.")

def aligns_to_step(value: float, vmin: float, step: float, tol: float = 1e-9) -> bool:
    if step <= 0:
        return True
    k = (value - vmin) / step
    return abs(k - round(k)) <= tol * max(1.0, abs(k))

def snap_to_step(value: float, vmin: float, vmax: float, step: float) -> float:
    """Round to nearest valid step within [vmin, vmax]."""
    if step <= 0:
        return min(max(value, vmin), vmax)
    k = round((value - vmin) / step)
    snapped = vmin + k * step
    return float(min(max(snapped, vmin), vmax))

def to_unix_timestamp(v: Any) -> int:
    """
    Accept int/float epoch seconds, datetime/date, or ISO-8601-ish string.
    Naive datetimes are treated as local time (Python .timestamp()).
    """
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, _dt.datetime):
        return int(v.timestamp())
    if isinstance(v, _dt.date):
        dt = _dt.datetime(v.year, v.month, v.day)
        return int(dt.timestamp())
    if isinstance(v, str):
        s = v.strip()
        try:
            dt = _dt.datetime.fromisoformat(s)
            return int(dt.timestamp())
        except Exception:
            pass
        try:
            return int(float(s))
        except Exception:
            pass
    raise ValueError(f"Expected a Unix timestamp, datetime/date, or ISO8601 string, got {v!r}.")

def coerce_to_choice(value: Any, valid: List[str]) -> str:
    """
    Map arbitrary input to one of the camera's choices (strict).
    - Strings must match exactly (case-sensitive).
    - Booleans map to 'On'/'Off' or '1'/'0' if available.
    - Numbers attempt exact string matches.
    """
    if isinstance(value, str):
        if value in valid:
            return value
        raise ValueError(f"Invalid value {value!r}; choices={valid}.")

    if isinstance(value, bool):
        s = set(valid)
        if {"On", "Off"} <= s:
            return "On" if value else "Off"
        if {"1", "0"} <= s:
            return "1" if value else "0"
        if {"True", "False"} <= s:
            return "True" if value else "False"
        raise ValueError(f"Cannot map boolean to choices {valid}.")

    if isinstance(value, (int, float)):
        s_int = str(int(value))
        s_float = str(value)
        if s_int in valid:
            return s_int
        if s_float in valid:
            return s_float
        raise ValueError(f"Invalid numeric value {value!r}; choices={valid}.")

    raise ValueError(f"Unsupported type {type(value).__name__} for choices {valid}.")

def normalize_for_widget(
    widget: gp.CameraWidget,
    raw: Any,
    *,
    step_policy: str = "strict"  # "strict" | "snap"
) -> Any:
    """
    Convert *raw* into the exact type/value that widget.set_value expects.
    step_policy:
      - "strict": reject RANGE values not on the declared step
      - "snap":   snap to the nearest valid step within bounds
    """
    wtype = widget.get_type()

    if wtype == gp.GP_WIDGET_TOGGLE:
        return to_bool_like(raw)

    if wtype in (gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
        return coerce_to_choice(raw, choices(widget))

    if wtype == gp.GP_WIDGET_RANGE:
        vmin, vmax, step = widget.get_range()
        val = to_number(raw)
        if step_policy == "snap":
            val = snap_to_step(val, vmin, vmax, step)
        if not (vmin <= val <= vmax):
            raise ValueError(f"Range out of bounds: {val} not in [{vmin}, {vmax}].")
        if step_policy == "strict" and not aligns_to_step(val, vmin, step):
            raise ValueError(f"Value {val} does not align to step {step} from min {vmin}.")
        # keep ints when range is integral
        if float(val).is_integer() and float(vmin).is_integer() and float(step).is_integer():
            return int(val)
        return float(val)

    if wtype == gp.GP_WIDGET_TEXT:
        if raw is None:
            raise ValueError("Text cannot be None.")
        return str(raw)

    if wtype == gp.GP_WIDGET_DATE:
        return to_unix_timestamp(raw)

    # BUTTON, WINDOW, SECTION, unknown → pass through (usually ignored by drivers)
    return raw
