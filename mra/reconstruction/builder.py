"""Build an OpenCascade B-Rep solid from recovered design intent.

Current capability (v1): the *extruded part* class — a planar profile
extruded along one direction, minus drilled holes and non-circular cutouts,
plus cylindrical bosses. This covers plates, brackets, spacers and simple
enclosure halves. Fillets recovered by Stage 3 are reported but not yet
applied to the solid.

Everything produced here is true analytic geometry: planar faces, real
cylinders — never tessellations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
)
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
)
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec
from OCP.TopoDS import TopoDS_Shape, TopoDS_Wire

from mra.core import Feature, FeatureType, SurfaceType, Tolerances
from mra.core.loops import (
    boundary_loops_3d,
    loop_is_circle,
    loop_is_rounded_rect,
    simplify_loop,
)
from mra.intent import IntentResult
from mra.recognition import SegmentationResult


@dataclass
class BuildResult:
    """Stage-5 output.

    Attributes:
        shape: The reconstructed solid, or None when building failed.
        log: Human-readable build steps and warnings for the sidebar.
        applied_features: Feature ids incorporated into the solid.
        skipped_features: Feature ids recognised but not yet buildable.
    """

    shape: TopoDS_Shape | None = None
    log: list[str] = field(default_factory=list)
    applied_features: list[int] = field(default_factory=list)
    skipped_features: list[int] = field(default_factory=list)


def build_solid(
    mesh: trimesh.Trimesh,
    segmentation: SegmentationResult,
    intent: IntentResult,
    tol: Tolerances | None = None,
) -> BuildResult:
    """Reconstruct an analytic solid from the recovered feature set.

    Args:
        mesh: Repaired evidence mesh (profiles are lifted from it).
        segmentation: Stage-2 patches.
        intent: Stage-3 features.
        tol: Tolerances (profile simplification uses the angular one).

    Returns:
        BuildResult; ``shape`` is None when no buildable base was found.
    """
    tol = tol or Tolerances()
    result = BuildResult()

    extrusion = next(
        (f for f in intent.features
         if f.feature_type == FeatureType.EXTRUSION), None
    )
    if extrusion is None:
        result.log.append(
            "No extrusion base recovered — cannot reconstruct this part "
            "class yet."
        )
        return result

    shape = _build_base(mesh, segmentation, extrusion, intent, tol, result)
    if shape is None:
        return result

    # Material first: pads, pockets (which can disconnect pieces), boss
    # fuses, then bridge the disconnects and apply fillets. All CUTS come
    # after — holes, windows and wall cutouts are made exactly once,
    # through the final material, so a bridge hull can never plug them.
    shape = _apply_pads(shape, mesh, intent, tol, result)
    shape = _apply_pockets(shape, mesh, intent, tol, result)
    shape = _apply_cylindrical_features(
        shape, intent, extrusion, result, bosses_only=True
    )
    shape = _prune_debris_solids(shape, result)

    from mra.reconstruction.bridges import bridge_disconnected
    from mra.reconstruction.fillets import apply_fillets

    shape = bridge_disconnected(
        shape, mesh, segmentation, result.log,
        direction=np.asarray(extrusion.params["direction"], dtype=np.float64),
    )
    shape = apply_fillets(
        shape, mesh, intent.features, result.log,
        result.applied_features, result.skipped_features,
    )
    # Clean up before cutting: fuse chains (fills, bridges) leave
    # coincident-face debris on which a later cut can return an EMPTY
    # shape while reporting success.
    shape = _unify_faces(shape, result)

    shape = _apply_cylindrical_features(
        shape, intent, extrusion, result, holes_only=True
    )
    shape = _cut_unclaimed_openings(
        shape, mesh, segmentation, intent, extrusion, tol, result
    )
    shape = _apply_wall_cutouts(shape, intent, tol, result)
    shape = _clamp_to_envelope(shape, mesh, result)
    shape = _prune_debris_solids(shape, result)
    shape = _unify_faces(shape, result)
    result.shape = shape
    return result


def build_sheet(
    mesh: trimesh.Trimesh,
    segmentation: SegmentationResult,
    intent: IntentResult,
    thickness: float | None = None,
    tol: Tolerances | None = None,
) -> BuildResult:
    """Reconstruct a FLAT SHEET-METAL version of the part.

    Drops every depth feature — towers, pads, pockets, bosses, recesses —
    and keeps only the 2D footprint (outer profile plus all through
    openings) extruded to one uniform thickness. This is the laser/
    waterjet-cuttable form of the part: on services like SendCutSend a
    flat sheet part costs a fraction of a 3-axis machined one.

    Circular and rounded-rect openings are recovered as true arcs by the
    profile builder, so bolt holes come out as clean circles.

    Args:
        mesh: Repaired evidence mesh.
        segmentation: Stage-2 patches.
        intent: Stage-3 features (used for the default thickness and to
            keep hole diameters the user set in the wizard).
        thickness: Sheet thickness (mm). Defaults to the detected common
            wall thickness, else the extrusion height, else 2 mm.
        tol: Tolerances.

    Returns:
        BuildResult with a flat plate solid.
    """
    tol = tol or Tolerances()
    result = BuildResult()

    extrusion = next(
        (f for f in intent.features
         if f.feature_type == FeatureType.EXTRUSION), None
    )
    direction = (np.asarray(extrusion.params["direction"], dtype=np.float64)
                 if extrusion is not None else np.array([0.0, 0.0, 1.0]))
    along = np.asarray(mesh.vertices, dtype=np.float64) @ direction
    outer_off = float(along.min())

    if thickness is None:
        thickness = (intent.wall_thickness
                     or (extrusion.params["height"] if extrusion else None)
                     or 2.0)
    thickness = float(thickness)

    face = _footprint_face(mesh, direction, outer_off, tol, result)
    if face is None:
        result.log.append("Could not build the sheet footprint.")
        return result

    shape = BRepPrimAPI_MakePrism(
        face, gp_Vec(*(float(c) for c in direction * thickness))
    ).Shape()

    # Re-cut analytic bores for any hole the user re-sized in the wizard
    # (the wizard changes diameters after the footprint was measured).
    if extrusion is not None:
        for feature in intent.features:
            if feature.feature_type != FeatureType.HOLE:
                continue
            try:
                shape = _boolean(shape, _hole_cutter(feature), cut=True)
            except RuntimeError:
                continue

    shape = _unify_faces(shape, result)
    result.shape = shape
    result.log.append(
        f"Flat sheet: footprint extruded to {thickness:.2f} mm "
        "(all depth features dropped)."
    )
    return result


def _footprint_face(
    mesh: trimesh.Trimesh,
    direction: np.ndarray,
    plane_off: float,
    tol: Tolerances,
    result: BuildResult,
):
    """Planar face of the part's full 2D footprint (outline + through-holes).

    Unions every triangle projected onto the plane perpendicular to the
    extrusion direction, giving the true outer boundary plus the interior
    loops of genuine through-openings. Circular/rounded-rect loops become
    true arcs via ``_wire_from_loop``. Returns None on any failure.
    """
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        from mra.recognition.fitting import _orthonormal_basis

        b1, b2 = _orthonormal_basis(np.asarray(direction, dtype=np.float64))
        verts = np.asarray(mesh.vertices, dtype=np.float64)
        uv = np.column_stack([verts @ b1, verts @ b2])
        tris = [Polygon(uv[f]) for f in mesh.faces]
        tris = [t for t in tris if t.is_valid and t.area > 1e-9]
        if not tris:
            return None
        union = unary_union(tris)
        poly = (max(union.geoms, key=lambda p: p.area)
                if union.geom_type == "MultiPolygon" else union)
        poly = poly.simplify(0.05, preserve_topology=True)
        if poly.is_empty or poly.area < 1e-6:
            return None

        def ring_to_3d(coords) -> np.ndarray:
            pts = np.asarray(coords)[:-1]  # drop the closing duplicate
            return (np.outer(pts[:, 0], b1) + np.outer(pts[:, 1], b2)
                    + np.outer(np.full(len(pts), plane_off), direction))

        # Shapely can emit degenerate interior rings (zero-area slivers
        # where feature edges touch in projection); they crash the wire
        # builder, so keep only real openings.
        interiors = [
            r for r in poly.interiors
            if Polygon(r).area > max(0.25, tol.min_feature_size**2)
        ]
        loops = [ring_to_3d(poly.exterior.coords)]
        loops += [ring_to_3d(r.coords) for r in interiors]
        face = _face_with_holes(loops, direction, tol, result)
        if face is not None:
            result.log.append(
                f"Footprint: {len(interiors)} through-opening(s)."
            )
        return face
    except Exception as exc:
        result.log.append(f"Footprint unavailable ({exc}).")
        return None


def _clamp_to_envelope(
    shape: TopoDS_Shape, mesh: trimesh.Trimesh, result: BuildResult
) -> TopoDS_Shape:
    """Trim everything outside the scan's bounding box.

    Bridge hulls are inflated for fuse robustness and can poke ~0.1 mm
    past the part's outer faces — visible warts on flat machined
    surfaces. Nothing reconstructed may exceed the evidence envelope.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    lo, hi = mesh.bounds
    size = hi - lo
    box = BRepPrimAPI_MakeBox(
        gp_Pnt(float(lo[0]), float(lo[1]), float(lo[2])),
        float(size[0]), float(size[1]), float(size[2]),
    ).Shape()
    try:
        common = BRepAlgoAPI_Common(shape, box)
        common.SetFuzzyValue(1e-5)
        common.Build()
        if not common.IsDone():
            return shape
        return common.Shape()
    except RuntimeError:
        return shape


