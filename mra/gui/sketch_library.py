"""Sketch Library dialog: browse, preview, save, load and export assets.

A thin view over ``mra.sketch_assets``: all persistence, validation and
export logic lives in the headless package. The preview is the same SVG the
exporter writes, rendered by Qt's SVG renderer.
"""

from __future__ import annotations

from collections.abc import Callable
from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mra.sketch_assets import SketchAsset, SketchLibrary, default_library_dir
from mra.sketch_assets.export_dxf import export_dxf
from mra.sketch_assets.export_svg import export_svg, svg_text
from mra.sketch_assets.geometry import asset_bounds
from mra.sketch_assets.library import asset_key

SETTINGS_KEY = "sketch_library/dir"

#: Builds an asset from the current workspace: (name, tags) -> asset.
CaptureFn = Callable[[str, list[str]], SketchAsset]
#: Loads an asset into the workspace; returns True when it was loaded.
LoadFn = Callable[[SketchAsset], bool]


def configured_library() -> SketchLibrary:
    """Library at the folder chosen in the GUI, else the default."""
    stored = QSettings("mesh2cad", "mra").value(SETTINGS_KEY, "")
    return SketchLibrary(stored or default_library_dir())


class SketchLibraryDialog(QDialog):
    """Browse the local sketch library."""

    def __init__(
        self,
        capture: CaptureFn | None,
        load: LoadFn,
        parent: QWidget | None = None,
        library: SketchLibrary | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sketch Library")
        self.resize(900, 560)
        self._capture = capture
        self._load = load
        self._library = library or configured_library()

        self._folder = QLabel()
        self._folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        change = QPushButton("Change Folder…")
        change.clicked.connect(self._change_folder)
        top = QHBoxLayout()
        top.addWidget(QLabel("Folder:"))
        top.addWidget(self._folder, 1)
        top.addWidget(change)

        self._query = QLineEdit()
        self._query.setPlaceholderText("Search name or tag")
        self._query.textChanged.connect(self.refresh)
        self._tags = QLineEdit()
        self._tags.setPlaceholderText("Required tags, comma separated")
        self._tags.textChanged.connect(self.refresh)
        search = QHBoxLayout()
        search.addWidget(self._query, 2)
        search.addWidget(self._tags, 1)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._show_selected)
        self._preview = QSvgWidget()
        self._preview.setMinimumSize(360, 300)
        self._info = QLabel()
        self._info.setWordWrap(True)
        self._info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._problems = QLabel()
        self._problems.setStyleSheet("color: #b03a2e")
        right = QVBoxLayout()
        right.addWidget(self._preview, 1)
        right.addWidget(self._info)
        middle = QHBoxLayout()
        middle.addWidget(self._list, 1)
        middle.addLayout(right, 2)

        self._btn_save = QPushButton("Save Current Part…")
        self._btn_save.setEnabled(capture is not None)
        self._btn_save.setToolTip(
            "Capture the current solid's largest planar face (e.g. the Sheet "
            "Version footprint) as an asset" if capture else
            "Build a solid first (Reconstruct CAD or Sheet Version)"
        )
        self._btn_save.clicked.connect(self._save_current)
        self._btn_load = QPushButton("Load into Workspace")
        self._btn_load.clicked.connect(self._load_selected)
        self._btn_dxf = QPushButton("Export DXF…")
        self._btn_dxf.clicked.connect(lambda: self._export("dxf"))
        self._btn_svg = QPushButton("Export SVG…")
        self._btn_svg.clicked.connect(lambda: self._export("svg"))
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        for b in (self._btn_save, self._btn_load, self._btn_dxf, self._btn_svg):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(search)
        layout.addLayout(middle, 1)
        layout.addWidget(self._problems)
        layout.addLayout(buttons)
        self.refresh()

    # ------------------------------------------------------------ state

    def selected_asset(self) -> SketchAsset | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self) -> None:
        """Re-scan the folder and apply the search fields."""
        self._folder.setText(str(self._library.root))
        tags = [t for t in self._tags.text().split(",") if t.strip()]
        entries, problems = self._library.search(self._query.text(), tags)
        self._list.clear()
        for entry in entries:
            item = QListWidgetItem(entry.asset.name)
            item.setData(Qt.ItemDataRole.UserRole, entry.asset)
            item.setToolTip(str(entry.path))
            self._list.addItem(item)
        if problems:
            self._problems.setText(f"{len(problems)} unreadable file(s) — hover for details")
            self._problems.setToolTip("\n".join(p.error for p in problems))
        else:
            self._problems.setText("")
            self._problems.setToolTip("")
        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._show_selected()

    def _show_selected(self, *_args) -> None:
        asset = self.selected_asset()
        for b in (self._btn_load, self._btn_dxf, self._btn_svg):
            b.setEnabled(asset is not None)
        if asset is None:
            self._preview.load(QByteArray())
            self._info.setText("No asset selected.")
            return
        self._preview.load(QByteArray(svg_text(asset).encode("utf-8")))
        renderer = self._preview.renderer()
        if hasattr(renderer, "setAspectRatioMode"):  # Qt >= 6.7
            renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        bounds = asset_bounds(asset)
        size = (f"{bounds[2] - bounds[0]:g} × {bounds[3] - bounds[1]:g} {asset.units}"
                if bounds else "empty")
        source = asset.provenance.get("source") or asset.provenance.get("method") or "—"
        self._info.setText(
            f"<b>{asset.name}</b><br>Size: {size} · Units: {asset.units} · "
            f"Schema {asset.schema_version}<br>"
            f"{len(asset.circles)} circle(s), {len(asset.profiles)} profile(s), "
            f"{len(asset.polygons)} polygon(s)<br>"
            f"Tags: {', '.join(asset.tags) or '—'}<br>Source: {source}"
        )

    # ---------------------------------------------------------- actions

    def _change_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Sketch library folder", str(self._library.root)
        )
        if folder:
            QSettings("mesh2cad", "mra").setValue(SETTINGS_KEY, folder)
            self._library = SketchLibrary(folder)
            self.refresh()

    def _save_current(self) -> None:
        assert self._capture is not None
        name, ok = QInputDialog.getText(self, "Save sketch asset", "Asset name:")
        if not ok or not name.strip():
            return
        tags_text, ok = QInputDialog.getText(
            self, "Save sketch asset", "Tags (comma separated, optional):"
        )
        if not ok:
            return
        tags = [t.strip() for t in tags_text.split(",") if t.strip()]
        try:
            asset = self._capture(name.strip(), tags)
            try:
                path = self._library.save(asset)
            except FileExistsError as exc:
                answer = QMessageBox.question(self, "Replace asset?", f"{exc}\n\nReplace it?")
                if answer != QMessageBox.StandardButton.Yes:
                    return
                path = self._library.save(asset, overwrite=True)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Could not save asset", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "Sketch Library", f"Saved {path}")

    def _load_selected(self) -> None:
        asset = self.selected_asset()
        if asset is not None and self._load(asset):
            self.accept()

    def _export(self, kind: str) -> None:
        asset = self.selected_asset()
        if asset is None:
            return
        suffix = {"dxf": "DXF R12 (*.dxf)", "svg": "SVG (*.svg)"}[kind]
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {kind.upper()}", f"{asset_key(asset.name)}.{kind}", suffix
        )
        if not path:
            return
        try:
            (export_dxf if kind == "dxf" else export_svg)(asset, path)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Sketch Library", f"Exported {path}")
