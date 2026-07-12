"""Stage-4 hole wizard: assign a purpose to every detected hole.

Mesh measurements carry molding/scan noise, and molded holes are often
sized for heat-set inserts rather than the screws themselves. Instead of
trusting raw diameters, the user says what each hole IS — "M3 clearance",
"M3 tapped", "keep measured", or "fill it in" — and the reconstruction
uses the engineering dimension.

Holes are grouped by diameter so a plate with four identical bores is one
decision, not four.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mra.core import Feature

# Purpose -> new diameter (mm); None keeps the measured value.
# "cut tap" = conventional fluted tap drill sizes; "roll tap" = form
# taps (no chips — what SendCutSend and most production CNC shops use),
# which need a LARGER pilot hole than a cut tap for the same thread.
_PURPOSES: list[tuple[str, float | None]] = [
    ("Keep measured size", None),
    ("M2 clearance (Ø2.4)", 2.4),
    ("M2 cut tap (Ø1.6)", 1.6),
    ("M2 roll tap (Ø1.8)", 1.8),
    ("M2.5 clearance (Ø2.9)", 2.9),
    ("M2.5 cut tap (Ø2.05)", 2.05),
    ("M2.5 roll tap (Ø2.3)", 2.3),
    ("M3 clearance (Ø3.4)", 3.4),
    ("M3 cut tap (Ø2.5)", 2.5),
    ("M3 roll tap (Ø2.75)", 2.75),
    ("M4 clearance (Ø4.5)", 4.5),
    ("M4 cut tap (Ø3.3)", 3.3),
    ("M4 roll tap (Ø3.7)", 3.7),
    ("Fill in (remove hole)", -1.0),
]


class HoleWizardDialog(QDialog):
    """One combo box per hole-diameter group.

    Call :meth:`apply` after acceptance; it rewrites diameters in place
    and returns the features the user chose to fill in (the caller
    removes those from the intent).
    """

    def __init__(
        self, holes: list[Feature], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hole purposes")
        self.setMinimumWidth(430)

        self._groups: list[tuple[list[Feature], QComboBox, QCheckBox]] = []

        hint = QLabel(
            "Tell the reconstruction what each hole is for. Molded parts "
            "often carry insert-sized bores; pick the thread they serve "
            "and the CAD gets the machining dimension instead of the "
            "measured plastic. Tick 'flatten' to erase molded "
            "counterbore steps and get a plain through-hole in a flat "
            "face (use a longer screw)."
        )
        hint.setWordWrap(True)

        form_holder = QWidget()
        form = QFormLayout(form_holder)
        for group in _group_by_diameter(holes):
            diameter = group[0].params["diameter"]
            combo = QComboBox()
            for label, _ in _PURPOSES:
                combo.addItem(label)
            flatten = QCheckBox("flatten recess")
            flatten.setToolTip(
                "Fill any molded counterbore/recess steps around this "
                "hole so the face is flat and the hole is a plain "
                "through-bore"
            )
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(combo)
            row_layout.addWidget(flatten)
            label = (
                f"{len(group)} hole(s) Ø{diameter:.2f} mm, "
                f"depth {group[0].params['depth']:.1f} mm"
            )
            form.addRow(label, row)
            self._groups.append((group, combo, flatten))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(form_holder)
        layout.addWidget(buttons)

    def apply(self) -> tuple[list[Feature], set[int]]:
        """Write chosen purposes onto the features.

        Returns:
            ``(removed, flatten_ids)`` — features the user chose to fill
            in (caller removes them so no cut is made), and the ids of
            holes whose surrounding recesses should be flattened.
        """
        removed: list[Feature] = []
        flatten_ids: set[int] = set()
        for group, combo, flatten in self._groups:
            _, new_diameter = _PURPOSES[combo.currentIndex()]
            for feature in group:
                feature.user_resolved = True
                if flatten.isChecked():
                    flatten_ids.add(feature.feature_id)
                if new_diameter is None:
                    continue
                if new_diameter < 0:
                    removed.append(feature)
                else:
                    feature.params["purpose"] = _PURPOSES[
                        combo.currentIndex()
                    ][0]
                    feature.params["diameter"] = new_diameter
        return removed, flatten_ids


def _group_by_diameter(
    holes: list[Feature], rel_tol: float = 0.05
) -> list[list[Feature]]:
    """Group holes whose diameters agree within ``rel_tol``."""
    groups: list[list[Feature]] = []
    for hole in sorted(holes, key=lambda h: h.params["diameter"]):
        d = hole.params["diameter"]
        if groups and abs(groups[-1][0].params["diameter"] - d) \
                <= rel_tol * max(d, 1.0):
            groups[-1].append(hole)
        else:
            groups.append([hole])
    return groups