def _unify_faces(shape: TopoDS_Shape, result: BuildResult) -> TopoDS_Shape:
    """Merge same-surface faces split by boolean chains.

    Bore walls fragmented into stacked cylinder segments confuse CAM hole
    detection ("not all holes identified"); coplanar face fragments and
    sliver rings clutter flat surfaces. UnifySameDomain rebuilds each
    maximal face in one piece.
    """
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    try:
        unify = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
        unify.Build()
        candidate = unify.Shape()
    except RuntimeError:
        return shape
    if not BRepCheck_Analyzer(candidate).IsValid():
        return shape
    return candidate


def _prune_debris_solids(
    shape: TopoDS_Shape, result: BuildResult
) -> TopoDS_Shape:
    """Drop microscopic solids left behind by boolean noise.

    Cut/fuse chains can shed slivers when cut faces nearly coincide with
    part faces. Solids below 0.1 % of the largest volume are noise and are
    removed; genuinely disjoint pieces above that are kept and reported —
    they mean real connecting geometry was not reconstructed.
    """
    from OCP.BRep import BRep_Builder
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Compound

    solids = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(explorer.Current(), props)
        solids.append((explorer.Current(), abs(props.Mass())))
        explorer.Next()
    if len(solids) <= 1:
        return shape

    # Absolute sliver threshold: boolean noise is sub-0.001 mm^3, while
    # real thin webs and ribs on small parts can be just a few mm^3 — a
    # percentage-of-part threshold silently deleted those.
    keep = [s for s, v in solids if v >= 1e-3]
    dropped = len(solids) - len(keep)
    if dropped:
        result.log.append(
            f"Removed {dropped} sliver solid(s) left by boolean noise."
        )
    if len(keep) == 1:
        return keep[0]
    result.log.append(
        f"WARNING: {len(keep)} disjoint solids remain — connecting "
        "geometry (fillets/blends) was not reconstructed."
    )
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for s in keep:
        builder.Add(compound, s)
    return compound


