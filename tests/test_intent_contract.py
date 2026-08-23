"""Versioned design-intent interchange contract tests."""

from __future__ import annotations

import json

import numpy as np
import pytest

from mra.core import (
    Confidence,
    Feature,
    FeatureType,
    Question,
    SurfacePatch,
    SurfaceType,
)
from mra.intent import IntentDocument, IntentResult, dump_intent, load_intent


def _example_intent() -> IntentResult:
    patch = SurfacePatch(
        patch_id=3,
        surface_type=SurfaceType.PLANE,
        face_indices=np.array([1, 4, 7], dtype=np.int64),
        params={"origin": np.array([0.0, 0.0, 2.5]),
                "normal": np.array([0.0, 0.0, 1.0])},
        confidence=Confidence(0.98, "flat within tolerance"),
        rms_error=0.01,
        area=120.0,
    )
    feature = Feature(
        feature_id=5,
        feature_type=FeatureType.EXTRUSION,
        patches=[patch],
        params={"height": 5.0, "direction": np.array([0.0, 0.0, 1.0])},
        confidence=Confidence(0.95, "parallel cap pair"),
        user_resolved=True,
    )
    feature.semantic_name = "base_plate"
    feature.references = []
    feature.constraints = [{"type": "symmetric", "axis": "x"}]
    feature.locked = True
    feature.alternatives = [{"feature_type": "shell", "confidence": 0.2}]
    return IntentResult(
        features=[feature],
        questions=[Question(2, "Keep nominal height?", ["Yes", "No"],
                            feature_ids=[5], patch_ids=[3], answer=0)],
        extrude_direction=np.array([0.0, 0.0, 1.0]),
        wall_thickness=2.0,
        symmetry_planes=[np.array([1.0, 0.0, 0.0])],
    )


def test_runtime_intent_round_trips_without_mutation() -> None:
    original = _example_intent()
    document = IntentDocument.from_intent(
        original, metadata={"source": "synthetic_fixture"}
    )
    restored = document.to_intent()
    assert restored is not original
    assert restored.features[0].feature_type == FeatureType.EXTRUSION
    assert restored.features[0].params["height"] == 5.0
    assert np.array_equal(restored.features[0].patches[0].face_indices,
                          np.array([1, 4, 7]))
    assert restored.features[0].semantic_name == "base_plate"
    assert restored.features[0].locked is True
    assert restored.features[0].alternatives[0]["feature_type"] == "shell"
    assert restored.questions[0].answer == 0
    assert np.array_equal(restored.extrude_direction, [0.0, 0.0, 1.0])


def test_json_file_round_trip(tmp_path) -> None:
    path = dump_intent(_example_intent(), tmp_path / "part-intent")
    assert path.name == "part-intent.json"
    raw = json.loads(path.read_text())
    assert raw["schema_version"] == 1
    assert raw["units"] == "mm"
    restored = load_intent(path).to_intent()
    assert restored.features[0].params["height"] == 5.0


def test_unknown_schema_version_is_rejected() -> None:
    document = IntentDocument.from_intent(_example_intent()).to_dict()
    document["schema_version"] = 999
    with pytest.raises(ValueError, match="unsupported intent schema"):
        IntentDocument.from_dict(document)


def test_duplicate_top_level_feature_ids_are_rejected() -> None:
    document = IntentDocument.from_intent(_example_intent())
    document.features.append(dict(document.features[0]))
    with pytest.raises(ValueError, match="unique"):
        document.validate()
