"""Tests for blend bridging and fillet application."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Trsf, gp_Vec
from OCP.TopoDS import TopoDS_Compound

from mra.core import Confidence, Feature, FeatureType, SurfacePatch, SurfaceType
from mra.reconstruction.bridges import (
    _convex_hull_solid,
    _shape_volume,
    _try_fuse_hull,
    solid_count,
)
from mra.reconstruction.fillets import apply_fillets
from mra.validation import validate_shape


def two_boxes_with_gap(gap: float = 0.5):
    """Compound of two 10-cubes separated along X by ``gap``."""
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    builder.Add(compound, BRepPrimAPI_MakeBox(10, 10, 10).Shape())
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(10 + gap, 0, 0))
    builder.Add(
        compound,
        BRepBuilderAPI_Transform(
            BRepPrimAPI_MakeBox(10, 10, 10).Shape(), t
        ).Shape(),
    )
    return compound


class TestHullBridge:
    def test_gap_spanning_hull_connects(self) -> None:
        shape = two_boxes_with_gap(0.5)
        assert solid_count(shape) == 2
        # A web spanning the gap (like a gusset region's vertices).
        web = np.array([
            [9.0, 3.0, 3.0], [11.5, 3.0, 3.0],
            [9.0, 7.0, 3.0], [11.5, 7.0, 3.0],
            [9.0, 3.0, 7.0], [11.5, 3.0, 7.0],
            [9.0, 7.0, 7.0], [11.5, 7.0, 7.0],
        ])
        bridged = _try_fuse_hull(shape, web)
        assert solid_count(bridged) == 1

    def test_degenerate_hull_rejected(self) -> None:
        flat = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                        dtype=float)
        assert _convex_hull_solid(flat, 0.0) is None

    def test_fuse_never_loses_volume(self) -> None:
        shape = two_boxes_with_gap(0.5)
        before = _shape_volume(shape)
        web = np.array([
            [9.0, 3.0, 3.0], [11.5, 3.0, 3.0],
            [9.0, 7.0, 3.0], [11.5, 7.0, 3.0],
            [9.0, 3.0, 7.0], [11.5, 3.0, 7.0],
            [9.0, 7.0, 7.0], [11.5, 7.0, 7.0],
        ])
        bridged = _try_fuse_hull(shape, web)
        assert _shape_volume(bridged) >= before


class TestFilletApplication:
    def test_rounds_matching_vertical_edge(self) -> None:
        # 20x20x10 box; round the vertical edge at (20, 20) with r=2.
        solid = BRepPrimAPI_MakeBox(20, 20, 10).Shape()
        mesh = trimesh.creation.box(extents=(20, 20, 10))
        mesh.apply_translation([10, 10, 5])  # match OCP box at origin

        # Fake fillet patch: mesh faces with any vertex on the target edge.
        tri_verts = mesh.vertices[mesh.faces]  # (n, 3, 3)
        near = np.flatnonzero(
            np.any((tri_verts[:, :, 0] > 19) & (tri_verts[:, :, 1] > 19),
                   axis=1)
        )
        assert len(near) > 0
        patch = SurfacePatch(
            patch_id=0,
            surface_type=SurfaceType.CYLINDER,
            face_indices=near,
            params={"axis": np.array([0.0, 0.0, 1.0]), "radius": 2.0,
                    "origin": np.array([20.0, 20.0, 5.0])},
            confidence=Confidence(0.9),
        )
        feature = Feature(
            feature_id=0,
            feature_type=FeatureType.FILLET,
            patches=[patch],
            params={"radius": 2.0, "axis": np.array([0.0, 0.0, 1.0]),
                    "concave": False, "length": 10.0},
        )
        log: list[str] = []
        applied: list[int] = []
        skipped: list[int] = []
        result = apply_fillets(solid, mesh, [feature], log, applied, skipped)

        assert applied == [0], (log, skipped)
        report = validate_shape(result)
        assert report.is_valid
        # The whole-face patch bbox matches all 4 vertical edges; each
        # rounded edge removes (1 - pi/4) r^2 * h. (Real fillet patches
        # are the small blend surfaces themselves, so they match locally.)
        expected = 20 * 20 * 10 - 4 * (1 - np.pi / 4) * 4 * 10
        assert report.volume == pytest.approx(expected, rel=1e-3)

    def test_unmatched_fillet_skipped_not_fatal(self) -> None:
        solid = BRepPrimAPI_MakeBox(20, 20, 10).Shape()
        mesh = trimesh.creation.box(extents=(20, 20, 10))
        patch = SurfacePatch(
            patch_id=0,
            surface_type=SurfaceType.CYLINDER,
            face_indices=np.array([0]),
            params={"axis": np.array([1.0, 0.0, 0.0]), "radius": 1.0,
                    "origin": np.zeros(3)},
            confidence=Confidence(0.9),
        )
        feature = Feature(
            feature_id=7,
            feature_type=FeatureType.FILLET,
            patches=[patch],
            params={"radius": 1.0, "axis": np.array([1.0, 0.0, 0.0])},
        )
        log: list[str] = []
        applied: list[int] = []
        skipped: list[int] = []
        result = apply_fillets(solid, mesh, [feature], log, applied, skipped)
        assert validate_shape(result).volume == pytest.approx(4000.0)
