"""Bridge named optimization parameters to Mesh2CAD design intent."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Mapping

import numpy as np
import trimesh

from mra.core import Tolerances
from mra.intent import IntentResult
from mra.recognition import SegmentationResult

if TYPE_CHECKING:
    from mra.optimization.bounded import OptimizationConfig, OptimizationResult


@dataclass(frozen=True)
class IntentParameterBinding:
    """Address one numeric value inside an intent feature's parameters.

    ``index`` selects a component of a vector-valued parameter such as a
    feature center.  Scalar parameters such as ``height`` or ``diameter``
    leave it as ``None``.
    """

    name: str
    feature_id: int
    parameter_key: str
    minimum: float
    maximum: float
    step: float
    index: int | None = None
    locked: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.parameter_key:
            raise ValueError("binding name and parameter_key cannot be empty")
        if self.minimum > self.maximum:
            raise ValueError(f"invalid bounds for {self.name}")
        if self.step <= 0:
            raise ValueError(f"step for {self.name} must be positive")
        if self.index is not None and self.index < 0:
            raise ValueError("vector index cannot be negative")

    def read(self, intent: IntentResult) -> float:
        feature = _feature(intent, self.feature_id)
        if self.parameter_key not in feature.params:
            raise KeyError(
                f"feature {self.feature_id} has no {self.parameter_key!r} parameter"
            )
        value = feature.params[self.parameter_key]
        if self.index is not None:
            value = np.asarray(value, dtype=float)[self.index]
        return float(value)

    def as_parameter_spec(self, intent: IntentResult):
        # Local import avoids making the core intent adapter depend on the
        # search implementation at module import time.
        from mra.optimization.bounded import ParameterSpec

        return ParameterSpec(
            name=self.name,
            initial=self.read(intent),
            minimum=self.minimum,
            maximum=self.maximum,
            step=self.step,
            locked=self.locked,
        )


@dataclass(frozen=True)
class IntentRefinementResult:
    """Search history plus the independently copied best intent model."""

    optimization: OptimizationResult
    best_intent: IntentResult


def _feature(intent: IntentResult, feature_id: int):
    matches = [f for f in intent.features if f.feature_id == feature_id]
    if len(matches) != 1:
        raise KeyError(
            f"expected one feature with id {feature_id}, found {len(matches)}"
        )
    return matches[0]


def apply_intent_parameters(
    intent: IntentResult,
    bindings: list[IntentParameterBinding],
    values: Mapping[str, float],
) -> IntentResult:
    """Return a deep-copied intent with bounded values applied.

    The caller's intent, features, parameter arrays and questions remain
    untouched.  Missing, duplicate, locked or out-of-bounds values fail
    explicitly instead of being silently clamped.
    """
    names = [binding.name for binding in bindings]
    if len(names) != len(set(names)):
        raise ValueError("binding names must be unique")
    unknown = set(values) - set(names)
    if unknown:
        raise KeyError(f"unbound optimization parameters: {sorted(unknown)}")

    candidate = copy.deepcopy(intent)
    for binding in bindings:
        if binding.name not in values:
            continue
        value = float(values[binding.name])
        original = binding.read(intent)
        if binding.locked and value != original:
            raise ValueError(f"locked parameter {binding.name!r} cannot change")
        if not binding.minimum <= value <= binding.maximum:
            raise ValueError(f"parameter {binding.name!r} is outside its bounds")

        feature = _feature(candidate, binding.feature_id)
        if binding.index is None:
            feature.params[binding.parameter_key] = value
        else:
            vector = np.asarray(
                feature.params[binding.parameter_key], dtype=float
            ).copy()
            vector[binding.index] = value
            feature.params[binding.parameter_key] = vector
    return candidate


def make_occ_candidate_builder(
    source_mesh: trimesh.Trimesh,
    segmentation: SegmentationResult | None,
    original_intent: IntentResult,
    bindings: list[IntentParameterBinding],
    tol: Tolerances | None = None,
):
    """Create the callback consumed by :func:`refine_parameters`.

    Every evaluation rebuilds a fresh OCC solid from a copied intent, applies
    the existing export-readiness validation gate, and only then tessellates
    the shape for scoring.  A failed/invalid build becomes a rejected trial.
    """
    tol = tol or Tolerances()

    def build(values: Mapping[str, float]) -> trimesh.Trimesh:
        from mra.reconstruction import build_solid, shape_to_trimesh
        from mra.validation import validate_shape

        intent = apply_intent_parameters(original_intent, bindings, values)
        result = build_solid(source_mesh, segmentation, intent, tol)
        if result.shape is None:
            detail = "; ".join(result.log[-3:]) or "no shape returned"
            raise RuntimeError(f"reconstruction failed: {detail}")
        validation = validate_shape(result.shape)
        if not validation.ready_for_export:
            detail = "; ".join(validation.problems) or "export gate failed"
            raise RuntimeError(f"candidate is not export-ready: {detail}")
        candidate = shape_to_trimesh(result.shape)
        if candidate.is_empty:
            raise RuntimeError("candidate tessellation is empty")
        return candidate

    return build


def refine_intent_parameters(
    source_mesh: trimesh.Trimesh,
    segmentation: SegmentationResult,
    original_intent: IntentResult,
    bindings: list[IntentParameterBinding],
    *,
    tol: Tolerances | None = None,
    config: OptimizationConfig | None = None,
    candidate_builder: Callable[[Mapping[str, float]], trimesh.Trimesh]
    | None = None,
) -> IntentRefinementResult:
    """Run bounded refinement and return a new best intent model.

    ``candidate_builder`` is an explicit seam for alternate CAD backends and
    headless tests.  When omitted, candidates are rebuilt and validated with
    Mesh2CAD's current OCC pipeline.
    """
    if not bindings:
        raise ValueError("at least one intent parameter binding is required")
    from mra.optimization.bounded import refine_parameters

    if candidate_builder is None:
        if segmentation is None:
            raise ValueError("segmentation is required for the OCC builder")
        builder = make_occ_candidate_builder(
            source_mesh, segmentation, original_intent, bindings, tol
        )
    else:
        builder = candidate_builder
    optimization = refine_parameters(
        source_mesh,
        [binding.as_parameter_spec(original_intent) for binding in bindings],
        builder,
        config=config,
    )
    best_intent = apply_intent_parameters(
        original_intent, bindings, optimization.best_parameters
    )
    return IntentRefinementResult(optimization, best_intent)
