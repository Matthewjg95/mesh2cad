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

__all__ = [
    "GeometricScore",
    "OptimizationConfig",
    "OptimizationResult",
    "ParameterSpec",
    "Trial",
    "refine_parameters",
    "score_meshes",
]
