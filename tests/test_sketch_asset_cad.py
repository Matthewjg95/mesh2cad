"""Sketch assets <-> OpenCascade: load into the workflow and capture from it."""

from __future__ import annotations

import math

import pytest
import trimesh

from mra.export import export_step
from mra.intent import recover_intent
from mra.recognition import segment_mesh
from mra.reconstruction import build_sheet
from mra.sketch_assets import Arc, Circle, Line, Polygon, Profile, SketchAsset, SketchLibrary
from mra.sketch_assets.cad import UnsupportedGeometryError, asset_to_plate, shape_to_asset
from mra.sketch_assets.examples import device_outline, mounting_hole_pattern, rounded_plate
from mra.validation import validate_shape


def _area(asset: SketchAsset, role: str) -> float:
    """Exact enclosed area of all primitives with ``role``."""
    total = 0.0
    for p in [*asset.polygons, *asset.profiles]:
        if p.role != role:
            continue
        if isinstance(p, Polygon):
            p = Profile(tuple(Line(a, b) for a, b in
                              zip(p.points, p.points[1:] + p.points[:1])))
        total += abs(p.signed_area())
    for c in asset.circles:
        if c.role == role:
            total += math.pi * (c.diameter / 2) ** 2
    return total


def test_rounded_plate_builds_exact_volume():
    asset = rounded_plate()
    plate = asset_to_plate(asset, 3.0)
    report = validate_shape(plate.shape)
    assert report.is_valid and report.solid_count == 1
    expected = (_area(asset, "outline") - _area(asset, "cutout")
                - _area(asset, "mounting-hole")) * 3.0
    assert report.volume == pytest.approx(expected, rel=1e-6)


def test_inch_asset_is_converted_explicitly_and_keep_out_not_cut():
    asset = device_outline()
    plate = asset_to_plate(asset, 2.0)
    assert any("x25.4" in line for line in plate.log)
    assert any("Not cut" in line and "keep-out" in line for line in plate.log)
    expected = (_area(asset, "outline") - _area(asset, "mounting-hole")) * 25.4**2 * 2.0
    assert validate_shape(plate.shape).volume == pytest.approx(expected, rel=1e-6)
    assert asset == device_outline()  # the asset itself is untouched


@pytest.mark.parametrize(
    "asset, thickness, message",
    [
        (mounting_hole_pattern(), 3.0, "exactly one 'outline'"),
        (SketchAsset(name="two", circles=[Circle((0.0, 0.0), 5.0, role="outline"),
                                          Circle((9.0, 0.0), 5.0, role="outline")]),
         3.0, "exactly one 'outline'"),
        (rounded_plate(), 0.0, "positive"),
        (rounded_plate(), float("nan"), "positive"),
    ],
)
def test_plate_build_rejects_unbuildable_requests(asset, thickness, message):
    with pytest.raises(ValueError, match=message):
        asset_to_plate(asset, thickness)


def test_capture_round_trip_keeps_arcs_and_circles():
    original = rounded_plate()
    captured = shape_to_asset(asset_to_plate(original, 3.0).shape, "captured",
                              provenance={"source": "test"})
    assert captured.units == "mm" and captured.schema_version == "1.1"
    assert captured.provenance["source"] == "test"
    plane = captured.provenance["sketch_plane"]
    assert plane["normal"] == pytest.approx([0.0, 0.0, 1.0])
    assert plane["x_axis"] == pytest.approx([1.0, 0.0, 0.0])

    assert sorted((round(c.center[0], 9), round(c.center[1], 9), round(c.diameter, 9))
                  for c in captured.circles) == sorted(
        (c.center[0], c.center[1], c.diameter) for c in original.circles)
    outline = next(p for p in captured.profiles if p.role == "outline")
    arcs = [s for s in outline.segments if isinstance(s, Arc)]
    assert len(arcs) == 4 and all(a.radius == pytest.approx(6.0) for a in arcs)
    assert abs(outline.signed_area()) == pytest.approx(abs(original.profiles[0].signed_area()))
    slot = next(p for p in captured.profiles if p.role == "cutout")
    assert abs(slot.signed_area()) == pytest.approx(original.profiles[1].signed_area())

    # Captured asset persists and rebuilds to the same solid.
    rebuilt = asset_to_plate(captured, 3.0).shape
    assert validate_shape(rebuilt).volume == pytest.approx(
        validate_shape(asset_to_plate(original, 3.0).shape).volume, rel=1e-9)


