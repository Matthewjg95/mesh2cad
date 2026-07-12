"""Tests for the design-for-machining cost model and transforms."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from mra.core import FeatureType
from mra.dfm import (
    CostModel,
    drop_pockets,
    drop_shallow_pockets,
    estimate_cost,
    flatten_hole_recesses,
    setup_plans,
    unify_pocket_depths,
)
from mra.intent import recover_intent
from mra.recognition import segment_mesh


def stepped_plate():
    """Plate with a shallow cosmetic recess and two deeper pockets."""
    plate = trimesh.creation.box(extents=(60, 40, 6))
    cosmetic = trimesh.creation.box(extents=(20, 10, 0.6))
    cosmetic.apply_translation([-15, 0, 3.0])   # 0.3 mm deep recess
    p1 = trimesh.creation.box(extents=(10, 8, 4))
    p1.apply_translation([10, 8, 3.0])          # 2.0 mm pocket
    p2 = trimesh.creation.box(extents=(10, 8, 4.4))
    p2.apply_translation([10, -8, 3.0])         # 2.2 mm pocket
    return trimesh.boolean.difference([plate, cosmetic, p1, p2])


class TestCostModel:
    def test_report_totals_and_quantity(self) -> None:
        mesh = stepped_plate()
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        report = estimate_cost(mesh, seg, intent)
        assert report.total > 0
        assert report.total == pytest.approx(
            sum(ln.cost for ln in report.lines)
        )
        # Amortisation must strictly reduce per-part cost.
        assert report.at_quantity(5) < report.total
        assert report.at_quantity(10) < report.at_quantity(5)
        assert "setup" in report.summary()

    def test_fewer_features_cost_less(self) -> None:
        mesh = stepped_plate()
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        before = estimate_cost(mesh, seg, intent).total
        drop_shallow_pockets(intent, max_depth=0.5)
        after = estimate_cost(mesh, seg, intent).total
        assert after < before


def two_sided_plate():
    """Plate with pockets on BOTH faces (forces two setups)."""
    plate = trimesh.creation.box(extents=(60, 40, 6))
    top = trimesh.creation.box(extents=(12, 10, 4))
    top.apply_translation([15, 0, 3.0])       # 2 mm pocket, top
    bottom = trimesh.creation.box(extents=(12, 10, 4))
    bottom.apply_translation([-15, 0, -3.0])  # 2 mm recess, bottom
    return trimesh.boolean.difference([plate, top, bottom])


class TestSetupElimination:
    def test_plans_report_both_sides(self) -> None:
        mesh = two_sided_plate()
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        plans = {p.side: p for p in setup_plans(intent)}
        assert plans[+1].pockets and plans[-1].pockets
        assert plans[-1].eliminable
        assert plans[-1].cost > 0
        assert any("proud" in t for t in plans[-1].tradeoffs)

    def test_dropping_side_pockets_removes_setup(self) -> None:
        mesh = two_sided_plate()
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        before = estimate_cost(mesh, seg, intent)
        assert before.sides == 2
        bottom_ids = {
            f.feature_id for f in intent.features
            if f.feature_type == FeatureType.POCKET
            and int(f.params["side"]) == -1
        }
        log = drop_pockets(intent, bottom_ids)
        assert log
        after = estimate_cost(mesh, seg, intent)
        assert after.sides == 1
        # Must save at least the setup fee.
        assert before.total - after.total >= CostModel().setup_cost_per_side

    def test_selective_drop_marks_only_selected(self) -> None:
        mesh = two_sided_plate()
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        pockets = [f for f in intent.features
                   if f.feature_type == FeatureType.POCKET]
        drop_pockets(intent, {pockets[0].feature_id})
        assert pockets[0].params.get("fill")
        assert not any(f.params.get("fill") for f in pockets[1:])

    def test_filled_pocket_restores_volume(self) -> None:
        from mra.reconstruction import build_solid
        from mra.validation import validate_shape

        mesh = two_sided_plate()
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        bottom = [f for f in intent.features
                  if f.feature_type == FeatureType.POCKET
                  and int(f.params["side"]) == -1]
        assert bottom
        expected_fill = sum(
            float(f.params["depth"]) * f.patches[0].area for f in bottom
        )
        baseline = validate_shape(
            build_solid(mesh, seg, recover_intent(mesh, seg)).shape
        ).volume
        drop_pockets(intent, {f.feature_id for f in bottom})
        filled = validate_shape(
            build_solid(mesh, seg, intent).shape
        ).volume
        assert filled - baseline == pytest.approx(expected_fill, rel=0.05)


class TestFlattenHoleRecesses:
    def test_marks_pockets_near_hole_for_fill(self) -> None:
        # Plate with a hole through a recess step beside it.
        plate = trimesh.creation.box(extents=(60, 40, 6))
        recess = trimesh.creation.box(extents=(12, 12, 2))
        recess.apply_translation([15, 0, 3.0])   # 1 mm recess, top side
        mesh = trimesh.boolean.difference([plate, recess])
        drill = trimesh.creation.cylinder(radius=1.5, height=20, sections=48)
        drill.apply_translation([15, 0, 0])       # hole in the recess
        mesh = trimesh.boolean.difference([mesh, drill])

        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        holes = [f for f in intent.features
                 if f.feature_type == FeatureType.HOLE]
        assert holes
        pockets = [f for f in intent.features
                   if f.feature_type == FeatureType.POCKET]
        assert pockets and not any(p.params.get("fill") for p in pockets)

        log = flatten_hole_recesses(
            intent, {holes[0].feature_id}, radius=8.0
        )
        assert log
        assert any(p.params.get("fill") for p in pockets)

    def test_far_pockets_untouched(self) -> None:
        mesh = stepped_plate()
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        # No holes here; flattening an empty set does nothing.
        assert flatten_hole_recesses(intent, set()) == []
        assert not any(
            f.params.get("fill") for f in intent.features
            if f.feature_type == FeatureType.POCKET
        )


class TestTransforms:
    def test_drop_shallow_pockets(self) -> None:
        mesh = stepped_plate()
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        shallow = [f for f in intent.features
                   if f.feature_type == FeatureType.POCKET
                   and float(f.params["depth"]) <= 0.5]
        assert len(shallow) >= 1
        log = drop_shallow_pockets(intent, max_depth=0.5)
        assert len(log) == len(shallow)
        # Dropped pockets stay as features, flagged for FILL (the
        # builder fuses the material; removal would leave voids under
        # floating pads).
        assert all(
            f.params.get("fill")
            for f in intent.features
            if f.feature_type == FeatureType.POCKET
            and float(f.params["depth"]) <= 0.5
        )

    def test_unify_pocket_depths_takes_deepest(self) -> None:
        mesh = stepped_plate()
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        log = unify_pocket_depths(intent, rel_tol=0.15)
        assert log, "expected the 2.0/2.2 pockets to unify"
        depths = {
            round(float(f.params["depth"]), 2)
            for f in intent.features
            if f.feature_type == FeatureType.POCKET
            and float(f.params["depth"]) > 1.0
        }
        assert depths == {2.2}  # deepest wins, material never left behind