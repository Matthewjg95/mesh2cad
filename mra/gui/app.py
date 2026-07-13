"""Main window and application entry point.

Wires the pipeline buttons to the pipeline modules. Stages that are not yet
implemented have their buttons disabled with an explanatory tooltip, so the
app is honest about its current capabilities.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import trimesh
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QToolBar,
    QWidget,
)

from mra import __version__
from mra.core import FeatureType, Tolerances
from mra.export import export_step
from mra.gui.sidebar import Sidebar
from mra.gui.viewport import Viewport
from mra.intent import IntentResult, recover_intent
from mra.gui.body_picker import BodyPickerDialog
from mra.meshproc import compute_stats, load_mesh, repair, split_bodies
from mra.recognition import SegmentationResult, segment_mesh
from mra.reconstruction import BuildResult, build_solid, shape_to_trimesh
from mra.validation import validate_shape


class _RecognitionWorker(QObject):
    """Runs Stage-2 segmentation off the GUI thread.

    Signals:
        finished: Emitted with the ``SegmentationResult``.
        failed: Emitted with a traceback string.
    """

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, mesh: trimesh.Trimesh, tol: Tolerances) -> None:
        super().__init__()
        self._mesh = mesh
        self._tol = tol

    def run(self) -> None:
        try:
            self.finished.emit(segment_mesh(self._mesh, self._tol))
        except Exception:
            self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    """The reverse-engineering workbench window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(
            f"Mechanical Reverse Engineering Assistant v{__version__}"
        )
        self.resize(1400, 900)

        self._mesh: trimesh.Trimesh | None = None
        self._bodies: list[trimesh.Trimesh] = []
        self._segmentation: SegmentationResult | None = None
        self._intent: IntentResult | None = None
        self._build: BuildResult | None = None
        self._worker_thread: QThread | None = None

        self._viewport = Viewport()
        self.setCentralWidget(self._viewport)

        self._sidebar = Sidebar()
        self._sidebar.feature_selected.connect(self._on_feature_selected)
        dock = _dock("Analysis", self._sidebar)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        self.setStatusBar(QStatusBar())
        self._build_toolbar()

    # ----------------------------------------------------------- actions

    def import_stl(self) -> None:
        """Pick and load an STL, show stats and geometry."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import mesh", "", "Meshes (*.stl *.obj *.ply *.3mf)"
        )
        if not path:
            return
        try:
            self._mesh = load_mesh(path)
        except (FileNotFoundError, ValueError) as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self._bodies = split_bodies(self._mesh)
        self._btn_isolate.setEnabled(len(self._bodies) > 1)
        self._segmentation = None
        self._intent = None
        self._build = None
        self._btn_reconstruct.setEnabled(False)
        self._btn_sheet.setEnabled(False)
        self._btn_report.setEnabled(False)
        self._btn_validate.setEnabled(False)
        self._btn_export.setEnabled(False)
        self._viewport.clear()
        self._viewport.show_mesh(self._mesh)
        self._sidebar.show_stats(compute_stats(self._mesh))
        self.statusBar().showMessage(f"Loaded {path}")
        self._btn_repair.setEnabled(True)
        self._btn_recognize.setEnabled(True)
        self._btn_report.setEnabled(True)
        if len(self._bodies) > 1:
            self.isolate_body()

    def isolate_body(self) -> None:
        """Let the user pick one body from a multi-body import."""
        if not self._bodies or len(self._bodies) < 2:
            return
        dialog = BodyPickerDialog(
            self._bodies, preview=self._preview_body, parent=self
        )
        if dialog.exec() == BodyPickerDialog.DialogCode.Accepted:
            body = dialog.selected_body()
            if body is not None:
                self._mesh = body
                self._segmentation = None
                self._intent = None
                self._build = None
                self._btn_validate.setEnabled(False)
                self._btn_export.setEnabled(False)
                self.statusBar().showMessage(
                    "Isolated one body — run Repair Mesh next"
                )
        # Show whatever is now current (chosen body, or the full mesh
        # again if the user cancelled).
        self._preview_body(self._mesh)
        self._sidebar.show_stats(compute_stats(self._mesh))

    def _preview_body(self, body: trimesh.Trimesh) -> None:
        self._viewport.clear()
        self._viewport.show_mesh(body)

    def repair_mesh(self) -> None:
        """Run Stage-1 repair on the loaded mesh."""
        if self._mesh is None:
            return
        try:
            repaired, report = repair(self._mesh, self._sidebar.tolerances())
        except Exception as exc:  # repair must never take down the app
            traceback.print_exc()
            QMessageBox.critical(self, "Repair failed", str(exc))
            return
        self._mesh = repaired
        self._viewport.clear()
        self._viewport.show_mesh(self._mesh)
        self._sidebar.show_stats(compute_stats(self._mesh))
        self._sidebar.show_repair_report(report)
        self.statusBar().showMessage("Repair complete")

    def recognize_features(self) -> None:
        """Run Stage-2 segmentation in a worker thread."""
        if self._mesh is None or self._worker_thread is not None:
            return
        self._btn_recognize.setEnabled(False)
        self.statusBar().showMessage(
            f"Recognising surfaces on {len(self._mesh.faces):,} triangles..."
        )
        thread = QThread(self)
        worker = _RecognitionWorker(self._mesh, self._sidebar.tolerances())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_recognition_done)
        worker.failed.connect(self._on_recognition_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._clear_worker)
        self._worker_thread = thread
        self._worker = worker  # keep a reference while the thread runs
        thread.start()

    def _on_recognition_done(self, result: SegmentationResult) -> None:
        self._segmentation = result
        assert self._mesh is not None
        self._viewport.show_patches(self._mesh, result.face_patch_ids)
        descriptions = [
            _describe_patch(i, p, self._mesh)
            for i, p in enumerate(result.patches)
        ]
        self._sidebar.show_features(
            descriptions, [{p.patch_id} for p in result.patches]
        )
        self.statusBar().showMessage(
            f"Found {len(result.patches)} surface patches "
            f"({result.coverage() * 100:.0f}% of mesh explained)"
        )
        self._btn_recognize.setEnabled(True)
        self._btn_reconstruct.setEnabled(True)
        self._btn_sheet.setEnabled(True)
        self._btn_report.setEnabled(True)

    def _on_recognition_failed(self, tb: str) -> None:
        print(tb, file=sys.stderr)
        QMessageBox.critical(self, "Recognition failed", tb.splitlines()[-1])
        self.statusBar().showMessage("Recognition failed")
        self._btn_recognize.setEnabled(True)

    def _clear_worker(self) -> None:
        if self._worker_thread is not None:
            self._worker_thread.deleteLater()
        self._worker_thread = None

    # ----------------------------------------------- stages 3-5: rebuild

    def reconstruct_cad(self) -> None:
        """Recover intent (Stage 3), ask the user (Stage 4), build (Stage 5)."""
        if self._mesh is None or self._segmentation is None:
            QMessageBox.information(
                self, "Reconstruct CAD",
                "Run Recognize Features first."
            )
            return
        tol = self._sidebar.tolerances()
        try:
            self._intent = recover_intent(self._mesh, self._segmentation, tol)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Intent recovery failed", str(exc))
            return

        self._ask_questions(self._intent)
        self._ask_hole_purposes(self._intent)
        self._offer_simplifications(self._intent)
        lines = self._intent.summary_lines()
        numbered = [f"#{i:<2} {ln}" for i, ln in enumerate(lines)]
        payloads = [
            {p.patch_id for p in f.patches}
            for f in self._intent.features
        ] + [set()] * (len(lines) - len(self._intent.features))
        self._sidebar.show_features(numbered, payloads)

        try:
            self._build = build_solid(
                self._mesh, self._segmentation, self._intent, tol
            )
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Reconstruction failed", str(exc))
            return

        if self._build.shape is None:
            QMessageBox.warning(
                self, "Reconstruct CAD",
                "\n".join(self._build.log) or "No solid could be built."
            )
            return
        preview = shape_to_trimesh(self._build.shape)
        self._viewport.show_preview(preview)
        self._viewport.set_layer_visible("patches", False)
        self._viewport.set_layer_visible("original", False)
        self.statusBar().showMessage(
            "Reconstruction complete — " + "; ".join(self._build.log[-2:])
        )
        self._btn_validate.setEnabled(True)
        self._btn_export.setEnabled(True)

    def _ask_questions(self, intent: IntentResult) -> None:
        """Stage 4: present each open question as a dialog."""
        for q in intent.questions:
            if q.answered:
                continue
            box = QMessageBox(self)
            box.setWindowTitle("Interactive recovery")
            box.setText(q.text)
            buttons = [box.addButton(opt, QMessageBox.ButtonRole.AcceptRole)
                       for opt in q.options]
            box.setDefaultButton(buttons[0])
            box.exec()
            clicked = box.clickedButton()
            q.answer = next(
                (i for i, b in enumerate(buttons) if b is clicked), 0
            )
            self._apply_answer(intent, q)

    def _on_feature_selected(self, patch_ids: set) -> None:
        """Highlight the selected feature's patches in the viewport."""
        if self._mesh is None or self._segmentation is None:
            return
        self._viewport.highlight_patches(
            self._mesh, self._segmentation.face_patch_ids, set(patch_ids)
        )

    def _ask_hole_purposes(self, intent: IntentResult) -> None:
        """Stage 4: let the user assign a purpose to each hole group."""
        from mra.gui.hole_wizard import HoleWizardDialog

        holes = [f for f in intent.features
                 if f.feature_type == FeatureType.HOLE
                 and not f.user_resolved]
        if not holes:
            return
        dialog = HoleWizardDialog(holes, parent=self)
        if dialog.exec() != HoleWizardDialog.DialogCode.Accepted:
            return
        removed, flatten_ids = dialog.apply()
        if flatten_ids:
            from mra.dfm import flatten_hole_recesses

            for line in flatten_hole_recesses(intent, flatten_ids):
                self.statusBar().showMessage(line, 2000)
        if removed:
            intent.features[:] = [
                f for f in intent.features if f not in removed
            ]

    def report_part(self) -> None:
        """Package this part + analysis into a shareable repro bundle."""
        if self._mesh is None:
            return
        from PySide6.QtWidgets import (
            QComboBox, QDialog, QDialogButtonBox, QFormLayout,
            QPlainTextEdit,
        )
        from mra.diagnostics import ReproReport, build_repro_bundle

        dlg = QDialog(self)
        dlg.setWindowTitle("Report Part")
        form = QFormLayout(dlg)
        part_class = QComboBox()
        part_class.addItems([
            "unknown", "plate/backplate", "bracket", "enclosure/housing",
            "cover/lid", "adapter/spacer", "heat sink", "other",
        ])
        stage = QComboBox()
        stage.addItems([
            "none / it worked", "import/repair", "recognize",
            "reconstruct", "sheet", "validate/export",
        ])
        expected = QPlainTextEdit()
        expected.setPlaceholderText("What did you expect?")
        expected.setMaximumHeight(60)
        actual = QPlainTextEdit()
        actual.setPlaceholderText("What actually happened?")
        actual.setMaximumHeight(60)
        notes = QPlainTextEdit()
        notes.setPlaceholderText("Anything else…")
        notes.setMaximumHeight(60)
        form.addRow("Part class", part_class)
        form.addRow("Stage wrong", stage)
        form.addRow("Expected", expected)
        form.addRow("Actual", actual)
        form.addRow("Notes", notes)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save repro bundle", "mesh2cad_report.zip",
            "Zip (*.zip)"
        )
        if not path:
            return
        report = ReproReport(
            user_notes=notes.toPlainText(),
            part_class=part_class.currentText(),
            stage_where_wrong=stage.currentText(),
            expected=expected.toPlainText(),
            actual=actual.toPlainText(),
        )
        try:
            out = build_repro_bundle(
                path, self._mesh, report,
                self._segmentation, self._intent, self._build,
            )
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Report Part failed", str(exc))
            return
        box = QMessageBox(self)
        box.setWindowTitle("Repro bundle saved")
        box.setText(
            f"Saved {out.name}.\n\nAttach it to a new issue at\n"
            "github.com/Matthewjg95/mesh2cad/issues/new/choose\n\n"
            "(Nothing was uploaded — the file is on your disk.)"
        )
        box.exec()

    def build_sheet_version(self) -> None:
        """Build a flat sheet-metal version (laser/waterjet cuttable)."""
        if self._mesh is None or self._segmentation is None:
            QMessageBox.information(
                self, "Sheet Version", "Run Recognize Features first."
            )
            return
        from PySide6.QtWidgets import QInputDialog
        from mra.reconstruction import build_sheet

        tol = self._sidebar.tolerances()
        if self._intent is None:
            try:
                self._intent = recover_intent(
                    self._mesh, self._segmentation, tol
                )
            except Exception as exc:
                traceback.print_exc()
                QMessageBox.critical(self, "Sheet Version", str(exc))
                return
        default_t = round(self._intent.wall_thickness or 2.0, 2)
        thickness, ok = QInputDialog.getDouble(
            self, "Sheet thickness",
            "Sheet thickness (mm) — pick your stock gauge:",
            default_t, 0.1, 25.0, 2,
        )
        if not ok:
            return
        try:
            self._build = build_sheet(
                self._mesh, self._segmentation, self._intent, thickness, tol
            )
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Sheet Version failed", str(exc))
            return
        if self._build.shape is None:
            QMessageBox.warning(
                self, "Sheet Version",
                "\n".join(self._build.log) or "Could not build a sheet."
            )
            return
        preview = shape_to_trimesh(self._build.shape)
        self._viewport.show_preview(preview)
        self._viewport.set_layer_visible("patches", False)
        self._viewport.set_layer_visible("original", False)
        self.statusBar().showMessage(
            f"Flat sheet at {thickness:.2f} mm — Validate / Export STEP "
            "(cut this on flat sheet for a fraction of machined cost)"
        )
        self._btn_validate.setEnabled(True)
        self._btn_export.setEnabled(True)

    def _offer_simplifications(self, intent: IntentResult) -> None:
        """Stage 4: show the machining-cost savings menu."""
        from mra.gui.dfm_dialog import DfmDialog

        assert self._mesh is not None and self._segmentation is not None
        dialog = DfmDialog(
            self._mesh, self._segmentation, intent, parent=self,
            highlight=self._on_feature_selected,
        )
        if dialog.exec() == DfmDialog.DialogCode.Accepted:
            for line in dialog.apply():
                self.statusBar().showMessage(line, 2000)

    def _apply_answer(self, intent: IntentResult, q) -> None:
        """Apply a user's decision back onto the affected features."""
        if q.accepted_default():
            return  # recommended interpretation is already applied
        # "Keep measured sizes": restore pre-snap hole diameters.
        for f in intent.features:
            if f.feature_id in q.feature_ids and \
                    f.feature_type == FeatureType.HOLE and \
                    "measured_diameter" in f.params:
                f.params["diameter"] = f.params["measured_diameter"]
                f.user_resolved = True

    # -------------------------------------------------- stage 6: validate

    def validate_solid(self) -> None:
        """Run Stage-6 checks and show the report."""
        if self._build is None or self._build.shape is None:
            return
        reference = float(self._mesh.volume) \
            if self._mesh is not None and self._mesh.is_watertight else None
        report = validate_shape(self._build.shape, reference)
        QMessageBox.information(
            self, "Validation report", "\n".join(report.summary_lines())
        )
        self.statusBar().showMessage(
            "Validation: " + ("PASS" if report.ready_for_export else "FAIL")
        )

    # ---------------------------------------------------- stage 7: export

    def export_step_file(self) -> None:
        """Write the reconstructed solid to STEP."""
        if self._build is None or self._build.shape is None:
            return
        path, selected = QFileDialog.getSaveFileName(
            self, "Export STEP", "part.step",
            "STEP AP242 (*.step);;STEP AP214 (*.stp)"
        )
        if not path:
            return
        schema = "AP214" if "AP214" in selected else "AP242"
        try:
            export_step(self._build.shape, path, schema=schema)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        message = f"Exported {schema} STEP: {path}"
        if self._intent is not None:
            from mra.export import write_hole_schedule

            schedule = write_hole_schedule(self._intent, path)
            if schedule is not None:
                message += f"  (+ hole schedule {schedule.name})"
        self.statusBar().showMessage(message)

    # ------------------------------------------------------------- build

    def _build_toolbar(self) -> None:
        bar = QToolBar("Pipeline")
        bar.setMovable(False)
        self.addToolBar(bar)

        self._btn_import = _btn("Import STL", self.import_stl)
        self._btn_isolate = _btn(
            "Isolate Body", self.isolate_body, enabled=False
        )
        self._btn_isolate.setToolTip(
            "Pick one body out of a multi-body import"
        )
        self._btn_repair = _btn("Repair Mesh", self.repair_mesh, enabled=False)
        self._btn_recognize = _btn(
            "Recognize Features", self.recognize_features, enabled=False
        )
        self._btn_reconstruct = _btn(
            "Reconstruct CAD", self.reconstruct_cad, enabled=False
        )
        self._btn_sheet = _btn(
            "Sheet Version", self.build_sheet_version, enabled=False
        )
        self._btn_sheet.setToolTip(
            "Flatten to a laser/waterjet-cuttable sheet: keeps the profile "
            "and through-holes, drops all towers/pockets/bosses"
        )
        self._btn_validate = _btn(
            "Validate", self.validate_solid, enabled=False
        )
        self._btn_export = _btn(
            "Export STEP", self.export_step_file, enabled=False
        )
        self._btn_report = _btn(
            "Report Part", self.report_part, enabled=False
        )
        self._btn_report.setToolTip(
            "Package this part + the tool's analysis into a zip you can "
            "attach to a GitHub issue (nothing is uploaded)"
        )
        for b in (self._btn_import, self._btn_isolate, self._btn_repair,
                  self._btn_recognize, self._btn_reconstruct,
                  self._btn_sheet, self._btn_validate, self._btn_export,
                  self._btn_report):
            bar.addWidget(b)


