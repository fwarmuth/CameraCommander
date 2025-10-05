"""Path helpers shared across UI modules."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def slugify_plan_name(plan_name: Optional[str], *, fallback: Optional[str] = None) -> str:
    """Generate a filesystem-friendly slug for ``plan_name``.

    Whitespace and punctuation are normalised to hyphens and the result is
    lowercased.  When ``plan_name`` is empty the provided ``fallback`` is
    returned; if that is also ``None`` a UTC timestamp slug is produced.
    """

    if plan_name:
        candidate = _SLUG_PATTERN.sub("-", plan_name.strip().lower()).strip("-")
        if candidate:
            return candidate
    if fallback:
        return fallback
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def default_plan_output_dir(
    plan_name: Optional[str], *, fallback_slug: Optional[str] = None
) -> Path:
    """Return the default output directory for ``plan_name`` timelapses."""

    slug = slugify_plan_name(plan_name, fallback=fallback_slug)
    return Path.home() / "timelapse_output" / slug
