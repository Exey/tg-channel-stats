"""Channel tags: a lightweight taxonomy loaded from a Markdown table
(| tag | long tag | description |, see load_from_md) rather than managed
in-app like folders — editing the source .md file and reloading is the only
way to add, rename or remove a tag. Only the per-channel *assignment* (which
single tag a channel carries) is app-owned and persists here, the same
shape as app.folders.FolderStore's channel assignments.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .config import config_dir


def tags_path() -> Path:
    return config_dir() / "tags.json"


def parse_md_table(text: str) -> list[dict]:
    """[{"name","long","description"}, …] from a Markdown table shaped like
    "| tag | long tag | description |" — tolerant of extra whitespace and a
    missing "description" column, skips the header/divider rows and any
    data row with an empty tag name."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c or "-") for c in cells):
            continue  # divider row, e.g. |---|---|---|
        rows.append(cells)
    if not rows:
        return []
    header = [c.lower() for c in rows[0]]
    out = []
    for cells in rows[1:]:
        row = dict(zip(header, cells))
        name = (row.get("tag") or "").strip()
        if not name:
            continue
        out.append({"name": name, "long": (row.get("long tag") or "").strip(),
                    "description": (row.get("description") or "").strip()})
    return out


class TagStore:
    def __init__(self) -> None:
        self.path = tags_path()
        self.tags: list[dict] = []              # [{"name","long","description"}, …]
        self.assignments: dict[str, str] = {}    # channel key -> tag name
        self.source_path: str = ""
        self.load()

    # --------------------------------------------------------------- io
    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.tags = data.get("tags") or []
            self.assignments = data.get("assignments") or {}
            self.source_path = data.get("source_path", "")
        except (OSError, json.JSONDecodeError, ValueError):
            self.tags = []
            self.assignments = {}
            self.source_path = ""

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"tags": self.tags, "assignments": self.assignments,
                "source_path": self.source_path}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # ------------------------------------------------------------- tags
    def list_tags(self) -> list[dict]:
        return list(self.tags)

    def has_tag(self, name: str) -> bool:
        return any(t["name"] == name for t in self.tags)

    def load_from_md(self, path: str) -> int:
        """Replace the whole tag list from `path`'s Markdown table —
        channels assigned to a tag no longer present become untagged, the
        same way a deleted folder drops its assignments. Returns how many
        tags were loaded."""
        text = Path(path).read_text(encoding="utf-8")
        tags = parse_md_table(text)
        names = {t["name"] for t in tags}
        self.tags = tags
        self.assignments = {k: v for k, v in self.assignments.items() if v in names}
        self.source_path = str(path)
        self.save()
        return len(tags)

    # ------------------------------------------------------- assignment
    def tag_for_channel(self, key: str) -> str | None:
        return self.assignments.get(key)

    def set_channel_tag(self, key: str, name: str | None) -> None:
        if name:
            self.assignments[key] = name
        else:
            self.assignments.pop(key, None)
        self.save()