def _orientation_label(normal) -> str:
    """Human name for a plane's facing direction (assumes Z is 'up')."""
    import numpy as np

    n = np.asarray(normal, dtype=float)
    axis = int(np.argmax(np.abs(n)))
    sign = "+" if n[axis] >= 0 else "-"
    if axis == 2:
        return "top face" if sign == "+" else "bottom face"
    side = {0: "X", 1: "Y"}[axis]
    hint = {("+", 0): "right", ("-", 0): "left",
            ("+", 1): "back", ("-", 1): "front"}.get((sign, axis), "")
    return f"{sign}{side} wall ({hint})" if hint else f"{sign}{side} wall"


def _describe_patch(index: int, patch, mesh) -> str:
    """Numbered, human-readable one-liner for the recognised-feature list.

    Example: ``#3  bottom face   2388 mm²  @z-12.0   [1.00]``
             ``#12 cylinder Ø5.9  axis+Z  @(75,19)   [1.00]``
    """
    import numpy as np

    verts = mesh.vertices[np.unique(mesh.faces[patch.face_indices])]
    c = verts.mean(axis=0)
    conf = patch.confidence.value
    p = patch.params
    kind = patch.surface_type.value

    if kind == "plane":
        label = _orientation_label(p["normal"])
        axis = int(np.argmax(np.abs(np.asarray(p["normal"]))))
        loc = f"@{'xyz'[axis]}{c[axis]:+.1f}"
        return (f"#{index:<2} {label:<16} {patch.area:6.0f} mm²  "
                f"{loc:<9} [{conf:.2f}]")
    if kind == "cylinder":
        ax = np.asarray(p["axis"])
        aname = "XYZ"[int(np.argmax(np.abs(ax)))]
        asign = "+" if ax[int(np.argmax(np.abs(ax)))] >= 0 else "-"
        return (f"#{index:<2} cylinder Ø{2 * p['radius']:.1f}  axis{asign}{aname}"
                f"  @({c[0]:.0f},{c[1]:.0f})   [{conf:.2f}]")
    if kind == "cone":
        return (f"#{index:<2} cone {np.degrees(p['half_angle']):.0f}°  "
                f"@({c[0]:.0f},{c[1]:.0f})   [{conf:.2f}]")
    if kind == "sphere":
        return (f"#{index:<2} sphere r{p['radius']:.1f}  "
                f"@({c[0]:.0f},{c[1]:.0f})   [{conf:.2f}]")
    return (f"#{index:<2} {kind:<16} {patch.area:6.0f} mm²  "
            f"@({c[0]:.0f},{c[1]:.0f})   [{conf:.2f}]")


def _btn(text: str, slot, enabled: bool = True) -> QPushButton:
    b = QPushButton(text)
    b.clicked.connect(slot)
    b.setEnabled(enabled)
    return b


def _dock(title: str, widget: QWidget):
    from PySide6.QtWidgets import QDockWidget

    dock = QDockWidget(title)
    dock.setWidget(widget)
    dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
    return dock


def main() -> int:
    """Application entry point (``python -m mra``)."""
    app = QApplication(sys.argv)
    icon_path = Path(__file__).resolve().parents[2] / "assets" / "mra.ico"
    if icon_path.exists():
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
