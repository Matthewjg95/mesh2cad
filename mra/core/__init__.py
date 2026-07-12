"""Shared datatypes used across every pipeline stage.

This package deliberately has no dependency on the GUI or on OpenCascade so
that mesh processing and recognition can be tested headlessly.
"""

from mra.core.tolerances import Tolerances
from mra.core.features import (
    Confidence,
    Feature,
    FeatureType,
    SurfacePatch,
    SurfaceType,
)
from mra.core.questions import Question

__all__ = [
    "Confidence",
    "Feature",
    "FeatureType",
    "Question",
    "SurfacePatch",
    "SurfaceType",
    "Tolerances",
]
