"""Left sidebar: a Config entry, then one entry per fetched channel.

The channel entries are rebuilt from the checkpoint store whenever a fetch
completes or a channel is removed. Config and every channel share one
QButtonGroup so exactly one is highlighted at a time (like the
analytics_dashboard nav).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from .theme import COLORS
from .widgets import NavButton, hline


class SidePanel(QFrame):
    config_selected = Signal()
    channel_selected = Signal(str)   # checkpoint key

    def __init__(self, i18n, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setObjectName("sidebar")
        self.setFixedWidth(256)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 22, 16, 18)
        root.setSpacing(10)

        brand = QLabel()
        brand.setObjectName("brand")
        brand.setText(f"TG&nbsp;Channel<span style='color:{COLORS['accent']};'> Stat</span>")
        brand.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(brand)

        root.addSpacing(10)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

        self.config_btn = NavButton("settings", i18n.tr("nav_config"))
        self.config_btn.clicked.connect(lambda: self.config_selected.emit())
        self.group.addButton(self.config_btn)
        root.addWidget(self.config_btn)

        root.addSpacing(8)
        self.section_lbl = QLabel(i18n.tr("nav_channels"))
        self.section_lbl.setObjectName("sectionLabel")
        root.addWidget(self.section_lbl)
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
        """channels: [{key, title, ...}] — newest first (as store.list gives)."""
        for btn in self._channel_btns.values():
            self.group.removeButton(btn)
            btn.deleteLater()
        self._channel_btns.clear()

        # Everything before the trailing stretch gets cleared except empty_lbl.
        self.empty_lbl.setVisible(not channels)

        for ch in channels:
            btn = NavButton("reports", ch.get("title") or ch.get("key", "?"))
            btn.setToolTip(ch.get("channel") or ch.get("title", ""))
            key = ch["key"]
            btn.clicked.connect(lambda _=False, k=key: self.channel_selected.emit(k))
            self.group.addButton(btn)
            # insert above the stretch (last item)
            self.list_lay.insertWidget(self.list_lay.count() - 1, btn)
            self._channel_btns[key] = btn

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
        self.section_lbl.setText(self.i18n.tr("nav_channels"))
        self.empty_lbl.setText(self.i18n.tr("nav_no_channels"))
