"""Config screen: Telegram credentials + the "fetch a channel" card.

Combines tg-super-admin's Config tab (profiles, connection fields, QR / check
login, .env import-export) with a compact fetch panel that drives the
channel_stat tool in a background worker and emits the finished payload.
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..config import CONN_FIELDS, config_dir
from ..tools.channel_stat import run_channel_stat
from ..worker import CheckLoginWorker, ToolWorker
from .qr_login_dialog import QrLoginDialog
from .widgets import Card, SectionCard

PERIOD_KEYS = ["3m", "6m", "1y", "2y", "3y", "all"]


class ConfigView(QWidget):
    channel_fetched = Signal(dict)   # full channel_stat payload

    def __init__(self, cfg, i18n, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.i18n = i18n
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
        title = QLabel(self.tr_("nav_config"))
        title.setObjectName("pageTitle")
        header.addWidget(title)
        sub = QLabel(self.tr_("app_title"))
        sub.setObjectName("pageSub")
        header.addWidget(sub)
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
        root.addStretch()

    def _connection_card(self) -> Card:
        card = SectionCard("Telegram")

        prow = QHBoxLayout()
        prow.addWidget(QLabel(self.tr_("profile")))
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(sorted(self.cfg.profiles))
        self.profile_combo.setCurrentText(self.cfg.current_profile)
        self.profile_combo.currentTextChanged.connect(self._switch_profile)
        prow.addWidget(self.profile_combo, 1)
        new_btn = QPushButton(self.tr_("new_profile"))
        new_btn.clicked.connect(self._new_profile)
        prow.addWidget(new_btn)
        del_btn = QPushButton(self.tr_("delete_profile"))
        del_btn.clicked.connect(self._delete_profile)
        prow.addWidget(del_btn)
        card.body.addLayout(prow)

        form = QFormLayout()
        form.setSpacing(10)
        self.edits: dict[str, QLineEdit] = {}
        for key in CONN_FIELDS:
            edit = QLineEdit()
            if key == "API_HASH":
                edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
            self.edits[key] = edit
            form.addRow(self.tr_(f"field_{key}"), edit)
        card.body.addLayout(form)

        brow = QHBoxLayout()
        save_btn = QPushButton(self.tr_("save"))
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        brow.addWidget(save_btn)
        qr_btn = QPushButton(self.tr_("qr_login_button"))
        qr_btn.clicked.connect(self._qr_login)
        brow.addWidget(qr_btn)
        self.check_login_btn = QPushButton(self.tr_("check_login_button"))
        self.check_login_btn.clicked.connect(self._check_login)
        brow.addWidget(self.check_login_btn)
        brow.addStretch()
        card.body.addLayout(brow)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        card.body.addWidget(self.status)

        loc = QLabel(self.tr_("config_location", path=str(self.cfg.path)))
        loc.setObjectName("hint")
        loc.setWordWrap(True)
        card.body.addWidget(loc)
        return card

    def _fetch_card(self) -> Card:
        card = SectionCard(self.tr_("fetch_title"))

        help_lbl = QLabel(self.tr_("fetch_help"))
        help_lbl.setObjectName("hint")
        help_lbl.setWordWrap(True)
        card.body.addWidget(help_lbl)

        form = QFormLayout()
        form.setSpacing(10)
        self.channel_edit = QLineEdit(self.cfg.get("CHANNEL_ID"))
        self.channel_edit.setPlaceholderText(self.tr_("fetch_channel_placeholder"))
        form.addRow(self.tr_("fetch_channel"), self.channel_edit)

        self.top_spin = QSpinBox()
        self.top_spin.setRange(1, 1000)
        self.top_spin.setValue(20)
        form.addRow(self.tr_("fetch_top_n"), self.top_spin)

        self.period_combo = QComboBox()
        self.period_combo.addItems([self.tr_(f"period_{k}") for k in PERIOD_KEYS])
        self.period_combo.setCurrentIndex(2)  # 1 year
        form.addRow(self.tr_("fetch_period"), self.period_combo)
        card.body.addLayout(form)

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
        box.setCheckable(True)
        box.setChecked(False)
        lay = QVBoxLayout(box)
        info = QLabel(self.tr_("instructions_text"))
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setVisible(False)
        lay.addWidget(info)
        box.toggled.connect(info.setVisible)
        return box

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
            self.channel_fetched.emit(payload)
        else:
            self._append_log(self.tr_("done_fail", msg=msg))
