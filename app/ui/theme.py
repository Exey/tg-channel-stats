"""Visual theme: palette, stylesheet and SVG helpers.

The look is lifted from analytics_dashboard — a white sidebar, a light-grey
card grid, drop shadows, rounded corners and a single blue accent — but the
QSvgPixmap recolor trick and shadow helper are generalised here so every
widget in the app can share them.

Light and dark are just two colour dicts (LIGHT / DARK). `COLORS` is the
*active* one — kept as a single mutable dict object (rather than rebound)
so every module that did `from .theme import COLORS` sees a switch without
re-importing; callers just need to rebuild their widgets afterwards (see
MainWindow._switch_theme, which mirrors the existing language-switch
rebuild).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
SVGS = ASSETS / "svgs"

# ------------------------------------------------------------------ palette
LIGHT = {
    "bg": "#F4F6FA",
    "card": "#FFFFFF",
    "card_border": "transparent",
    "accent": "#1B59F8",
    "accent_soft": "#E9F0FF",
    "accent_track": "#EEF3FF",
    "accent_hover": "#1449D6",
    "accent_disabled": "#A9C1F7",
    "accent_disabled_text": "#EEF3FF",
    "text": "#12203A",
    "muted": "#6B7480",
    "faint": "#9AA3AF",
    "line": "#E7EBF1",
    "scrollbar": "#CBD3DE",
    "good": "#22C55E",
    "warn": "#F59E0B",
    "hot": "#F04438",
    "win": "#F2C230",
    "hour": "#1B59F8",
    "weekday": "#7C4DFF",
    "activity": "#1B59F8",
    "bar_from": "#3B7BFF",
    "bar_to": "#1B59F8",
    "shadow": (20, 32, 58),
}

DARK = {
    "bg": "#10141F",
    "card": "#1A2032",
    "card_border": "#2A3142",
    "accent": "#4C8DFF",
    "accent_soft": "#1E2A47",
    "accent_track": "#212C46",
    "accent_hover": "#6FA3FF",
    "accent_disabled": "#2C3B5E",
    "accent_disabled_text": "#7C88A3",
    "text": "#E7ECF5",
    "muted": "#97A2B8",
    "faint": "#6B7690",
    "line": "#2A3142",
    "scrollbar": "#3A4256",
    "good": "#34D399",
    "warn": "#FBBF24",
    "hot": "#F87171",
    "win": "#F2C230",
    "hour": "#4C8DFF",
    "weekday": "#9D7BFF",
    "activity": "#4C8DFF",
    "bar_from": "#5B93FF",
    "bar_to": "#3B6FE0",
    "shadow": (0, 0, 0),
}

# Fixed 16-swatch palette offered when picking a folder color. Same set in
# both themes — these are saturated enough to read on light or dark card
# backgrounds.
FOLDER_COLORS = [
    "#EF4444", "#F97316", "#F59E0B", "#EAB308",
    "#84CC16", "#22C55E", "#10B981", "#14B8A6",
    "#06B6D4", "#0EA5E9", "#3B82F6", "#6366F1",
    "#8B5CF6", "#A855F7", "#D946EF", "#EC4899",
]

# The currently active palette, mutated in place by set_theme() — see the
# module docstring for why this stays one dict object rather than a rebound
# name.
COLORS = dict(LIGHT)
_mode = "light"  # resolved 'light' | 'dark', kept for add_shadow()/queries


def resolve_system_dark() -> bool:
    """Best-effort read of the OS appearance (Qt 6.5+); False if unavailable."""
    try:
        hints = QGuiApplication.styleHints()
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def resolve_mode(pref: str) -> str:
    """pref: 'light' | 'dark' | 'system' -> resolved 'light'/'dark'."""
    if pref == "dark":
        return "dark"
    if pref == "light":
        return "light"
    return "dark" if resolve_system_dark() else "light"


def current_mode() -> str:
    return _mode


def set_theme(pref: str) -> str:
    """Resolve `pref` and update COLORS in place. Returns the resolved mode."""
    global _mode
    _mode = resolve_mode(pref)
    COLORS.clear()
    COLORS.update(DARK if _mode == "dark" else LIGHT)
    return _mode


def apply_theme(app, pref: str) -> str:
    """set_theme() + push the resulting QSS onto the running QApplication."""
    mode = set_theme(pref)
    app.setStyleSheet(build_qss())
    return mode


def add_shadow(widget, blur: int = 24, dy: int = 8, alpha: int = 28) -> None:
    r, g, b = COLORS["shadow"]
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(dy)
    effect.setColor(QColor(r, g, b, alpha))
    widget.setGraphicsEffect(effect)


def svg_pixmap(name: str, color: str | QColor = "#4c4c4c",
               size: int | None = None) -> QPixmap:
    """Load an SVG from assets/svgs and tint it a flat colour (SourceIn)."""
    path = SVGS / (name if name.endswith(".svg") else f"{name}.svg")
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return pixmap
    if size:
        pixmap = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
    tinted = QPixmap(pixmap.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor(color))
    painter.end()
    return tinted


def svg_icon(name: str, color: str | QColor = "#4c4c4c", size: int = 24) -> QIcon:
    return QIcon(svg_pixmap(name, color, size))


def build_qss() -> str:
    c = COLORS
    return f"""
    QWidget {{
        color: {c['text']};
        font-size: 14px;
    }}
    QWidget#root {{ background: {c['bg']}; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollArea {{ border: none; }}

    /* ---------------- sidebar ---------------- */
    QFrame#sidebar {{
        background: {c['card']};
        border: none;
    }}
    QLabel#brand {{
        font-size: 19px; font-weight: 800; color: {c['text']};
        padding: 4px 6px;
    }}
    QLabel#brandDot {{ color: {c['accent']}; }}
    QLabel#sectionLabel {{
        color: {c['faint']}; font-size: 11px; font-weight: 700;
        letter-spacing: 1px; padding: 4px 8px;
    }}
    QPushButton#navBtn {{
        text-align: left; border: none; border-radius: 12px;
        padding: 10px 12px; font-size: 14px; font-weight: 600;
        color: {c['muted']}; background: transparent;
    }}
    QPushButton#navBtn:hover {{ background: {c['bg']}; }}
    QPushButton#navBtn:checked {{
        background: {c['accent_soft']}; color: {c['accent']}; font-weight: 700;
    }}
    QLabel#navEmpty {{ color: {c['faint']}; font-size: 12px; padding: 6px 10px; }}
    QLabel#navMeta {{ color: {c['faint']}; font-size: 11px; font-weight: 700; }}

    /* ---------------- cards ---------------- */
    QFrame#card {{
        background: {c['card']};
        border-radius: 18px;
        border: 1px solid {c['card_border']};
    }}
    QLabel#cardTitle {{ color: {c['muted']}; font-size: 13px; font-weight: 600; }}
    QLabel#statValue {{ color: {c['text']}; font-size: 26px; font-weight: 800; }}
    QLabel#statSub {{ color: {c['faint']}; font-size: 12px; }}

    QLabel#pageTitle {{ font-size: 24px; font-weight: 800; color: {c['text']}; }}
    QLabel#pageSub {{ color: {c['muted']}; font-size: 13px; }}
    QLabel#sectionTitle {{ font-size: 15px; font-weight: 700; color: {c['text']}; }}
    QLabel#hint {{ color: {c['muted']}; font-size: 12px; }}
    QLabel#status {{ color: {c['muted']}; font-size: 12px; }}

    /* ---------------- inputs ---------------- */
    QLineEdit, QSpinBox, QComboBox {{
        background: {c['card']}; border: 1px solid {c['line']};
        border-radius: 10px; padding: 7px 10px; selection-background-color: {c['accent_soft']};
    }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {c['card']}; border: 1px solid {c['line']};
        selection-background-color: {c['accent_soft']}; selection-color: {c['accent']};
        outline: none;
    }}

    /* ---------------- buttons ---------------- */
    QPushButton {{
        background: {c['card']}; border: 1px solid {c['line']};
        border-radius: 10px; padding: 8px 14px; font-weight: 600; color: {c['text']};
    }}
    QPushButton:hover {{ background: {c['bg']}; }}
    QPushButton:disabled {{ color: {c['faint']}; }}
    QPushButton#primary {{
        background: {c['accent']}; color: white; border: none; font-weight: 700;
    }}
    QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
    QPushButton#primary:disabled {{
        background: {c['accent_disabled']}; color: {c['accent_disabled_text']};
    }}
    QPushButton#ghost {{ border: none; background: transparent; color: {c['muted']}; }}
    QPushButton#ghost:hover {{ color: {c['accent']}; }}
    QPushButton#ghost:checked {{
        color: {c['accent']}; background: {c['accent_soft']}; border-radius: 8px;
    }}

    /* ---------------- table ---------------- */
    QTableWidget {{
        background: {c['card']}; border: none; gridline-color: transparent;
        selection-background-color: {c['accent_soft']}; selection-color: {c['text']};
    }}
    QHeaderView::section {{
        background: {c['card']}; color: {c['muted']}; border: none;
        border-bottom: 1px solid {c['line']}; padding: 8px 6px;
        font-weight: 700; font-size: 12px;
    }}
    QTableWidget::item {{ padding: 6px; border-bottom: 1px solid {c['line']}; }}

    QProgressBar {{
        background: {c['bg']}; border: none; border-radius: 6px;
        height: 8px; text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 6px; }}

    QPlainTextEdit {{
        background: {c['bg']}; border: 1px solid {c['line']}; border-radius: 10px;
        color: {c['muted']}; font-family: "SF Mono","Menlo",monospace; font-size: 12px;
    }}
    QGroupBox {{
        border: 1px solid {c['line']}; border-radius: 12px; margin-top: 10px;
        font-weight: 700; padding-top: 6px;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {c['muted']}; }}

    QMenuBar {{ background: {c['card']}; }}
    QMenuBar::item:selected {{ background: {c['accent_soft']}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['scrollbar']}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """
