"""Channel folders: user-defined groups with a color, for organizing the
sidebar. Persisted separately from checkpoints (see store.py) since a
folder assignment is presentation metadata, not fetched channel data.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

from .config import config_dir


def folders_path() -> Path:
    return config_dir() / "folders.json"


class FolderStore:
    def __init__(self) -> None:
        self.path = folders_path()
        self.folders: list[dict] = []          # [{"id","name","color"}, …]
        self.assignments: dict[str, str] = {}  # channel key -> folder id
        self.load()

    # --------------------------------------------------------------- io
    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.folders = data.get("folders") or []
            self.assignments = data.get("assignments") or {}
        except (OSError, json.JSONDecodeError, ValueError):
            self.folders = []
            self.assignments = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"folders": self.folders, "assignments": self.assignments}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # ---------------------------------------------------------- folders
    def list_folders(self) -> list[dict]:
        return list(self.folders)

    def get_folder(self, folder_id: str) -> dict | None:
        return next((f for f in self.folders if f["id"] == folder_id), None)

    def add_folder(self, name: str, color: str) -> str:
        folder_id = uuid.uuid4().hex[:8]
        self.folders.append({"id": folder_id, "name": name.strip() or "Folder",
                             "color": color})
        self.save()
        return folder_id

    def update_folder(self, folder_id: str, name: str | None = None,
                       color: str | None = None) -> bool:
        folder = self.get_folder(folder_id)
        if not folder:
            return False
        if name is not None and name.strip():
            folder["name"] = name.strip()
        if color is not None:
            folder["color"] = color
        self.save()
        return True

    def remove_folder(self, folder_id: str) -> bool:
        before = len(self.folders)
        self.folders = [f for f in self.folders if f["id"] != folder_id]
        if len(self.folders) == before:
            return False
        self.assignments = {k: v for k, v in self.assignments.items() if v != folder_id}
        self.save()
        return True

    # ------------------------------------------------------- assignment
    def folder_for_channel(self, key: str) -> str | None:
        return self.assignments.get(key)

    def folder_sort_key(self, channel_key: str, members: int = 0) -> tuple:
        """(folder_rank, -members) for one channel — grouped by folder in
        `self.folders`' own order (an unassigned channel sorts after every
        real folder), members descending within each group. Shared by the
        sidebar's "Sort Fols" toggle and Config's folder MD export so both
        group channels the exact same way."""
        order = {f["id"]: i for i, f in enumerate(self.folders)}
        rank = order.get(self.assignments.get(channel_key), len(order))
        return (rank, -(members or 0))

    def sorted_by_folder(self, channels: list[dict]) -> list[dict]:
        """channels: [{"key", "members", ...}, ...] (e.g. ChannelStore.list()
        summaries) — see folder_sort_key for the ordering."""
        return sorted(channels,
                      key=lambda ch: self.folder_sort_key(ch["key"], ch.get("members", 0)))

    def set_channel_folder(self, key: str, folder_id: str | None) -> None:
        if folder_id:
            self.assignments[key] = folder_id
        else:
            self.assignments.pop(key, None)
        self.save()

    def assign_all(self, folder_id: str, keys: list[str]) -> None:
        """Bulk-assign every given channel to one folder in a single save,
        instead of N individual set_channel_folder() writes — used by
        Config's "assign every channel to a folder" action."""
        for key in keys:
            self.assignments[key] = folder_id
        self.save()
