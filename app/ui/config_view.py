"""Config screen: Telegram credentials + the "fetch a channel" card.

Combines tg-super-admin's Config tab (profiles, connection fields, QR / check
login, .env import-export) with a compact fetch panel that drives the
channel_stat tool in a background worker and emits the finished payload.
"""
from __future__ import annotations

import html
import json

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..config import CONN_FIELDS, config_dir
from ..folders import FolderStore
from ..store import ChannelStore
from ..tools.channel_stat import run_channel_stat
from ..tools.comments_refresh import run_comments_refresh
from ..worker import CheckLoginWorker, ToolWorker
from .dashboard_view import fmt_int
from .folder_dialog import FolderManagerDialog
from .qr_login_dialog import QrLoginDialog
from .widgets import Card, SectionCard

PERIOD_KEYS = ["2y", "3y", "all"]


class ConfigView(QWidget):
    channel_fetched = Signal(dict)   # full channel_stat payload
    folders_changed = Signal()
    checkpoints_changed = Signal()   # a folder's checkpoints were updated in place

    def __init__(self, cfg, i18n, folder_store: FolderStore, channel_store: ChannelStore,
                parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.i18n = i18n
        self.folder_store = folder_store
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
        root.addWidget(self._folders_card())
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

        self.fetch_form = QFormLayout()
        self.fetch_form.setSpacing(10)
        self.channel_edit = QLineEdit(self.cfg.get("CHANNEL_ID"))
        self.channel_edit.setPlaceholderText(self.tr_("fetch_channel_placeholder"))
        self.fetch_form.addRow(self.tr_("fetch_channel"), self.channel_edit)

        self.top_spin = QSpinBox()
        self.top_spin.setRange(1, 1000)
        self.top_spin.setValue(20)
        self.fetch_form.addRow(self.tr_("fetch_top_n"), self.top_spin)

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
        self.folders_export_md_btn = QPushButton(self.tr_("folder_export_md_btn"))
        self.folders_export_md_btn.setToolTip(self.tr_("folder_export_md_hint"))
        self.folders_export_md_btn.clicked.connect(self._on_export_folders_md)
        row.addWidget(self.folders_export_md_btn)
        row.addStretch()
        card.body.addLayout(row)

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
        within each group."""
        summaries = self.folder_store.sorted_by_folder(self.channel_store.list())
        folder_name = {f["id"]: f["name"] for f in self.folder_store.list_folders()}
        lines = [
            "| " + " | ".join([self.tr_("folder_export_col_folder"),
                               self.tr_("folder_export_col_followers"),
                               self.tr_("folder_export_col_id")]) + " |",
            "| --- | --- | --- |",
        ]
        for ch in summaries:
            fid = self.folder_store.folder_for_channel(ch["key"])
            folder = folder_name.get(fid, self.tr_("folder_none"))
            username = ch.get("username") or ""
            ident = f"@{username}" if username else ch["key"]
            lines.append(f"| {folder} | {fmt_int(ch.get('members', 0))} | {ident} |")
        return "\n".join(lines) + "\n"

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
            QMessageBox.warning(self, self.tr_("app_title"), str(exc))
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
        lbl = self.fetch_form.labelForField(self.channel_edit)
        if lbl:
            lbl.setText(self.tr_("fetch_channel"))
        self.channel_edit.setPlaceholderText(self.tr_("fetch_channel_placeholder"))
        lbl = self.fetch_form.labelForField(self.top_spin)
        if lbl:
            lbl.setText(self.tr_("fetch_top_n"))
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
        self.comments_refresh_lbl.setText(self.tr_("folder_comments_refresh_label"))
        self.refresh_comments_btn.setText(self.tr_("folder_comments_refresh_btn"))
        self.refresh_comments_btn.setToolTip(self.tr_("folder_comments_refresh_hint"))
        self.assign_all_lbl.setText(self.tr_("folder_assign_all_label"))
        self.assign_all_btn.setText(self.tr_("folder_assign_all_btn"))
        self.assign_all_btn.setToolTip(self.tr_("folder_assign_all_hint"))
        self.refresh_folders_list()

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
            "top_n": self.top_spin.value(),
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
        self.top_spin.setValue(int(params.get("top_n") or 20))
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
