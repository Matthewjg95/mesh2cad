"""Stage 2: analytic surface recognition on repaired meshes."""

from mra.recognition.fitting import (
    FitResult,
    fit_cone,
    fit_cylinder,
    fit_plane,
    fit_sphere,
)
from mra.recognition.segmentation import SegmentationResult, segment_mesh

__all__ = [
    "FitResult",
    "SegmentationResult",
    "fit_cone",
    "fit_cylinder",
    "fit_plane",
    "fit_sphere",
    "segment_mesh",
]
