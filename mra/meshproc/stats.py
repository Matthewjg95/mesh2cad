"""Mesh statistics shown in the GUI sidebar."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh


@dataclass(frozen=True)
class MeshStats:
    """Summary statistics for a loaded mesh.

    Attributes:
        vertex_count: Number of vertices.
        face_count: Number of triangles.
        extents: Bounding-box size (x, y, z) in mesh units.
        surface_area: Total triangle area.
        volume: Enclosed volume; only meaningful when ``watertight``.
        is_watertight: Every edge shared by exactly two faces.
        is_winding_consistent: Neighbouring faces agree on orientation.
        euler_number: V - E + F; 2 for a single closed shell without holes.
        body_count: Number of connected components.
    """

    vertex_count: int
    face_count: int
    extents: tuple[float, float, float]
    surface_area: float
    volume: float
    is_watertight: bool
    is_winding_consistent: bool
    euler_number: int
    body_count: int


def compute_stats(mesh: trimesh.Trimesh) -> MeshStats:
    """Compute sidebar statistics for ``mesh``."""
    extents = mesh.extents if len(mesh.vertices) else np.zeros(3)
    return MeshStats(
        vertex_count=len(mesh.vertices),
        face_count=len(mesh.faces),
        extents=tuple(float(e) for e in extents),
        surface_area=float(mesh.area),
        volume=float(mesh.volume) if mesh.is_watertight else 0.0,
        is_watertight=bool(mesh.is_watertight),
        is_winding_consistent=bool(mesh.is_winding_consistent),
        euler_number=int(mesh.euler_number),
        body_count=int(mesh.body_count),
    )
