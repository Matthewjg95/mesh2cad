"""Feature and surface-patch datatypes shared by recognition and intent.

A ``SurfacePatch`` is the Stage-2 output: a group of mesh faces explained by
one analytic surface (plane, cylinder, ...). A ``Feature`` is the Stage-3
output: an engineering-level operation (hole, boss, shell, fillet, pattern)
built from one or more patches. Both carry a ``Confidence`` so Stage 4 knows
when to ask the user.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

import numpy as np


class SurfaceType(enum.Enum):
    """Analytic surface classes recoverable from mesh regions."""

    PLANE = "plane"
    CYLINDER = "cylinder"
    CONE = "cone"
    SPHERE = "sphere"
    TORUS = "torus"          # fillet blends tessellate as torus segments
    FREEFORM = "freeform"    # could not be explained analytically


class FeatureType(enum.Enum):
    """Engineering feature classes recovered during intent inference."""

    EXTRUSION = "extrusion"
    REVOLUTION = "revolution"
    SHELL = "shell"
    POCKET = "pocket"
    PAD = "pad"  # raised plateau (prismatic island above a face)
    SLOT = "slot"
    TAB = "tab"
    RIB = "rib"
    BOSS = "boss"
    HOLE = "hole"
    COUNTERBORE = "counterbore"
    COUNTERSINK = "countersink"
    FILLET = "fillet"
    CHAMFER = "chamfer"
    MIRROR = "mirror"
    LINEAR_PATTERN = "linear_pattern"
    CIRCULAR_PATTERN = "circular_pattern"
    # Enclosure-specific high-priority features
    OUTER_SHELL = "outer_shell"
    INTERNAL_CAVITY = "internal_cavity"
    DISPLAY_OPENING = "display_opening"
    CONNECTOR_CUTOUT = "connector_cutout"
    BUTTON_HOLE = "button_hole"
    VENT_SLOT = "vent_slot"
    SCREW_BOSS = "screw_boss"
    STANDOFF = "standoff"
    SNAP_FIT = "snap_fit"
    MOUNTING_TAB = "mounting_tab"


@dataclass(frozen=True)
class Confidence:
    """A [0, 1] confidence with a human-readable justification.

    Attributes:
        value: 0 = pure guess, 1 = certain.
        reason: Short explanation shown in the sidebar and in Stage-4
            questions ("87% of vertices within 0.02 mm of fitted plane").
    """

    value: float
    reason: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.value}")

    def needs_user(self, ask_threshold: float) -> bool:
        """Whether this confidence is low enough to pause and ask (Stage 4)."""
        return self.value < ask_threshold


@dataclass
class SurfacePatch:
    """A contiguous mesh region explained by one analytic surface.

    Attributes:
        patch_id: Stable id, unique within one recognition run.
        surface_type: Which analytic class the region fits.
        face_indices: Indices into the mesh's triangle array.
        params: Analytic parameters, keys depend on ``surface_type``:
            PLANE: ``origin`` (3,), ``normal`` (3,)
            CYLINDER: ``origin`` (3,), ``axis`` (3,), ``radius`` (float)
            CONE: ``apex`` (3,), ``axis`` (3,), ``half_angle`` (float, rad)
            SPHERE: ``center`` (3,), ``radius`` (float)
            TORUS: ``center`` (3,), ``axis`` (3,), ``major_radius``,
            ``minor_radius`` (floats)
        confidence: Fit quality (inlier ratio, residual statistics).
        rms_error: Root-mean-square vertex distance to the fitted surface.
        area: Total triangle area of the patch (mm^2).
    """

    patch_id: int
    surface_type: SurfaceType
    face_indices: np.ndarray
    params: dict[str, Any]
    confidence: Confidence
    rms_error: float = 0.0
    area: float = 0.0

    def __repr__(self) -> str:  # keep sidebar/debug output compact
        return (
            f"SurfacePatch(#{self.patch_id} {self.surface_type.value}, "
            f"{len(self.face_indices)} tris, conf={self.confidence.value:.2f})"
        )


@dataclass
class Feature:
    """An engineering feature recovered by intent inference (Stage 3).

    Attributes:
        feature_id: Stable id, unique within one intent-recovery run.
        feature_type: Engineering classification.
        patches: Surface patches that evidence this feature.
        params: Feature parameters (e.g. hole: ``axis``, ``diameter``,
            ``depth``, ``through``; shell: ``wall_thickness``).
        confidence: Combined confidence for the interpretation.
        children: Sub-features (a counterbore owns its pilot hole).
        user_resolved: True once Stage 4 asked and the user answered;
            such features are never re-questioned.
    """

    feature_id: int
    feature_type: FeatureType
    patches: list[SurfacePatch] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    confidence: Confidence = field(default_factory=lambda: Confidence(1.0))
    children: list["Feature"] = field(default_factory=list)
    user_resolved: bool = False

    @property
    def face_indices(self) -> np.ndarray:
        """All mesh triangle indices covered by this feature's patches."""
        if not self.patches:
            return np.empty(0, dtype=np.int64)
        return np.unique(np.concatenate([p.face_indices for p in self.patches]))

    def __repr__(self) -> str:
        return (
            f"Feature(#{self.feature_id} {self.feature_type.value}, "
            f"{len(self.patches)} patches, conf={self.confidence.value:.2f})"
        )