# ------------------------------------------------------------------- base

def _build_base(
    mesh: trimesh.Trimesh,
    segmentation: SegmentationResult,
    extrusion: Feature,
    intent: IntentResult,
    tol: Tolerances,
    result: BuildResult,
) -> TopoDS_Shape | None:
    direction = np.asarray(extrusion.params["direction"], dtype=np.float64)
    height = float(extrusion.params["height"])
    # patches = [top, bottom, profile_source]; the profile source is the
    # cap with the widest outline — the part silhouette — which on stepped
    # plates can sit at an intermediate level.
    top, bottom = extrusion.patches[0], extrusion.patches[1]
    profile_patch = (extrusion.patches[2] if len(extrusion.patches) > 2
                     else bottom)
    bottom_off = float(
        np.asarray(bottom.params["origin"], dtype=np.float64) @ direction
    )

    loops = boundary_loops_3d(mesh, profile_patch.face_indices)
    if not loops:
        result.log.append("Silhouette cap has no closed boundary loop.")
        return None

    # Project the silhouette onto the bottom plane of the base solid.
    outer = loops[0] + np.outer(
        bottom_off - loops[0] @ direction, direction
    )
    wire = _wire_from_loop(outer, direction, tol)
    if wire is None:
        result.log.append("Failed to build the profile wire.")
        return None
    result.log.append(
        f"Base profile from {len(loops[0])} mesh vertices, "
        f"extruded {height:.2f} mm."
    )
    face = BRepBuilderAPI_MakeFace(wire, True)
    if not face.IsDone():
        result.log.append("Failed to build the profile face.")
        return None

    extrude_vec = direction * height
    prism = BRepPrimAPI_MakePrism(
        face.Face(), gp_Vec(*(float(c) for c in extrude_vec))
    )
    shape = prism.Shape()
    result.applied_features.append(extrusion.feature_id)

    # Non-circular inner loops of the BOTTOM cap are through-openings.
    # A loop around a blind recess must NOT be tunnelled through — the
    # pocket pass models those — so require a clear ray through the part.
    for inner in boundary_loops_3d(mesh, bottom.face_indices)[1:]:
        if loop_is_circle(inner) is not None:
            continue  # round openings handled globally afterwards
        if not _loop_region_open(mesh, inner, direction):
            continue
        cut_shape = _prism_cut_from_loop(inner, extrude_vec, result)
        if cut_shape is not None:
            shape = _boolean(shape, cut_shape, cut=True)
            result.log.append(
                f"Cut non-circular cap opening ({len(inner)} boundary points)."
            )
    return shape


