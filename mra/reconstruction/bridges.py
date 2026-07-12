"""Reconnect disjoint solids using the mesh's connective tissue.

Molded parts attach towers, ribs and bosses to the body through blends and
gussets — regions Stage 2 typically labels FREEFORM (or small fillet
cylinders). The terrace cuts remove material around them, leaving attached
pieces floating.

The fix is graph-targeted: each floating solid is mapped back to the mesh
patches it was built from; the patches ADJACENT to those in the mesh are
the real connectors. Each connector region is rebuilt as a slightly
inflated convex hull (a gusset is a convex prism, so its hull IS the
gusset; the inflation guarantees the fuse actually penetrates both sides)
and fused in.

This intentionally converts cosmetic blends into simple machinable webs —
per the project philosophy of preferring manufacturable geometry over
triangle fidelity.
"""

from __future__ import annotations

import numpy as np
import trimesh

from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeSolid,
    BRepBuilderAPI_Sewing,
)
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from mra.recognition import SegmentationResult

# Connector patches larger than this fraction of total patch area are
# structural surfaces, not blends; never hull them.
_MAX_BRIDGE_AREA_FRACTION = 0.05
# Hull inflation (mm): guarantees the fused web penetrates both sides
# instead of touching at zero thickness.
_HULL_INFLATION = 0.1
# A patch belongs to a floating solid when at least this fraction of its
# vertices lie inside the solid's (slightly inflated) bounding box.
_ASSIGN_FRACTION = 0.6