def test_capture_rejects_non_analytic_edges():
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                    BRepBuilderAPI_MakeWire)
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Ax2, gp_Dir, gp_Elips, gp_Pnt, gp_Vec

    ellipse = gp_Elips(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 10.0, 5.0)
    wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(ellipse).Edge()).Wire()
    solid = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire).Face(),
                                  gp_Vec(0, 0, 2)).Shape()
    with pytest.raises(UnsupportedGeometryError, match="only lines and circular arcs"):
        shape_to_asset(solid, "ellipse")


def test_sheet_version_footprint_becomes_an_asset(tmp_path):
    """Mesh -> Sheet Version -> asset -> library -> reload -> STEP."""
    box = trimesh.creation.box(extents=(60.0, 40.0, 5.0))
    drills = []
    for x, y in ((-20.0, -10.0), (20.0, 10.0)):
        c = trimesh.creation.cylinder(radius=2.0, height=20.0, sections=48)
        c.apply_translation([x, y, 0.0])
        drills.append(c)
    mesh = trimesh.boolean.difference([box, *drills])
    seg = segment_mesh(mesh)
    sheet = build_sheet(mesh, seg, recover_intent(mesh, seg), thickness=3.0)
    assert sheet.shape is not None, sheet.log

    asset = shape_to_asset(sheet.shape, "Sheet capture", metadata={"tags": ["test"]})
    assert len(asset.circles) == 2
    assert all(c.diameter == pytest.approx(4.0, abs=0.05) and c.role == "hole"
               for c in asset.circles)
    outline = [*asset.polygons, *asset.profiles]
    assert len(outline) == 1 and outline[0].role == "outline"
    if isinstance(outline[0], Polygon):
        xs = [p[0] for p in outline[0].points]
        ys = [p[1] for p in outline[0].points]
        assert max(xs) - min(xs) == pytest.approx(60.0, abs=0.05)
        assert max(ys) - min(ys) == pytest.approx(40.0, abs=0.05)

    library = SketchLibrary(tmp_path / "lib")
    library.save(asset)
    reloaded = library.load("sheet-capture")
    assert reloaded == asset
    plate = asset_to_plate(reloaded, 3.0)
    assert validate_shape(plate.shape).volume == pytest.approx(
        validate_shape(sheet.shape).volume, rel=1e-3)

    step = tmp_path / "plate.step"
    export_step(plate.shape, step)
    assert "CIRCLE" in step.read_text()


def test_loaded_rounded_plate_survives_step_as_analytic_geometry(tmp_path):
    """Asset -> plate -> STEP -> re-import -> asset keeps true arcs/circles.

    (OCC also writes 2D parameter-space pcurves as B_SPLINE_CURVE entities;
    those are not model geometry, so the check is on re-imported edges.)
    """
    from mra.export import import_step

    step = tmp_path / "rounded.step"
    export_step(asset_to_plate(rounded_plate(), 3.0).shape, step)
    text = step.read_text()
    assert text.count("CYLINDRICAL_SURFACE") >= 10  # 4 corners + 4 holes + 2 slot ends
    recaptured = shape_to_asset(import_step(step), "from step")
    assert len(recaptured.circles) == 4
    assert all(c.diameter == pytest.approx(3.4, abs=1e-6) for c in recaptured.circles)
    outline = next(p for p in recaptured.profiles if p.role == "outline")
    assert sum(isinstance(s, Arc) for s in outline.segments) == 4
