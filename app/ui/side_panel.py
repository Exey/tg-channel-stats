"""Left sidebar: a Config entry, then one entry per fetched channel.

The channel entries are rebuilt from the checkpoint store whenever a fetch
completes or a channel is removed. Config and every channel share one
QButtonGroup so exactly one is highlighted at a time (like the
analytics_dashboard nav).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..folders import FolderStore
from .compare_view import MAX_COMPARE
from .dashboard_view import short_num
from .folder_dialog import FolderManagerDialog
from .theme import COLORS
from .widgets import NavButton, hline


def _folder_icon(color: str) -> QIcon:
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, 12, 12)
    painter.end()
    return QIcon(pixmap)


class SidePanel(QFrame):
    config_selected = Signal()
    channel_selected = Signal(str)   # checkpoint key
    compare_requested = Signal(list)  # 2-8 checkpoint keys
    compare_mode_off = Signal()
    compare_md_requested = Signal()
    fold_requested = Signal()
    language_toggle_requested = Signal()

    def __init__(self, i18n, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setObjectName("sidebar")
        self.setFixedWidth(256)
        self.compare_mode = False
        self._compare_keys: list[str] = []
        self.folder_store = FolderStore()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 22, 16, 18)
        root.setSpacing(10)

        brand_row = QHBoxLayout()
        brand = QLabel()
        brand.setObjectName("brand")
        brand.setText(f"TG&nbsp;Channel<span style='color:{COLORS['accent']};'> Stats</span>")
        brand.setTextFormat(Qt.TextFormat.RichText)
        brand_row.addWidget(brand, 1)
        self.fold_btn = QPushButton("◀")
        self.fold_btn.setObjectName("ghost")
        self.fold_btn.setMinimumWidth(28)
        self.fold_btn.setToolTip(i18n.tr("nav_fold_hint"))
        self.fold_btn.clicked.connect(lambda: self.fold_requested.emit())
        brand_row.addWidget(self.fold_btn)
        root.addLayout(brand_row)

        root.addSpacing(10)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

        config_row = QHBoxLayout()
        self.config_btn = NavButton("settings", i18n.tr("nav_config"))
        self.config_btn.clicked.connect(lambda: self.config_selected.emit())
        self.group.addButton(self.config_btn)
        config_row.addWidget(self.config_btn, 1)
        self.lang_btn = QPushButton(i18n.lang.upper())
        self.lang_btn.setObjectName("ghost")
        self.lang_btn.setMinimumWidth(36)
        self.lang_btn.setToolTip(i18n.tr("nav_lang_hint"))
        self.lang_btn.clicked.connect(lambda: self.language_toggle_requested.emit())
        config_row.addWidget(self.lang_btn)
        root.addLayout(config_row)

        root.addSpacing(8)
        section_row = QHBoxLayout()
        self.compare_btn = QPushButton(i18n.tr("nav_compare"))
        self.compare_btn.setObjectName("ghost")
        self.compare_btn.setCheckable(True)
        self.compare_btn.setToolTip(i18n.tr("nav_compare_hint"))
        self.compare_btn.toggled.connect(self._toggle_compare_mode)
        section_row.addWidget(self.compare_btn, 1)
        self.compare_md_btn = QPushButton(i18n.tr("nav_compare_md"))
        self.compare_md_btn.setObjectName("ghost")
        self.compare_md_btn.setToolTip(i18n.tr("nav_compare_md_hint"))
        self.compare_md_btn.clicked.connect(lambda: self.compare_md_requested.emit())
        section_row.addWidget(self.compare_md_btn, 1)
        root.addLayout(section_row)
        root.addWidget(hline())

        # Scrollable channel list.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.list_lay = QVBoxLayout(holder)
        self.list_lay.setContentsMargins(0, 6, 0, 6)
        self.list_lay.setSpacing(4)
        self.empty_lbl = QLabel(i18n.tr("nav_no_channels"))
        self.empty_lbl.setObjectName("navEmpty")
        self.empty_lbl.setWordWrap(True)
        self.list_lay.addWidget(self.empty_lbl)
        self.list_lay.addStretch()
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)

        self._channel_btns: dict[str, NavButton] = {}

    # ------------------------------------------------------------ rebuild
    def set_channels(self, channels: list[dict]) -> None:
        """channels: [{key, title, members, ...}] — sorted here by members desc."""
        for btn in self._channel_btns.values():
            self.group.removeButton(btn)
            btn.deleteLater()
        self._channel_btns.clear()

        # Everything before the trailing stretch gets cleared except empty_lbl.
        self.empty_lbl.setVisible(not channels)

        channels = sorted(channels, key=lambda c: c.get("members", 0) or 0, reverse=True)

        # Drop folder assignments for channels that no longer exist (removed
        # from the sidebar) so folders.json doesn't accumulate dead keys.
        live_keys = {ch["key"] for ch in channels}
        stale = [k for k in self.folder_store.assignments if k not in live_keys]
        for k in stale:
            self.folder_store.set_channel_folder(k, None)

        self._compare_keys.clear()
        for ch in channels:
            btn = NavButton("reports", ch.get("title") or ch.get("key", "?"))
            btn.setToolTip(ch.get("channel") or ch.get("title", ""))
            members = ch.get("members", 0) or 0
            btn.set_meta(short_num(members, 1) if members else "")
            key = ch["key"]
            btn.clicked.connect(lambda _=False, k=key: self._on_channel_clicked(k))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, k=key, b=btn: self._show_channel_menu(k, b, pos))
            self.group.addButton(btn)
            # insert above the stretch (last item)
            self.list_lay.insertWidget(self.list_lay.count() - 1, btn)
            self._channel_btns[key] = btn
        self.group.setExclusive(not self.compare_mode)
        self._refresh_folder_dots()

    # -------------------------------------------------------------- folders
    def _refresh_folder_dots(self) -> None:
        for key, btn in self._channel_btns.items():
            folder_id = self.folder_store.folder_for_channel(key)
            folder = self.folder_store.get_folder(folder_id) if folder_id else None
            btn.set_folder_color(folder["color"] if folder else None)

    def _show_channel_menu(self, key: str, btn: NavButton, pos) -> None:
        menu = QMenu(self)
        current = self.folder_store.folder_for_channel(key)

        none_act = menu.addAction(self.i18n.tr("folder_none"))
        none_act.setCheckable(True)
        none_act.setChecked(current is None)
        none_act.triggered.connect(lambda: self._assign_folder(key, None))

        folders = self.folder_store.list_folders()
        if folders:
            menu.addSeparator()
            for folder in folders:
                act = menu.addAction(_folder_icon(folder["color"]), folder["name"])
                act.setCheckable(True)
                act.setChecked(folder["id"] == current)
                act.triggered.connect(
                    lambda _=False, fid=folder["id"]: self._assign_folder(key, fid))

        menu.addSeparator()
        manage_act = menu.addAction(self.i18n.tr("folder_manage"))
        manage_act.triggered.connect(self._open_folder_manager)

        menu.exec(btn.mapToGlobal(pos))

    def _assign_folder(self, key: str, folder_id: str | None) -> None:
        self.folder_store.set_channel_folder(key, folder_id)
        self._refresh_folder_dots()

    def _open_folder_manager(self) -> None:
        dlg = FolderManagerDialog(self.folder_store, self.i18n, self)
        dlg.exec()
        self._refresh_folder_dots()

    # ------------------------------------------------------------- compare
    def _toggle_compare_mode(self, on: bool) -> None:
        self.compare_mode = on
        self._compare_keys.clear()
        self.group.setExclusive(not on)
        for btn in self._channel_btns.values():
            btn.setChecked(False)
        if on:
            self.config_btn.setChecked(False)
        else:
            self.compare_mode_off.emit()

    def _on_channel_clicked(self, key: str) -> None:
        if not self.compare_mode:
            self.channel_selected.emit(key)
            return
        btn = self._channel_btns[key]
        if btn.isChecked():
            if len(self._compare_keys) >= MAX_COMPARE:
                stale = self._compare_keys.pop(0)
                self._channel_btns[stale].setChecked(False)
            self._compare_keys.append(key)
        elif key in self._compare_keys:
            self._compare_keys.remove(key)
        if len(self._compare_keys) >= 2:
            self.compare_requested.emit(list(self._compare_keys))

    # ------------------------------------------------------------- select
    def select_config(self) -> None:
        self.config_btn.setChecked(True)

    def select_channel(self, key: str) -> None:
        btn = self._channel_btns.get(key)
        if btn:
            btn.setChecked(True)

    def has_channel(self, key: str) -> bool:
        return key in self._channel_btns

    def first_channel_key(self) -> str | None:
        return next(iter(self._channel_btns), None)

    def retranslate(self) -> None:
        self.config_btn.set_text(self.i18n.tr("nav_config"))
        self.empty_lbl.setText(self.i18n.tr("nav_no_channels"))
        self.compare_btn.setText(self.i18n.tr("nav_compare"))
        self.compare_btn.setToolTip(self.i18n.tr("nav_compare_hint"))
        self.compare_md_btn.setText(self.i18n.tr("nav_compare_md"))
        self.compare_md_btn.setToolTip(self.i18n.tr("nav_compare_md_hint"))
        self.fold_btn.setToolTip(self.i18n.tr("nav_fold_hint"))
        self.lang_btn.setText(self.i18n.lang.upper())
        self.lang_btn.setToolTip(self.i18n.tr("nav_lang_hint"))
