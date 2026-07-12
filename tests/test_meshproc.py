"""Stage 1 tests: loader, stats, diagnose, repair.

All test meshes are built synthetically so the suite needs no fixture files.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh

from mra.core import Tolerances
from mra.meshproc import (
    body_infos,
    compute_stats,
    diagnose,
    load_mesh,
    repair,
    split_bodies,
)


def make_box(extents=(10.0, 20.0, 5.0)) -> trimesh.Trimesh:
    """A clean watertight box."""
    return trimesh.creation.box(extents=extents)


def make_box_with_duplicate_vertices() -> trimesh.Trimesh:
    """Box whose faces share no vertices (like a raw STL: 3 verts/triangle)."""
    box = make_box()
    tri_verts = box.vertices[box.faces].reshape(-1, 3)
    faces = np.arange(len(tri_verts)).reshape(-1, 3)
    return trimesh.Trimesh(vertices=tri_verts, faces=faces, process=False)


def make_box_with_hole() -> trimesh.Trimesh:
    """Box with one triangle removed (small hole, open boundary)."""
    box = make_box()
    faces = box.faces[1:]  # drop one triangle
    m = trimesh.Trimesh(vertices=box.vertices, faces=faces, process=False)
    assert not m.is_watertight
    return m


def make_box_with_debris() -> trimesh.Trimesh:
    """Box plus a tiny far-away fragment (scan debris)."""
    box = make_box()
    debris = trimesh.creation.box(extents=(0.05, 0.05, 0.05))
    debris.apply_translation([100.0, 100.0, 100.0])
    return trimesh.util.concatenate([box, debris])


def make_box_with_flipped_faces() -> trimesh.Trimesh:
    """Box with a few faces wound backwards."""
    box = make_box()
    faces = box.faces.copy()
    faces[:3] = faces[:3][:, ::-1]
    return trimesh.Trimesh(vertices=box.vertices, faces=faces, process=False)


# ---------------------------------------------------------------- loader

class TestLoader:
    def test_load_stl_roundtrip(self, tmp_path: Path) -> None:
        box = make_box()
        stl = tmp_path / "box.stl"
        box.export(stl)
        loaded = load_mesh(stl)
        assert len(loaded.faces) == len(box.faces)

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_mesh("does_not_exist.stl")

    def test_binary_stl_has_raw_duplicate_vertices(self, tmp_path: Path) -> None:
        # STL stores 3 verts per triangle; process=False must preserve that
        # so the repair report can count the welds.
        box = make_box()
        stl = tmp_path / "box.stl"
        box.export(stl)
        loaded = load_mesh(stl)
        assert len(loaded.vertices) == 3 * len(loaded.faces)


# ---------------------------------------------------------------- stats

class TestStats:
    def test_clean_box(self) -> None:
        s = compute_stats(make_box())
        assert s.face_count == 12
        assert s.is_watertight
        assert s.volume == pytest.approx(10 * 20 * 5)
        assert s.extents == pytest.approx((10.0, 20.0, 5.0))
        assert s.body_count == 1

    def test_open_mesh_reports_zero_volume(self) -> None:
        s = compute_stats(make_box_with_hole())
        assert not s.is_watertight
        assert s.volume == 0.0


# ---------------------------------------------------------------- diagnose

class TestDiagnose:
    def test_clean_box_is_clean(self) -> None:
        r = diagnose(make_box())
        assert r.nonmanifold_edges_before == 0
        assert r.holes_remaining == 0
        assert r.watertight_after

    def test_hole_detected(self) -> None:
        r = diagnose(make_box_with_hole())
        assert r.holes_remaining == 1
        assert not r.watertight_after

    def test_multibody_detected(self) -> None:
        r = diagnose(make_box_with_debris())
        assert r.components_before == 2


# ---------------------------------------------------------------- repair

class TestRepair:
    def test_welds_duplicate_vertices(self) -> None:
        m, report = repair(make_box_with_duplicate_vertices())
        assert report.duplicate_vertices_merged > 0
        assert len(m.vertices) == 8
        assert m.is_watertight
        assert report.watertight_after

    def test_fills_small_hole(self) -> None:
        # One missing triangle on a 10x20 face has a ~52 mm perimeter, so
        # raise the small-hole threshold to cover it for this test.
        tol = Tolerances(hole_perimeter_max=60.0)
        m, report = repair(make_box_with_hole(), tol)
        assert report.holes_filled == 1
        assert report.holes_remaining == 0
        assert m.is_watertight

    def test_removes_debris(self) -> None:
        m, report = repair(make_box_with_debris())
        assert report.components_removed == 1
        assert m.body_count == 1
        assert m.volume == pytest.approx(1000.0, rel=1e-3)

    def test_fixes_winding(self) -> None:
        m, report = repair(make_box_with_flipped_faces())
        assert m.is_winding_consistent
        assert m.is_watertight
        # Volume positive means normals point outward after fix_inversion.
        assert m.volume == pytest.approx(1000.0, rel=1e-3)

    def test_input_not_modified(self) -> None:
        broken = make_box_with_duplicate_vertices()
        n_before = len(broken.vertices)
        repair(broken)
        assert len(broken.vertices) == n_before

    def test_clean_mesh_unchanged(self) -> None:
        m, report = repair(make_box())
        assert report.duplicate_vertices_merged == 0
        assert report.degenerate_faces_removed == 0
        assert m.volume == pytest.approx(1000.0, rel=1e-3)

    def test_report_summary_lines(self) -> None:
        _, report = repair(make_box())
        lines = report.summary_lines()
        assert any("watertight" in ln.lower() for ln in lines)


# ---------------------------------------------------------------- bodies

class TestSplitBodies:
    def test_two_boxes_split_largest_first(self) -> None:
        big = trimesh.creation.box(extents=(20, 20, 20))
        small = trimesh.creation.box(extents=(5, 5, 5))
        small.apply_translation([50, 0, 0])
        combined = trimesh.util.concatenate([big, small])
        bodies = split_bodies(combined)
        assert len(bodies) == 2
        assert bodies[0].area > bodies[1].area
        assert bodies[0].volume == pytest.approx(8000.0, rel=1e-3)

    def test_unwelded_stl_is_not_shattered(self) -> None:
        # Raw STL vertex duplication must not make every triangle a body.
        broken = make_box_with_duplicate_vertices()
        bodies = split_bodies(broken)
        assert len(bodies) == 1
        assert len(bodies[0].faces) == 12

    def test_single_body_returns_itself(self) -> None:
        bodies = split_bodies(make_box())
        assert len(bodies) == 1

    def test_body_infos_labels(self) -> None:
        big = trimesh.creation.box(extents=(20, 10, 5))
        infos = body_infos(split_bodies(big))
        assert len(infos) == 1
        assert "20.0 x 10.0 x 5.0 mm" in infos[0].label()