def _cut_unclaimed_openings(
    shape: TopoDS_Shape,
    mesh: trimesh.Trimesh,
    segmentation: SegmentationResult,
    intent: IntentResult,
    extrusion: Feature,
    tol: Tolerances,
    result: BuildResult,
    quiet: bool = False,
) -> TopoDS_Shape:
    """Cut non-circular through-openings on any cap-parallel plane.

    Rectangular windows and slots pierce pocket floors and pad tops, not
    just the bottom cap; their inner loops must be cut wherever they
    appear. Round openings are recovered as HOLE features by intent and
    cut analytically, so only non-circles are handled here. Islands
    (loops around towers) and blind recesses are excluded by requiring a
    clear ray through the part in both directions.
    """
    direction = np.asarray(extrusion.params["direction"], dtype=np.float64)
    height = float(extrusion.params["height"])
    bottom_off = float(
        np.asarray(extrusion.patches[1].params["origin"]) @ direction
    )
    seen_centroids: list[np.ndarray] = []

    for patch in segmentation.patches:
        if patch.surface_type != SurfaceType.PLANE:
            continue
        if abs(np.asarray(patch.params["normal"]) @ direction) < 0.999:
            continue
        for inner in boundary_loops_3d(mesh, patch.face_indices)[1:]:
            if loop_is_circle(inner) is not None:
                continue
            centroid = inner.mean(axis=0)
            if any(np.linalg.norm(centroid - s) < 1.0
                   for s in seen_centroids):
                continue  # same window seen from the other face
            from mra.core.loops import region_is_through

            if not region_is_through(mesh, inner, direction):
                continue  # blind recess or island, not a through window
            # Project the loop to below the part and cut a full-height
            # prism with margins on both ends.
            margin = 0.1 * height
            lifted = inner + np.outer(
                bottom_off - margin - inner @ direction, direction
            )
            wire = _wire_from_loop(lifted, direction, tol)
            if wire is None:
                continue
            face = BRepBuilderAPI_MakeFace(wire, True)
            if not face.IsDone():
                continue
            cut = BRepPrimAPI_MakePrism(
                face.Face(),
                gp_Vec(*(float(c) for c in direction * (height + 2 * margin))),
            ).Shape()
            try:
                shape = _boolean(shape, cut, cut=True)
            except RuntimeError as exc:
                result.log.append(f"SKIPPED window: {exc}")
                continue
            seen_centroids.append(centroid)
            if not quiet:
                result.log.append(
                    f"Cut through window ({len(inner)} outline points)."
                )
    return shape


