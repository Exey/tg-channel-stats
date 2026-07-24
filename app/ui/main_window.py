"""Main window: sidebar (Config + fetched channels) beside a stacked content
area (the Config screen and a reusable channel dashboard)."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget, QWidget,
)

from ..config import Config, config_dir
from ..i18n import I18n
from ..store import ChannelStore
from .config_view import ConfigView
from .dashboard_view import DashboardView
from .side_panel import SidePanel
from .theme import apply_theme


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config()
        self.i18n = I18n(self.cfg.language)
        self.store = ChannelStore()
        self.resize(1240, 860)
        self.setMinimumSize(1040, 720)
        self._current_key: str | None = None   # None => Config screen
        apply_theme(QApplication.instance(), self.cfg.theme)
        self._build_ui()
        try:
            # Live-follow the OS appearance while pref == "system".
            QGuiApplication.styleHints().colorSchemeChanged.connect(
                self._on_system_theme_changed)
        except Exception:
            pass

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        self.setWindowTitle(self.i18n.tr("app_title"))

        central = QWidget()
        central.setObjectName("root")
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.side = SidePanel(self.i18n)
        self.side.config_selected.connect(self._show_config)
        self.side.channel_selected.connect(self._show_channel)
        lay.addWidget(self.side)

        self.stack = QStackedWidget()
        self.config_view = ConfigView(self.cfg, self.i18n)
        self.config_view.channel_fetched.connect(self._on_channel_fetched)
        self.dashboard = DashboardView(self.i18n)
        self.dashboard.refetch_requested.connect(self._on_refetch)
        self.dashboard.remove_requested.connect(self._on_remove)
        self.stack.addWidget(self.config_view)   # index 0
        self.stack.addWidget(self.dashboard)     # index 1
        lay.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self._build_menu()
        self._refresh_sidebar()

        # Restore selection after a rebuild (language switch).
        if self._current_key and self.side.has_channel(self._current_key):
            self._show_channel(self._current_key)
        else:
            self._current_key = None
            self._show_config()

    def _build_menu(self) -> None:
        tr = self.i18n.tr
        self.menuBar().clear()

        file_menu = self.menuBar().addMenu(tr("menu_file"))
        imp = QAction(tr("import_env"), self)
        imp.triggered.connect(self.config_view.import_env)
        file_menu.addAction(imp)
        exp = QAction(tr("export_env"), self)
        exp.triggered.connect(self.config_view.export_env)
        file_menu.addAction(exp)
        file_menu.addSeparator()
        open_cfg = QAction(tr("open_config_folder"), self)
        open_cfg.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir()))))
        file_menu.addAction(open_cfg)
        file_menu.addSeparator()
        quit_act = QAction(tr("quit"), self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        lang_menu = self.menuBar().addMenu(tr("menu_language"))
        for code, label_key in (("en", "lang_en"), ("ru", "lang_ru")):
            act = QAction(tr(label_key), self)
            act.setCheckable(True)
            act.setChecked(self.i18n.lang == code)
            act.triggered.connect(lambda _=False, c=code: self._switch_language(c))
            lang_menu.addAction(act)

        theme_menu = self.menuBar().addMenu(tr("menu_theme"))
        for pref, label_key in (("system", "theme_system"), ("light", "theme_light"),
                                ("dark", "theme_dark")):
            act = QAction(tr(label_key), self)
            act.setCheckable(True)
            act.setChecked(self.cfg.theme == pref)
            act.triggered.connect(lambda _=False, p=pref: self._switch_theme(p))
            theme_menu.addAction(act)

    # ------------------------------------------------------------- sidebar
    def _refresh_sidebar(self) -> None:
        self.side.set_channels(self.store.list())

    # ------------------------------------------------------------- actions
    def _show_config(self) -> None:
        self._current_key = None
        self.side.select_config()
        self.stack.setCurrentWidget(self.config_view)

    def _show_channel(self, key: str) -> None:
        data = self.store.load(key)
        if not data:
            QMessageBox.warning(self, self.i18n.tr("app_title"),
                                self.i18n.tr("nav_no_channels"))
            self._refresh_sidebar()
            self._show_config()
            return
        self._current_key = key
        self.side.select_channel(key)
        self.dashboard.load(data)
        self.stack.setCurrentWidget(self.dashboard)

    def _on_channel_fetched(self, payload: dict) -> None:
        key = self.store.save(payload)
        self._refresh_sidebar()
        self._show_channel(key)

    def _on_refetch(self, params: dict) -> None:
        self._show_config()
        self.config_view.fetch(params)

    def _on_remove(self, key: str) -> None:
        self.store.delete(key)
        self._refresh_sidebar()
        nxt = self.side.first_channel_key()
        if nxt:
            self._show_channel(nxt)
        else:
            self._show_config()

    # ------------------------------------------------------------ language
    def _switch_language(self, code: str) -> None:
        if code == self.i18n.lang:
            self._build_menu()
            return
        if self.config_view.is_running():
            QMessageBox.warning(self, self.i18n.tr("app_title"),
                                self.i18n.tr("worker_running"))
            self._build_menu()
            return
        self.i18n.lang = code
        self.cfg.language = code
        self.cfg.save()
        self._build_ui()  # rebuild everything with the new language

    # --------------------------------------------------------------- theme
    def _switch_theme(self, pref: str) -> None:
        if pref == self.cfg.theme:
            self._build_menu()
            return
        if self.config_view.is_running():
            QMessageBox.warning(self, self.i18n.tr("app_title"),
                                self.i18n.tr("worker_running"))
            self._build_menu()
            return
        self.cfg.theme = pref
        self.cfg.save()
        apply_theme(QApplication.instance(), pref)
        self._build_ui()  # rebuild everything with the new palette

    def _on_system_theme_changed(self, *_args) -> None:
        """OS appearance flipped while pref == 'system' — follow it live."""
        if self.cfg.theme != "system" or self.config_view.is_running():
            return
        apply_theme(QApplication.instance(), "system")
        self._build_ui()

    # --------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self.config_view.is_running():
            if QMessageBox.question(self, self.i18n.tr("app_title"),
                                    self.i18n.tr("worker_running")) \
                    != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self.config_view.worker:
                self.config_view.worker.request_cancel()
        event.accept()
