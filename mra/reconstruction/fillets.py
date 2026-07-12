"""Apply recognised fillets to the reconstructed solid.

Each Stage-3 FILLET feature carries a radius, an axis and the mesh region
it explains. This module finds the solid's edges that run through that
region parallel to the fillet axis and rounds them with
``BRepFilletAPI_MakeFillet``.

OpenCascade fillets fail readily on awkward topology, so every fillet is
attempted in isolation: a failure is logged and skipped, never fatal, and
the pre-fillet solid is kept when the result would be invalid.
"""

from __future__ import annotations

import numpy as np
import trimesh

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from mra.core import Feature, FeatureType


def apply_fillets(
    shape: TopoDS_Shape,
    mesh: trimesh.Trimesh,
    features: list[Feature],
    log: list[str],
    applied: list[int],
    skipped: list[int],
) -> TopoDS_Shape:
    """Round solid edges matching the recognised fillet features.

    Args:
        shape: The reconstructed solid (should be one solid already).
        mesh: Evidence mesh (fillet patches index into it).
        features: All intent features; only FILLETs are used.
        log: Build log lines (appended).
        applied: Feature ids successfully applied (appended).
        skipped: Feature ids that could not be applied (appended).

    Returns:
        The filleted shape (input shape when nothing could be applied).
    """
    fillets = [f for f in features if f.feature_type == FeatureType.FILLET]
    if not fillets:
        return shape

    ok = 0
    for feature in fillets:
        radius = float(feature.params["radius"])
        axis = np.asarray(feature.params["axis"], dtype=np.float64)
        patch = feature.patches[0]
        verts = mesh.vertices[np.unique(mesh.faces[patch.face_indices])]
        lo = verts.min(axis=0) - radius
        hi = verts.max(axis=0) + radius

        edges = _matching_edges(shape, lo, hi, axis)
        if not edges:
            skipped.append(feature.feature_id)
            continue

        try:
            maker = BRepFilletAPI_MakeFillet(shape)
            added = 0
            for edge in edges:
                try:
                    maker.Add(radius, edge)
                    added += 1
                except Exception:
                    continue  # seam/boundary edges can't take fillets
            if added == 0:
                raise RuntimeError("no filletable edges")
            maker.Build()
            if not maker.IsDone():
                raise RuntimeError("fillet build incomplete")
            candidate = maker.Shape()
        except Exception:
            skipped.append(feature.feature_id)
            continue
        if not BRepCheck_Analyzer(candidate).IsValid():
            skipped.append(feature.feature_id)
            continue
        shape = candidate
        applied.append(feature.feature_id)
        ok += 1

    if ok:
        log.append(f"Applied {ok}/{len(fillets)} recognised fillet(s).")
    if len(fillets) - ok:
        log.append(
            f"{len(fillets) - ok} fillet(s) could not be applied "
            "(no matching edge or OCC failure) — left as sharp edges."
        )
    return shape


def _matching_edges(
    shape: TopoDS_Shape, lo: np.ndarray, hi: np.ndarray, axis: np.ndarray
) -> list:
    """Edges inside the padded patch bbox, running parallel to ``axis``."""
    edges = []
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        edge = TopoDS.Edge_s(ex.Current())
        try:
            adaptor = BRepAdaptor_Curve(edge)
        except RuntimeError:
            ex.Next()
            continue
        t0, t1 = adaptor.FirstParameter(), adaptor.LastParameter()
        p0 = _to_np(adaptor.Value(t0))
        p1 = _to_np(adaptor.Value(t1))
        mid = _to_np(adaptor.Value((t0 + t1) / 2.0))
        if np.all(mid >= lo) and np.all(mid <= hi):
            direction = p1 - p0
            norm = np.linalg.norm(direction)
            if norm > 1e-9:
                direction /= norm
                if abs(direction @ axis) > 0.95:
                    edges.append(edge)
        ex.Next()
    return edges


def _to_np(pnt) -> np.ndarray:
    return np.array([pnt.X(), pnt.Y(), pnt.Z()])