def _loop_region_open(
    mesh: trimesh.Trimesh, loop: np.ndarray, direction: np.ndarray
) -> bool:
    """Whether the region inside a cap loop is a through-opening.

    Casts a ray from the loop's centroid (nudged past the cap plane)
    along the extrusion; hitting the mesh means a blind recess floor or
    internal structure — not a through-opening.
    """
    centroid = loop.mean(axis=0)
    origin = centroid + direction * 1e-3
    hit = mesh.ray.intersects_any(
        origin[None, :], direction[None, :]
    )
    return not bool(hit[0])


def _prism_cut_from_loop(
    loop: np.ndarray, extrude_vec: np.ndarray, result: BuildResult
) -> TopoDS_Shape | None:
    simplified = simplify_loop(loop)
    polygon = BRepBuilderAPI_MakePolygon()
    # Extend the cut slightly beyond both faces for robust booleans.
    margin = 0.05 * np.linalg.norm(extrude_vec)
    unit = extrude_vec / np.linalg.norm(extrude_vec)
    for p in simplified:
        q = p - unit * margin
        polygon.Add(gp_Pnt(*(float(c) for c in q)))
    polygon.Close()
    if not polygon.IsDone():
        result.log.append("Skipped one cap opening (bad boundary loop).")
        return None
    face = BRepBuilderAPI_MakeFace(polygon.Wire(), True)
    if not face.IsDone():
        result.log.append("Skipped one cap opening (bad profile face).")
        return None
    vec = extrude_vec + unit * 2 * margin
    return BRepPrimAPI_MakePrism(
        face.Face(), gp_Vec(*(float(c) for c in vec))
    ).Shape()


# ------------------------------------------------------------------- pads

def _apply_pads(
    shape: TopoDS_Shape,
    mesh: trimesh.Trimesh,
    intent: IntentResult,
    tol: Tolerances,
    result: BuildResult,
) -> TopoDS_Shape:
    """Fuse raised plateaus: the pad's own region extruded from the
    silhouette face up to the pad level. Inner loops stay as openings."""
    for feature in intent.features:
        if feature.feature_type != FeatureType.PAD:
            continue
        patch = feature.patches[0]
        direction = np.asarray(feature.params["direction"], dtype=np.float64)
        side = int(feature.params["side"])
        height = float(feature.params["height"])

        loops = boundary_loops_3d(mesh, patch.face_indices)
        if not loops:
            result.log.append("Skipped one pad (no boundary loop).")
            result.skipped_features.append(feature.feature_id)
            continue

        outward = direction * side
        # The pad top loops are at the pad level; extrude back toward the
        # silhouette face (slight sink for a robust fuse).
        margin = max(0.05 * height, 0.05)
        face = _face_with_holes(loops, direction, tol, result)
        if face is None:
            result.skipped_features.append(feature.feature_id)
            continue
        vec = -outward * (height + margin)
        pad = BRepPrimAPI_MakePrism(face, gp_Vec(*(float(c) for c in vec)))
        shape = _guarded_fuse(
            shape, pad.Shape(), result, f"pad ({height:.2f} mm)"
        )
        result.applied_features.append(feature.feature_id)
        result.log.append(
            f"Fused {height:.2f} mm pad "
            f"({'top' if side > 0 else 'bottom'} side, "
            f"{len(loops) - 1} opening(s))."
        )
    return shape


# ---------------------------------------------------------------- pockets

