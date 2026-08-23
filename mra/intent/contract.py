"""Versioned, JSON-safe interchange contract for recovered design intent.

The runtime ``IntentResult`` remains authoritative for the current builder.
This module supplies the stable boundary that future mesh, photo and learned
recognizers can produce without depending on Mesh2CAD internals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from mra.core import (
    Confidence,
    Feature,
    FeatureType,
    Question,
    SurfacePatch,
    SurfaceType,
)
from mra.intent.recover import IntentResult

INTENT_SCHEMA_VERSION = 1


def _encode(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return {"$type": "ndarray", "value": value.tolist()}
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, tuple):
        return {"$type": "tuple", "value": [_encode(v) for v in value]}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported intent value: {type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if isinstance(value, dict):
        marker = value.get("$type")
        if marker == "ndarray":
            return np.asarray(value["value"])
        if marker == "tuple":
            return tuple(_decode(v) for v in value["value"])
        return {k: _decode(v) for k, v in value.items()}
    return value


def _patch_to_dict(patch: SurfacePatch) -> dict[str, Any]:
    return {
        "patch_id": patch.patch_id,
        "surface_type": patch.surface_type.value,
        "face_indices": patch.face_indices.tolist(),
        "parameters": _encode(patch.params),
        "confidence": {
            "value": patch.confidence.value,
            "reason": patch.confidence.reason,
        },
        "rms_error": patch.rms_error,
        "area": patch.area,
    }


def _patch_from_dict(data: Mapping[str, Any]) -> SurfacePatch:
    confidence = data["confidence"]
    return SurfacePatch(
        patch_id=int(data["patch_id"]),
        surface_type=SurfaceType(str(data["surface_type"])),
        face_indices=np.asarray(data["face_indices"], dtype=np.int64),
        params=_decode(data.get("parameters", {})),
        confidence=Confidence(
            float(confidence["value"]), str(confidence.get("reason", ""))
        ),
        rms_error=float(data.get("rms_error", 0.0)),
        area=float(data.get("area", 0.0)),
    )


def _feature_to_dict(feature: Feature) -> dict[str, Any]:
    provenance = getattr(feature, "provenance", None)
    if provenance is None:
        provenance = [
            {
                "kind": "mesh_patch",
                "patch_id": patch.patch_id,
                "surface_type": patch.surface_type.value,
            }
            for patch in feature.patches
        ]
    return {
        "feature_id": feature.feature_id,
        "feature_type": feature.feature_type.value,
        "semantic_name": getattr(feature, "semantic_name", ""),
        "references": _encode(getattr(feature, "references", [])),
        "parameters": _encode(feature.params),
        "constraints": _encode(getattr(feature, "constraints", [])),
        "confidence": {
            "value": feature.confidence.value,
            "reason": feature.confidence.reason,
        },
        "provenance": _encode(provenance),
        "locked": bool(getattr(feature, "locked", False)),
        "alternatives": _encode(getattr(feature, "alternatives", [])),
        "user_resolved": feature.user_resolved,
        "evidence_patches": [_patch_to_dict(p) for p in feature.patches],
        "children": [_feature_to_dict(child) for child in feature.children],
    }


def _feature_from_dict(data: Mapping[str, Any]) -> Feature:
    confidence = data["confidence"]
    feature = Feature(
        feature_id=int(data["feature_id"]),
        feature_type=FeatureType(str(data["feature_type"])),
        patches=[_patch_from_dict(p) for p in data.get("evidence_patches", [])],
        params=_decode(data.get("parameters", {})),
        confidence=Confidence(
            float(confidence["value"]), str(confidence.get("reason", ""))
        ),
        children=[_feature_from_dict(c) for c in data.get("children", [])],
        user_resolved=bool(data.get("user_resolved", False)),
    )
    # Feature is intentionally not slotted, so contract-only metadata can
    # survive a round trip before the runtime model formally adopts it.
    feature.semantic_name = str(data.get("semantic_name", ""))
    feature.references = _decode(data.get("references", []))
    feature.constraints = _decode(data.get("constraints", []))
    feature.provenance = _decode(data.get("provenance", []))
    feature.locked = bool(data.get("locked", False))
    feature.alternatives = _decode(data.get("alternatives", []))
    return feature


@dataclass
class IntentDocument:
    """Backend-neutral, versioned representation of one intent hypothesis."""

    features: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    extrude_direction: Any = None
    wall_thickness: float | None = None
    symmetry_planes: list[Any] = field(default_factory=list)
    units: str = "mm"
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = INTENT_SCHEMA_VERSION

    @classmethod
    def from_intent(
        cls, intent: IntentResult, *, metadata: Mapping[str, Any] | None = None
    ) -> "IntentDocument":
        return cls(
            features=[_feature_to_dict(f) for f in intent.features],
            questions=[
                {
                    "question_id": q.question_id,
                    "text": q.text,
                    "options": list(q.options),
                    "feature_ids": list(q.feature_ids),
                    "patch_ids": list(q.patch_ids),
                    "answer": q.answer,
                }
                for q in intent.questions
            ],
            extrude_direction=_encode(intent.extrude_direction),
            wall_thickness=intent.wall_thickness,
            symmetry_planes=[_encode(p) for p in intent.symmetry_planes],
            metadata=_encode(dict(metadata or {})),
        )

    def to_intent(self) -> IntentResult:
        self.validate()
        return IntentResult(
            features=[_feature_from_dict(f) for f in self.features],
            questions=[
                Question(
                    question_id=int(q["question_id"]),
                    text=str(q["text"]),
                    options=[str(v) for v in q["options"]],
                    feature_ids=[int(v) for v in q.get("feature_ids", [])],
                    patch_ids=[int(v) for v in q.get("patch_ids", [])],
                    answer=(None if q.get("answer") is None
                            else int(q["answer"])),
                )
                for q in self.questions
            ],
            extrude_direction=_decode(self.extrude_direction),
            wall_thickness=self.wall_thickness,
            symmetry_planes=[_decode(p) for p in self.symmetry_planes],
        )

    def validate(self) -> None:
        if self.schema_version != INTENT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported intent schema version {self.schema_version}"
            )
        if self.units != "mm":
            raise ValueError(f"unsupported units {self.units!r}; expected 'mm'")
        ids = [int(feature["feature_id"]) for feature in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("top-level feature IDs must be unique")
        for feature in self.features:
            FeatureType(str(feature["feature_type"]))
            confidence = feature.get("confidence", {})
            Confidence(float(confidence["value"]),
                       str(confidence.get("reason", "")))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "units": self.units,
            "metadata": self.metadata,
            "features": self.features,
            "questions": self.questions,
            "extrude_direction": self.extrude_direction,
            "wall_thickness": self.wall_thickness,
            "symmetry_planes": self.symmetry_planes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntentDocument":
        required = {"schema_version", "units", "features"}
        missing = required - set(data)
        if missing:
            raise ValueError(f"intent document missing fields: {sorted(missing)}")
        document = cls(
            schema_version=int(data["schema_version"]),
            units=str(data["units"]),
            metadata=dict(data.get("metadata", {})),
            features=list(data["features"]),
            questions=list(data.get("questions", [])),
            extrude_direction=data.get("extrude_direction"),
            wall_thickness=data.get("wall_thickness"),
            symmetry_planes=list(data.get("symmetry_planes", [])),
        )
        document.validate()
        return document


def dump_intent(
    intent: IntentResult | IntentDocument,
    path: str | Path,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Write a v1 intent JSON document."""
    document = (intent if isinstance(intent, IntentDocument)
                else IntentDocument.from_intent(intent, metadata=metadata))
    path = Path(path)
    if path.suffix.lower() != ".json":
        path = path.with_suffix(".json")
    path.write_text(json.dumps(document.to_dict(), indent=2) + "\n")
    return path


def load_intent(path: str | Path) -> IntentDocument:
    """Load and validate a versioned intent JSON document."""
    return IntentDocument.from_dict(json.loads(Path(path).read_text()))
