"""Stage 3 tests: intent recovery on boolean-built synthetic parts."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from mra.core import FeatureType, Tolerances
from mra.intent import recover_intent
from mra.recognition import segment_mesh


def plate_with_holes(
    hole_xy: list[tuple[float, float]],
    plate=(60.0, 40.0, 5.0),
    radius: float = 2.0,
) -> trimesh.Trimesh:
    """A rectangular plate with through-holes drilled along Z."""
    box = trimesh.creation.box(extents=plate)
    drills = []
    for x, y in hole_xy:
        c = trimesh.creation.cylinder(radius=radius, height=plate[2] * 3,
                                      sections=48)
        c.apply_translation([x, y, 0])
        drills.append(c)
    return trimesh.boolean.difference([box, *drills])


def _features_of(mesh, kind: FeatureType, tol: Tolerances | None = None):
    seg = segment_mesh(mesh, tol)
    result = recover_intent(mesh, seg, tol)
    return result, [f for f in result.features if f.feature_type == kind]


class TestExtrusion:
    def test_box_is_extrusion(self) -> None:
        box = trimesh.creation.box(extents=(30, 20, 8)).subdivide()
        result, extrusions = _features_of(box, FeatureType.EXTRUSION)
        assert len(extrusions) == 1
        e = extrusions[0]
        # Boxes are degenerate (any of 3 directions works); accept any axis
        # but the height must match that axis' extent.
        d = np.abs(e.params["direction"])
        expected = {0: 30, 1: 20, 2: 8}[int(np.argmax(d))]
        assert e.params["height"] == pytest.approx(expected, rel=0.01)
        assert e.confidence.value > 0.9

    def test_plate_extrudes_along_z(self) -> None:
        plate = plate_with_holes([(0.0, 0.0)])
        result, extrusions = _features_of(plate, FeatureType.EXTRUSION)
        assert len(extrusions) == 1
        # Plate is 60x40x5: caps normal to Z dominate? No — the largest
        # cap pair is the 60x40 faces, normal Z.
        assert abs(extrusions[0].params["direction"][2]) == pytest.approx(1.0)
        assert extrusions[0].params["height"] == pytest.approx(5.0, abs=0.01)


class TestHoles:
    def test_single_through_hole(self) -> None:
        plate = plate_with_holes([(0.0, 0.0)], radius=2.0)
        result, holes = _features_of(plate, FeatureType.HOLE)
        assert len(holes) == 1
        assert holes[0].params["diameter"] == pytest.approx(4.0, rel=0.01)
        assert holes[0].params["depth"] == pytest.approx(5.0, abs=0.05)
        assert abs(holes[0].params["axis"][2]) == pytest.approx(1.0)

    def test_boss_is_not_a_hole(self) -> None:
        # A cylinder standing on a plate is a boss (convex).
        plate = trimesh.creation.box(extents=(40, 40, 4))
        boss = trimesh.creation.cylinder(radius=4.0, height=10.0, sections=48)
        boss.apply_translation([0, 0, 2 + 5])
        part = trimesh.boolean.union([plate, boss])
        result, holes = _features_of(part, FeatureType.HOLE)
        assert len(holes) == 0
        _, bosses = _features_of(part, FeatureType.BOSS)
        assert len(bosses) == 1
        assert bosses[0].params["diameter"] == pytest.approx(8.0, rel=0.01)

    def test_equal_diameters_snapped(self) -> None:
        plate = plate_with_holes([(-15, 0), (0, 0), (15, 0)], radius=2.0)
        result, holes = _features_of(plate, FeatureType.HOLE)
        diameters = {h.params["diameter"] for h in holes}
        assert len(diameters) == 1  # all snapped to a single value


class TestPatterns:
    def test_linear_pattern(self) -> None:
        plate = plate_with_holes([(-20, 0), (-10, 0), (0, 0), (10, 0), (20, 0)])
        result, patterns = _features_of(plate, FeatureType.LINEAR_PATTERN)
        assert len(patterns) == 1
        assert patterns[0].params["count"] == 5
        assert patterns[0].params["spacing"] == pytest.approx(10.0, abs=0.05)

    def test_circular_pattern(self) -> None:
        angles = np.radians([0, 60, 120, 180, 240, 300])
        centers = [(12 * np.cos(a), 12 * np.sin(a)) for a in angles]
        plate = plate_with_holes(centers, plate=(50.0, 50.0, 5.0), radius=1.5)
        result, patterns = _features_of(plate, FeatureType.CIRCULAR_PATTERN)
        assert len(patterns) == 1
        assert patterns[0].params["count"] == 6
        assert patterns[0].params["circle_radius"] == pytest.approx(
            12.0, abs=0.05
        )

    def test_two_holes_no_pattern(self) -> None:
        plate = plate_with_holes([(-10, 0), (10, 0)])
        result, patterns = _features_of(plate, FeatureType.LINEAR_PATTERN)
        assert len(patterns) == 0


class TestWallThickness:
    def test_plate_thickness_found(self) -> None:
        plate = plate_with_holes([(0.0, 0.0)])
        seg = segment_mesh(plate)
        result = recover_intent(plate, seg)
        assert result.wall_thickness == pytest.approx(5.0, abs=0.05)


class TestSymmetry:
    def test_symmetric_plate(self) -> None:
        plate = plate_with_holes([(-10, 0), (10, 0)])
        seg = segment_mesh(plate)
        result = recover_intent(plate, seg)
        # Symmetric about X and Y centroid planes (and Z: plate mid-plane).
        assert len(result.symmetry_planes) == 3

    def test_asymmetric_part(self) -> None:
        plate = plate_with_holes([(-20, -10)])  # one off-centre hole
        seg = segment_mesh(plate)
        result = recover_intent(plate, seg)
        normals = [tuple(np.abs(n)) for n in result.symmetry_planes]
        assert (1.0, 0.0, 0.0) not in normals
        assert (0.0, 1.0, 0.0) not in normals


class TestQuestions:
    def test_noisy_hole_diameters_ask_user(self) -> None:
        # Two holes intentionally 0.06 mm apart in diameter: inside the
        # 2% equal-dimension band but beyond mesh noise -> ask.
        plate1 = plate_with_holes([(-15, 0)], radius=2.00)
        plate2 = plate_with_holes([(15, 0)], radius=2.03)
        # Build one mesh containing both plates' hole geometry.
        plate = trimesh.creation.box(extents=(60, 40, 5))
        d1 = trimesh.creation.cylinder(radius=2.00, height=15, sections=48)
        d1.apply_translation([-15, 0, 0])
        d2 = trimesh.creation.cylinder(radius=2.03, height=15, sections=48)
        d2.apply_translation([15, 0, 0])
        plate = trimesh.boolean.difference([plate, d1, d2])
        seg = segment_mesh(plate)
        result = recover_intent(plate, seg)
        texts = [q.text for q in result.questions]
        assert any("identical" in t or "holes" in t for t in texts), texts