def _apply_pockets(
    shape: TopoDS_Shape,
    mesh: trimesh.Trimesh,
    intent: IntentResult,
    tol: Tolerances,
    result: BuildResult,
) -> TopoDS_Shape:
    """Cut terrace pockets: each planar level's own region, sunk to its
    depth from the silhouette face on its side.

    The pocket face keeps the patch's inner loops as islands, so bosses
    and standoffs rising through a pocket floor survive the cut.
    """
    for feature in intent.features:
        if feature.feature_type != FeatureType.POCKET:
            continue
        patch = feature.patches[0]
        direction = np.asarray(feature.params["direction"], dtype=np.float64)
        side = int(feature.params["side"])
        depth = float(feature.params["depth"])

        loops = boundary_loops_3d(mesh, patch.face_indices)
        if not loops:
            result.log.append("Skipped one pocket (no boundary loop).")
            result.skipped_features.append(feature.feature_id)
            continue

        outward = direction * side
        margin = max(0.05 * depth, 0.05)

        if feature.params.get("fill"):
            # User chose to FILL this recess: fuse the floor region out
            # to the face. Explicit addition — some recesses are voids
            # under floating pads (notched silhouette), not cuts, so
            # skipping the cut alone would leave the void. Islands
            # (bores) stay open; holes are cut later anyway.
            face = _face_with_holes(
                [lp - outward * margin for lp in loops],
                direction, tol, result,
            )
            if face is None:
                result.skipped_features.append(feature.feature_id)
                continue
            vec = outward * (depth + margin)
            pad = BRepPrimAPI_MakePrism(
                face, gp_Vec(*(float(c) for c in vec))
            )
            shape = _guarded_fuse(
                shape, pad.Shape(), result,
                f"recess fill ({depth:.2f} mm)",
            )
            result.applied_features.append(feature.feature_id)
            result.log.append(
                f"Filled {depth:.2f} mm recess flat "
                f"({'top' if side > 0 else 'bottom'} side)."
            )
            continue

        # Cut direction: from outside the part toward the pocket floor.
        # Lift the region to just outside the silhouette face.
        lift = outward * (depth + margin)
        face = _face_with_holes(
            [lp + lift for lp in loops], direction, tol, result
        )
        if face is None:
            result.skipped_features.append(feature.feature_id)
            continue
        vec = -outward * (depth + margin)
        cut = BRepPrimAPI_MakePrism(face, gp_Vec(*(float(c) for c in vec)))
        shape = _boolean(shape, cut.Shape(), cut=True)
        result.applied_features.append(feature.feature_id)
        result.log.append(
            f"Cut {depth:.2f} mm pocket "
            f"({'top' if side > 0 else 'bottom'} side, "
            f"{len(loops) - 1} island(s))."
        )
    return shape


def _face_with_holes(
    loops: list[np.ndarray],
    normal: np.ndarray,
    tol: Tolerances,
    result: BuildResult,
):
    """Planar face from an outer loop plus hole loops (islands)."""
    from OCP.ShapeFix import ShapeFix_Face

    wires = []
    for loop in loops:
        wire = _wire_from_loop(loop, normal, tol)
        if wire is None:
            result.log.append("Skipped one pocket (bad boundary loop).")
            return None
        wires.append(wire)

    maker = BRepBuilderAPI_MakeFace(wires[0], True)
    if not maker.IsDone():
        result.log.append("Skipped one pocket (bad outer face).")
        return None
    for wire in wires[1:]:
        maker.Add(wire)
    if not maker.IsDone():
        result.log.append("Skipped one pocket (bad island loop).")
        return None
    # Inner-wire orientation is data-dependent; let ShapeFix sort it out.
    fixer = ShapeFix_Face(maker.Face())
    fixer.FixOrientation()
    return fixer.Face()


# ------------------------------------------------------ holes and bosses

def _apply_cylindrical_features(
    shape: TopoDS_Shape,
    intent: IntentResult,
    extrusion: Feature,
    result: BuildResult,
    holes_only: bool = False,
    bosses_only: bool = False,
    quiet: bool = False,
) -> TopoDS_Shape:
    """Fuse bosses and/or cut holes.

    The pipeline calls this twice: bosses (material) before bridging,
    holes (cuts) after — a hole cut before a boss fuse or bridge hull
    would be filled back in.
    """
    ordered = list(intent.features)
    if holes_only:
        ordered = [f for f in ordered
                   if f.feature_type == FeatureType.HOLE]
    if bosses_only:
        ordered = [f for f in ordered
                   if f.feature_type == FeatureType.BOSS]
    for feature in ordered:
        if feature.feature_type == FeatureType.HOLE:
            try:
                shape = _boolean(shape, _hole_cutter(feature), cut=True)
            except RuntimeError as exc:
                result.log.append(
                    f"SKIPPED hole Ø{feature.params['diameter']:.2f}: {exc}"
                )
                result.skipped_features.append(feature.feature_id)
                continue
            if not quiet:
                result.applied_features.append(feature.feature_id)
                result.log.append(
                    f"Cut hole Ø{feature.params['diameter']:.2f} mm."
                )
        elif feature.feature_type == FeatureType.BOSS:
            axis = np.asarray(feature.params["axis"], dtype=np.float64)
            center = np.asarray(feature.params["center"], dtype=np.float64)
            radius = feature.params["diameter"] / 2.0
            height = float(feature.params["height"])
            start = center - axis * (height / 2.0)
            cyl = BRepPrimAPI_MakeCylinder(
                gp_Ax2(
                    gp_Pnt(*(float(c) for c in start)),
                    gp_Dir(*(float(c) for c in axis)),
                ),
                float(radius),
                height,
            ).Shape()
            shape = _boolean(shape, cyl, cut=False)
            if not quiet:
                result.applied_features.append(feature.feature_id)
                result.log.append(
                    f"Fused boss Ø{feature.params['diameter']:.2f} mm."
                )
        elif feature.feature_type in (
            FeatureType.LINEAR_PATTERN, FeatureType.CIRCULAR_PATTERN
        ):
            # Children are already individual holes; pattern is metadata.
            continue
    return shape


