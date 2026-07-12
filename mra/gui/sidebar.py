"""Sidebar: mesh statistics, repair report, features, tolerance controls."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mra.core import Tolerances
from mra.meshproc import MeshStats, RepairReport


class Sidebar(QWidget):
    """Right-hand panel of the workbench.

    Signals:
        tolerances_changed: Emitted with a fresh ``Tolerances`` whenever the
            user edits a tolerance spin box.
        feature_selected: Emitted with the set of patch ids behind the
            selected feature row (empty set when deselected), so the
            viewport can highlight exactly what the row refers to.
    """

    tolerances_changed = Signal(object)
    feature_selected = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(280)

        self._stats_label = QLabel("No mesh loaded")
        self._stats_label.setWordWrap(True)
        stats_box = _boxed("Mesh Statistics", self._stats_label)

        self._repair_label = QLabel("Not repaired yet")
        self._repair_label.setWordWrap(True)
        repair_box = _boxed("Repair Report", self._repair_label)

        self._feature_list = QListWidget()
        self._feature_payloads: list[set[int]] = []
        self._feature_list.currentRowChanged.connect(self._on_feature_row)
        # Monospace so the numbered, column-aligned labels line up and the
        # list is scannable without screenshots.
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)
        self._feature_list.setFont(mono)
        feature_box = _boxed("Recovered Features", self._feature_list)

        self._tol_widgets: dict[str, QDoubleSpinBox] = {}
        tol_box = self._build_tolerance_box()

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(stats_box)
        layout.addWidget(repair_box)
        layout.addWidget(feature_box)
        layout.addWidget(tol_box)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ----------------------------------------------------------- updates

    def show_stats(self, stats: MeshStats) -> None:
        """Render mesh statistics."""
        ex = stats.extents
        lines = [
            f"Vertices: {stats.vertex_count:,}",
            f"Triangles: {stats.face_count:,}",
            f"Size: {ex[0]:.2f} x {ex[1]:.2f} x {ex[2]:.2f} mm",
            f"Area: {stats.surface_area:,.1f} mm²",
            f"Volume: {stats.volume:,.1f} mm³" if stats.is_watertight
            else "Volume: n/a (not watertight)",
            f"Watertight: {'yes' if stats.is_watertight else 'NO'}",
            f"Bodies: {stats.body_count}",
        ]
        self._stats_label.setText("\n".join(lines))

    def show_repair_report(self, report: RepairReport) -> None:
        """Render the Stage-1 repair report."""
        self._repair_label.setText("\n".join(report.summary_lines()))

    def show_features(
        self,
        descriptions: list[str],
        patch_ids: list[set[int]] | None = None,
    ) -> None:
        """Fill the recovered-features list (Stage 2/3 output).

        Args:
            descriptions: One line per row.
            patch_ids: Per-row patch ids to highlight on selection;
                rows without ids highlight nothing.
        """
        self._feature_list.blockSignals(True)
        self._feature_list.clear()
        self._feature_list.addItems(descriptions)
        self._feature_list.blockSignals(False)
        self._feature_payloads = (
            patch_ids if patch_ids is not None
            else [set() for _ in descriptions]
        )

    def _on_feature_row(self, row: int) -> None:
        if 0 <= row < len(self._feature_payloads):
            self.feature_selected.emit(self._feature_payloads[row])
        else:
            self.feature_selected.emit(set())

    def tolerances(self) -> Tolerances:
        """Current tolerance values from the spin boxes."""
        return Tolerances(
            point_distance=self._tol_widgets["point_distance"].value(),
            normal_angle_deg=self._tol_widgets["normal_angle_deg"].value(),
            hole_perimeter_max=self._tol_widgets["hole_perimeter_max"].value(),
            equal_dimension_ratio=self._tol_widgets["equal_dimension_ratio"].value(),
            ask_threshold=self._tol_widgets["ask_threshold"].value(),
        )

    # ------------------------------------------------------------ build

    def _build_tolerance_box(self) -> QGroupBox:
        defaults = Tolerances()
        rows: list[tuple[str, str, float, float, float, int]] = [
            # (attr, label, min, max, step, decimals)
            ("point_distance", "Fit distance (mm)", 0.001, 5.0, 0.01, 3),
            ("normal_angle_deg", "Normal angle (°)", 0.5, 45.0, 0.5, 1),
            ("hole_perimeter_max", "Max hole fill (mm)", 0.0, 500.0, 1.0, 1),
            ("equal_dimension_ratio", "Equal-dim ratio", 0.001, 0.2, 0.005, 3),
            ("ask_threshold", "Ask below conf.", 0.0, 1.0, 0.05, 2),
        ]
        form_holder = QWidget()
        form = QFormLayout(form_holder)
        for attr, label, lo, hi, step, decimals in rows:
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            spin.setValue(getattr(defaults, attr))
            spin.valueChanged.connect(self._emit_tolerances)
            self._tol_widgets[attr] = spin
            form.addRow(label, spin)
        return _boxed("Tolerances", form_holder)

    def _emit_tolerances(self) -> None:
        self.tolerances_changed.emit(self.tolerances())


def _boxed(title: str, inner: QWidget) -> QGroupBox:
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    layout.addWidget(inner)
    return box
