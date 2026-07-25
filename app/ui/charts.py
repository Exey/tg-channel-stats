"""Native, QPainter-drawn charts — no matplotlib, no QtCharts.

Two widgets cover the whole dashboard:

* BarChart   — the dashboard-style bar chart: a faint full-height "track"
               behind every column, a rounded gradient value bar on top,
               light y-gridlines with labels, and (thinned) x labels.
* Sparkline  — a small trend line with a soft gradient fill, for stat cards.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from .theme import COLORS


def _nice_ceiling(value: float) -> int:
    """Round an axis maximum up to a friendly 1/2/5 × 10ⁿ number."""
    if value <= 0:
        return 1
    import math
    exp = math.floor(math.log10(value))
    base = 10 ** exp
    for mult in (1, 2, 2.5, 5, 10):
        if value <= mult * base:
            return int(mult * base) if mult * base >= 1 else 1
    return int(10 * base)


class BarChart(QWidget):
    PAD_L, PAD_R, PAD_T, PAD_B = 44, 8, 14, 26

    def __init__(self, values=None, labels=None, accent: str | None = None,
                 max_labels: int = 14, value_fmt=str, tooltips=None,
                 parent=None) -> None:
        super().__init__(parent)
        self._values = list(values or [])
        self._labels = list(labels or [])
        self._tooltips = list(tooltips or [])
        self._accent = accent or COLORS["accent"]
        self._max_labels = max_labels
        self._value_fmt = value_fmt
        self._empty_text = "No data"
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)

    def set_data(self, values, labels=None, accent: str | None = None,
                 empty_text: str | None = None, tooltips=None) -> None:
        self._values = list(values or [])
        if labels is not None:
            self._labels = list(labels)
        if tooltips is not None:
            self._tooltips = list(tooltips)
        elif labels is not None:
            self._tooltips = []
        if accent:
            self._accent = accent
        if empty_text is not None:
            self._empty_text = empty_text
        self.update()

    def _plot_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        return QRectF(self.PAD_L, self.PAD_T,
                      w - self.PAD_L - self.PAD_R, h - self.PAD_T - self.PAD_B)

    # ------------------------------------------------------------- paint
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        pad_l = self.PAD_L
        plot = self._plot_rect()

        if not self._values or max(self._values) <= 0:
            p.setPen(QColor(COLORS["faint"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return

        top = _nice_ceiling(max(self._values))

        # y gridlines + labels
        p.setFont(QFont(self.font().family(), 9))
        grid_pen = QPen(QColor(COLORS["line"]))
        grid_pen.setWidthF(1.0)
        steps = 4
        for i in range(steps + 1):
            frac = i / steps
            y = plot.bottom() - frac * plot.height()
            p.setPen(grid_pen)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(COLORS["faint"]))
            p.drawText(QRectF(0, y - 8, pad_l - 8, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       self._short_num(round(top * frac)))

        n = len(self._values)
        slot = plot.width() / n
        bar_w = min(34.0, slot * 0.6)
        radius = min(7.0, bar_w / 2)
        max_val = max(self._values)
        label_every = max(1, round(n / self._max_labels))

        for i, val in enumerate(self._values):
            cx = plot.left() + slot * (i + 0.5)
            x = cx - bar_w / 2
            # track
            track = QRectF(x, plot.top(), bar_w, plot.height())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLORS["accent_track"]))
            p.drawRoundedRect(track, radius, radius)
            # value bar
            bh = plot.height() * (val / top) if top else 0
            if bh > 0:
                bar = QRectF(x, plot.bottom() - bh, bar_w, bh)
                grad = QLinearGradient(bar.topLeft(), bar.bottomLeft())
                hot = val == max_val
                bar_gradient = (COLORS["bar_from"], COLORS["bar_to"])
                c0, c1 = (bar_gradient if not hot else (self._accent, self._accent))
                grad.setColorAt(0, QColor(self._accent if hot else c0))
                grad.setColorAt(1, QColor(c1))
                p.setBrush(QBrush(grad))
                p.drawRoundedRect(bar, radius, radius)

            if i % label_every == 0 and i < len(self._labels):
                p.setPen(QColor(COLORS["faint"]))
                p.setFont(QFont(self.font().family(), 9))
                p.drawText(QRectF(cx - slot / 2, plot.bottom() + 4, slot, 18),
                           Qt.AlignmentFlag.AlignCenter, str(self._labels[i]))
        p.end()

    # ---------------------------------------------------------- tooltip
    def mouseMoveEvent(self, event) -> None:
        idx = self._bar_at(event.position() if hasattr(event, "position")
                           else event.pos())
        if idx is None:
            QToolTip.hideText()
        else:
            pos = (event.globalPosition().toPoint() if hasattr(event, "globalPosition")
                   else event.globalPos())
            QToolTip.showText(pos, self._tooltip_text(idx), self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)

    def _bar_at(self, pos) -> int | None:
        if not self._values:
            return None
        plot = self._plot_rect()
        x, y = pos.x(), pos.y()
        if x < plot.left() or x > plot.right() or y < plot.top() or y > plot.bottom():
            return None
        n = len(self._values)
        slot = plot.width() / n
        return min(n - 1, max(0, int((x - plot.left()) // slot))) if slot else None

    def _tooltip_text(self, idx: int) -> str:
        if idx < len(self._tooltips) and self._tooltips[idx]:
            return self._tooltips[idx]
        label = self._labels[idx] if idx < len(self._labels) else ""
        value = self._value_fmt(self._values[idx])
        return f"{label}: {value}" if label else str(value)

    @staticmethod
    def _short_num(v: float) -> str:
        v = float(v)
        if v >= 1_000_000:
            return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
        if v >= 1_000:
            return f"{v / 1_000:.1f}k".replace(".0k", "k")
        return str(int(v))


class Sparkline(QWidget):
    def __init__(self, values=None, accent: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._values = list(values or [])
        self._accent = accent or COLORS["accent"]
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, values, accent: str | None = None) -> None:
        self._values = list(values or [])
        if accent:
            self._accent = accent
        self.update()

    def paintEvent(self, _event) -> None:
        vals = self._values
        if len(vals) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad = 2.0
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        n = len(vals)

        def pt(i: int) -> QPointF:
            x = pad + (w - 2 * pad) * (i / (n - 1))
            y = (h - pad) - (h - 2 * pad) * ((vals[i] - lo) / span)
            return QPointF(x, y)

        line = QPainterPath(pt(0))
        for i in range(1, n):
            line.lineTo(pt(i))

        fill = QPainterPath(QPointF(pad, h - pad))
        fill.lineTo(pt(0))
        for i in range(1, n):
            fill.lineTo(pt(i))
        fill.lineTo(QPointF(w - pad, h - pad))
        fill.closeSubpath()

        grad = QLinearGradient(0, 0, 0, h)
        col = QColor(self._accent)
        grad.setColorAt(0, QColor(col.red(), col.green(), col.blue(), 60))
        grad.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
        p.fillPath(fill, QBrush(grad))

        pen = QPen(col)
        pen.setWidthF(2.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPath(line)
        p.end()
