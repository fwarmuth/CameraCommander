#!/usr/bin/env python3
"""
Dump all gphoto2 camera settings and their possible values.

Requires:
  pip install gphoto2
and a camera supported by libgphoto2 connected and unlocked (PC control enabled).

Usage:
  python dump_camera_settings.py               # pretty print
  python dump_camera_settings.py --json out.json
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

try:
    import gphoto2 as gp
except Exception as e:
    print("Error: Python bindings for libgphoto2 not found. Try `pip install gphoto2`.", file=sys.stderr)
    raise

WIDGET_TYPES = {
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

def widget_type_name(wtype: int) -> str:
    return WIDGET_TYPES.get(wtype, f"UNKNOWN({wtype})")

def get_widget_value(widget) -> Optional[Any]:
    try:
        return widget.get_value()
    except gp.GPhoto2Error:
        return None

def get_widget_choices(widget) -> Optional[List[str]]:
    # For MENU and RADIO, libgphoto2 exposes a finite set of choices.
    try:
        n = widget.count_choices()
    except gp.GPhoto2Error:
        return None
    if n and n > 0:
        choices = []
        for i in range(n):
            try:
                choices.append(widget.get_choice(i))
            except gp.GPhoto2Error:
                pass
        return choices or None
    return None

def get_widget_range(widget) -> Optional[Dict[str, float]]:
    # For RANGE widgets, return min/max/step.
    try:
        rmin, rmax, rstep = widget.get_range()
        return {"min": float(rmin), "max": float(rmax), "step": float(rstep)}
    except gp.GPhoto2Error:
        return None

def get_widget_flags(widget) -> Optional[List[str]]:
    # Flags are optional; not all bindings expose named flags.
    try:
        flags = widget.get_flags()  # bitfield
    except Exception:
        return None
    names = []
    # Known bit(s) in recent bindings; if unavailable, this silently no-ops.
    try:
        if hasattr(gp, "GP_WIDGET_FLAG_READONLY") and flags & gp.GP_WIDGET_FLAG_READONLY:
            names.append("READONLY")
    except Exception:
        pass
    return names or None

def walk_widget(widget, path: str = "") -> List[Dict[str, Any]]:
    """
    Recursively traverse the configuration tree and collect metadata.
    """
    data: List[Dict[str, Any]] = []

    wtype = widget.get_type()
    wname = widget.get_name() or ""
    wlabel = widget.get_label() or ""
    wpath = path + ("/" if path and wname else "") + (wname or wlabel or widget_type_name(wtype))

    entry: Dict[str, Any] = {
        "path": wpath,
        "name": wname or None,
        "label": wlabel or None,
        "type": widget_type_name(wtype),
        "value": get_widget_value(widget),
        "choices": None,
        "range": None,
        "flags": get_widget_flags(widget),
        "readable": True,   # default assumptions; lib may not expose
        "writable": True,   # both in all environments. Adjust if needed.
    }

    # Add possible values info depending on widget type
    if wtype in (gp.GP_WIDGET_MENU, gp.GP_WIDGET_RADIO):
        entry["choices"] = get_widget_choices(widget)
    elif wtype == gp.GP_WIDGET_RANGE:
        entry["range"] = get_widget_range(widget)

    data.append(entry)

    # Recurse into children (for WINDOW/SECTION and sometimes others with children)
    try:
        count = widget.count_children()
    except gp.GPhoto2Error:
        count = 0

    for i in range(count or 0):
        try:
            child = widget.get_child(i)
        except gp.GPhoto2Error:
            continue
        data.extend(walk_widget(child, wpath))

    return data

def pretty_print(entries: List[Dict[str, Any]]) -> None:
    for e in entries:
        print(f"{e['path']}")
        print(f"  type:    {e['type']}")
        if e.get("label"):
            print(f"  label:   {e['label']}")
        if e.get("name"):
            print(f"  name:    {e['name']}")
        if e.get("value") is not None:
            print(f"  value:   {e['value']}")
        if e.get("choices"):
            print(f"  choices: {', '.join(map(str, e['choices']))}")
        if e.get("range"):
            r = e["range"]
            print(f"  range:   min={r['min']} max={r['max']} step={r['step']}")
        if e.get("flags"):
            print(f"  flags:   {', '.join(e['flags'])}")
        print()

def main():
    ap = argparse.ArgumentParser(description="List all gphoto2 camera settings with possible values.")
    ap.add_argument("--json", metavar="FILE", help="Write results as JSON to FILE (use '-' for stdout).")
    args = ap.parse_args()

    camera = gp.Camera()
    try:
        camera.init()
    except gp.GPhoto2Error as e:
        print(f"Failed to init camera: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        config = camera.get_config()
    except gp.GPhoto2Error as e:
        print(f"Failed to get camera config: {e}", file=sys.stderr)
        try:
            camera.exit("--json", )
        except Exception:
            pass
        sys.exit(1)

    entries = walk_widget(config)

    # Attach some device info for context
    try:
        cam_summary = camera.get_summary()
        summary_text = str(cam_summary.text).strip()
    except Exception:
        summary_text = None

    result = {
        "camera_summary": summary_text,
        "settings": entries,
    }

    try:
        camera.exit()
    except Exception:
        pass

    if args.json:
        if args.json == "-":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"Wrote {len(entries)} settings to {args.json}")
    else:
        if summary_text:
            print("# Camera summary")
            print(summary_text)
            print()
        print("# Settings")
        pretty_print(entries)

if __name__ == "__main__":
    main()
