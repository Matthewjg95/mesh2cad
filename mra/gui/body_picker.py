"""Dialog for isolating one body from a multi-body import.

Selecting a row previews that body alone in the main viewport, so the user
can visually find their part (e.g. the rear panel inside a full product
assembly) before committing to it.
"""

from __future__ import annotations

from typing import Callable

import trimesh
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from mra.meshproc import body_infos


class BodyPickerDialog(QDialog):
    """Pick one body out of a split assembly.

    Args:
        bodies: Candidate bodies, largest first (from ``split_bodies``).
        preview: Called with the currently highlighted body so the caller
            can show it in the viewport.
        parent: Qt parent.
    """

    def __init__(
        self,
        bodies: list[trimesh.Trimesh],
        preview: Callable[[trimesh.Trimesh], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Isolate body — {len(bodies)} bodies found")
        self.setMinimumWidth(420)
        self._bodies = bodies
        self._preview = preview

        hint = QLabel(
            "This file contains several separate bodies. Click one to "
            "preview it in the viewport, then OK to work on it alone."
        )
        hint.setWordWrap(True)

        self._list = QListWidget()
        for info in body_infos(bodies):
            self._list.addItem(f"Body {info.index}:  {info.label()}")
        self._list.currentRowChanged.connect(self._on_row_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self._list)
        layout.addWidget(buttons)

        self._list.setCurrentRow(0)

    def _on_row_changed(self, row: int) -> None:
        if 0 <= row < len(self._bodies):
            self._preview(self._bodies[row])

    def selected_body(self) -> trimesh.Trimesh | None:
        """The chosen body, or None when nothing is selected."""
        row = self._list.currentRow()
        if 0 <= row < len(self._bodies):
            return self._bodies[row]
        return None