def _shape_volume(shape: TopoDS_Shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def solid_count(shape: TopoDS_Shape) -> int:
    """Number of solids in ``shape``."""
    n = 0
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        n += 1
        ex.Next()
    return n


def bridge_disconnected(
    shape: TopoDS_Shape,
    mesh: trimesh.Trimesh,
    segmentation: SegmentationResult,
    log: list[str],
    direction: np.ndarray | None = None,
) -> TopoDS_Shape:
    """Fuse connector-region webs until the shape is one solid.

    Bridge webs may cover hole bores; that is fine — every cutting
    operation (holes, windows, cutouts) runs AFTER bridging with cutters
    spanning the full part, so cuts always win.

    Args:
        shape: The (possibly disjoint) reconstructed shape.
        mesh: The evidence mesh the patches index into.
        segmentation: Stage-2 result (patches + per-face patch ids).
        log: Build log to append progress lines to.
        direction: Extrusion axis. When given, connector regions are
            filled with VERTICAL PRISMS (the footprint extruded straight
            through the part thickness) — a clean machinable rib. Without
            it, the legacy 3D convex hull is used, which can render as a
            diagonal "spoke".

    Returns:
        The bridged shape (single solid when successful).
    """
    before = solid_count(shape)
    if before <= 1:
        return shape

    patches = segmentation.patches
    total_area = sum(p.area for p in patches) or 1.0
    patch_adjacency = _patch_adjacency(mesh, segmentation)

    web_ctx = None
    if direction is not None:
        d = np.asarray(direction, dtype=np.float64)
        along = np.asarray(mesh.vertices) @ d
        web_ctx = (d, float(along.min()), float(along.max()))

    def make_web(verts: np.ndarray) -> TopoDS_Shape | None:
        if web_ctx is not None:
            web = _prismatic_web(verts, *web_ctx)
            if web is not None:
                return web
        return _convex_hull_solid(verts, _HULL_INFLATION)

    # Phase 1: hull each connector patch adjacent to a floating solid.
    # Catches volumetric blends (gussets, fillet coves) around towers.
    # Hulls are COLLECTED and fused in one multi-tool operation — long
    # chains of sequential fuses accumulate coincident-face debris that
    # makes later boolean cuts fail partially (observed: plugged bores).
    hulls: list[TopoDS_Shape] = []
    minors = _minor_solid_bboxes(shape)
    if minors:
        connector_ids = _connector_patches(
            mesh, patches, patch_adjacency, minors
        )
        for pid in sorted(connector_ids, key=lambda i: patches[i].area):
            patch = patches[pid]
            if patch.area / total_area > _MAX_BRIDGE_AREA_FRACTION:
                continue
            verts = mesh.vertices[np.unique(mesh.faces[patch.face_indices])]
            web = make_web(verts)
            if web is not None:
                hulls.append(web)
    if hulls:
        shape = _fuse_many(shape, hulls)

    # Phase 2: floating pieces whose connectors are thin planar webs
    # (zero-thickness hulls). Hull the piece's own region TOGETHER with
    # its small web neighbours — that hull has real thickness and lands
    # on the main body's skin.
    if solid_count(shape) > 1:
        hulls = []
        for bbox in _minor_solid_bboxes(shape):
            member_ids = _patches_in_bbox(mesh, patches, bbox)
            if not member_ids:
                continue
            group = set(member_ids)
            for pid in member_ids:
                for nb in patch_adjacency.get(pid, set()):
                    if patches[nb].area <= 30.0:
                        group.add(nb)
            faces = np.concatenate(
                [patches[pid].face_indices for pid in group]
            )
            verts = mesh.vertices[np.unique(mesh.faces[faces])]
            web = make_web(verts)
            if web is not None:
                hulls.append(web)
        if hulls:
            shape = _fuse_many(shape, hulls)

    after = solid_count(shape)
    if after < before:
        kind = "prismatic ribs" if web_ctx is not None else "hull webs"
        log.append(
            f"Bridged blend/gusset regions as {kind}: "
            f"{before} -> {after} solid(s)."
        )
    return shape


# ---------------------------------------------------------------- helpers

def _try_fuse_hull(shape: TopoDS_Shape, verts: np.ndarray) -> TopoDS_Shape:
    """Fuse the inflated hull of ``verts`` if it is sane and lossless."""
    hull_solid = _convex_hull_solid(verts, _HULL_INFLATION)
    if hull_solid is None:
        return shape
    return _fuse_many(shape, [hull_solid])


def _fuse_many(
    shape: TopoDS_Shape, hulls: list[TopoDS_Shape]
) -> TopoDS_Shape:
    """One multi-tool fuzzy fuse of all hulls; lossless or rejected.

    A single N-tool fuse produces far cleaner topology than N chained
    fuses; the fuzzy value lets OCC merge near-coincident faces instead
    of leaving sliver shells that break later cuts.
    """
    from OCP.TopTools import TopTools_ListOfShape

    try:
        volume_before = _shape_volume(shape)
        fuse = BRepAlgoAPI_Fuse()
        args = TopTools_ListOfShape()
        args.Append(shape)
        tools = TopTools_ListOfShape()
        for h in hulls:
            tools.Append(h)
        fuse.SetArguments(args)
        fuse.SetTools(tools)
        fuse.SetFuzzyValue(1e-5)
        fuse.Build()
        if not fuse.IsDone():
            return shape
        candidate = fuse.Shape()
        # A bad hull (inside-out sewing, self-intersection) can gut the
        # whole shape; a fuse must never LOSE volume.
        if _shape_volume(candidate) < volume_before * 0.999:
            return shape
        return candidate
    except RuntimeError:
        return shape


def _patches_in_bbox(
    mesh: trimesh.Trimesh, patches, bbox: np.ndarray
) -> list[int]:
    """Patch ids whose vertices lie mostly inside ``bbox``."""
    members = []
    for patch in patches:
        verts = mesh.vertices[np.unique(mesh.faces[patch.face_indices])]
        inside = np.all((verts >= bbox[0]) & (verts <= bbox[1]), axis=1)
        if np.mean(inside) >= _ASSIGN_FRACTION:
            members.append(patch.patch_id)
    return members

def _minor_solid_bboxes(shape: TopoDS_Shape) -> list[np.ndarray]:
    """Inflated bboxes of every solid except the largest, as (2, 3)."""
    entries = []
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        box = Bnd_Box()
        BRepBndLib.Add_s(ex.Current(), box)
        x0, y0, z0, x1, y1, z1 = box.Get()
        bbox = np.array([[x0, y0, z0], [x1, y1, z1]])
        volume = np.prod(bbox[1] - bbox[0])
        entries.append((volume, bbox))
        ex.Next()
    if len(entries) <= 1:
        return []
    entries.sort(key=lambda e: -e[0])
    pad = 0.3
    return [bbox + np.array([[-pad] * 3, [pad] * 3])
            for _, bbox in entries[1:]]


def _patch_adjacency(
    mesh: trimesh.Trimesh, segmentation: SegmentationResult
) -> dict[int, set[int]]:
    """patch id -> ids of patches sharing at least one mesh edge."""
    ids = segmentation.face_patch_ids
    pairs = np.asarray(mesh.face_adjacency)
    a, b = ids[pairs[:, 0]], ids[pairs[:, 1]]
    diff = (a != b) & (a >= 0) & (b >= 0)
    adjacency: dict[int, set[int]] = {}
    for pa, pb in zip(a[diff], b[diff]):
        adjacency.setdefault(int(pa), set()).add(int(pb))
        adjacency.setdefault(int(pb), set()).add(int(pa))
    return adjacency


def _connector_patches(
    mesh: trimesh.Trimesh,
    patches,
    adjacency: dict[int, set[int]],
    minor_bboxes: list[np.ndarray],
    rings: int = 1,
) -> set[int]:
    """Patches within ``rings`` adjacency hops of any floating solid.

    A floating solid's patches are those whose vertices lie mostly inside
    its bounding box; their mesh neighbours (and neighbours-of-neighbours
    when the connective chain is longer, e.g. rib -> end face -> gusset)
    are the connective regions that were never built.
    """
    connector_ids: set[int] = set()
    for bbox in minor_bboxes:
        member_ids = _patches_in_bbox(mesh, patches, bbox)
        members = set(member_ids)
        frontier = set(member_ids)
        reached: set[int] = set()
        for _ in range(rings):
            frontier = {
                nb for pid in frontier
                for nb in adjacency.get(pid, set())
            } - members - reached
            reached |= frontier
        connector_ids.update(reached)
    return connector_ids


def _convex_hull_solid(
    points: np.ndarray, inflation: float
) -> TopoDS_Shape | None:
    """Convex hull of ``points``, inflated outward, as an OCP solid."""
    if len(points) < 4:
        return None
    return _convex_hull_solid_impl(points, inflation)


def _prismatic_web(
    points: np.ndarray,
    direction: np.ndarray,
    span_lo: float,
    span_hi: float,
) -> TopoDS_Shape | None:
    """A clean vertical rib: the 2D footprint of ``points`` extruded
    straight through only its OWN local thickness along ``direction``.

    Convex-hull webs span diagonally from a floating boss down to the
    plate and render as "spokes"; a footprint prism instead rises flush,
    reading as a machinable rib. Extruding only the connector region's own
    along-axis extent (not the whole part) keeps the rib from becoming a
    tall extra wall — it fills exactly the blend gap it needs to. The
    passed part span only clamps it. Trimmed to the envelope afterward.
    """
    from shapely.geometry import MultiPoint
    from mra.recognition.fitting import _orthonormal_basis
    from OCP.gp import gp_Pnt

    d = np.asarray(direction, dtype=np.float64)
    b1, b2 = _orthonormal_basis(d)
    pts = np.asarray(points, dtype=np.float64)
    uv = np.column_stack([pts @ b1, pts @ b2])
    hull2d = MultiPoint([tuple(p) for p in uv]).convex_hull
    if hull2d.geom_type != "Polygon" or hull2d.area < 0.2:
        return None
    # Small outward buffer so the rib overlaps both pieces it joins.
    hull2d = hull2d.buffer(0.15, join_style=2)
    ring = np.asarray(hull2d.exterior.coords)[:-1]
    if len(ring) < 3:
        return None

    # Span only the connector's own thickness (+ a little overlap into the
    # pieces it bridges), clamped to the part envelope.
    along = pts @ d
    margin = 0.3
    lo = max(float(along.min()) - margin, span_lo - 0.1)
    hi = min(float(along.max()) + margin, span_hi + 0.1)
    height = hi - lo
    if height <= 0:
        return None

    polygon = BRepBuilderAPI_MakePolygon()
    for u, v in ring:
        p = u * b1 + v * b2 + lo * d
        polygon.Add(gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
    polygon.Close()
    if not polygon.IsDone():
        return None
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace as _MF
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism as _Prism
    from OCP.gp import gp_Vec as _Vec

    face = _MF(polygon.Wire(), True)
    if not face.IsDone():
        return None
    vec = d * height
    solid = _Prism(
        face.Face(), _Vec(float(vec[0]), float(vec[1]), float(vec[2]))
    ).Shape()
    return solid if _shape_volume(solid) > 0 else None


def _convex_hull_solid_impl(
    points: np.ndarray, inflation: float
) -> TopoDS_Shape | None:
    if len(points) < 4:
        return None
    try:
        hull = trimesh.convex.convex_hull(points)
    except Exception:
        return None
    # Near-flat regions (a lone plane strip) make degenerate slivers that
    # poison boolean fuses — require a real 3D hull.
    if len(hull.faces) < 4 or not np.isfinite(hull.volume) \
            or hull.volume <= 0.05:
        return None
    if inflation > 0:
        centroid = hull.vertices.mean(axis=0)
        offsets = hull.vertices - centroid
        norms = np.linalg.norm(offsets, axis=1, keepdims=True)
        norms[norms < 1e-12] = 1.0
        hull = trimesh.Trimesh(
            vertices=hull.vertices + offsets / norms * inflation,
            faces=hull.faces,
            process=False,
        )

    from OCP.gp import gp_Pnt

    sewing = BRepBuilderAPI_Sewing(1e-6)
    for tri in hull.faces:
        polygon = BRepBuilderAPI_MakePolygon()
        for vi in tri:
            p = hull.vertices[vi]
            polygon.Add(gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
        polygon.Close()
        if not polygon.IsDone():
            return None
        face = BRepBuilderAPI_MakeFace(polygon.Wire(), True)
        if not face.IsDone():
            return None
        sewing.Add(face.Face())
    sewing.Perform()
    sewn = sewing.SewedShape()

    ex = TopExp_Explorer(sewn, TopAbs_SHELL)
    if not ex.More():
        return None
    maker = BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(ex.Current()))
    if not maker.IsDone():
        return None
    solid = maker.Solid()
    # Sewing can produce an inside-out shell; such a "solid" reports a
    # negative or wildly wrong volume and must never reach a fuse.
    ocp_volume = _shape_volume(solid)
    if ocp_volume <= 0 or abs(ocp_volume - hull.volume) > 0.5 * hull.volume:
        return None
    return solid
