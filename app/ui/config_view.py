"""Config screen: Telegram credentials + the "fetch a channel" card.

Combines tg-super-admin's Config tab (profiles, connection fields, QR / check
login, .env import-export) with a compact fetch panel that drives the
channel_stat tool in a background worker and emits the finished payload.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..config import CONN_FIELDS, config_dir
from ..errors import friendly_os_error
from ..folders import FolderStore
from ..periods import period_key_label
from ..rating import score_entries
from ..scoring import post_gauge_value, post_score_raw
from ..store import ChannelStore
from ..tags import TagStore
from ..tools.channel_stat import run_channel_stat
from ..tools.comments_refresh import run_comments_refresh
from ..worker import CheckLoginWorker, ToolWorker
from .dashboard_view import fmt_int
from .folder_dialog import FolderManagerDialog
from .qr_login_dialog import QrLoginDialog
from .widgets import Card, SectionCard

PERIOD_KEYS = ["2y", "3y", "all"]


def _channel_display_name(ch: dict) -> str:
    username = ch.get("username") or ""
    return f"@{username}" if username else (ch.get("title") or ch.get("key", "?"))


class ConfigView(QWidget):
    channel_fetched = Signal(dict)   # full channel_stat payload
    folders_changed = Signal()
    tags_changed = Signal()
    checkpoints_changed = Signal()   # a folder's checkpoints were updated in place

    def __init__(self, cfg, i18n, folder_store: FolderStore, tag_store: TagStore,
                channel_store: ChannelStore, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.i18n = i18n
        self.folder_store = folder_store
        self.tag_store = tag_store
        self.channel_store = channel_store
        self.worker: ToolWorker | None = None
        self._build_ui()
        self._load_fields()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 28, 40, 24)
        outer.setSpacing(18)

        header = QVBoxLayout()
        header.setSpacing(2)
        self.title_lbl = QLabel(self.tr_("nav_config"))
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        self.sub_lbl = QLabel(self.tr_("app_title"))
        self.sub_lbl.setObjectName("pageSub")
        header.addWidget(self.sub_lbl)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 6, 0)
        root.setSpacing(18)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        root.addWidget(self._connection_card())
        root.addWidget(self._fetch_card())
        root.addWidget(self._instructions_card())
        taxonomy_row = QHBoxLayout()
        taxonomy_row.setSpacing(18)
        taxonomy_row.addWidget(self._folders_card(), 1)
        taxonomy_row.addWidget(self._tags_card(), 1)
        root.addLayout(taxonomy_row)
        root.addStretch()

    def _connection_card(self) -> Card:
        card = SectionCard("Telegram")

        prow = QHBoxLayout()
        self.profile_lbl = QLabel(self.tr_("profile"))
        prow.addWidget(self.profile_lbl)
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(sorted(self.cfg.profiles))
        self.profile_combo.setCurrentText(self.cfg.current_profile)
        self.profile_combo.currentTextChanged.connect(self._switch_profile)
        prow.addWidget(self.profile_combo, 1)
        self.new_profile_btn = QPushButton(self.tr_("new_profile"))
        self.new_profile_btn.clicked.connect(self._new_profile)
        prow.addWidget(self.new_profile_btn)
        self.del_profile_btn = QPushButton(self.tr_("delete_profile"))
        self.del_profile_btn.clicked.connect(self._delete_profile)
        prow.addWidget(self.del_profile_btn)
        card.body.addLayout(prow)

        self.conn_form = QFormLayout()
        self.conn_form.setSpacing(10)
        self.edits: dict[str, QLineEdit] = {}
        for key in CONN_FIELDS:
            edit = QLineEdit()
            if key == "API_HASH":
                edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
            self.edits[key] = edit
            self.conn_form.addRow(self.tr_(f"field_{key}"), edit)
        card.body.addLayout(self.conn_form)

        brow = QHBoxLayout()
        self.save_btn = QPushButton(self.tr_("save"))
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        brow.addWidget(self.save_btn)
        self.qr_btn = QPushButton(self.tr_("qr_login_button"))
        self.qr_btn.clicked.connect(self._qr_login)
        brow.addWidget(self.qr_btn)
        self.check_login_btn = QPushButton(self.tr_("check_login_button"))
        self.check_login_btn.clicked.connect(self._check_login)
        brow.addWidget(self.check_login_btn)
        brow.addStretch()
        card.body.addLayout(brow)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        card.body.addWidget(self.status)

        loc_row = QHBoxLayout()
        self.loc_lbl = QLabel(self.tr_("config_location", path=str(self.cfg.path)))
        self.loc_lbl.setObjectName("hint")
        self.loc_lbl.setWordWrap(True)
        loc_row.addWidget(self.loc_lbl, 1)
        self.open_folder_btn = QPushButton(self.tr_("open_config_folder"))
        self.open_folder_btn.setObjectName("ghost")
        self.open_folder_btn.clicked.connect(self._open_config_folder)
        loc_row.addWidget(self.open_folder_btn)
        card.body.addLayout(loc_row)
        return card

    def _open_config_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir())))

    def _fetch_card(self) -> Card:
        card = SectionCard(self.tr_("fetch_title"))
        self.fetch_card_ref = card

        self.fetch_help_lbl = QLabel(self.tr_("fetch_help"))
        self.fetch_help_lbl.setObjectName("hint")
        self.fetch_help_lbl.setWordWrap(True)
        card.body.addWidget(self.fetch_help_lbl)

        self.channel_lbl = QLabel(self.tr_("fetch_channel"))
        card.body.addWidget(self.channel_lbl)
        self.channel_edit = QLineEdit(self.cfg.get("CHANNEL_ID"))
        self.channel_edit.setPlaceholderText(self.tr_("fetch_channel_placeholder"))
        self.channel_edit.setClearButtonEnabled(True)
        card.body.addWidget(self.channel_edit)

        self.fetch_form = QFormLayout()
        self.fetch_form.setSpacing(10)
        self.period_combo = QComboBox()
        self.period_combo.addItems([self.tr_(f"period_{k}") for k in PERIOD_KEYS])
        self.period_combo.setCurrentIndex(0)  # 2 years
        self.fetch_form.addRow(self.tr_("fetch_period"), self.period_combo)
        card.body.addLayout(self.fetch_form)

        self.public_check = QCheckBox(self.tr_("fetch_public"))
        card.body.addWidget(self.public_check)

        brow = QHBoxLayout()
        self.fetch_btn = QPushButton(self.tr_("fetch_button"))
        self.fetch_btn.setObjectName("primary")
        self.fetch_btn.clicked.connect(self._on_fetch_clicked)
        brow.addWidget(self.fetch_btn)
        self.stop_btn = QPushButton(self.tr_("stop"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        brow.addWidget(self.stop_btn)
        brow.addStretch()
        card.body.addLayout(brow)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        card.body.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        self.log_view.setFixedHeight(120)
        card.body.addWidget(self.log_view)
        return card

    def _instructions_card(self) -> QGroupBox:
        box = QGroupBox(self.tr_("instructions_title"))
        self.instructions_box = box
        box.setCheckable(True)
        box.setChecked(False)
        lay = QVBoxLayout(box)
        self.instructions_info = QLabel(self.tr_("instructions_text"))
        self.instructions_info.setWordWrap(True)
        self.instructions_info.setOpenExternalLinks(True)
        self.instructions_info.setTextFormat(Qt.TextFormat.RichText)
        self.instructions_info.setVisible(False)
        lay.addWidget(self.instructions_info)
        box.toggled.connect(self.instructions_info.setVisible)
        return box

    def _folders_card(self) -> Card:
        card = SectionCard(self.tr_("folder_section_title"))
        self.folders_card_ref = card

        self.folders_help_lbl = QLabel(self.tr_("folder_manage_help"))
        self.folders_help_lbl.setObjectName("hint")
        self.folders_help_lbl.setWordWrap(True)
        card.body.addWidget(self.folders_help_lbl)

        self.folders_list_lbl = QLabel()
        self.folders_list_lbl.setObjectName("hint")
        self.folders_list_lbl.setWordWrap(True)
        self.folders_list_lbl.setTextFormat(Qt.TextFormat.RichText)
        card.body.addWidget(self.folders_list_lbl)

        row = QHBoxLayout()
        self.folders_manage_btn = QPushButton(self.tr_("folder_manage"))
        self.folders_manage_btn.clicked.connect(self._open_folder_manager)
        row.addWidget(self.folders_manage_btn)
        row.addStretch()
        card.body.addLayout(row)

        export_row = QHBoxLayout()
        self.folders_export_md_btn = QPushButton(self.tr_("folder_export_md_btn"))
        self.folders_export_md_btn.setToolTip(self.tr_("folder_export_md_hint"))
        self.folders_export_md_btn.clicked.connect(self._on_export_folders_md)
        export_row.addWidget(self.folders_export_md_btn)
        self.folders_export_extra_chk = QCheckBox(self.tr_("folder_export_extra_cols"))
        self.folders_export_extra_chk.setToolTip(self.tr_("folder_export_extra_cols_hint"))
        self.folders_export_extra_chk.toggled.connect(
            lambda on: self.folders_export_period_combo.setEnabled(on))
        export_row.addWidget(self.folders_export_extra_chk)
        export_row.addStretch()
        # Which specific period Rating/Views/Viral share are computed over
        # — every Half-Year bucket across all tracked channels (newest
        # first), then every Season bucket the same way, then a trailing
        # "All time" entry (see _collect_export_periods/
        # _channel_period_metrics). No "Monthly" entries on purpose: a
        # single calendar month is too noisy a window for a per-channel
        # export meant to compare many channels at a glance.
        self.folders_export_period_combo = QComboBox()
        self.folders_export_period_combo.setToolTip(self.tr_("folder_export_period_hint"))
        self.folders_export_period_combo.setEnabled(self.folders_export_extra_chk.isChecked())
        self.refresh_export_periods()
        export_row.addWidget(self.folders_export_period_combo)
        card.body.addLayout(export_row)

        # Lightweight partial re-fetch: only patches the `comments` field on
        # each channel's already-stored checkpoint rows (see
        # tools.comments_refresh) instead of a full re-scan — for a folder
        # whose channels were fetched before that field existed, or whose
        # comment counts have just gone stale.
        comments_row = QHBoxLayout()
        self.comments_refresh_lbl = QLabel(self.tr_("folder_comments_refresh_label"))
        comments_row.addWidget(self.comments_refresh_lbl)
        self.comments_folder_combo = QComboBox()
        comments_row.addWidget(self.comments_folder_combo, 1)
        self.refresh_comments_btn = QPushButton(self.tr_("folder_comments_refresh_btn"))
        self.refresh_comments_btn.setToolTip(self.tr_("folder_comments_refresh_hint"))
        self.refresh_comments_btn.clicked.connect(self._on_refresh_comments_clicked)
        comments_row.addWidget(self.refresh_comments_btn)
        card.body.addLayout(comments_row)

        # Bulk move: every tracked channel into one folder at once, instead
        # of assigning them one by one from the sidebar's right-click menu —
        # handy right after creating a folder for a batch of channels
        # that were all fetched before any folders existed.
        assign_all_row = QHBoxLayout()
        self.assign_all_lbl = QLabel(self.tr_("folder_assign_all_label"))
        assign_all_row.addWidget(self.assign_all_lbl)
        self.assign_all_combo = QComboBox()
        assign_all_row.addWidget(self.assign_all_combo, 1)
        self.assign_all_btn = QPushButton(self.tr_("folder_assign_all_btn"))
        self.assign_all_btn.setToolTip(self.tr_("folder_assign_all_hint"))
        self.assign_all_btn.clicked.connect(self._on_assign_all_clicked)
        assign_all_row.addWidget(self.assign_all_btn)
        card.body.addLayout(assign_all_row)

        self.refresh_folders_list()
        return card

    def _tags_card(self) -> Card:
        card = SectionCard(self.tr_("tag_section_title"))
        self.tags_card_ref = card

        self.tags_help_lbl = QLabel(self.tr_("tag_manage_help"))
        self.tags_help_lbl.setObjectName("hint")
        self.tags_help_lbl.setWordWrap(True)
        card.body.addWidget(self.tags_help_lbl)

        self.tags_list_lbl = QLabel()
        self.tags_list_lbl.setObjectName("hint")
        self.tags_list_lbl.setWordWrap(True)
        self.tags_list_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.tags_list_lbl.setStyleSheet("font-size: 14px;")
        card.body.addWidget(self.tags_list_lbl)

        row = QHBoxLayout()
        self.tags_load_btn = QPushButton(self.tr_("tag_load_md_btn"))
        self.tags_load_btn.setToolTip(self.tr_("tag_load_md_hint"))
        self.tags_load_btn.clicked.connect(self._on_load_tags_md)
        row.addWidget(self.tags_load_btn)
        row.addStretch()
        card.body.addLayout(row)

        self.refresh_tags_list()
        return card

    def refresh_tags_list(self) -> None:
        """One tag per line, biggest tag (most assigned channels) first,
        each followed by up to 3 of its biggest channels by followers — a
        quick "what's actually in this tag" glance without opening the
        sidebar's right-click menu on every channel."""
        tags = self.tag_store.list_tags()
        if not tags:
            self.tags_list_lbl.setText(self.tr_("tag_list_empty"))
            return
        summaries = {s["key"]: s for s in self.channel_store.list()}
        channels_by_tag: dict[str, list[dict]] = {}
        for key, name in self.tag_store.assignments.items():
            channels_by_tag.setdefault(name, []).append(summaries.get(key, {"key": key}))

        rows = []
        for t in tags:
            channels = sorted(channels_by_tag.get(t["name"], []),
                              key=lambda c: c.get("members", 0) or 0, reverse=True)
            rows.append((len(channels), t["name"], channels[:3]))
        rows.sort(key=lambda r: r[0], reverse=True)

        lines = []
        for count, name, top in rows:
            examples = ", ".join(html.escape(_channel_display_name(c)) for c in top)
            suffix = f" ({examples})" if examples else ""
            lines.append(f"{count} — {html.escape(name)}{suffix}")
        self.tags_list_lbl.setText("<br>".join(lines))

    def _on_load_tags_md(self) -> None:
        start_dir = str(Path(self.tag_store.source_path).parent) if self.tag_store.source_path \
            else str(Path.home() / "Desktop")
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr_("tag_load_md_btn"), start_dir, "Markdown (*.md)")
        if not path:
            return
        try:
            n = self.tag_store.load_from_md(path)
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), friendly_os_error(exc))
            return
        if n == 0:
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("tag_load_md_empty"))
            return
        self.refresh_tags_list()
        self.tags_changed.emit()
        QMessageBox.information(self, self.tr_("app_title"), self.tr_("tag_load_md_done", n=n))

    def refresh_folders_list(self) -> None:
        current_folder_id = self.comments_folder_combo.currentData()
        self.comments_folder_combo.blockSignals(True)
        self.comments_folder_combo.clear()
        for folder in self.folder_store.list_folders():
            self.comments_folder_combo.addItem(folder["name"], folder["id"])
        if self.comments_folder_combo.count():
            idx = self.comments_folder_combo.findData(current_folder_id)
            self.comments_folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.comments_folder_combo.blockSignals(False)
        has_folders = self.comments_folder_combo.count() > 0
        self.comments_refresh_lbl.setVisible(has_folders)
        self.comments_folder_combo.setVisible(has_folders)
        self.refresh_comments_btn.setVisible(has_folders)

        current_assign_id = self.assign_all_combo.currentData()
        self.assign_all_combo.blockSignals(True)
        self.assign_all_combo.clear()
        for folder in self.folder_store.list_folders():
            self.assign_all_combo.addItem(folder["name"], folder["id"])
        if self.assign_all_combo.count():
            idx = self.assign_all_combo.findData(current_assign_id)
            self.assign_all_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.assign_all_combo.blockSignals(False)
        self.assign_all_lbl.setVisible(has_folders)
        self.assign_all_combo.setVisible(has_folders)
        self.assign_all_btn.setVisible(has_folders)

        self.refresh_export_periods()

        folders = self.folder_store.list_folders()
        if not folders:
            self.folders_list_lbl.setText(self.tr_("folder_list_empty"))
            return
        counts: dict[str, int] = {}
        for fid in self.folder_store.assignments.values():
            counts[fid] = counts.get(fid, 0) + 1
        chips = [
            f'<span style="color:{html.escape(f["color"])};">&#9679;</span> '
            f'{html.escape(f["name"])} ({counts.get(f["id"], 0)})'
            for f in folders
        ]
        self.folders_list_lbl.setText("&nbsp;&nbsp;&nbsp;".join(chips))

    def _collect_export_periods(self) -> list[tuple[str, str, tuple]]:
        """(label, mode, period_key) for every Half-Year bucket across all
        tracked channels regardless of folder (newest first), then every
        Season bucket the same way — matches FolderStatView's own period
        picker (see its _collect_all_period_keys), just flattened into one
        combo here instead of mode buttons on their own page."""
        def keys_for(mode: str) -> list[tuple[str, str, tuple]]:
            keys: dict[tuple, str] = {}
            for summary in self.channel_store.list():
                data = self.channel_store.load(summary["key"])
                if not data:
                    continue
                for m in data.get("distributions", {}).get("monthly") or []:
                    if not int(m.get("count", 0) or 0):
                        continue
                    try:
                        year, month = (int(x) for x in m.get("label", "").split("-"))
                    except ValueError:
                        continue
                    key, label = period_key_label(year, month, mode)
                    keys[key] = label
            return [(label, mode, key) for key, label in
                    sorted(keys.items(), key=lambda kv: kv[0], reverse=True)]

        return keys_for("halfyear") + keys_for("season")

    def refresh_export_periods(self) -> None:
        current = self.folders_export_period_combo.currentData()
        self.folders_export_period_combo.blockSignals(True)
        self.folders_export_period_combo.clear()
        for label, mode, key in self._collect_export_periods():
            self.folders_export_period_combo.addItem(label, (mode, key))
        self.folders_export_period_combo.addItem(self.tr_("period_year_all"), ("all", None))
        # Not combo.findData(current) — PySide6 can't reliably match a
        # tuple-valued itemData (our (mode, period_key) pairs) through
        # QVariant equality, so it always misses and silently resets the
        # selection back to index 0. A plain Python == comparison over
        # itemData works correctly for tuples.
        idx = -1
        if current is not None:
            for i in range(self.folders_export_period_combo.count()):
                if self.folders_export_period_combo.itemData(i) == current:
                    idx = i
                    break
        self.folders_export_period_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.folders_export_period_combo.blockSignals(False)

    def _open_folder_manager(self) -> None:
        dlg = FolderManagerDialog(self.folder_store, self.i18n, self)
        dlg.exec()
        self.refresh_folders_list()
        self.folders_changed.emit()

    # ------------------------------------------------------- folders export
    def _build_folders_md(self) -> str:
        """One row per tracked channel, grouped/sorted exactly like the
        sidebar's "Sort Fols" toggle (see FolderStore.sorted_by_folder) —
        folder list order, unassigned channels last, followers descending
        within each group. Rating/Views/Viral share (see
        folders_export_extra_chk) reuse app.rating.score_entries so they
        come out numerically identical to what Folder Stats itself would
        show for the same folder/period — see _collect_export_metrics."""
        summaries = self.folder_store.sorted_by_folder(self.channel_store.list())
        folder_name = {f["id"]: f["name"] for f in self.folder_store.list_folders()}
        extra = self.folders_export_extra_chk.isChecked()

        headers = [self.tr_("folder_export_col_folder"), self.tr_("folder_export_col_followers"),
                  self.tr_("folder_export_col_id"), self.tr_("folder_export_col_tag")]
        if extra:
            headers += [self.tr_("col_rating"), self.tr_("col_views"),
                       self.tr_("col_viral_share"), self.tr_("col_post_quality")]
        lines = ["| " + " | ".join(headers) + " |",
                 "| " + " | ".join(["---"] * len(headers)) + " |"]

        metrics_by_key: dict[str, tuple] = {}
        if extra:
            mode, target_key = self.folders_export_period_combo.currentData() or ("all", None)
            metrics_by_key = self._collect_export_metrics(summaries, mode, target_key)

        for ch in summaries:
            fid = self.folder_store.folder_for_channel(ch["key"])
            folder = folder_name.get(fid, self.tr_("folder_none"))
            username = ch.get("username") or ""
            if username:
                ident = f"@{username}"
            else:
                # No public username to link to — a title snippet reads
                # better in the exported table than the bare checkpoint
                # key, with the id still there in parens for a lookup.
                # "|" would otherwise be read as a column separator and
                # break the row, so it's stripped rather than escaped.
                title = (ch.get("title") or ch["key"]).replace("|", "")
                ident = f"{title[:18]}({ch['key']})"
            tag = self.tag_store.tag_for_channel(ch["key"]) or ""
            row = [folder, fmt_int(ch.get("members", 0)), ident, tag]
            if extra:
                metrics = metrics_by_key.get(ch["key"])
                if metrics is None:
                    row += ["—", "—", "—", "—"]
                else:
                    score, views, viral_share, quality = metrics
                    row += [f"{score:.3f}", fmt_int(views), f"{viral_share:.1f}%",
                            str(round(quality))]
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines) + "\n"

    def _collect_export_metrics(self, summaries: list[dict], mode: str,
                                target_key: tuple | None) -> dict[str, tuple]:
        """channel key -> (score, views, viral_share_pct, quality), scored
        exactly like FolderStatView's Periodic Stats: entries are grouped by folder
        (channels with no folder form their own group) and normalized
        against only their own group's peers for the same period, via
        app.rating.score_entries — that's what makes a channel's Rating
        here match what Folder Stats itself would show, unlike a
        channel-global metric which couldn't reproduce that per-folder
        normalization at all."""
        groups: dict[str | None, list[str]] = {}
        for ch in summaries:
            fid = self.folder_store.folder_for_channel(ch["key"])
            groups.setdefault(fid, []).append(ch["key"])

        out: dict[str, tuple] = {}
        for keys in groups.values():
            entries = []
            for key in keys:
                data = self.channel_store.load(key) or {}
                totals = self._channel_bucket_totals(data, mode, target_key)
                if totals is None:
                    continue
                entries.append({"key": key, **totals})
            if not entries:
                continue
            score_entries(entries)
            for e in entries:
                out[e["key"]] = (e["score"], e["views"], e["viral_share"], e["quality"])
        return out

    @staticmethod
    def _channel_bucket_totals(data: dict, mode: str,
                               target_key: tuple | None) -> dict | None:
        """{"views","shares","reactions","viral_share","quality"} for one
        channel's full checkpoint, scoped to `mode` — "all" sums every
        scanned post ever (`distributions.monthly`); "halfyear"/"season"
        use just the one bucket matching `target_key`. None if the channel
        has no data in scope at all (empty monthly history for "all", or no
        bucket matching `target_key`), so the caller can tell "genuinely no
        data" apart from "scored zero".

        Quality is the same gauge score every post card/trend line in the
        app shows (app.scoring), averaged over whatever of the channel's
        stored top-N pool (`rows`) falls in scope — `rows` is a sample, not
        every scanned post, same caveat as Folder Stats' own Post Quality
        column (see FolderStatView._collect_periods)."""
        monthly = data.get("distributions", {}).get("monthly") or []
        avg_views = data.get("stats", {}).get("avg_views", 0) or 0

        def _quality(rows: list[dict]) -> float:
            if not rows:
                return 0
            return sum(post_gauge_value(post_score_raw(r, avg_views)) for r in rows) / len(rows)

        if mode == "all":
            if not monthly:
                return None
            count = sum(int(m.get("count", 0) or 0) for m in monthly)
            viral_count = sum(int(m.get("viral_count", 0) or 0) for m in monthly)
            return {
                "views": sum(int(m.get("views", 0) or 0) for m in monthly),
                "shares": sum(int(m.get("shares", 0) or 0) for m in monthly),
                "reactions": sum(int(m.get("reactions", 0) or 0) for m in monthly),
                "viral_share": viral_count / count * 100 if count else 0,
                "quality": _quality(data.get("rows", []) or []),
            }

        buckets: dict[tuple, dict] = {}
        for m in monthly:
            count = int(m.get("count", 0) or 0)
            if not count:
                continue
            try:
                year, month = (int(x) for x in m.get("label", "").split("-"))
            except ValueError:
                continue
            key, _label = period_key_label(year, month, mode)
            b = buckets.setdefault(key, {"count": 0, "views": 0, "shares": 0,
                                         "reactions": 0, "viral_count": 0})
            b["count"] += count
            b["views"] += int(m.get("views", 0) or 0)
            b["shares"] += int(m.get("shares", 0) or 0)
            b["reactions"] += int(m.get("reactions", 0) or 0)
            b["viral_count"] += int(m.get("viral_count", 0) or 0)
        if target_key not in buckets:
            return None
        b = buckets[target_key]

        period_rows = []
        for r in data.get("rows", []) or []:
            try:
                dt = datetime.fromisoformat((r.get("date") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if period_key_label(dt.year, dt.month, mode)[0] == target_key:
                period_rows.append(r)
        return {
            "views": b["views"], "shares": b["shares"], "reactions": b["reactions"],
            "viral_share": b["viral_count"] / b["count"] * 100 if b["count"] else 0,
            "quality": _quality(period_rows),
        }

    def _on_export_folders_md(self) -> None:
        if not self.channel_store.list():
            QMessageBox.information(self, self.tr_("app_title"), self.tr_("report_empty"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("folder_export_md_btn"), "folders.md", "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._build_folders_md())
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), friendly_os_error(exc))
            return
        QMessageBox.information(self, self.tr_("app_title"),
                                self.tr_("md_saved", path=path))

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.title_lbl.setText(self.tr_("nav_config"))
        self.sub_lbl.setText(self.tr_("app_title"))
        self.profile_lbl.setText(self.tr_("profile"))
        self.new_profile_btn.setText(self.tr_("new_profile"))
        self.del_profile_btn.setText(self.tr_("delete_profile"))
        for key, edit in self.edits.items():
            lbl = self.conn_form.labelForField(edit)
            if lbl:
                lbl.setText(self.tr_(f"field_{key}"))
        self.save_btn.setText(self.tr_("save"))
        self.qr_btn.setText(self.tr_("qr_login_button"))
        self.check_login_btn.setText(self.tr_("check_login_button"))
        self.loc_lbl.setText(self.tr_("config_location", path=str(self.cfg.path)))
        self.open_folder_btn.setText(self.tr_("open_config_folder"))

        self.fetch_card_ref.title_lbl.setText(self.tr_("fetch_title"))
        self.fetch_help_lbl.setText(self.tr_("fetch_help"))
        self.channel_lbl.setText(self.tr_("fetch_channel"))
        self.channel_edit.setPlaceholderText(self.tr_("fetch_channel_placeholder"))
        lbl = self.fetch_form.labelForField(self.period_combo)
        if lbl:
            lbl.setText(self.tr_("fetch_period"))
        for i, k in enumerate(PERIOD_KEYS):
            self.period_combo.setItemText(i, self.tr_(f"period_{k}"))
        self.public_check.setText(self.tr_("fetch_public"))
        self.fetch_btn.setText(self.tr_("fetch_button"))
        self.stop_btn.setText(self.tr_("stop"))

        self.instructions_box.setTitle(self.tr_("instructions_title"))
        self.instructions_info.setText(self.tr_("instructions_text"))

        self.folders_card_ref.title_lbl.setText(self.tr_("folder_section_title"))
        self.folders_help_lbl.setText(self.tr_("folder_manage_help"))
        self.folders_manage_btn.setText(self.tr_("folder_manage"))
        self.folders_export_md_btn.setText(self.tr_("folder_export_md_btn"))
        self.folders_export_md_btn.setToolTip(self.tr_("folder_export_md_hint"))
        self.folders_export_extra_chk.setText(self.tr_("folder_export_extra_cols"))
        self.folders_export_extra_chk.setToolTip(self.tr_("folder_export_extra_cols_hint"))
        self.folders_export_period_combo.setToolTip(self.tr_("folder_export_period_hint"))
        self.refresh_export_periods()
        self.comments_refresh_lbl.setText(self.tr_("folder_comments_refresh_label"))
        self.refresh_comments_btn.setText(self.tr_("folder_comments_refresh_btn"))
        self.refresh_comments_btn.setToolTip(self.tr_("folder_comments_refresh_hint"))
        self.assign_all_lbl.setText(self.tr_("folder_assign_all_label"))
        self.assign_all_btn.setText(self.tr_("folder_assign_all_btn"))
        self.assign_all_btn.setToolTip(self.tr_("folder_assign_all_hint"))
        self.refresh_folders_list()

        self.tags_card_ref.title_lbl.setText(self.tr_("tag_section_title"))
        self.tags_help_lbl.setText(self.tr_("tag_manage_help"))
        self.tags_load_btn.setText(self.tr_("tag_load_md_btn"))
        self.tags_load_btn.setToolTip(self.tr_("tag_load_md_hint"))
        self.refresh_tags_list()

    # ------------------------------------------------------ field helpers
    def _load_fields(self) -> None:
        for key, edit in self.edits.items():
            edit.setText(self.cfg.get(key))

    def _store_fields(self) -> None:
        for key, edit in self.edits.items():
            self.cfg.profile[key] = edit.text().strip()

    def _save(self) -> None:
        self._store_fields()
        self.cfg.save()
        self.status.setText(self.tr_("saved"))

    def _has_conn(self) -> bool:
        return bool(self.cfg.get("API_ID").strip() and self.cfg.get("API_HASH").strip()
                    and self.cfg.get("PHONE_NUMBER").strip())

    # ------------------------------------------------------------- login
    def _qr_login(self) -> None:
        self._store_fields()
        if not (self.cfg.get("API_ID") and self.cfg.get("API_HASH")):
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return
        QrLoginDialog(self.cfg, self.i18n, self).run_and_report()

    def _check_login(self) -> None:
        self._store_fields()
        if not (self.cfg.get("API_ID") and self.cfg.get("API_HASH")):
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return
        self.check_login_btn.setEnabled(False)
        self.status.setText(self.tr_("check_login_checking"))
        self._login_worker = CheckLoginWorker(
            self.cfg.get("API_ID"), self.cfg.get("API_HASH"),
            self.cfg.session_path(), parent=self)
        self._login_worker.sig_done.connect(self._on_check_login_done)
        self._login_worker.start()

    def _on_check_login_done(self, ok: bool, name: str, phone: str) -> None:
        self.check_login_btn.setEnabled(True)
        if ok:
            self.status.setText(self.tr_("check_login_ok", name=name, phone=phone))
        elif name:
            self.status.setText(self.tr_("done_fail", msg=name))
        else:
            self.status.setText(self.tr_("check_login_not_authorized"))

    # ---------------------------------------------------------- profiles
    def _switch_profile(self, name: str) -> None:
        if not name or name == self.cfg.current_profile:
            return
        self._store_fields()
        self.cfg.current_profile = name
        self.cfg.save()
        self._load_fields()
        self.channel_edit.setText(self.cfg.get("CHANNEL_ID"))

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, self.tr_("new_profile"),
                                        self.tr_("profile_name"))
        if not ok:
            return
        self._store_fields()
        if self.cfg.add_profile(name):
            self.cfg.save()
            self.profile_combo.blockSignals(True)
            self.profile_combo.addItem(name.strip())
            self.profile_combo.setCurrentText(name.strip())
            self.profile_combo.blockSignals(False)
            self._load_fields()

    def _delete_profile(self) -> None:
        name = self.profile_combo.currentText()
        if QMessageBox.question(self, self.tr_("delete_profile"),
                                self.tr_("delete_profile_confirm", name=name)) \
                != QMessageBox.StandardButton.Yes:
            return
        if self.cfg.delete_profile(name):
            self.cfg.save()
            self.profile_combo.blockSignals(True)
            self.profile_combo.removeItem(self.profile_combo.currentIndex())
            self.profile_combo.setCurrentText(self.cfg.current_profile)
            self.profile_combo.blockSignals(False)
            self._load_fields()

    # -------------------------------------------------------------- env io
    def import_env(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("import_env"), "",
                                              "env (*.env *.*)")
        if not path:
            return
        n = self.cfg.import_env(path)
        self._load_fields()
        self.cfg.save()
        self.status.setText(self.tr_("env_imported", n=n))

    def export_env(self) -> None:
        self._store_fields()
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("export_env"), ".env",
                                              "env (*.env *.*)")
        if not path:
            return
        self.cfg.export_env(path)
        self.status.setText(path)

    # --------------------------------------------------------------- fetch
    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _on_fetch_clicked(self) -> None:
        channel = self.channel_edit.text().strip()
        if not channel:
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("fetch_channel"))
            return
        params = {
            "channel": channel,
            "period": PERIOD_KEYS[self.period_combo.currentIndex()],
            "fetch_public": self.public_check.isChecked(),
        }
        self.fetch(params)

    def fetch(self, params: dict) -> None:
        """Start a channel scan. Reused by the dashboard's Re-fetch button."""
        self._store_fields()
        if not self._has_conn():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return
        # Reflect params into the form so the UI matches what's running.
        self.channel_edit.setText(params["channel"])
        if params.get("period") in PERIOD_KEYS:
            self.period_combo.setCurrentIndex(PERIOD_KEYS.index(params["period"]))
        self.public_check.setChecked(bool(params.get("fetch_public")))

        self.cfg.profile["CHANNEL_ID"] = params["channel"]
        self.cfg.save()

        self.log_view.clear()
        conn = {
            "api_id": self.cfg.get("API_ID").strip(),
            "api_hash": self.cfg.get("API_HASH").strip(),
            "phone": self.cfg.get("PHONE_NUMBER").strip(),
            "session": self.cfg.session_path(),
        }
        self.worker = ToolWorker(run_channel_stat, params, conn, parent=self)
        self.worker.sig_log.connect(self._append_log)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_ask.connect(self._on_ask)
        self.worker.sig_done.connect(self._on_fetch_done)

        self.fetch_btn.setEnabled(False)
        self.refresh_comments_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setRange(0, 0)
        self.worker.start()

    def _on_stop(self) -> None:
        if self.worker:
            self._append_log(self.tr_("cancelled"))
            self.worker.request_cancel()
            self.stop_btn.setEnabled(False)

    def _append_log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        else:
            self.progress.setRange(0, 0)

    def _on_ask(self, kind: str, _prompt: str) -> None:
        if kind == "password":
            prompt = self.tr_("login_password_prompt")
            echo = QLineEdit.EchoMode.Password
        else:
            prompt = self.tr_("login_code_prompt")
            echo = QLineEdit.EchoMode.Normal
        text, ok = QInputDialog.getText(self, self.tr_("login_title"), prompt, echo)
        if not self.worker:
            return
        if ok and text.strip():
            self.worker.provide_answer(text.strip())
        else:
            self.worker.request_cancel()

    def _on_fetch_done(self, ok: bool, msg: str) -> None:
        self.fetch_btn.setEnabled(True)
        self.refresh_comments_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        payload = None
        if ok:
            try:
                payload = json.loads(msg)
            except (ValueError, TypeError):
                ok = False
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if ok else 0)
        self.worker = None

        if ok and payload is not None:
            self._append_log(self.tr_("fetch_done",
                                      title=payload.get("title", ""),
                                      n=len(payload.get("rows", [])),
                                      scanned=payload.get("scanned", 0)))
            self.channel_edit.clear()
            self.channel_fetched.emit(payload)
        else:
            self._append_log(self.tr_("done_fail", msg=msg))

    # --------------------------------------------------- refresh comments
    def _on_assign_all_clicked(self) -> None:
        folder_id = self.assign_all_combo.currentData()
        if not folder_id:
            return
        keys = [ch["key"] for ch in self.channel_store.list()]
        if not keys:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("folder_assign_all_none"))
            return
        folder_name = self.assign_all_combo.currentText()
        reply = QMessageBox.question(
            self, self.tr_("app_title"),
            self.tr_("folder_assign_all_confirm", count=len(keys), folder=folder_name))
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.folder_store.assign_all(folder_id, keys)
        self.refresh_folders_list()
        self.folders_changed.emit()

    def _on_refresh_comments_clicked(self) -> None:
        folder_id = self.comments_folder_combo.currentData()
        if not folder_id:
            return
        keys = [k for k, fid in self.folder_store.assignments.items() if fid == folder_id]
        if not keys:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("folder_stat_empty_channels"))
            return
        self._store_fields()
        if not self._has_conn():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("missing_conn"))
            return
        if self.is_running():
            QMessageBox.warning(self, self.tr_("app_title"), self.tr_("worker_running"))
            return

        self.log_view.clear()
        conn = {
            "api_id": self.cfg.get("API_ID").strip(),
            "api_hash": self.cfg.get("API_HASH").strip(),
            "phone": self.cfg.get("PHONE_NUMBER").strip(),
            "session": self.cfg.session_path(),
        }
        self.worker = ToolWorker(run_comments_refresh, {"keys": keys}, conn, parent=self)
        self.worker.sig_log.connect(self._append_log)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_ask.connect(self._on_ask)
        self.worker.sig_done.connect(self._on_refresh_comments_done)

        self.fetch_btn.setEnabled(False)
        self.refresh_comments_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setRange(0, 0)
        self.worker.start()

    def _on_refresh_comments_done(self, ok: bool, msg: str) -> None:
        self.fetch_btn.setEnabled(True)
        self.refresh_comments_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if ok else 0)
        self.worker = None
        if ok:
            self.checkpoints_changed.emit()
        else:
            self._append_log(self.tr_("done_fail", msg=msg))