def _apply_wall_cutouts(
    shape: TopoDS_Shape,
    intent: IntentResult,
    tol: Tolerances,
    result: BuildResult,
    quiet: bool = False,
) -> TopoDS_Shape:
    """Cut connector/display openings through side walls."""
    for feature in intent.features:
        if feature.feature_type != FeatureType.CONNECTOR_CUTOUT:
            continue
        outline = np.asarray(feature.params["outline"], dtype=np.float64)
        normal = np.asarray(feature.params["normal"], dtype=np.float64)
        depth = float(feature.params["depth"])

        margin = 0.25 * depth
        wire = _wire_from_loop(outline + normal * margin, normal, tol)
        if wire is None:
            result.log.append("Skipped one wall cutout (bad outline).")
            result.skipped_features.append(feature.feature_id)
            continue
        face = BRepBuilderAPI_MakeFace(wire, True)
        if not face.IsDone():
            result.log.append("Skipped one wall cutout (bad face).")
            result.skipped_features.append(feature.feature_id)
            continue
        vec = -normal * (depth * 1.5 + margin)
        cut = BRepPrimAPI_MakePrism(
            face.Face(), gp_Vec(*(float(c) for c in vec))
        ).Shape()
        try:
            shape = _boolean(shape, cut, cut=True)
        except RuntimeError as exc:
            result.log.append(f"SKIPPED wall cutout: {exc}")
            result.skipped_features.append(feature.feature_id)
            continue
        if not quiet:
            result.applied_features.append(feature.feature_id)
            result.log.append(
                f"Cut wall opening {feature.params['width']:.1f} x "
                f"{feature.params['height']:.1f} mm."
            )
    return shape


# -------------------------------------------------------- analytic wires

def _wire_from_loop(
    loop: np.ndarray, normal: np.ndarray, tol: Tolerances
) -> TopoDS_Wire | None:
    """Best analytic wire for a boundary loop.

    Preference order: true circle, rounded rectangle (lines + real arcs),
    plain polygon. Recovering arcs matters for machining: a corner radius
    in the STEP is a tool-radius decision, a chorded polyline is noise.
    """
    tol_abs = max(4 * tol.point_distance, 0.05)

    hit = loop_is_circle(loop)
    if hit is not None:
        center, radius = hit
        circ = gp_Circ(
            gp_Ax2(gp_Pnt(*(float(c) for c in center)),
                   gp_Dir(*(float(c) for c in normal))),
            float(radius),
        )
        wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circ).Edge())
        return wire.Wire() if wire.IsDone() else None

    rect = loop_is_rounded_rect(loop, normal, tol_abs)
    if rect is not None and rect["radius"] > 0:
        return _rounded_rect_wire(rect)

    simplified = simplify_loop(loop)
    polygon = BRepBuilderAPI_MakePolygon()
    for p in simplified:
        polygon.Add(gp_Pnt(*(float(c) for c in p)))
    polygon.Close()
    return polygon.Wire() if polygon.IsDone() else None


