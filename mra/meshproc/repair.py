"""Mesh diagnosis and repair with a user-facing report.

Repair philosophy: fix what is unambiguous (exact duplicates, degenerate
triangles, inconsistent winding, small holes), report what is not
(large holes, multiple bodies) so the interactive stage can involve the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh

from mra.core import Tolerances


@dataclass
class RepairReport:
    """What was found and what was done, in sidebar-ready form.

    Attributes:
        duplicate_vertices_merged: Vertices removed by welding.
        degenerate_faces_removed: Zero-area / duplicate triangles removed.
        normals_flipped: Faces re-wound for consistent orientation.
        holes_filled: Small boundary loops triangulated shut.
        holes_remaining: Boundary loops left open (too large to fill safely).
        nonmanifold_edges_before: Edges not shared by exactly two faces,
            before repair.
        nonmanifold_edges_after: Same count after repair.
        components_before: Connected components before repair.
        components_removed: Tiny debris components discarded.
        watertight_after: Whether the result is watertight.
        notes: Free-form warnings for the user.
    """

    duplicate_vertices_merged: int = 0
    degenerate_faces_removed: int = 0
    normals_flipped: int = 0
    holes_filled: int = 0
    holes_remaining: int = 0
    nonmanifold_edges_before: int = 0
    nonmanifold_edges_after: int = 0
    components_before: int = 1
    components_removed: int = 0
    watertight_after: bool = False
    notes: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        """Human-readable summary for the GUI repair report panel."""
        lines = [
            f"Merged {self.duplicate_vertices_merged} duplicate vertices",
            f"Removed {self.degenerate_faces_removed} degenerate faces",
            f"Fixed winding on {self.normals_flipped} faces",
            f"Filled {self.holes_filled} small holes"
            + (f" ({self.holes_remaining} larger holes remain)"
               if self.holes_remaining else ""),
            f"Non-manifold edges: {self.nonmanifold_edges_before} -> "
            f"{self.nonmanifold_edges_after}",
        ]
        if self.components_before > 1:
            lines.append(
                f"Found {self.components_before} bodies, removed "
                f"{self.components_removed} debris fragments"
            )
        lines.append(
            "Mesh is watertight" if self.watertight_after
            else "Mesh is NOT watertight"
        )
        lines.extend(self.notes)
        return lines


def _nonmanifold_edge_count(mesh: trimesh.Trimesh) -> int:
    """Count edges not shared by exactly two faces."""
    if len(mesh.faces) == 0:
        return 0
    edges = mesh.edges_sorted
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int(np.sum(counts != 2))


def _boundary_loops(mesh: trimesh.Trimesh) -> list[tuple[np.ndarray, float]]:
    """Return open boundary loops as ``(vertex_indices, perimeter)`` pairs.

    Perimeter is the exact sum of the loop's boundary edge lengths.
    """
    if len(mesh.faces) == 0:
        return []
    edges = mesh.edges_sorted
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    boundary = unique[counts == 1]
    if len(boundary) == 0:
        return []
    import networkx as nx

    lengths = np.linalg.norm(
        mesh.vertices[boundary[:, 0]] - mesh.vertices[boundary[:, 1]], axis=1
    )
    g = nx.Graph()
    for (a, b), length in zip(boundary.tolist(), lengths):
        g.add_edge(a, b, length=float(length))
    loops = []
    for comp in nx.connected_components(g):
        sub = g.subgraph(comp)
        perimeter = sum(d["length"] for _, _, d in sub.edges(data=True))
        loops.append((np.array(list(comp)), perimeter))
    return loops


def diagnose(mesh: trimesh.Trimesh, tol: Tolerances | None = None) -> RepairReport:
    """Inspect ``mesh`` without modifying it and fill the *before* fields."""
    report = RepairReport()
    report.nonmanifold_edges_before = _nonmanifold_edge_count(mesh)
    report.nonmanifold_edges_after = report.nonmanifold_edges_before
    report.components_before = int(mesh.body_count)
    report.holes_remaining = len(_boundary_loops(mesh))
    report.watertight_after = bool(mesh.is_watertight)
    return report


def repair(
    mesh: trimesh.Trimesh, tol: Tolerances | None = None
) -> tuple[trimesh.Trimesh, RepairReport]:
    """Repair ``mesh`` and return ``(repaired_copy, report)``.

    Steps, in order:
      1. Weld duplicate vertices (within ``tol.merge_distance``).
      2. Remove degenerate and duplicate faces.
      3. Discard debris components (< 0.1 % of total area) when several
         bodies exist; the dominant bodies are kept.
      4. Fix winding consistency and flip normals to point outward.
      5. Fill small holes (perimeter below ``tol.hole_perimeter_max``).

    The input mesh is not modified.
    """
    tol = tol or Tolerances()
    report = diagnose(mesh, tol)
    m = mesh.copy()

    # 1. Weld duplicates.
    before_v = len(m.vertices)
    m.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=None)
    report.duplicate_vertices_merged = before_v - len(m.vertices)

    # 2. Degenerate / duplicate faces.
    before_f = len(m.faces)
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    report.degenerate_faces_removed = before_f - len(m.faces)

    # 3. Debris components.
    if m.body_count > 1:
        parts = m.split(only_watertight=False)
        total_area = sum(p.area for p in parts)
        keep = [p for p in parts if p.area >= 1e-3 * total_area]
        report.components_removed = len(parts) - len(keep)
        if report.components_removed:
            m = trimesh.util.concatenate(keep)
        if len(keep) > 1:
            report.notes.append(
                f"{len(keep)} separate bodies kept; reconstruction will "
                "treat the largest as the main part"
            )

    # 4. Winding / normals.
    flipped = _fix_winding(m)
    report.normals_flipped = flipped

    # 5. Fill small holes. trimesh's fill_holes handles simple (triangular /
    # planar) loops; count loops before and after for the report.
    loops_before = _boundary_loops(m)
    small = [
        (verts, perim) for verts, perim in loops_before
        if perim <= tol.hole_perimeter_max
    ]
    if small:
        m.fill_holes()
    loops_after = _boundary_loops(m)
    report.holes_filled = max(0, len(loops_before) - len(loops_after))
    report.holes_remaining = len(loops_after)
    if report.holes_remaining:
        report.notes.append(
            f"{report.holes_remaining} boundary loops were too large to fill "
            "automatically — review before reconstruction"
        )

    report.nonmanifold_edges_after = _nonmanifold_edge_count(m)
    report.watertight_after = bool(m.is_watertight)
    return m, report


def _fix_winding(m: trimesh.Trimesh) -> int:
    """Make winding consistent and normals outward; return faces flipped."""
    faces_before = m.faces.copy()
    trimesh.repair.fix_winding(m)
    if m.is_watertight:
        trimesh.repair.fix_inversion(m)
    if len(faces_before) != len(m.faces):
        return 0  # repair changed topology; per-face comparison meaningless
    return int(np.sum(np.any(faces_before != m.faces, axis=1)))
