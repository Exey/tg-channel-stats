"""Reusable card widgets and the sidebar nav button.

Everything here is a thin QFrame/QPushButton styled by theme.build_qss() plus
a soft drop shadow, so the dashboard reads as one system the way the
analytics_dashboard cards do.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .charts import Sparkline
from .theme import COLORS, add_shadow, svg_pixmap


class Card(QFrame):
    """White rounded panel with a soft shadow."""

    def __init__(self, parent=None, shadow: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        if shadow:
            add_shadow(self)


class StatCard(Card):
    """Small KPI tile: title, big value, optional sub-line and sparkline."""

    def __init__(self, title: str, value: str = "—", sub: str = "",
                 spark=None, accent: str | None = None, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(6)

        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("cardTitle")
        lay.addWidget(self.title_lbl)

        self.value_lbl = QLabel(value)
        self.value_lbl.setObjectName("statValue")
        lay.addWidget(self.value_lbl)

        self.sub_lbl = QLabel(sub)
        self.sub_lbl.setObjectName("statSub")
        self.sub_lbl.setVisible(bool(sub))
        lay.addWidget(self.sub_lbl)

        self.spark = Sparkline(spark or [], accent=accent or COLORS["accent"])
        self.spark.setVisible(bool(spark))
        lay.addWidget(self.spark)
        lay.addStretch()
        self.setMinimumHeight(132)

        self._compact = False
        self._winner = False
        self._lowest = False

    def set_value(self, value: str, sub: str = "", spark=None) -> None:
        self.value_lbl.setText(value)
        self.sub_lbl.setText(sub)
        self.sub_lbl.setVisible(bool(sub))
        if spark is not None:
            self.spark.set_data(spark)
            self.spark.setVisible(bool(spark))

    def set_compact(self, on: bool = True) -> None:
        """Tighter padding + smaller type — used by compare mode, where each
        card is much shorter than the dashboard's full-size tiles."""
        self._compact = on
        if on:
            self.layout().setContentsMargins(14, 6, 14, 6)
            self.layout().setSpacing(2)
        else:
            self.layout().setContentsMargins(18, 16, 18, 16)
            self.layout().setSpacing(6)
        self._apply_label_style()

    def _apply_label_style(self) -> None:
        # title_lbl/value_lbl each need at most one local stylesheet, so
        # compact sizing and the lowest-value text color are combined here
        # rather than in set_compact()/set_lowest() directly (setStyleSheet
        # replaces the whole local sheet, it doesn't merge across calls).
        title = "font-size: 11px;" if self._compact else ""
        value = "font-size: 18px;" if self._compact else ""
        if self._lowest:
            title += " color: #D9B8DE;"
            value += " color: #FFFFFF;"
        self.title_lbl.setStyleSheet(title)
        self.value_lbl.setStyleSheet(value)

    def set_highlighted(self, on: bool) -> None:
        """Gold border — used by compare mode to mark the winning metric."""
        self._winner = on
        self._apply_card_style()

    def set_lowest(self, on: bool) -> None:
        """Dark fill — used by compare mode to mark the worst metric."""
        self._lowest = on
        self._apply_label_style()
        self._apply_card_style()

    def _apply_card_style(self) -> None:
        rules = []
        if self._lowest:
            rules.append("background: #241D34;")
        if self._winner:
            rules.append(f"border: 2px solid {COLORS['win']};")
        self.setStyleSheet(f"QFrame#card {{ {' '.join(rules)} }}" if rules else "")


class ChartCard(Card):
    """A titled card that hosts a chart (or any content widget)."""

    def __init__(self, title: str, content: QWidget, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("sectionTitle")
        lay.addWidget(self.title_lbl)
        lay.addWidget(content, 1)
        self.content = content

    def set_title(self, title: str) -> None:
        self.title_lbl.setText(title)


class SectionCard(Card):
    """A titled card with a vertical body layout callers fill in."""

    def __init__(self, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(12)
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("sectionTitle")
        self.title_lbl.setVisible(bool(title))
        self.body.addWidget(self.title_lbl)


class NavButton(QPushButton):
    """Sidebar entry: recolorable SVG icon + label, checkable & exclusive."""

    def __init__(self, icon_name: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("navBtn")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_name = icon_name

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(12)
        self._icon = QLabel()
        self._icon.setFixedSize(22, 22)
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self._icon)
        self._label = QLabel(text)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self._label, 1)

        self._meta = QLabel()
        self._meta.setObjectName("navMeta")
        self._meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self._meta)

        self.setMinimumHeight(46)
        self.toggled.connect(self._sync_icon)
        self._sync_icon(False)

    def _sync_icon(self, checked: bool) -> None:
        color = COLORS["accent"] if checked else COLORS["muted"]
        self._icon.setPixmap(svg_pixmap(self._icon_name, color, 20))

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def set_meta(self, text: str) -> None:
        self._meta.setText(text)


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{COLORS['line']}; border:none;")
    return line
