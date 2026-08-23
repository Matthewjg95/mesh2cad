"""CADFit-inspired bounded refinement experiments."""

from __future__ import annotations

import pytest
import trimesh
import numpy as np

from mra.optimization import (
    IntentParameterBinding,
    OptimizationConfig,
    ParameterSpec,
    apply_intent_parameters,
    refine_parameters,
    result_to_dict,
    score_meshes,
)
from mra.core import Confidence, Feature, FeatureType
from mra.intent import IntentResult


def _box(values):
    return trimesh.creation.box(
        extents=(values["width"], values["depth"], values["height"])
    )


def test_score_is_deterministic_and_prefers_matching_geometry() -> None:
    source = trimesh.creation.box(extents=(10.0, 20.0, 4.0))
    match_a = score_meshes(source, source.copy(), sample_count=512, seed=9)
    match_b = score_meshes(source, source.copy(), sample_count=512, seed=9)
    wrong = score_meshes(
        source, trimesh.creation.box(extents=(7.0, 16.0, 3.0)),
        sample_count=512, seed=9,
    )
    assert match_a == match_b
    assert match_a.loss < wrong.loss


def test_refinement_improves_fixed_feature_sequence() -> None:
    source = trimesh.creation.box(extents=(10.0, 20.0, 4.0))
    result = refine_parameters(
        source,
        [
            ParameterSpec("width", 8.0, 6.0, 14.0, 1.0),
            ParameterSpec("depth", 16.0, 12.0, 24.0, 2.0),
            ParameterSpec("height", 3.0, 2.0, 7.0, 0.5),
        ],
        _box,
        config=OptimizationConfig(sample_count=768, seed=3,
                                  max_evaluations=80),
    )
    assert result.best_score.loss < result.initial_score.loss
    assert result.best_parameters == pytest.approx(
        {"width": 10.0, "depth": 20.0, "height": 4.0}, abs=0.51
    )
    assert any(trial.accepted for trial in result.trials[1:])


def test_locked_parameter_is_immutable() -> None:
    source = trimesh.creation.box(extents=(10.0, 20.0, 4.0))
    result = refine_parameters(
        source,
        [
            ParameterSpec("width", 8.0, 6.0, 14.0, 1.0, locked=True),
            ParameterSpec("depth", 16.0, 12.0, 24.0, 2.0),
            ParameterSpec("height", 3.0, 2.0, 7.0, 0.5),
        ],
        _box,
        config=OptimizationConfig(sample_count=384, max_evaluations=50),
    )
    assert result.best_parameters["width"] == 8.0


def test_invalid_candidates_are_logged_and_rejected() -> None:
    source = trimesh.creation.box(extents=(10.0, 20.0, 4.0))

    def sometimes_invalid(values):
        if values["width"] > 9.0:
            raise RuntimeError("simulated CAD-kernel failure")
        return trimesh.creation.box(extents=(values["width"], 20.0, 4.0))

    result = refine_parameters(
        source,
        [ParameterSpec("width", 8.0, 6.0, 12.0, 2.0)],
        sometimes_invalid,
        config=OptimizationConfig(sample_count=256, max_passes=3),
    )
    assert result.best_parameters["width"] <= 9.0
    assert any(not trial.valid for trial in result.trials)


def test_intent_binding_copies_and_updates_scalar_and_vector() -> None:
    intent = IntentResult(features=[Feature(
        feature_id=7,
        feature_type=FeatureType.EXTRUSION,
        params={"height": 3.0, "origin": np.array([1.0, 2.0, 3.0])},
        confidence=Confidence(0.9),
    )])
    bindings = [
        IntentParameterBinding("height", 7, "height", 1.0, 8.0, 0.5),
        IntentParameterBinding("origin_x", 7, "origin", -5.0, 5.0, 0.25,
                               index=0),
    ]
    candidate = apply_intent_parameters(
        intent, bindings, {"height": 4.5, "origin_x": -2.0}
    )
    assert intent.features[0].params["height"] == 3.0
    assert intent.features[0].params["origin"][0] == 1.0
    assert candidate.features[0].params["height"] == 4.5
    assert candidate.features[0].params["origin"][0] == -2.0


def test_locked_intent_binding_rejects_change() -> None:
    intent = IntentResult(features=[Feature(
        feature_id=2,
        feature_type=FeatureType.HOLE,
        params={"diameter": 3.2},
    )])
    binding = IntentParameterBinding(
        "diameter", 2, "diameter", 2.0, 5.0, 0.1, locked=True
    )
    with pytest.raises(ValueError, match="locked"):
        apply_intent_parameters(intent, [binding], {"diameter": 3.3})


def test_result_report_is_machine_readable() -> None:
    source = trimesh.creation.box(extents=(10.0, 20.0, 4.0))
    result = refine_parameters(
        source,
        [ParameterSpec("width", 8.0, 6.0, 12.0, 1.0)],
        lambda values: trimesh.creation.box(
            extents=(values["width"], 20.0, 4.0)
        ),
        config=OptimizationConfig(sample_count=128, max_passes=2),
    )
    report = result_to_dict(result)
    assert report["schema_version"] == 1
    assert report["trials"][0]["accepted"]
    assert report["best_score"]["loss"] <= report["initial_score"]["loss"]
