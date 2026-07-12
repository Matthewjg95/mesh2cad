"""Stage-4 machining-cost dialog: the savings menu.

Shows the itemised cost estimate, then lets the user trade features for
money — including whole CNC setups. Each machined side is a section: its
pockets are keep/drop checkboxes with the functional tradeoff spelled out
(an un-recessed screw sits proud → longer screw), and when every pocket
on an eliminable side is dropped, the entire setup disappears from the
job. Selecting a row highlights that pocket in the viewport.
"""

from __future__ import annotations

from typing import Callable

import trimesh
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from mra.core import Feature
from mra.dfm import (
    drop_pockets,
    drop_shallow_pockets,
    estimate_cost,
    setup_plans,
    unify_pocket_depths,
)
from mra.intent import IntentResult
from mra.recognition import SegmentationResult


class DfmDialog(QDialog):
    """Cost report + simplification and setup-elimination choices.

    Call :meth:`apply` after acceptance to run the chosen transforms on
    the intent; returns log lines for the build log.
    """

    def __init__(
        self,
        mesh: trimesh.Trimesh,
        segmentation: SegmentationResult,
        intent: IntentResult,
        parent: QWidget | None = None,
        highlight: Callable[[set], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Machining cost — savings menu")
        self.setMinimumSize(620, 560)
        self._intent = intent
        self._highlight = highlight

        report = estimate_cost(mesh, segmentation, intent)
        text = QPlainTextEdit(report.summary())
        text.setReadOnly(True)
        text.setMaximumHeight(180)

        layout = QVBoxLayout(self)
        layout.addWidget(text)

        hint = QLabel(
            "Price on one-offs is programming and setups, not cutting. "
            "Untick any recess you can live without — untick ALL of a "
            "side's recesses and that whole setup (fixture + flip) "
            "leaves the job:"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Per-side pocket keep/drop sections.
        self._pocket_boxes: list[tuple[Feature, QCheckBox]] = []
        for plan in setup_plans(intent):
            if not plan.pockets and not plan.pads:
                continue
            side_name = ("Top/inner side" if plan.side > 0
                         else "Bottom/outer side")
            status = (
                f"setup eliminable — save ~${plan.cost:,.0f} if all "
                "recesses below are dropped"
                if plan.eliminable
                else f"{len(plan.pads)} raised pad(s) keep this setup "
                "regardless"
            )
            box = QGroupBox(f"{side_name} ({status})")
            inner = QVBoxLayout(box)
            for f, note in zip(plan.pockets, plan.tradeoffs):
                depth = float(f.params["depth"])
                area = f.patches[0].area if f.patches else 0.0
                chk = QCheckBox(
                    f"Keep pocket {depth:.2f} mm deep, {area:.0f} mm²"
                )
                chk.setChecked(True)
                chk.setToolTip(note)
                if self._highlight is not None and f.patches:
                    patch_ids = {p.patch_id for p in f.patches}
                    chk.clicked.connect(
                        lambda _=False, ids=patch_ids:
                        self._highlight(ids)
                    )
                inner.addWidget(chk)
                self._pocket_boxes.append((f, chk))
            layout.addWidget(box)

        self._chk_pockets = QCheckBox(
            "Drop cosmetic pockets shallower than 0.5 mm"
        )
        self._chk_pockets.setChecked(True)
        self._chk_depths = QCheckBox(
            "Unify near-equal pocket depths (fewer Z levels)"
        )
        self._chk_depths.setChecked(True)
        layout.addWidget(self._chk_pockets)
        layout.addWidget(self._chk_depths)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> list[str]:
        """Run the chosen transforms; returns log lines."""
        log: list[str] = []
        dropped_ids = {
            f.feature_id for f, chk in self._pocket_boxes
            if not chk.isChecked()
        }
        if dropped_ids:
            log += drop_pockets(self._intent, dropped_ids)
        if self._chk_pockets.isChecked():
            log += drop_shallow_pockets(self._intent, max_depth=0.5)
        if self._chk_depths.isChecked():
            log += unify_pocket_depths(self._intent, rel_tol=0.15)
        return log
