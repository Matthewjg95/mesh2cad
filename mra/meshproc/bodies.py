"""Splitting multi-body meshes into candidate parts.

Assembly STLs (like an exported product model) contain many disconnected
bodies. Reconstruction works on one part at a time, so the GUI lets the
user isolate a body; this module provides the mesh-side logic.
"""

from __future__ import annotations

from dataclasses import dataclass

import trimesh


@dataclass(frozen=True)
class BodyInfo:
    """Summary of one disconnected body inside a mesh.

    Attributes:
        index: Index into the list returned by :func:`split_bodies`.
        face_count: Triangle count.
        extents: Bounding-box size (x, y, z).
        area: Surface area.
        watertight: Whether the body is closed.
    """

    index: int
    face_count: int
    extents: tuple[float, float, float]
    area: float
    watertight: bool

    def label(self) -> str:
        """Human-readable one-liner for list widgets."""
        ex = self.extents
        return (
            f"{ex[0]:.1f} x {ex[1]:.1f} x {ex[2]:.1f} mm  "
            f"({self.face_count:,} tris"
            f"{'' if self.watertight else ', open'})"
        )


def split_bodies(
    mesh: trimesh.Trimesh, min_faces: int = 4
) -> list[trimesh.Trimesh]:
    """Split ``mesh`` into disconnected bodies, largest area first.

    Vertices are welded on a copy first: raw STLs store three vertices per
    triangle, which makes every triangle its own "body" otherwise. Bodies
    with fewer than ``min_faces`` triangles are dropped as debris.

    Args:
        mesh: Any mesh, possibly containing several parts.
        min_faces: Minimum triangle count for a body to be kept.

    Returns:
        Bodies sorted by surface area, descending. A single-body mesh
        returns a one-element list (the welded copy).
    """
    welded = mesh.copy()
    welded.merge_vertices(merge_tex=True, merge_norm=True)
    parts = welded.split(only_watertight=False)
    if len(parts) == 0:
        return [welded]
    parts = [p for p in parts if len(p.faces) >= min_faces]
    parts.sort(key=lambda p: -p.area)
    return parts if parts else [welded]


def body_infos(bodies: list[trimesh.Trimesh]) -> list[BodyInfo]:
    """Sidebar-ready summaries for a list of bodies."""
    return [
        BodyInfo(
            index=i,
            face_count=len(b.faces),
            extents=tuple(float(e) for e in b.extents),
            area=float(b.area),
            watertight=bool(b.is_watertight),
        )
        for i, b in enumerate(bodies)
    ]
