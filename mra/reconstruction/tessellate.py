"""Tessellate an OCP shape into a trimesh for viewport preview.

Preview-only: STEP export always uses the analytic shape, never this mesh.
"""

from __future__ import annotations

import numpy as np
import trimesh

from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_FACE, TopAbs_Orientation
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape


def shape_to_trimesh(
    shape: TopoDS_Shape, linear_deflection: float = 0.1
) -> trimesh.Trimesh:
    """Triangulate ``shape`` for display.

    Args:
        shape: Any OCP shape with faces.
        linear_deflection: Max chord deviation (mm); smaller = finer mesh.

    Returns:
        A single concatenated trimesh (not guaranteed watertight — for
        rendering only).
    """
    BRepMesh_IncrementalMesh(shape, linear_deflection, False, 0.5, True)

    all_vertices: list[np.ndarray] = []
    all_faces: list[np.ndarray] = []
    offset = 0

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            explorer.Next()
            continue
        transform = location.Transformation()

        n_nodes = triangulation.NbNodes()
        vertices = np.empty((n_nodes, 3))
        for i in range(1, n_nodes + 1):
            pnt = triangulation.Node(i).Transformed(transform)
            vertices[i - 1] = (pnt.X(), pnt.Y(), pnt.Z())

        n_tris = triangulation.NbTriangles()
        faces = np.empty((n_tris, 3), dtype=np.int64)
        reversed_face = face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED
        for i in range(1, n_tris + 1):
            tri = triangulation.Triangle(i)
            a, b, c = tri.Get()
            if reversed_face:
                a, c = c, a
            faces[i - 1] = (a - 1, b - 1, c - 1)

        all_vertices.append(vertices)
        all_faces.append(faces + offset)
        offset += n_nodes
        explorer.Next()

    if not all_vertices:
        return trimesh.Trimesh()
    return trimesh.Trimesh(
        vertices=np.vstack(all_vertices),
        faces=np.vstack(all_faces),
        process=False,
    )