def _rounded_rect_wire(rect: dict) -> TopoDS_Wire | None:
    """Wire of 4 lines + 4 quarter arcs from rounded-rect parameters."""
    center, b1, b2 = rect["center"], rect["b1"], rect["b2"]
    hw, hh, r = rect["half_w"], rect["half_h"], rect["radius"]

    def pt(u: float, v: float) -> gp_Pnt:
        p = center + u * b1 + v * b2
        return gp_Pnt(float(p[0]), float(p[1]), float(p[2]))

    s = r / np.sqrt(2.0)
    # Quarter arcs CCW starting at the top-right corner. Each entry is
    # (start, mid, end) in the (b1, b2) plane; the connecting straight
    # side runs from each arc's end to the next arc's start.
    cx, cy = hw - r, hh - r  # first-quadrant arc centre magnitudes
    arcs = [
        ((hw, cy), (cx + s, cy + s), (cx, hh)),        # top-right
        ((-cx, hh), (-cx - s, cy + s), (-hw, cy)),      # top-left
        ((-hw, -cy), (-cx - s, -cy - s), (-cx, -hh)),   # bottom-left
        ((cx, -hh), (cx + s, -cy - s), (hw, -cy)),      # bottom-right
    ]
    wire = BRepBuilderAPI_MakeWire()
    for k in range(4):
        a_start, a_mid, a_end = arcs[k]
        arc = GC_MakeArcOfCircle(pt(*a_start), pt(*a_mid), pt(*a_end))
        if not arc.IsDone():
            return None
        wire.Add(BRepBuilderAPI_MakeEdge(arc.Value()).Edge())
        p1, p2 = pt(*a_end), pt(*arcs[(k + 1) % 4][0])
        if p1.Distance(p2) > 1e-9:
            wire.Add(BRepBuilderAPI_MakeEdge(p1, p2).Edge())
    return wire.Wire() if wire.IsDone() else None


def _hole_cutter(feature: Feature) -> TopoDS_Shape:
    """The cutting cylinder for a HOLE feature (shared by builder and
    bridge pre-subtraction so both remove exactly the same volume)."""
    axis = np.asarray(feature.params["axis"], dtype=np.float64)
    center = np.asarray(feature.params["center"], dtype=np.float64)
    radius = feature.params["diameter"] / 2.0
    depth = float(feature.params["depth"])
    start = center - axis * depth
    return BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*(float(c) for c in start)),
               gp_Dir(*(float(c) for c in axis))),
        float(radius),
        depth * 2.0,
    ).Shape()


def _guarded_fuse(
    shape: TopoDS_Shape,
    tool: TopoDS_Shape,
    result: BuildResult,
    what: str,
) -> TopoDS_Shape:
    """Fuse that can never lose material.

    A degenerate tool (sliver prism, coincident faces) can make OCC
    return a near-empty shape while reporting success; on a fuse that is
    always wrong, so reject and keep the input.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    def vol(s: TopoDS_Shape) -> float:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(s, props)
        return float(props.Mass())

    before = vol(shape)
    try:
        fuse = BRepAlgoAPI_Fuse(shape, tool)
        fuse.SetFuzzyValue(1e-5)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError("fuse not done")
        candidate = fuse.Shape()
        if vol(candidate) < before * 0.999:
            raise RuntimeError("fuse lost volume")
        return candidate
    except RuntimeError as exc:
        result.log.append(f"Skipped {what} (unsafe fuse: {exc}).")
        return shape


def _boolean(a: TopoDS_Shape, b: TopoDS_Shape, cut: bool) -> TopoDS_Shape:
    op = BRepAlgoAPI_Cut(a, b) if cut else BRepAlgoAPI_Fuse(a, b)
    # Fuzzy merging keeps cuts reliable on shapes whose faces nearly
    # coincide (e.g. after bridge-hull fuses).
    op.SetFuzzyValue(1e-5)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("boolean operation failed")
    if cut:
        # OCC can return an EMPTY result from a cut on dirty topology
        # while reporting success. A cut can never remove more than the
        # tool's own volume — reject anything that claims otherwise.
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        def vol(s: TopoDS_Shape) -> float:
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(s, props)
            return float(props.Mass())

        before, tool = vol(a), vol(b)
        after = vol(op.Shape())
        if after < before - tool * 1.05 - 1e-3:
            raise RuntimeError(
                f"cut removed {before - after:.1f} mm3 but the tool is "
                f"only {tool:.1f} mm3 — corrupt topology"
            )
    return op.Shape()
