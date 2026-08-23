"""Deterministic geometric metrics for source/candidate comparison."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh
from scipy.spatial import cKDTree


@dataclass(frozen=True)
class GeometricScore:
    """Comparable score for one valid reconstruction candidate."""

    normalized_chamfer: float
    relative_volume_error: float
    loss: float


def _sample_surface_deterministic(
    mesh: trimesh.Trimesh, count: int, seed: int
) -> np.ndarray:
    """Area-weighted surface samples without global random state."""
    if count <= 0:
        raise ValueError("sample_count must be positive")
    if mesh.is_empty or len(mesh.faces) == 0:
        raise ValueError("cannot sample an empty mesh")

    areas = np.asarray(mesh.area_faces, dtype=float)
    total = float(areas.sum())
    if not np.isfinite(total) or total <= 0:
        raise ValueError("mesh has no finite surface area")

    rng = np.random.default_rng(seed)
    face_ids = rng.choice(len(mesh.faces), size=count, p=areas / total)
    triangles = np.asarray(mesh.triangles)[face_ids]

    # Uniform barycentric sampling over each selected triangle.
    uv = rng.random((count, 2))
    flip = uv.sum(axis=1) > 1.0
    uv[flip] = 1.0 - uv[flip]
    return (
        triangles[:, 0]
        + uv[:, :1] * (triangles[:, 1] - triangles[:, 0])
        + uv[:, 1:] * (triangles[:, 2] - triangles[:, 0])
    )


def score_meshes(
    source: trimesh.Trimesh,
    candidate: trimesh.Trimesh,
    *,
    sample_count: int = 2048,
    seed: int = 0,
    volume_weight: float = 0.25,
) -> GeometricScore:
    """Score surface and volume agreement; lower is better.

    Chamfer distance is symmetric and normalized by the source bounding-box
    diagonal so scores remain comparable across part sizes.  The sampling is
    deterministic for a fixed seed.
    """
    source_points = _sample_surface_deterministic(source, sample_count, seed)
    candidate_points = _sample_surface_deterministic(
        candidate, sample_count, seed + 1
    )

    source_tree = cKDTree(source_points)
    candidate_tree = cKDTree(candidate_points)
    source_to_candidate = candidate_tree.query(source_points, workers=1)[0]
    candidate_to_source = source_tree.query(candidate_points, workers=1)[0]
    chamfer = 0.5 * (
        float(np.mean(source_to_candidate))
        + float(np.mean(candidate_to_source))
    )

    diagonal = float(np.linalg.norm(np.asarray(source.extents, dtype=float)))
    if not np.isfinite(diagonal) or diagonal <= 0:
        raise ValueError("source mesh has invalid bounds")
    normalized_chamfer = chamfer / diagonal

    source_volume = abs(float(source.volume))
    candidate_volume = abs(float(candidate.volume))
    if not np.isfinite(source_volume) or source_volume <= 0:
        raise ValueError("source mesh must enclose positive volume")
    if not np.isfinite(candidate_volume):
        raise ValueError("candidate mesh has invalid volume")
    relative_volume_error = abs(candidate_volume - source_volume) / source_volume

    return GeometricScore(
        normalized_chamfer=normalized_chamfer,
        relative_volume_error=relative_volume_error,
        loss=normalized_chamfer + volume_weight * relative_volume_error,
    )
