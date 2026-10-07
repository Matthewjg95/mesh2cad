"""Offscreen smoke test of the Sketch Library GUI integration."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mra.sketch_assets import SketchLibrary  # noqa: E402
from mra.sketch_assets.examples import all_examples, device_outline, rounded_plate  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_dialog_browses_searches_and_previews(app, tmp_path):
    from mra.gui.sketch_library import SketchLibraryDialog

    library = SketchLibrary(tmp_path)
    for asset in all_examples():
        library.save(asset)
    (tmp_path / "broken.sketch.json").write_text("{")
    dialog = SketchLibraryDialog(capture=None, load=lambda a: False, library=library)
    assert dialog._list.count() == 3
    assert "1 unreadable" in dialog._problems.text()
    assert dialog._preview.renderer().isValid()
    dialog._query.setText("device")
    assert dialog._list.count() == 1
    assert dialog.selected_asset() == device_outline()
    dialog._query.setText("")
    dialog._tags.setText("plate, rounded-rectangle")
    assert [dialog._list.item(i).text() for i in range(dialog._list.count())] == [
        rounded_plate().name
    ]
    assert not dialog._btn_save.isEnabled()


def test_main_window_loads_and_captures_assets(app, monkeypatch):
    from mra.gui import app as app_module

    window = app_module.MainWindow()
    monkeypatch.setattr(QtWidgets.QInputDialog, "getDouble",
                        staticmethod(lambda *a, **k: (2.5, True)))
    assert window._load_sketch_asset(device_outline())
    assert window._build is not None and window._build.shape is not None
    assert window._btn_export.isEnabled() and window._btn_validate.isEnabled()
    assert not window._btn_recognize.isEnabled()
    assert "x25.4" in window.statusBar().currentMessage()

    captured = window._capture_sketch_asset("Round trip", ["demo"])
    assert captured.units == "mm" and captured.tags == ["demo"]
    assert sorted(round(c.diameter, 9) for c in captured.circles) == [3.175, 3.175]
    window.close()
