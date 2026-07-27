"""Folder management dialog: rename/recolor/delete folders and add new ones,
plus the small fixed-palette color picker it opens per row."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QInputDialog, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
    QWidget,
)

from ..folders import FolderStore
from .theme import FOLDER_COLORS


class ColorPickDialog(QDialog):
    """4x4 grid of the fixed folder palette; click a swatch to pick it."""

    def __init__(self, i18n, current: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("folder_pick_color"))
        self.selected: str | None = None

        grid = QGridLayout(self)
        grid.setSpacing(8)
        for i, color in enumerate(FOLDER_COLORS):
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ring = "2px solid #FFFFFF" if color == current else "1px solid transparent"
            btn.setStyleSheet(
                f"background:{color}; border-radius:14px; border:{ring};")
            btn.clicked.connect(lambda _=False, c=color: self._pick(c))
            grid.addWidget(btn, i // 4, i % 4)

    def _pick(self, color: str) -> None:
        self.selected = color
        self.accept()


class FolderManagerDialog(QDialog):
    """Add/rename/recolor/delete folders. Every edit saves immediately via
    FolderStore, so there's no separate Save/Cancel — just Close."""

    def __init__(self, folder_store: FolderStore, i18n, parent=None) -> None:
        super().__init__(parent)
        self.store = folder_store
        self.i18n = i18n
        self.setWindowTitle(i18n.tr("folder_manage_title"))
        self.setMinimumSize(340, 320)

        lay = QVBoxLayout(self)
        self.list_w = QListWidget()
        self.list_w.setSpacing(2)
        lay.addWidget(self.list_w, 1)

        add_btn = QPushButton(i18n.tr("folder_add"))
        add_btn.clicked.connect(self._add_folder)
        lay.addWidget(add_btn)

        close_btn = QPushButton(i18n.tr("folder_close"))
        close_btn.setObjectName("primary")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)

        self._rebuild()

    # ------------------------------------------------------------- rebuild
    def _rebuild(self) -> None:
        self.list_w.clear()
        for folder in self.store.list_folders():
            item = QListWidgetItem(self.list_w)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 2, 4, 2)

            swatch = QPushButton()
            swatch.setFixedSize(20, 20)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setStyleSheet(
                f"background:{folder['color']}; border-radius:10px; border:none;")
            swatch.clicked.connect(
                lambda _=False, fid=folder["id"], btn=swatch: self._pick_color(fid, btn))
            h.addWidget(swatch)

            name_edit = QLineEdit(folder["name"])
            name_edit.editingFinished.connect(
                lambda fid=folder["id"], edit=name_edit: self._rename(fid, edit))
            h.addWidget(name_edit, 1)

            del_btn = QPushButton("🗑")
            del_btn.setObjectName("ghost")
            del_btn.setFixedWidth(32)
            del_btn.clicked.connect(lambda _=False, fid=folder["id"]: self._delete(fid))
            h.addWidget(del_btn)

            item.setSizeHint(row.sizeHint())
            self.list_w.addItem(item)
            self.list_w.setItemWidget(item, row)

    # -------------------------------------------------------------- actions
    def _add_folder(self) -> None:
        name, ok = QInputDialog.getText(
            self, self.i18n.tr("folder_add"), self.i18n.tr("folder_name_prompt"))
        if not ok or not name.strip():
            return
        color = FOLDER_COLORS[len(self.store.list_folders()) % len(FOLDER_COLORS)]
        self.store.add_folder(name, color)
        self._rebuild()

    def _rename(self, folder_id: str, edit: QLineEdit) -> None:
        text = edit.text().strip()
        if text:
            self.store.update_folder(folder_id, name=text)
        else:
            folder = self.store.get_folder(folder_id)
            if folder:
                edit.setText(folder["name"])

    def _pick_color(self, folder_id: str, swatch: QPushButton) -> None:
        folder = self.store.get_folder(folder_id)
        dlg = ColorPickDialog(self.i18n, current=folder["color"] if folder else None,
                              parent=self)
        if dlg.exec() and dlg.selected:
            self.store.update_folder(folder_id, color=dlg.selected)
            swatch.setStyleSheet(
                f"background:{dlg.selected}; border-radius:10px; border:none;")

    def _delete(self, folder_id: str) -> None:
        folder = self.store.get_folder(folder_id)
        name = folder["name"] if folder else ""
        if QMessageBox.question(
                self, self.i18n.tr("folder_delete"),
                self.i18n.tr("folder_delete_confirm", name=name)
        ) != QMessageBox.StandardButton.Yes:
            return
        self.store.remove_folder(folder_id)
        self._rebuild()
