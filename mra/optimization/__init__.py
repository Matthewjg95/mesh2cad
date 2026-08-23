"""Opt-in geometric refinement experiments.

The optimization layer is deliberately independent of the reconstruction
backend.  Callers provide a function that builds a candidate mesh from named
parameters; this package scores and searches candidates without mutating the
original intent model.
"""

from mra.optimization.bounded import (
    OptimizationConfig,
    OptimizationResult,
    ParameterSpec,
    Trial,
    refine_parameters,
)
from mra.optimization.metrics import GeometricScore, score_meshes
from mra.optimization.intent_adapter import (
    IntentParameterBinding,
    apply_intent_parameters,
    make_occ_candidate_builder,
)
from mra.optimization.reporting import result_to_dict, write_result_report

__all__ = [
    "GeometricScore",
    "IntentParameterBinding",
    "OptimizationConfig",
    "OptimizationResult",
    "ParameterSpec",
    "Trial",
    "apply_intent_parameters",
    "make_occ_candidate_builder",
    "refine_parameters",
    "result_to_dict",
    "score_meshes",
    "write_result_report",
]
