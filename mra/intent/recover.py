"""Design-intent recovery: from surface patches to an engineering feature set.

The recogniser (Stage 2) says *what shape* each region is; this module works
out *why it is there*: which cylinders are drilled holes versus bosses versus
fillets, what the part's extrusion direction is, what the designer's wall
thickness was, which holes belong to a pattern, and whether the part is
mirror-symmetric.

Engineering assumptions applied (see project brief): prefer orthogonal
geometry, equal hole diameters, common wall thicknesses, symmetry. Whenever
an assumption changes a measured value by more than mesh noise, a
``Question`` is emitted instead of silently rewriting the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from mra.core import (
    Confidence,
    Feature,
    FeatureType,
    Question,
    SurfacePatch,
    SurfaceType,
    Tolerances,
)
from mra.recognition import SegmentationResult

# A cylinder covering at least this much angle (radians) is a full drum
# (hole or boss); anything less is treated as an edge blend (fillet).
_FULL_ANGLE = np.radians(330.0)
# Wall-thickness candidates are plane-pair gaps in this range (mm).
_WALL_RANGE = (0.4, 8.0)
# Symmetry test: fraction of sampled vertices that must have a mirror twin.
_SYMMETRY_MIN_FRACTION = 0.98


@dataclass
class IntentResult:
    """Stage-3 output handed to the GUI and Stage 5.

    Attributes:
        features: Recovered engineering features (base extrusion first).
        questions: Unanswered decisions for interactive recovery.
        extrude_direction: Unit vector of the inferred primary extrusion,
            or None when the part does not look extruded.
        wall_thickness: Most common wall thickness (mm), or None.
        symmetry_planes: Unit normals of detected mirror planes (through
            the mesh centroid).
    """

    features: list[Feature] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    extrude_direction: np.ndarray | None = None
    wall_thickness: float | None = None
    symmetry_planes: list[np.ndarray] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        """Sidebar-ready description of what was recovered."""
        lines = []
        for f in self.features:
            lines.append(_describe_feature(f))
        if self.wall_thickness is not None:
            lines.append(f"Common wall thickness: {self.wall_thickness:.2f} mm")
        for n in self.symmetry_planes:
            lines.append(
                f"Mirror symmetry across plane n=({n[0]:+.0f},{n[1]:+.0f},{n[2]:+.0f})"
            )
        return lines


def recover_intent(
    mesh: trimesh.Trimesh,
    segmentation: SegmentationResult,
    tol: Tolerances | None = None,
) -> IntentResult:
    """Infer engineering features from a segmented mesh.

    Args:
        mesh: The repaired mesh (evidence).
        segmentation: Stage-2 output for the same mesh.
        tol: Tolerances; defaults tuned for ~100 mm enclosures.

    Returns:
        IntentResult with features ordered base-first and open questions.
    """
    tol = tol or Tolerances()
    result = IntentResult()
    patches = segmentation.patches
    _snap_directions(patches, tol)

    next_feature_id = _Counter()
    next_question_id = _Counter()

    # Base extrusion.
    extrusion = _detect_extrusion(mesh, patches, tol, next_feature_id)
    if extrusion is not None:
        result.features.append(extrusion)
        result.extrude_direction = extrusion.params["direction"]

        # Terrace pockets: every planar level below the silhouette top
        # (or above the silhouette bottom) is a milled/molded step. This
        # covers enclosure cavities, border lips, and recessed panels.
        result.features.extend(
            _detect_pockets(mesh, patches, extrusion, tol, next_feature_id)
        )

    # Wall thickness / shell evidence.
    thickness = _common_wall_thickness(patches, tol)
    if thickness is not None:
        result.wall_thickness = thickness

    # Connector/display cutouts: non-circular openings in side walls.
    if extrusion is not None and thickness is not None:
        result.features.extend(
            _detect_wall_cutouts(
                mesh, patches, extrusion, thickness, tol, next_feature_id
            )
        )

    # Cylinder roles: holes, bosses, fillets.
    holes, bosses, fillets = _classify_cylinders(
        mesh, patches, tol, next_feature_id
    )
    # Coarse tessellations can leave a hole's wall unclassified (too few
    # triangles for a cylinder fit); recover such holes from circular
    # outlines on cap-parallel planes so they are visible, editable and
    # cut like any other hole.
    if extrusion is not None:
        holes.extend(_holes_from_outlines(
            mesh, patches, extrusion, holes, tol, next_feature_id
        ))
    _equalize_hole_diameters(holes, tol, result.questions, next_question_id)
    result.features.extend(holes)
    result.features.extend(bosses)
    result.features.extend(fillets)

    # Patterns among equal holes.
    result.features.extend(
        _detect_patterns(holes, tol, next_feature_id)
    )

    # Mirror symmetry.
    result.symmetry_planes = _mirror_planes(mesh, tol)

    # Unexplained regions become questions, not silent gaps.
    _question_freeform(
        mesh, patches, tol, result.questions, next_question_id
    )
    return result


class _Counter:
    """Tiny id dispenser."""

    def __init__(self) -> None:
        self._n = 0

    def __call__(self) -> int:
        self._n += 1
        return self._n - 1


# ------------------------------------------------------- direction snapping

_GLOBAL_AXES = np.eye(3)


def _snap_directions(patches: list[SurfacePatch], tol: Tolerances) -> None:
    """Snap near-axis-aligned normals/axes exactly onto the global axes.

    CAD parts are overwhelmingly modelled orthogonal to their own coordinate
    system; sub-degree deviations in a tessellation are noise.
    """
    cos_snap = np.cos(np.radians(tol.axis_parallel_angle_deg))
    for patch in patches:
        for key in ("normal", "axis"):
            if key not in patch.params:
                continue
            v = np.asarray(patch.params[key], dtype=np.float64)
            dots = _GLOBAL_AXES @ v
            best = int(np.argmax(np.abs(dots)))
            if abs(dots[best]) >= cos_snap:
                patch.params[key] = _GLOBAL_AXES[best] * np.sign(dots[best])


# ------------------------------------------------------------- extrusion

def _detect_extrusion(
    mesh: trimesh.Trimesh,
    patches: list[SurfacePatch],
    tol: Tolerances,
    next_id: _Counter,
) -> Feature | None:
    """Find the dominant extrusion: a direction with large cap area at both
    ends and side walls parallel to it."""
    planes = [p for p in patches if p.surface_type == SurfaceType.PLANE]
    if not planes:
        return None

    # Vectorised cap search: stacked normals/areas instead of Python
    # filtering per candidate direction (O(dirs * planes) was minutes on
    # heavily fragmented meshes).
    plane_normals = np.array(
        [np.asarray(p.params["normal"]) for p in planes]
    )
    plane_areas = np.array([p.area for p in planes])

    best: tuple[float, np.ndarray, np.ndarray] | None = None
    for direction in _candidate_directions(planes):
        dots = plane_normals @ direction
        cap_mask = np.abs(dots) > 0.999
        if not (np.any(dots[cap_mask] > 0) and np.any(dots[cap_mask] < 0)):
            continue
        cap_area = float(plane_areas[cap_mask].sum())
        if best is None or cap_area > best[0]:
            best = (cap_area, direction, cap_mask)

    if best is None:
        return None
    cap_area, direction, cap_mask = best
    caps = [p for p, m in zip(planes, cap_mask) if m]

    # Bottom: the largest cap facing against the direction (the base).
    # Top: the FARTHEST cap whose outer silhouette matches the base's.
    # Area alone fails both ways: a boss top is small (must be skipped,
    # else the extrusion swallows the boss) and an enclosure's cavity
    # floor is huge (must be skipped, else the extrusion is just the
    # floor slab). The silhouette test resolves both.
    pos = [p for p in caps if np.asarray(p.params["normal"]) @ direction > 0]
    neg = [p for p in caps if np.asarray(p.params["normal"]) @ direction < 0]
    bottom = max(neg, key=lambda p: p.area)
    base_extents = _outline_extents(mesh, bottom, direction)
    # The part silhouette is the largest outer boundary outline among all
    # cap planes on either side (a stepped plate's widest cross-section
    # may sit at an intermediate level, e.g. a border lip).
    def outline_area(p) -> float:
        extents = _outline_extents(mesh, p, direction)
        if extents is None:
            return 0.0
        return float(extents[0] * extents[1])

    biggest = max(pos + neg, key=lambda p: p.area)
    plausible_pos = sorted(
        (p for p in pos if p.area >= 0.01 * biggest.area),
        key=lambda p: -p.area,
    )[:20]
    plausible_neg = sorted(
        (p for p in neg if p.area >= 0.01 * biggest.area),
        key=lambda p: -p.area,
    )[:20]
    if not plausible_pos or not plausible_neg:
        return None
    # Top of the base solid: the pos-side plane with the largest outline
    # (skips boss tops, which have small outlines above the silhouette).
    top = max(plausible_pos, key=outline_area)
    # Bottom of the base solid: the LOWEST neg-side plane (the terraces
    # pass cuts higher-lying neg-side regions back up to their level).
    bottom = min(
        plausible_neg,
        key=lambda p: float(np.asarray(p.params["origin"]) @ direction),
    )
    profile_source = max(plausible_pos + plausible_neg, key=outline_area)
    height = float(
        (np.asarray(top.params["origin"])
         - np.asarray(bottom.params["origin"])) @ direction
    )
    if height <= 0:
        return None
    caps = [top, bottom, profile_source]

    # Wall support: area of patches whose surface is parallel to the
    # direction (plane normals perpendicular, cylinder axes parallel).
    wall_area = 0.0
    for p in patches:
        if p.surface_type == SurfaceType.PLANE:
            if abs(np.asarray(p.params["normal"]) @ direction) < 0.02:
                wall_area += p.area
        elif p.surface_type == SurfaceType.CYLINDER:
            if abs(np.asarray(p.params["axis"]) @ direction) > 0.999:
                wall_area += p.area

    total = sum(p.area for p in patches)
    support = (cap_area + wall_area) / total if total else 0.0
    return Feature(
        feature_id=next_id(),
        feature_type=FeatureType.EXTRUSION,
        patches=caps,
        params={"direction": direction, "height": height},
        confidence=Confidence(
            float(np.clip(support, 0.0, 1.0)),
            f"{support * 100:.0f}% of surface area is caps or walls of "
            f"this direction",
        ),
    )


def _outline_extents(
    mesh: trimesh.Trimesh, patch: SurfacePatch, direction: np.ndarray
) -> np.ndarray | None:
    """Bounding extents of a patch's outer boundary loop, projected onto a
    fixed basis perpendicular to ``direction``. Comparable across patches."""
    from mra.core.loops import boundary_loops_3d

    loops = boundary_loops_3d(mesh, patch.face_indices)
    if not loops:
        return None
    pts2d = _project_to_plane(loops[0], direction)
    return pts2d.max(axis=0) - pts2d.min(axis=0)


def _candidate_directions(planes: list[SurfacePatch]) -> list[np.ndarray]:
    """Unique plane normal directions (sign-folded), largest area first.

    Capped to the 500 largest planes: the extrusion direction is defined
    by the part's dominant faces, and the O(n*k) accumulation below took
    minutes on meshes that fragment into tens of thousands of facets.
    """
    planes = sorted(planes, key=lambda p: -p.area)[:500]
    dirs: list[tuple[float, np.ndarray]] = []
    for p in planes:
        n = np.asarray(p.params["normal"], dtype=np.float64)
        if n[np.argmax(np.abs(n))] < 0:
            n = -n
        for i, (area, existing) in enumerate(dirs):
            if abs(existing @ n) > 0.999:
                dirs[i] = (area + p.area, existing)
                break
        else:
            dirs.append((p.area, n))
    dirs.sort(key=lambda t: -t[0])
    return [d for _, d in dirs]


# ---------------------------------------------------------------- pockets

def _detect_pockets(
    mesh: trimesh.Trimesh,
    patches: list[SurfacePatch],
    extrusion: Feature,
    tol: Tolerances,
    next_id: _Counter,
) -> list[Feature]:
    """Detect terrace pockets on both sides of the base extrusion.

    Every plane facing the same way as the silhouette top but lying below
    it is a pocket floor (enclosure cavity, recessed panel); every plane
    facing the same way as the bottom but lying above it is a relief cut
    from below (border lip, seating step). The pocket region is the
    patch's own boundary — inner loops become islands (bosses survive).
    """
    direction = np.asarray(extrusion.params["direction"], dtype=np.float64)
    top, bottom = extrusion.patches[0], extrusion.patches[1]
    top_off = float(np.asarray(top.params["origin"]) @ direction)
    bottom_off = float(np.asarray(bottom.params["origin"]) @ direction)
    min_step = max(4 * tol.point_distance, tol.dimension_snap)

    pockets: list[Feature] = []
    for p in patches:
        if p.surface_type != SurfaceType.PLANE or p is top or p is bottom:
            continue
        if p.area < tol.min_feature_size**2:
            continue
        n = np.asarray(p.params["normal"])
        off = float(np.asarray(p.params["origin"]) @ direction)
        if n @ direction > 0.999:            # faces up like the top
            depth = top_off - off
            side = +1
        elif n @ direction < -0.999:         # faces down like the bottom
            depth = off - bottom_off
            side = -1
        else:
            continue
        if abs(depth) < min_step:
            continue  # same level as the silhouette face (or noise)
        if not _open_above(mesh, p, direction * side):
            # Material above means this is the ledge of a lateral opening
            # (e.g. a USB cutout in a side wall), not a pocket floor.
            continue
        centroid = mesh.vertices[
            np.unique(mesh.faces[p.face_indices])
        ].mean(axis=0)
        if depth > 0:
            # Below its silhouette face: a pocket floor.
            pockets.append(Feature(
                feature_id=next_id(),
                feature_type=FeatureType.POCKET,
                patches=[p],
                params={
                    "direction": direction,
                    "side": side,
                    "depth": float(depth),
                    "level": off,
                    "centroid": centroid,
                },
                confidence=Confidence(
                    p.confidence.value,
                    f"planar level {depth:.2f} mm "
                    + ("below the top face" if side > 0
                       else "above the bottom face"),
                ),
            ))
        else:
            # Above its silhouette face: the top of a raised pad.
            # Circular plateaus are boss tops; the boss feature owns them.
            from mra.core.loops import boundary_loops_3d, loop_is_circle

            loops = boundary_loops_3d(mesh, p.face_indices)
            if not loops or loop_is_circle(loops[0]) is not None:
                continue
            pockets.append(Feature(
                feature_id=next_id(),
                feature_type=FeatureType.PAD,
                patches=[p],
                params={
                    "direction": direction,
                    "side": side,
                    "height": float(-depth),
                    "level": off,
                },
                confidence=Confidence(
                    p.confidence.value,
                    f"raised plateau {-depth:.2f} mm "
                    + ("above the top face" if side > 0
                       else "below the bottom face"),
                ),
            ))
    return pockets


# ---------------------------------------------------------- wall cutouts

def _detect_wall_cutouts(
    mesh: trimesh.Trimesh,
    patches: list[SurfacePatch],
    extrusion: Feature,
    wall_thickness: float,
    tol: Tolerances,
    next_id: _Counter,
) -> list[Feature]:
    """Find non-circular openings in side walls (USB/display cutouts).

    A side wall with an opening has inner boundary loops in its planar
    patch. Circular inner loops are already covered by hole features;
    everything else becomes a CONNECTOR_CUTOUT carrying its outline so the
    builder can cut it through the wall.
    """
    from mra.core.loops import boundary_loops_3d, loop_is_circle

    direction = np.asarray(extrusion.params["direction"], dtype=np.float64)
    total_plane_area = sum(
        p.area for p in patches if p.surface_type == SurfaceType.PLANE
    ) or 1.0

    cutouts: list[Feature] = []
    for p in patches:
        if p.surface_type != SurfaceType.PLANE:
            continue
        if abs(np.asarray(p.params["normal"]) @ direction) > 0.02:
            continue  # not a side wall
        if p.area < 0.005 * total_plane_area:
            continue  # too small to host a cutout
        loops = boundary_loops_3d(mesh, p.face_indices)
        for inner in loops[1:]:
            if loop_is_circle(inner) is not None:
                continue  # a drilled hole; handled analytically
            extents_2d = _project_to_plane(inner, np.asarray(p.params["normal"]))
            size = extents_2d.max(axis=0) - extents_2d.min(axis=0)
            cutouts.append(Feature(
                feature_id=next_id(),
                feature_type=FeatureType.CONNECTOR_CUTOUT,
                patches=[p],
                params={
                    "outline": inner,
                    "normal": np.asarray(p.params["normal"]),
                    "depth": wall_thickness,
                    "width": float(size[0]),
                    "height": float(size[1]),
                },
                confidence=Confidence(
                    0.8,
                    f"{size[0]:.1f} x {size[1]:.1f} mm opening in a side "
                    "wall: connector/display cutout",
                ),
            ))
    return cutouts


def _open_above(
    mesh: trimesh.Trimesh, patch: SurfacePatch, outward: np.ndarray
) -> bool:
    """Whether a pocket floor has a clear line of sight out of the part.

    Casts rays from a few of the patch's triangle centres along
    ``outward``; a genuine pocket floor exits into free space, while the
    ledge of a lateral opening hits the material above it.
    """
    centers = mesh.triangles_center[patch.face_indices]
    if len(centers) > 8:
        idx = np.linspace(0, len(centers) - 1, 8).astype(int)
        centers = centers[idx]
    origins = centers + outward * 1e-3
    directions = np.tile(outward, (len(origins), 1))
    hits = mesh.ray.intersects_any(origins, directions)
    return float(np.mean(hits)) < 0.5


# --------------------------------------------------------- wall thickness

def _common_wall_thickness(
    patches: list[SurfacePatch], tol: Tolerances
) -> float | None:
    """Most common gap between anti-parallel plane pairs, in wall range."""
    planes = [p for p in patches if p.surface_type == SurfaceType.PLANE]
    # O(n^2) pairing: cap at the 200 largest planes; tiny facets cannot
    # define the designer's wall thickness anyway.
    planes = sorted(planes, key=lambda p: -p.area)[:200]
    gaps: list[tuple[float, float]] = []  # (gap, pairing weight)
    for a, b in combinations(planes, 2):
        na = np.asarray(a.params["normal"])
        nb = np.asarray(b.params["normal"])
        if na @ nb > -0.999:  # must face each other / away
            continue
        gap = abs(
            (np.asarray(a.params["origin"]) - np.asarray(b.params["origin"]))
            @ na
        )
        if _WALL_RANGE[0] <= gap <= _WALL_RANGE[1]:
            gaps.append((gap, min(a.area, b.area)))
    if not gaps:
        return None
    # Weighted mode: cluster gaps within the equal-dimension ratio.
    gaps.sort()
    clusters: list[list[tuple[float, float]]] = [[gaps[0]]]
    for g in gaps[1:]:
        ref = clusters[-1][0][0]
        if abs(g[0] - ref) <= tol.equal_dimension_ratio * max(ref, 1.0):
            clusters[-1].append(g)
        else:
            clusters.append([g])
    best = max(clusters, key=lambda c: sum(w for _, w in c))
    values = np.array([v for v, _ in best])
    weights = np.array([w for _, w in best])
    return float(np.average(values, weights=weights))


# ------------------------------------------------------------- cylinders

def _classify_cylinders(
    mesh: trimesh.Trimesh,
    patches: list[SurfacePatch],
    tol: Tolerances,
    next_id: _Counter,
) -> tuple[list[Feature], list[Feature], list[Feature]]:
    """Split cylinder patches into hole / boss / fillet features."""
    holes: list[Feature] = []
    bosses: list[Feature] = []
    fillets: list[Feature] = []

    for patch in patches:
        if patch.surface_type != SurfaceType.CYLINDER:
            continue
        axis = np.asarray(patch.params["axis"], dtype=np.float64)
        origin = np.asarray(patch.params["origin"], dtype=np.float64)
        radius = float(patch.params["radius"])

        centers = mesh.triangles_center[patch.face_indices]
        normals = mesh.face_normals[patch.face_indices]
        rel = centers - origin
        radial = rel - np.outer(rel @ axis, axis)
        norms = np.linalg.norm(radial, axis=1)
        ok = norms > 1e-9
        concave_ratio = float(
            np.mean(np.einsum("ij,ij->i", normals[ok], radial[ok] / norms[ok, None]) < 0)
        )

        coverage = _angular_coverage(mesh, patch, origin, axis)
        # Depth from vertices (triangle centres under-report the extent).
        verts = mesh.vertices[np.unique(mesh.faces[patch.face_indices])]
        along_v = (verts - origin) @ axis
        depth = float(along_v.max() - along_v.min())

        if coverage >= _FULL_ANGLE:
            if concave_ratio > 0.5:
                holes.append(Feature(
                    feature_id=next_id(),
                    feature_type=FeatureType.HOLE,
                    patches=[patch],
                    params={
                        "axis": axis,
                        "center": origin,
                        "diameter": 2 * radius,
                        "depth": depth,
                    },
                    confidence=Confidence(
                        patch.confidence.value,
                        f"full cylinder, {concave_ratio*100:.0f}% concave: "
                        "drilled/cut hole",
                    ),
                ))
            else:
                bosses.append(Feature(
                    feature_id=next_id(),
                    feature_type=FeatureType.BOSS,
                    patches=[patch],
                    params={
                        "axis": axis,
                        "center": origin,
                        "diameter": 2 * radius,
                        "height": depth,
                    },
                    confidence=Confidence(
                        patch.confidence.value,
                        "full convex cylinder: boss/standoff",
                    ),
                ))
        else:
            kind = "concave (inside corner)" if concave_ratio > 0.5 \
                else "convex (rounded edge)"
            fillets.append(Feature(
                feature_id=next_id(),
                feature_type=FeatureType.FILLET,
                patches=[patch],
                params={
                    "radius": radius,
                    "axis": axis,
                    "concave": concave_ratio > 0.5,
                    "length": depth,
                },
                confidence=Confidence(
                    patch.confidence.value * 0.9,
                    f"partial cylinder ({np.degrees(coverage):.0f} deg), "
                    f"{kind}: edge fillet",
                ),
            ))
    return holes, bosses, fillets


def _holes_from_outlines(
    mesh: trimesh.Trimesh,
    patches: list[SurfacePatch],
    extrusion: Feature,
    known_holes: list[Feature],
    tol: Tolerances,
    next_id: _Counter,
) -> list[Feature]:
    """Recover holes whose walls produced no cylinder patch.

    Scans circular inner boundary loops on every cap-parallel plane;
    loops that are genuinely through (clear ray both ways), not claimed
    by an existing hole and not duplicates seen from the other face
    become through-HOLE features.
    """
    from mra.core.loops import boundary_loops_3d, loop_is_circle

    direction = np.asarray(extrusion.params["direction"], dtype=np.float64)
    # A through hole must clear the WHOLE part, not just the base slab:
    # towers and pads rise above the extrusion height, and a cutter sized
    # to the slab leaves the bore plugged inside them.
    along = mesh.vertices @ direction
    part_span = float(along.max() - along.min())

    def radial_dist(a: np.ndarray, b: np.ndarray) -> float:
        rel = a - b
        return float(np.linalg.norm(rel - (rel @ direction) * direction))

    found: list[Feature] = []
    for p in patches:
        if p.surface_type != SurfaceType.PLANE:
            continue
        if abs(np.asarray(p.params["normal"]) @ direction) < 0.999:
            continue
        for inner in boundary_loops_3d(mesh, p.face_indices)[1:]:
            circle = loop_is_circle(inner)
            if circle is None:
                continue
            center, radius = circle
            if any(radial_dist(np.asarray(h.params["center"]), center)
                   < radius for h in known_holes + found):
                continue
            base = center + direction * 1e-3
            up = mesh.ray.intersects_any(base[None, :], direction[None, :])
            down = mesh.ray.intersects_any(base[None, :], -direction[None, :])
            if bool(up[0]) or bool(down[0]):
                continue  # blind ring or island outline, not a through hole
            found.append(Feature(
                feature_id=next_id(),
                feature_type=FeatureType.HOLE,
                patches=[p],
                params={
                    "axis": direction.copy(),
                    "center": center,
                    "diameter": 2 * radius,
                    "depth": part_span,
                    "through": True,
                },
                confidence=Confidence(
                    0.7,
                    "circular outline on a face; hole wall too coarse to "
                    "classify — assumed a straight through hole",
                ),
            ))
    return found


def _angular_coverage(
    mesh: trimesh.Trimesh,
    patch: SurfacePatch,
    origin: np.ndarray,
    axis: np.ndarray,
) -> float:
    """How much of the full 360 deg the patch wraps around its axis."""
    verts = mesh.vertices[np.unique(mesh.faces[patch.face_indices])]
    rel = verts - origin
    radial = rel - np.outer(rel @ axis, axis)
    helper = np.array([1.0, 0.0, 0.0])
    if abs(axis @ helper) > 0.9:
        helper = np.array([0.0, 1.0, 0.0])
    b1 = np.cross(axis, helper)
    b1 /= np.linalg.norm(b1)
    b2 = np.cross(axis, b1)
    angles = np.sort(np.arctan2(radial @ b2, radial @ b1))
    if len(angles) < 3:
        return 0.0
    gaps = np.diff(np.concatenate([angles, [angles[0] + 2 * np.pi]]))
    return float(2 * np.pi - gaps.max())


def _equalize_hole_diameters(
    holes: list[Feature],
    tol: Tolerances,
    questions: list[Question],
    next_qid: _Counter,
) -> None:
    """Snap nearly-equal hole diameters to their common mean.

    Within mesh noise the snap is silent; larger (but still plausible)
    differences produce a Question so the user decides.
    """
    if len(holes) < 2:
        return
    holes_by_d = sorted(holes, key=lambda h: h.params["diameter"])
    cluster: list[Feature] = [holes_by_d[0]]
    clusters: list[list[Feature]] = [cluster]
    for h in holes_by_d[1:]:
        ref = cluster[0].params["diameter"]
        if abs(h.params["diameter"] - ref) <= tol.equal_dimension_ratio * ref:
            cluster.append(h)
        else:
            cluster = [h]
            clusters.append(cluster)
    for group in clusters:
        if len(group) < 2:
            continue
        diameters = np.array([h.params["diameter"] for h in group])
        mean = float(diameters.mean())
        spread = float(diameters.max() - diameters.min())
        for h in group:
            # Keep the measurement so a user's "keep measured sizes"
            # answer (Stage 4) can restore it.
            h.params["measured_diameter"] = h.params["diameter"]
            h.params["diameter"] = mean
        if spread > tol.point_distance:
            questions.append(Question(
                question_id=next_qid(),
                text=(
                    f"{len(group)} holes have diameters within {spread:.3f} mm "
                    f"of each other. Make them all {mean:.2f} mm?"
                ),
                options=["Yes, make identical", "No, keep measured sizes"],
                feature_ids=[h.feature_id for h in group],
                patch_ids=[p.patch_id for h in group for p in h.patches],
            ))


# --------------------------------------------------------------- patterns

def _detect_patterns(
    holes: list[Feature], tol: Tolerances, next_id: _Counter
) -> list[Feature]:
    """Group equal, axis-parallel holes into linear or circular patterns."""
    patterns: list[Feature] = []
    used: set[int] = set()

    groups: dict[tuple[float, tuple[float, float, float]], list[Feature]] = {}
    for h in holes:
        axis = np.asarray(h.params["axis"])
        key_axis = tuple(np.round(np.abs(axis), 3))
        key = (round(h.params["diameter"], 3), key_axis)
        groups.setdefault(key, []).append(h)

    for (_, _), group in groups.items():
        group = [h for h in group if h.feature_id not in used]
        if len(group) < 3:
            continue
        axis = np.asarray(group[0].params["axis"])
        centers = np.array([h.params["center"] for h in group])
        centers_2d = _project_to_plane(centers, axis)

        linear = _try_linear_pattern(centers_2d, tol)
        if linear is not None:
            spacing = linear
            patterns.append(Feature(
                feature_id=next_id(),
                feature_type=FeatureType.LINEAR_PATTERN,
                params={"count": len(group), "spacing": spacing},
                children=list(group),
                confidence=Confidence(
                    0.9,
                    f"{len(group)} equal holes, collinear, "
                    f"{spacing:.2f} mm pitch",
                ),
            ))
            used.update(h.feature_id for h in group)
            continue

        circular = _try_circular_pattern(centers_2d, tol)
        if circular is not None:
            radius = circular
            patterns.append(Feature(
                feature_id=next_id(),
                feature_type=FeatureType.CIRCULAR_PATTERN,
                params={"count": len(group), "circle_radius": radius},
                children=list(group),
                confidence=Confidence(
                    0.9,
                    f"{len(group)} equal holes on a {radius:.2f} mm circle "
                    "with even angular spacing",
                ),
            ))
            used.update(h.feature_id for h in group)
    return patterns


def _project_to_plane(points: np.ndarray, normal: np.ndarray) -> np.ndarray:
    helper = np.array([1.0, 0.0, 0.0])
    if abs(normal @ helper) > 0.9:
        helper = np.array([0.0, 1.0, 0.0])
    b1 = np.cross(normal, helper)
    b1 /= np.linalg.norm(b1)
    b2 = np.cross(normal, b1)
    return np.column_stack([points @ b1, points @ b2])


def _try_linear_pattern(centers: np.ndarray, tol: Tolerances) -> float | None:
    """Return the pitch when centers are collinear and evenly spaced."""
    centroid = centers.mean(axis=0)
    rel = centers - centroid
    _, s, vt = np.linalg.svd(rel, full_matrices=False)
    if len(s) > 1 and s[1] > tol.point_distance * 10:
        return None  # not collinear
    t = np.sort(rel @ vt[0])
    steps = np.diff(t)
    if len(steps) == 0:
        return None
    if steps.std() > tol.equal_dimension_ratio * max(steps.mean(), 1.0) * 5:
        return None
    return float(steps.mean())


def _try_circular_pattern(centers: np.ndarray, tol: Tolerances) -> float | None:
    """Return the circle radius when centers sit evenly on one circle."""
    a_mat = np.column_stack([2 * centers, np.ones(len(centers))])
    rhs = (centers**2).sum(axis=1)
    try:
        (cx, cy, c), *_ = np.linalg.lstsq(a_mat, rhs, rcond=None)
    except np.linalg.LinAlgError:
        return None
    center = np.array([cx, cy])
    radii = np.linalg.norm(centers - center, axis=1)
    radius = float(radii.mean())
    if radius < tol.min_feature_size:
        return None
    if radii.std() > tol.equal_dimension_ratio * radius * 2:
        return None
    angles = np.sort(np.arctan2(*(centers - center).T[::-1]))
    gaps = np.diff(np.concatenate([angles, [angles[0] + 2 * np.pi]]))
    if gaps.std() > 0.05 * gaps.mean():
        return None
    return radius


# --------------------------------------------------------------- symmetry

def _mirror_planes(mesh: trimesh.Trimesh, tol: Tolerances) -> list[np.ndarray]:
    """Detect mirror symmetry across centroid planes normal to X, Y, Z."""
    verts = np.asarray(mesh.vertices)
    if len(verts) > 5000:
        idx = np.random.default_rng(0).choice(len(verts), 5000, replace=False)
        sample = verts[idx]
    else:
        sample = verts
    tree = cKDTree(verts)
    centroid = verts.mean(axis=0)
    planes: list[np.ndarray] = []
    threshold = max(tol.point_distance * 4, 1e-3)
    for axis_idx in range(3):
        mirrored = sample.copy()
        mirrored[:, axis_idx] = 2 * centroid[axis_idx] - mirrored[:, axis_idx]
        dist, _ = tree.query(mirrored, k=1)
        if np.mean(dist < threshold) >= _SYMMETRY_MIN_FRACTION:
            planes.append(_GLOBAL_AXES[axis_idx].copy())
    return planes


# --------------------------------------------------------------- freeform

def _question_freeform(
    mesh: trimesh.Trimesh,
    patches: list[SurfacePatch],
    tol: Tolerances,
    questions: list[Question],
    next_qid: _Counter,
) -> None:
    total = sum(p.area for p in patches) or 1.0
    for p in patches:
        if p.surface_type != SurfaceType.FREEFORM:
            continue
        if p.area / total < 0.005:
            continue  # cosmetic noise, ignore
        questions.append(Question(
            question_id=next_qid(),
            text=(
                f"A region of {p.area:.0f} mm² ({p.area / total * 100:.1f}% "
                "of the surface) could not be explained by any analytic "
                "surface. Ignore it, or leave the mesh unexplained there?"
            ),
            options=["Ignore region", "Keep as unexplained"],
            patch_ids=[p.patch_id],
        ))


def _describe_feature(f: Feature) -> str:
    """One-line sidebar description."""
    t, p = f.feature_type, f.params
    if t == FeatureType.EXTRUSION:
        d = p["direction"]
        return (f"Extrusion along ({d[0]:+.0f},{d[1]:+.0f},{d[2]:+.0f}), "
                f"height {p['height']:.2f} mm  [{f.confidence.value:.2f}]")
    if t == FeatureType.CONNECTOR_CUTOUT:
        return (f"Wall cutout {p['width']:.1f} x {p['height']:.1f} mm  "
                f"[{f.confidence.value:.2f}]")
    if t == FeatureType.POCKET:
        where = "top" if p["side"] > 0 else "bottom"
        return (f"Pocket {p['depth']:.2f} mm deep ({where} side)  "
                f"[{f.confidence.value:.2f}]")
    if t == FeatureType.PAD:
        where = "top" if p["side"] > 0 else "bottom"
        return (f"Pad {p['height']:.2f} mm raised ({where} side)  "
                f"[{f.confidence.value:.2f}]")
    if t == FeatureType.HOLE:
        return (f"Hole Ø{p['diameter']:.2f} mm, depth {p['depth']:.2f} mm  "
                f"[{f.confidence.value:.2f}]")
    if t == FeatureType.BOSS:
        return (f"Boss Ø{p['diameter']:.2f} mm, height {p['height']:.2f} mm  "
                f"[{f.confidence.value:.2f}]")
    if t == FeatureType.FILLET:
        kind = "concave" if p.get("concave") else "convex"
        return (f"Fillet r={p['radius']:.2f} mm ({kind})  "
                f"[{f.confidence.value:.2f}]")
    if t == FeatureType.LINEAR_PATTERN:
        return (f"Linear pattern: {p['count']} holes, "
                f"{p['spacing']:.2f} mm pitch  [{f.confidence.value:.2f}]")
    if t == FeatureType.CIRCULAR_PATTERN:
        return (f"Circular pattern: {p['count']} holes on "
                f"Ø{2 * p['circle_radius']:.2f} mm  [{f.confidence.value:.2f}]")
    return f"{t.value}  [{f.confidence.value:.2f}]"
