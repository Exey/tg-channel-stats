"""Local disk cache for post-thumbnail images (see app.tools.media_fetch,
used by the High-Quality Posts view). Mirrors app.store's checkpoint
directory pattern, just for small cached image files instead of JSON.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import config_dir

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def media_cache_dir() -> Path:
    d = config_dir() / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_channel(channel: str) -> str:
    return _SAFE.sub("_", str(channel).lstrip("@")) or "channel"


def thumbnail_path(channel: str, post_id: int) -> Path:
    """Where a post's cached thumbnail lives — may or may not exist yet;
    callers check `.exists()` before trying to load/download it."""
    return media_cache_dir() / f"{_safe_channel(channel)}_{int(post_id)}.jpg"
