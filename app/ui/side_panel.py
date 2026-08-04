"""Left sidebar: a Config entry, then one entry per fetched channel.

The channel entries are rebuilt from the checkpoint store whenever a fetch
completes or a channel is removed. Config and every channel share one
QButtonGroup so exactly one is highlighted at a time (like the
analytics_dashboard nav).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..folders import FolderStore
from ..version import __version__
from .compare_view import MAX_COMPARE
from .dashboard_view import short_num
from .folder_dialog import FolderManagerDialog
from .theme import COLORS
from .widgets import NavButton, folder_icon, hline


class SidePanel(QFrame):
    config_selected = Signal()
    folder_stat_selected = Signal()
    content_quality_selected = Signal()
    channel_selected = Signal(str)   # checkpoint key
    compare_requested = Signal(list)  # 2-8 checkpoint keys
    compare_mode_off = Signal()
    compare_charts_selected = Signal(list)  # 0-8 checkpoint keys, live-updates
    compare_charts_mode_off = Signal()
    fold_requested = Signal()
    language_toggle_requested = Signal()
    folders_changed = Signal()

    def __init__(self, i18n, folder_store: FolderStore, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setObjectName("sidebar")
        self.setFixedWidth(256)
        self.compare_mode = False
        self.compare_charts_mode = False
        # Shared by both multi-select modes on purpose: switching from
        # Compare to Compare Charts (or back) is meant to carry the current
        # channel selection over, not reset it — see _toggle_compare_mode/
        # _toggle_compare_charts_mode.
        self._selected_keys: list[str] = []
        self._switching_modes = False
        self.folder_store = folder_store
        self.sort_by_folder = False
        self._last_channels: list[dict] = []

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

        version_lbl = QLabel(f"v{__version__}")
        version_lbl.setStyleSheet(f"color: {COLORS['faint']}; font-size: 11px;")
        root.addWidget(version_lbl)

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

        # icon_name=None on these three: the label carries its own emoji, so
        # a second SVG icon column would be redundant — left-aligned via
        # #navBtn's QSS the same as every other nav entry.
        self.folder_stat_btn = NavButton(None, i18n.tr("nav_folder_stat"))
        self.folder_stat_btn.clicked.connect(lambda: self.folder_stat_selected.emit())
        self.group.addButton(self.folder_stat_btn)
        root.addWidget(self.folder_stat_btn)

        self.content_quality_btn = NavButton(None, i18n.tr("nav_content_quality"))
        self.content_quality_btn.clicked.connect(lambda: self.content_quality_selected.emit())
        self.group.addButton(self.content_quality_btn)
        root.addWidget(self.content_quality_btn)

        # Not part of `self.group`: like Compare below, it repurposes channel
        # clicks into a multi-select instead of single-page navigation, so it
        # can't be an exclusive-group "current page" entry the way Config and
        # Folder Stats are.
        self.compare_charts_btn = QPushButton(i18n.tr("nav_compare_charts"))
        self.compare_charts_btn.setObjectName("ghost")
        self.compare_charts_btn.setCheckable(True)
        self.compare_charts_btn.setToolTip(i18n.tr("nav_compare_charts_hint"))
        self.compare_charts_btn.toggled.connect(self._toggle_compare_charts_mode)
        root.addWidget(self.compare_charts_btn)

        # Own full-width row directly below Compare Charts (not squeezed
        # into a shared row with Sort Fols) so "⭐ Compare Metrics" has
        # room to render without clipping.
        self.compare_btn = QPushButton(i18n.tr("nav_compare"))
        self.compare_btn.setObjectName("ghost")
        self.compare_btn.setCheckable(True)
        self.compare_btn.setToolTip(i18n.tr("nav_compare_hint"))
        self.compare_btn.toggled.connect(self._toggle_compare_mode)
        root.addWidget(self.compare_btn)

        root.addSpacing(8)
        # Toggle: grouped by folder (folder list order, unassigned last),
        # sorted by followers within each group — instead of the default
        # flat "everyone sorted by followers" list. See
        # FolderStore.sorted_by_folder for the actual ordering.
        self.sort_folders_btn = QPushButton(i18n.tr("nav_sort_folders"))
        self.sort_folders_btn.setObjectName("ghost")
        self.sort_folders_btn.setCheckable(True)
        self.sort_folders_btn.setToolTip(i18n.tr("nav_sort_folders_hint"))
        self.sort_folders_btn.toggled.connect(self._on_sort_folders_toggled)
        root.addWidget(self.sort_folders_btn)
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
        """channels: [{key, title, members, ...}] — sorted here by members
        desc, or grouped by folder (see FolderStore.sorted_by_folder) when
        the "Sort Fols" toggle is on. Cached in `_last_channels` so the
        toggle can re-sort and rebuild without needing fresh data from the
        caller — see _on_sort_folders_toggled."""
        self._last_channels = list(channels)
        for btn in self._channel_btns.values():
            self.group.removeButton(btn)
            btn.deleteLater()
        self._channel_btns.clear()

        # Everything before the trailing stretch gets cleared except empty_lbl.
        self.empty_lbl.setVisible(not channels)

        if self.sort_by_folder:
            channels = self.folder_store.sorted_by_folder(channels)
        else:
            channels = sorted(channels, key=lambda c: c.get("members", 0) or 0, reverse=True)

        # Drop folder assignments for channels that no longer exist (removed
        # from the sidebar) so folders.json doesn't accumulate dead keys.
        live_keys = {ch["key"] for ch in channels}
        stale = [k for k in self.folder_store.assignments if k not in live_keys]
        for k in stale:
            self.folder_store.set_channel_folder(k, None)

        self._selected_keys.clear()
        for ch in channels:
            username = ch.get("username") or ""
            label = f"@{username}" if username else (ch.get("title") or ch.get("key", "?"))
            btn = NavButton("reports", label)
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
        self.group.setExclusive(not (self.compare_mode or self.compare_charts_mode))
        self.refresh_folder_dots()

    def _on_sort_folders_toggled(self, on: bool) -> None:
        self.sort_by_folder = on
        self.set_channels(self._last_channels)

    # -------------------------------------------------------------- folders
    def refresh_folder_dots(self) -> None:
        for key, btn in self._channel_btns.items():
            folder_id = self.folder_store.folder_for_channel(key)
            folder = self.folder_store.get_folder(folder_id) if folder_id else None
            btn.set_folder_color(folder["color"] if folder else None,
                                 folder["name"] if folder else None)

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
                act = menu.addAction(folder_icon(folder["color"]), folder["name"])
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
        self.refresh_folder_dots()
        self.folders_changed.emit()

    def _open_folder_manager(self) -> None:
        dlg = FolderManagerDialog(self.folder_store, self.i18n, self)
        dlg.exec()
        self.refresh_folder_dots()
        self.folders_changed.emit()

    # ------------------------------------------------------------- compare
    def _sync_checked_buttons(self, keys: list[str]) -> None:
        key_set = set(keys)
        for key, btn in self._channel_btns.items():
            btn.setChecked(key in key_set)

    def _toggle_compare_mode(self, on: bool) -> None:
        self.compare_mode = on
        if on and self.compare_charts_mode:
            # Mutually exclusive multi-select modes, but switching between
            # them is meant to carry `_selected_keys` over — the guard stops
            # the nested _toggle_compare_charts_mode(False) call this
            # triggers from clearing the selection or emitting "mode off"
            # (which would navigate away, e.g. back to Config) on its way
            # out, since we're headed straight into Compare instead.
            self._switching_modes = True
            self.compare_charts_btn.setChecked(False)
            self._switching_modes = False
        self.group.setExclusive(not (self.compare_mode or self.compare_charts_mode))
        if on:
            self.config_btn.setChecked(False)
            self._sync_checked_buttons(self._selected_keys)
            if len(self._selected_keys) >= 2:
                self.compare_requested.emit(list(self._selected_keys))
        elif not self._switching_modes:
            self._selected_keys.clear()
            self._sync_checked_buttons(self._selected_keys)
            self.compare_mode_off.emit()

    def _toggle_compare_charts_mode(self, on: bool) -> None:
        self.compare_charts_mode = on
        if on and self.compare_mode:
            self._switching_modes = True
            self.compare_btn.setChecked(False)  # mutually exclusive — see _toggle_compare_mode
            self._switching_modes = False
        self.group.setExclusive(not (self.compare_mode or self.compare_charts_mode))
        if on:
            self.config_btn.setChecked(False)
            self.folder_stat_btn.setChecked(False)
            self.content_quality_btn.setChecked(False)
            self._sync_checked_buttons(self._selected_keys)
            self.compare_charts_selected.emit(list(self._selected_keys))
        elif not self._switching_modes:
            self._selected_keys.clear()
            self._sync_checked_buttons(self._selected_keys)
            self.compare_charts_mode_off.emit()

    def _on_channel_clicked(self, key: str) -> None:
        if self.compare_charts_mode:
            btn = self._channel_btns[key]
            if btn.isChecked():
                if len(self._selected_keys) >= MAX_COMPARE:
                    stale = self._selected_keys.pop(0)
                    self._channel_btns[stale].setChecked(False)
                self._selected_keys.append(key)
            elif key in self._selected_keys:
                self._selected_keys.remove(key)
            self.compare_charts_selected.emit(list(self._selected_keys))
            return
        if not self.compare_mode:
            self.channel_selected.emit(key)
            return
        btn = self._channel_btns[key]
        if btn.isChecked():
            if len(self._selected_keys) >= MAX_COMPARE:
                stale = self._selected_keys.pop(0)
                self._channel_btns[stale].setChecked(False)
            self._selected_keys.append(key)
        elif key in self._selected_keys:
            self._selected_keys.remove(key)
        if len(self._selected_keys) >= 2:
            self.compare_requested.emit(list(self._selected_keys))

    # ------------------------------------------------------------- select
    def select_config(self) -> None:
        self.config_btn.setChecked(True)

    def select_folder_stat(self) -> None:
        self.folder_stat_btn.setChecked(True)

    def select_content_quality(self) -> None:
        self.content_quality_btn.setChecked(True)

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
        self.folder_stat_btn.set_text(self.i18n.tr("nav_folder_stat"))
        self.content_quality_btn.set_text(self.i18n.tr("nav_content_quality"))
        self.empty_lbl.setText(self.i18n.tr("nav_no_channels"))
        self.compare_btn.setText(self.i18n.tr("nav_compare"))
        self.compare_btn.setToolTip(self.i18n.tr("nav_compare_hint"))
        self.sort_folders_btn.setText(self.i18n.tr("nav_sort_folders"))
        self.sort_folders_btn.setToolTip(self.i18n.tr("nav_sort_folders_hint"))
        self.compare_charts_btn.setText(self.i18n.tr("nav_compare_charts"))
        self.compare_charts_btn.setToolTip(self.i18n.tr("nav_compare_charts_hint"))
        self.fold_btn.setToolTip(self.i18n.tr("nav_fold_hint"))
        self.lang_btn.setText(self.i18n.lang.upper())
        self.lang_btn.setToolTip(self.i18n.tr("nav_lang_hint"))
