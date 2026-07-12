"""Stages 5-7 tests: B-Rep building, validation, STEP round-trip."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from mra.core import Tolerances
from mra.export import export_step, import_step
from mra.intent import recover_intent
from mra.recognition import segment_mesh
from mra.reconstruction import build_solid, shape_to_trimesh
from mra.reconstruction.profiles import (
    boundary_loops_3d,
    loop_is_circle,
    simplify_loop,
)
from mra.validation import validate_shape


def plate_with_holes(hole_xy, plate=(60.0, 40.0, 5.0), radius=2.0):
    box = trimesh.creation.box(extents=plate)
    drills = []
    for x, y in hole_xy:
        c = trimesh.creation.cylinder(radius=radius, height=plate[2] * 3,
                                      sections=48)
        c.apply_translation([x, y, 0])
        drills.append(c)
    if not drills:
        return box
    return trimesh.boolean.difference([box, *drills])


def reconstruct(mesh, tol: Tolerances | None = None):
    seg = segment_mesh(mesh, tol)
    intent = recover_intent(mesh, seg, tol)
    return build_solid(mesh, seg, intent, tol)


# ---------------------------------------------------------------- profiles

class TestProfiles:
    def test_box_cap_loop(self) -> None:
        box = trimesh.creation.box(extents=(20, 30, 6))
        # Bottom cap faces: normal (0,0,-1)
        bottom = np.flatnonzero(box.face_normals[:, 2] < -0.99)
        loops = boundary_loops_3d(box, bottom)
        assert len(loops) == 1
        simplified = simplify_loop(loops[0])
        assert len(simplified) == 4  # rectangle

    def test_circle_detection(self) -> None:
        theta = np.linspace(0, 2 * np.pi, 48, endpoint=False)
        pts = np.column_stack(
            [5 * np.cos(theta), 5 * np.sin(theta), np.zeros(48)]
        )
        hit = loop_is_circle(pts)
        assert hit is not None
        center, radius = hit
        assert radius == pytest.approx(5.0, abs=1e-9)

    def test_rectangle_is_not_circle(self) -> None:
        rect = np.array(
            [[0, 0, 0], [10, 0, 0], [10, 4, 0], [0, 4, 0]], dtype=float
        )
        assert loop_is_circle(rect) is None


# ------------------------------------------------------------------ build

class TestBuild:
    def test_plain_box(self) -> None:
        box = trimesh.creation.box(extents=(30, 20, 8))
        result = reconstruct(box)
        assert result.shape is not None
        report = validate_shape(result.shape)
        assert report.is_valid
        assert report.solid_count == 1
        assert report.volume == pytest.approx(30 * 20 * 8, rel=1e-6)

    def test_plate_with_one_hole(self) -> None:
        mesh = plate_with_holes([(0.0, 0.0)], radius=2.0)
        result = reconstruct(mesh)
        assert result.shape is not None
        report = validate_shape(result.shape)
        assert report.is_valid
        expected = 60 * 40 * 5 - np.pi * 4 * 5
        assert report.volume == pytest.approx(expected, rel=0.005)

    def test_plate_with_hole_row(self) -> None:
        mesh = plate_with_holes([(-15, 0), (0, 0), (15, 0)], radius=2.0)
        result = reconstruct(mesh)
        assert result.shape is not None
        report = validate_shape(result.shape, reference_volume=mesh.volume)
        assert report.ready_for_export
        assert not any("Volume differs" in p for p in report.problems)

    def test_boss_fused(self) -> None:
        plate = trimesh.creation.box(extents=(40, 40, 4))
        boss = trimesh.creation.cylinder(radius=4.0, height=10.0, sections=48)
        boss.apply_translation([0, 0, 2 + 5])
        mesh = trimesh.boolean.union([plate, boss])
        result = reconstruct(mesh)
        assert result.shape is not None
        report = validate_shape(result.shape)
        assert report.is_valid
        expected = 40 * 40 * 4 + np.pi * 16 * 10
        assert report.volume == pytest.approx(expected, rel=0.005)

    def test_preview_tessellation(self) -> None:
        mesh = plate_with_holes([(0.0, 0.0)])
        result = reconstruct(mesh)
        preview = shape_to_trimesh(result.shape)
        assert len(preview.faces) > 0
        # Preview must be in the same place as the evidence.
        assert np.allclose(preview.bounds, mesh.bounds, atol=0.5)


def open_enclosure(outer=(60.0, 40.0, 20.0), wall=2.0, floor=3.0):
    """An open-top box enclosure (the primary target part class)."""
    box = trimesh.creation.box(extents=outer)
    cavity = trimesh.creation.box(
        extents=(outer[0] - 2 * wall, outer[1] - 2 * wall,
                 outer[2] - floor)
    )
    # Sink the cavity so it opens through the top face.
    cavity.apply_translation([0, 0, floor / 2 + 1e-3])
    hollow = trimesh.boolean.difference([box, cavity])
    return hollow


class TestSteppedPlate:
    """Terrace pockets: the CNC backplate part class."""

    def test_border_lip_plate(self) -> None:
        # A plate whose center is 5 mm thick but whose border is a 2 mm
        # lip (like a backplate seating into an enclosure rim): full
        # 40x30 silhouette, center pad 30x20 extends further down.
        slab = trimesh.creation.box(extents=(40, 30, 2))
        slab.apply_translation([0, 0, 1.0])   # z 0..2
        pad = trimesh.creation.box(extents=(30, 20, 5))
        pad.apply_translation([0, 0, 2.5 - 5])  # z -2.5..2.5 -> union
        mesh = trimesh.boolean.union([slab, pad])
        result = reconstruct(mesh)
        assert result.shape is not None, result.log
        assert any("pocket" in ln.lower() for ln in result.log), result.log
        report = validate_shape(result.shape,
                                reference_volume=float(mesh.volume))
        assert report.is_valid
        assert report.volume == pytest.approx(float(mesh.volume), rel=0.01)

    def test_raised_pad(self) -> None:
        # A plate with a rectangular plateau on top (the rear-panel case).
        plate = trimesh.creation.box(extents=(50, 30, 3))
        plateau = trimesh.creation.box(extents=(20, 12, 2))
        plateau.apply_translation([5, 3, 2.5])  # sits on top, off-centre
        mesh = trimesh.boolean.union([plate, plateau])
        result = reconstruct(mesh)
        assert result.shape is not None, result.log
        assert any("pad" in ln.lower() for ln in result.log), result.log
        report = validate_shape(result.shape,
                                reference_volume=float(mesh.volume))
        assert report.is_valid
        assert report.volume == pytest.approx(float(mesh.volume), rel=0.01)

    def test_recessed_panel(self) -> None:
        # A plate with a shallow recessed rectangle on top (0.5 mm deep).
        plate = trimesh.creation.box(extents=(50, 30, 4))
        recess = trimesh.creation.box(extents=(30, 16, 1.0))
        recess.apply_translation([0, 0, 2.0])  # sinks 0.5 into the top
        mesh = trimesh.boolean.difference([plate, recess])
        result = reconstruct(mesh)
        assert result.shape is not None, result.log
        report = validate_shape(result.shape,
                                reference_volume=float(mesh.volume))
        assert report.is_valid
        assert report.volume == pytest.approx(float(mesh.volume), rel=0.01)


class TestEnclosure:
    def test_open_box_cavity(self) -> None:
        mesh = open_enclosure()
        result = reconstruct(mesh)
        assert result.shape is not None, result.log
        report = validate_shape(result.shape,
                                reference_volume=float(mesh.volume))
        assert report.is_valid
        assert report.ready_for_export
        # Volume must match the hollow shell, not the solid block.
        assert report.volume == pytest.approx(float(mesh.volume), rel=0.01)

    def test_enclosure_with_bosses(self) -> None:
        mesh = open_enclosure()
        bosses = []
        for x, y in [(-22, -12), (22, -12), (-22, 12), (22, 12)]:
            b = trimesh.creation.cylinder(radius=3.0, height=8.0, sections=48)
            # Floor top is at z = -10 + 3 = -7; boss stands on it.
            b.apply_translation([x, y, -7 + 4])
            bosses.append(b)
        mesh = trimesh.boolean.union([mesh, *bosses])
        result = reconstruct(mesh)
        assert result.shape is not None, result.log
        report = validate_shape(result.shape,
                                reference_volume=float(mesh.volume))
        assert report.is_valid
        assert report.volume == pytest.approx(float(mesh.volume), rel=0.01)

    def test_screw_boss_keeps_pilot_hole(self) -> None:
        # Boss with a concentric through-hole: the hole must be cut AFTER
        # the boss is fused, or the fuse fills it back in.
        plate = trimesh.creation.box(extents=(40, 40, 4))
        boss = trimesh.creation.cylinder(radius=4.0, height=8.0, sections=48)
        boss.apply_translation([0, 0, 2 + 4])
        solid = trimesh.boolean.union([plate, boss])
        drill = trimesh.creation.cylinder(radius=1.5, height=40, sections=48)
        mesh = trimesh.boolean.difference([solid, drill])
        result = reconstruct(mesh)
        assert result.shape is not None, result.log
        report = validate_shape(result.shape,
                                reference_volume=float(mesh.volume))
        assert report.is_valid
        assert report.volume == pytest.approx(float(mesh.volume), rel=0.01)

    def test_usb_cutout_in_side_wall(self) -> None:
        mesh = open_enclosure()  # walls 2 mm, outer 60x40x20
        # Rectangular opening through the front wall (y = -20 face),
        # 12 wide x 6 tall, fully interior to the wall face.
        cut = trimesh.creation.box(extents=(12, 8, 6))
        cut.apply_translation([0, -19, -2])
        mesh = trimesh.boolean.difference([mesh, cut])
        result = reconstruct(mesh)
        assert result.shape is not None, result.log
        assert any("wall opening" in ln.lower() for ln in result.log), \
            result.log
        report = validate_shape(result.shape,
                                reference_volume=float(mesh.volume))
        assert report.is_valid
        assert report.volume == pytest.approx(float(mesh.volume), rel=0.01)

    def test_enclosure_step_roundtrip(self, tmp_path: Path) -> None:
        mesh = open_enclosure()
        result = reconstruct(mesh)
        step_file = tmp_path / "enclosure.step"
        export_step(result.shape, step_file)
        reimported = validate_shape(import_step(step_file)).volume
        assert reimported == pytest.approx(float(mesh.volume), rel=0.01)


class TestSheetVersion:
    """Flat sheet-metal build: profile + through-holes, no depth."""

    def test_stepped_plate_flattens(self) -> None:
        from mra.reconstruction import build_sheet

        # Stepped plate with a through-hole: the sheet must be a flat
        # plate of exactly the requested thickness, hole preserved.
        slab = trimesh.creation.box(extents=(40, 30, 2))
        slab.apply_translation([0, 0, 1.0])
        pad = trimesh.creation.box(extents=(30, 20, 5))
        pad.apply_translation([0, 0, 2.5 - 5])
        mesh = trimesh.boolean.union([slab, pad])
        drill = trimesh.creation.cylinder(radius=2.0, height=20, sections=48)
        drill.apply_translation([5, 5, 0])
        mesh = trimesh.boolean.difference([mesh, drill])

        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        result = build_sheet(mesh, seg, intent, thickness=3.0)
        assert result.shape is not None, result.log
        report = validate_shape(result.shape)
        assert report.is_valid and report.solid_count == 1
        # Volume = footprint area x thickness - the hole.
        footprint = 40 * 30  # pad footprint lies inside the slab's
        expected = (footprint - np.pi * 4) * 3.0
        assert report.volume == pytest.approx(expected, rel=0.02)

    def test_default_thickness_from_wall(self) -> None:
        from mra.reconstruction import build_sheet

        mesh = plate_with_holes([(0.0, 0.0)])
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        result = build_sheet(mesh, seg, intent)  # no thickness given
        assert result.shape is not None
        assert any("Flat sheet" in ln for ln in result.log)


# ------------------------------------------------------------- validation

class TestValidation:
    def test_reports_reference_volume_mismatch(self) -> None:
        box = trimesh.creation.box(extents=(30, 20, 8))
        result = reconstruct(box)
        report = validate_shape(result.shape, reference_volume=9999.0)
        assert any("Volume differs" in p for p in report.problems)

    def test_disjoint_solids_fail_export_gate(self) -> None:
        from OCP.BRep import BRep_Builder
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.TopoDS import TopoDS_Compound

        builder = BRep_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        builder.Add(compound, BRepPrimAPI_MakeBox(10, 10, 10).Shape())
        b2 = BRepPrimAPI_MakeBox(5, 5, 5).Shape()
        from OCP.gp import gp_Trsf, gp_Vec
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

        t = gp_Trsf()
        t.SetTranslation(gp_Vec(50, 0, 0))
        builder.Add(compound, BRepBuilderAPI_Transform(b2, t).Shape())

        report = validate_shape(compound)
        assert report.solid_count == 2
        assert not report.ready_for_export
        assert any("disjoint" in p.lower() for p in report.problems)


# ------------------------------------------------------------- round trip

class TestStepRoundTrip:
    @pytest.mark.parametrize("schema", ["AP214", "AP242"])
    def test_export_and_reimport(self, tmp_path: Path, schema: str) -> None:
        mesh = plate_with_holes([(-15, 0), (15, 0)], radius=2.0)
        result = reconstruct(mesh)
        step_file = tmp_path / f"part_{schema}.step"
        export_step(result.shape, step_file, schema=schema)

        assert step_file.exists()
        head = step_file.read_text(errors="ignore")[:200]
        assert "ISO-10303-21" in head

        # Round-trip: volume must survive.
        original = validate_shape(result.shape).volume
        reimported = validate_shape(import_step(step_file)).volume
        assert reimported == pytest.approx(original, rel=1e-6)

    def test_step_is_analytic_not_tessellated(self, tmp_path: Path) -> None:
        mesh = plate_with_holes([(0.0, 0.0)], radius=2.0)
        result = reconstruct(mesh)
        step_file = tmp_path / "part.step"
        export_step(result.shape, step_file)
        text = step_file.read_text(errors="ignore")
        # Analytic hole → CYLINDRICAL_SURFACE entity; a tessellated export
        # would instead carry thousands of CARTESIAN_POINTs and no
        # analytic surfaces.
        assert "CYLINDRICAL_SURFACE" in text
        assert text.count("CARTESIAN_POINT") < 500
