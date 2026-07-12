"""Stage 2 tests: analytic fits and mesh segmentation on synthetic shapes."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from mra.core import SurfaceType, Tolerances
from mra.recognition import (
    fit_cone,
    fit_cylinder,
    fit_plane,
    fit_sphere,
    segment_mesh,
)

RNG = np.random.default_rng(42)


# ------------------------------------------------------------------ fits

class TestFitPlane:
    def test_exact_plane(self) -> None:
        pts = RNG.uniform(-10, 10, (200, 3))
        pts[:, 2] = 5.0
        fit = fit_plane(pts)
        assert fit.rms < 1e-9
        assert abs(fit.params["normal"][2]) == pytest.approx(1.0)

    def test_noisy_plane(self) -> None:
        pts = RNG.uniform(-10, 10, (500, 3))
        pts[:, 2] = 2.0 + RNG.normal(0, 0.01, 500)
        fit = fit_plane(pts)
        assert fit.rms < 0.02
        assert fit.inlier_ratio(0.05) > 0.95


class TestFitCylinder:
    @staticmethod
    def _cylinder_points(radius=4.0, height=20.0, n=400,
                         axis=np.array([0.0, 0.0, 1.0]),
                         origin=np.zeros(3)):
        theta = RNG.uniform(0, 2 * np.pi, n)
        z = RNG.uniform(-height / 2, height / 2, n)
        pts_local = np.column_stack(
            [radius * np.cos(theta), radius * np.sin(theta), z]
        )
        normals_local = np.column_stack(
            [np.cos(theta), np.sin(theta), np.zeros(n)]
        )
        rot = trimesh.geometry.align_vectors([0, 0, 1], axis)[:3, :3]
        return pts_local @ rot.T + origin, normals_local @ rot.T

    def test_exact_cylinder(self) -> None:
        pts, normals = self._cylinder_points()
        fit = fit_cylinder(pts, normals)
        assert fit.params["radius"] == pytest.approx(4.0, abs=1e-6)
        assert abs(fit.params["axis"][2]) == pytest.approx(1.0, abs=1e-6)
        assert fit.rms < 1e-6

    def test_tilted_cylinder(self) -> None:
        axis = np.array([1.0, 1.0, 1.0]) / np.sqrt(3)
        origin = np.array([5.0, -3.0, 2.0])
        pts, normals = self._cylinder_points(radius=2.5, axis=axis,
                                             origin=origin)
        fit = fit_cylinder(pts, normals)
        assert fit.params["radius"] == pytest.approx(2.5, abs=1e-5)
        assert abs(fit.params["axis"] @ axis) == pytest.approx(1.0, abs=1e-5)

    def test_noisy_cylinder(self) -> None:
        pts, normals = self._cylinder_points(radius=3.0)
        pts += RNG.normal(0, 0.02, pts.shape)
        fit = fit_cylinder(pts, normals)
        assert fit.params["radius"] == pytest.approx(3.0, abs=0.02)
        assert fit.inlier_ratio(0.08) > 0.95


class TestFitSphere:
    def test_exact_sphere(self) -> None:
        dirs = RNG.normal(size=(300, 3))
        dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
        center = np.array([1.0, 2.0, 3.0])
        pts = center + 7.5 * dirs
        fit = fit_sphere(pts)
        assert fit.params["radius"] == pytest.approx(7.5, abs=1e-9)
        assert fit.params["center"] == pytest.approx(center, abs=1e-9)


class TestFitCone:
    def test_exact_cone(self) -> None:
        half_angle = np.radians(20.0)
        apex = np.array([0.0, 0.0, 10.0])
        axis = np.array([0.0, 0.0, -1.0])  # opens downward
        theta = RNG.uniform(0, 2 * np.pi, 400)
        along = RNG.uniform(2.0, 12.0, 400)  # distance from apex along axis
        radius = along * np.tan(half_angle)
        pts = np.column_stack(
            [radius * np.cos(theta), radius * np.sin(theta),
             apex[2] - along]
        )
        # Outward normals of a downward-opening cone.
        normals = np.column_stack(
            [np.cos(theta) * np.cos(half_angle),
             np.sin(theta) * np.cos(half_angle),
             np.full(400, np.sin(half_angle))]
        )
        fit = fit_cone(pts, normals)
        assert fit.params["half_angle"] == pytest.approx(half_angle, abs=1e-4)
        assert fit.params["apex"] == pytest.approx(apex, abs=1e-3)
        assert fit.rms < 1e-5


# --------------------------------------------------------------- segment

class TestSegmentation:
    def test_box_gives_six_planes(self) -> None:
        box = trimesh.creation.box(extents=(20, 30, 10))
        # Subdivide so regions have enough faces to be meaningful.
        box = box.subdivide().subdivide()
        result = segment_mesh(box)
        planes = [p for p in result.patches
                  if p.surface_type == SurfaceType.PLANE]
        assert len(planes) == 6
        assert result.coverage() == pytest.approx(1.0)
        for p in planes:
            assert p.confidence.value > 0.95

    def test_box_plane_normals_axis_aligned(self) -> None:
        box = trimesh.creation.box(extents=(20, 30, 10)).subdivide()
        result = segment_mesh(box)
        normals = np.array(
            [p.params["normal"] for p in result.patches
             if p.surface_type == SurfaceType.PLANE]
        )
        # Every plane normal should be within a whisker of a global axis.
        alignment = np.max(np.abs(normals), axis=1)
        assert np.all(alignment > 0.9999)

    def test_cylinder_primitive(self) -> None:
        cyl = trimesh.creation.cylinder(radius=5.0, height=20.0, sections=64)
        result = segment_mesh(cyl)
        types = [p.surface_type for p in result.patches]
        assert types.count(SurfaceType.PLANE) == 2  # two caps
        cylinders = [p for p in result.patches
                     if p.surface_type == SurfaceType.CYLINDER]
        assert len(cylinders) == 1
        assert cylinders[0].params["radius"] == pytest.approx(5.0, rel=0.01)

    def test_annulus_inner_and_outer_walls(self) -> None:
        ann = trimesh.creation.annulus(r_min=3.0, r_max=6.0, height=8.0,
                                       sections=64)
        result = segment_mesh(ann)
        cylinders = sorted(
            (p for p in result.patches
             if p.surface_type == SurfaceType.CYLINDER),
            key=lambda p: p.params["radius"],
        )
        assert len(cylinders) == 2
        assert cylinders[0].params["radius"] == pytest.approx(3.0, rel=0.01)
        assert cylinders[1].params["radius"] == pytest.approx(6.0, rel=0.01)

    def test_sphere_primitive(self) -> None:
        sph = trimesh.creation.icosphere(subdivisions=3, radius=4.0)
        result = segment_mesh(sph)
        spheres = [p for p in result.patches
                   if p.surface_type == SurfaceType.SPHERE]
        assert len(spheres) == 1
        assert spheres[0].params["radius"] == pytest.approx(4.0, rel=0.02)

    def test_face_ids_match_patches(self) -> None:
        box = trimesh.creation.box(extents=(10, 10, 10)).subdivide()
        result = segment_mesh(box)
        for pid, patch in enumerate(result.patches):
            assert np.all(result.face_patch_ids[patch.face_indices] == pid)


class TestMerge:
    """Fragmented same-surface regions must merge back into one patch."""

    def test_split_plane_regions_merge(self) -> None:
        from mra.recognition.segmentation import (
            _MeshData,
            _merge_compatible_regions,
            _plane_of,
        )
        from mra.core import Tolerances

        box = trimesh.creation.box(extents=(20, 20, 6)).subdivide()
        md = _MeshData.from_mesh(box)
        top = np.flatnonzero(md.face_normals[:, 2] > 0.99)
        half_a, half_b = top[: len(top) // 2], top[len(top) // 2:]
        others = np.flatnonzero(md.face_normals[:, 2] <= 0.99)
        regions = [
            (half_a, _plane_of(md, half_a)),
            (half_b, _plane_of(md, half_b)),
            (others, None),  # freeform filler, must not merge
        ]
        merged = _merge_compatible_regions(md, regions, Tolerances())
        plane_regions = [r for r in merged if r[1] is not None]
        assert len(plane_regions) == 1
        assert len(plane_regions[0][0]) == len(top)

    def test_perpendicular_planes_do_not_merge(self) -> None:
        from mra.recognition.segmentation import (
            _MeshData,
            _merge_compatible_regions,
            _plane_of,
        )
        from mra.core import Tolerances

        box = trimesh.creation.box(extents=(20, 20, 6))
        md = _MeshData.from_mesh(box)
        top = np.flatnonzero(md.face_normals[:, 2] > 0.99)
        side = np.flatnonzero(md.face_normals[:, 0] > 0.99)
        regions = [
            (top, _plane_of(md, top)),
            (side, _plane_of(md, side)),
        ]
        merged = _merge_compatible_regions(md, regions, Tolerances())
        assert len(merged) == 2

    def test_whole_pipeline_still_six_planes(self) -> None:
        box = trimesh.creation.box(extents=(20, 30, 10)).subdivide()
        result = segment_mesh(box)
        planes = [p for p in result.patches
                  if p.surface_type == SurfaceType.PLANE]
        assert len(planes) == 6
