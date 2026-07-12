"""Mesh segmentation into analytic surface patches.

Strategy tuned for CAD-tessellated STLs:

  1. Split the mesh into *smooth components*: connected face groups whose
     shared edges have a dihedral angle below a threshold. CAD models
     tessellate with sharp edges exactly where faces meet, so components
     usually correspond one-to-one with B-Rep faces.

  2. Classify each component by fitting plane, cylinder, sphere and cone
     models and keeping the best fit within tolerance. Simpler surfaces win
     ties (a zero-angle cone must not beat a genuine cylinder).

  3. Components no primitive explains (e.g. a face merged with its fillet
     through a tangent transition) are *refined*: planar region growing
     extracts flat sub-regions, and the leftovers are re-classified as
     curved patches. Whatever still resists becomes a FREEFORM patch —
     honest input for Stage 4's user questions.

Performance: all mesh arrays are extracted once into a ``_MeshData`` bundle.
Accessing ``mesh.face_normals`` etc. inside per-region loops re-validates
trimesh's cache (hashing the full vertex buffer) and alone accounted for a
third of the runtime on 100k+ triangle meshes.

Output is a list of ``SurfacePatch`` plus a per-face patch-id array for
viewport colouring.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
import trimesh

from mra.core import Confidence, SurfacePatch, SurfaceType, Tolerances
from mra.recognition.fitting import (
    FitResult,
    fit_cone,
    fit_cylinder,
    fit_plane,
    fit_sphere,
)

# Adjacent faces whose dihedral angle exceeds this are in different smooth
# components (radians). Generous because coarse cylinder tessellations can
# step ~20 deg between strips.
_SMOOTH_DIHEDRAL = np.radians(35.0)
# A fit is accepted when at least this fraction of the region's vertices lie
# within Tolerances.point_distance of the surface.
_MIN_INLIER_RATIO = 0.90
# Regions smaller than this many faces are noise; they become FREEFORM.
_MIN_REGION_FACES = 4
# Curved (cylinder/sphere/cone) fits need at least this many faces — a
# handful of triangles cannot evidence a curved primitive, and the
# iterative fits dominated runtime when run on thousands of fragments.
_MIN_CURVED_FACES = 12
# Region growing refits the plane on a doubling schedule from this size.
_REFIT_INTERVAL = 64
# Surface preference for tie-breaking: lower is preferred when RMS is close.
# Cylinders outrank spheres because machined/molded parts are dominated by
# drilled holes and extruded walls; spherical regions are rare.
_COMPLEXITY = {
    SurfaceType.PLANE: 0,
    SurfaceType.CYLINDER: 1,
    SurfaceType.SPHERE: 2,
    SurfaceType.CONE: 3,
}


@dataclass
class _MeshData:
    """Plain-array snapshot of the mesh used by all segmentation loops."""

    vertices: np.ndarray       # (nv, 3) float64
    faces: np.ndarray          # (nf, 3) int64
    face_normals: np.ndarray   # (nf, 3)
    tri_centers: np.ndarray    # (nf, 3)
    face_areas: np.ndarray     # (nf,)
    adjacency_pairs: np.ndarray    # (ne, 2) int64
    adjacency_angles: np.ndarray   # (ne,)
    adjacency: list[list[int]]     # face -> neighbour faces

    @classmethod
    def from_mesh(cls, mesh: trimesh.Trimesh) -> "_MeshData":
        pairs = np.asarray(mesh.face_adjacency, dtype=np.int64)
        adjacency: list[list[int]] = [[] for _ in range(len(mesh.faces))]
        for a, b in pairs:
            adjacency[a].append(int(b))
            adjacency[b].append(int(a))
        return cls(
            vertices=np.asarray(mesh.vertices, dtype=np.float64),
            faces=np.asarray(mesh.faces, dtype=np.int64),
            face_normals=np.asarray(mesh.face_normals, dtype=np.float64),
            tri_centers=np.asarray(mesh.triangles_center, dtype=np.float64),
            face_areas=np.asarray(mesh.area_faces, dtype=np.float64),
            adjacency_pairs=pairs,
            adjacency_angles=np.asarray(
                mesh.face_adjacency_angles, dtype=np.float64
            ),
            adjacency=adjacency,
        )

    def region_vertices(self, region_faces: np.ndarray) -> np.ndarray:
        return self.vertices[np.unique(self.faces[region_faces])]


@dataclass
class SegmentationResult:
    """Everything Stage 2 hands to Stage 3 and the GUI.

    Attributes:
        patches: Recognised surface patches, largest area first.
        face_patch_ids: Per-triangle patch id (index into ``patches``),
            -1 for unassigned faces.
    """

    patches: list[SurfacePatch]
    face_patch_ids: np.ndarray

    def coverage(self) -> float:
        """Fraction of mesh triangles assigned to some patch."""
        if len(self.face_patch_ids) == 0:
            return 0.0
        return float(np.mean(self.face_patch_ids >= 0))


def segment_mesh(
    mesh: trimesh.Trimesh, tol: Tolerances | None = None
) -> SegmentationResult:
    """Segment ``mesh`` into analytic surface patches.

    Args:
        mesh: A repaired, ideally watertight mesh.
        tol: Recognition tolerances; defaults tuned for ~100 mm parts.

    Returns:
        SegmentationResult with patches sorted by area (largest first).
    """
    tol = tol or Tolerances()
    md = _MeshData.from_mesh(mesh)
    n_faces = len(md.faces)

    raw_regions: list[tuple[np.ndarray, FitResult | None]] = []
    all_faces = np.ones(n_faces, dtype=bool)
    flatness = _face_flatness(md)
    for component in _smooth_components(md, all_faces):
        fit = _classify_region(md, component, tol)
        if fit is not None or len(component) < _MIN_REGION_FACES:
            raw_regions.append((component, fit))
        else:
            raw_regions.extend(
                _refine_component(md, component, flatness, tol)
            )

    # Merge adjacent compatible regions: tessellation noise and tangent
    # splits fragment real CAD faces into many patches; a plane split
    # into 5,000 facets must come back as one face before Stage 3.
    raw_regions = _merge_compatible_regions(md, raw_regions, tol)

    # Package patches, largest area first.
    raw_regions.sort(key=lambda r: -md.face_areas[r[0]].sum())
    face_patch = np.full(n_faces, -1, dtype=np.int64)
    patches: list[SurfacePatch] = []
    for patch_id, (region_faces, fit) in enumerate(raw_regions):
        patches.append(_make_patch(patch_id, md, region_faces, fit, tol))
        face_patch[region_faces] = patch_id

    return SegmentationResult(patches=patches, face_patch_ids=face_patch)


# ------------------------------------------------------------ components

def _smooth_components(
    md: _MeshData, available: np.ndarray
) -> list[np.ndarray]:
    """Connected components of ``available`` faces across smooth edges.

    Vectorised: builds a sparse face-adjacency graph restricted to smooth
    edges between available faces, then runs scipy's connected components.
    Python BFS here was the No. 1 hot spot on 100k+ triangle meshes.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(md.faces)
    pairs = md.adjacency_pairs
    ok = (
        (md.adjacency_angles <= _SMOOTH_DIHEDRAL)
        & available[pairs[:, 0]]
        & available[pairs[:, 1]]
    )
    pairs = pairs[ok]
    graph = coo_matrix(
        (np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])),
        shape=(n, n),
    )
    _, labels = connected_components(graph, directed=False)
    components: list[np.ndarray] = []
    avail_idx = np.flatnonzero(available)
    avail_labels = labels[avail_idx]
    order = np.argsort(avail_labels, kind="stable")
    sorted_idx = avail_idx[order]
    sorted_labels = avail_labels[order]
    boundaries = np.flatnonzero(np.diff(sorted_labels)) + 1
    for chunk in np.split(sorted_idx, boundaries):
        if len(chunk):
            components.append(np.sort(chunk).astype(np.int64))
    return components


def _face_flatness(md: _MeshData) -> np.ndarray:
    """Per-face maximum dihedral angle to any neighbour (0 = locally flat)."""
    max_angle = np.zeros(len(md.faces))
    np.maximum.at(max_angle, md.adjacency_pairs[:, 0], md.adjacency_angles)
    np.maximum.at(max_angle, md.adjacency_pairs[:, 1], md.adjacency_angles)
    return max_angle


# --------------------------------------------------------- classification

def _classify_region(
    md: _MeshData, region_faces: np.ndarray, tol: Tolerances
) -> FitResult | None:
    """Best analytic explanation for a face region, or None.

    Candidate fits must pass two gates: the inlier threshold on vertex
    distances AND agreement between the region's triangle normals and the
    normals the fitted surface predicts. The second gate is what tells a
    short tube apart from a sphere — both can have zero position residual
    on coarse tessellations, but their normals differ wildly.

    Fitters run simplest-first with an early exit, so a near-perfect plane
    never pays for a cone optimisation.
    """
    verts = md.region_vertices(region_faces)
    normals = md.face_normals[region_faces]
    centers = md.tri_centers[region_faces]

    max_normal_dev = np.radians(max(2.0 * tol.normal_angle_deg, 10.0))

    def passes(c: FitResult) -> bool:
        return (
            c.inlier_ratio(tol.point_distance) >= _MIN_INLIER_RATIO
            and _normal_deviation(c, centers, normals) <= max_normal_dev
        )

    fitters = [lambda: fit_plane(verts)]
    if len(region_faces) >= _MIN_CURVED_FACES:
        fitters += [
            lambda: fit_cylinder(verts, normals),
            lambda: fit_sphere(verts),
            lambda: fit_cone(verts, normals),
        ]

    early_exit_rms = 0.25 * tol.point_distance
    passing: list[FitResult] = []
    for fitter in fitters:
        try:
            c = fitter()
        except (ValueError, np.linalg.LinAlgError):
            continue
        if passes(c):
            passing.append(c)
            if c.rms <= early_exit_rms:
                break

    if not passing:
        return None

    margin = 0.1 * tol.point_distance
    best = min(passing, key=lambda c: c.rms)
    simpler = [
        c for c in passing
        if _COMPLEXITY[c.surface_type] < _COMPLEXITY[best.surface_type]
        and c.rms <= best.rms + margin
    ]
    if simpler:
        best = min(simpler, key=lambda c: _COMPLEXITY[c.surface_type])
    if best.surface_type == SurfaceType.PLANE:
        # SVD normals have arbitrary sign; report the outward direction
        # the mesh triangles agree on.
        mean_normal = normals.mean(axis=0)
        if best.params["normal"] @ mean_normal < 0:
            best.params["normal"] = -best.params["normal"]
    return best


def _normal_deviation(
    fit: FitResult, centers: np.ndarray, normals: np.ndarray
) -> float:
    """Mean angle (radians) between actual and surface-predicted normals.

    Sign-agnostic: only the axis of the normal matters, since fitted
    surfaces do not know inside from outside.
    """
    p = fit.params
    if fit.surface_type == SurfaceType.PLANE:
        predicted = np.tile(p["normal"], (len(centers), 1))
    elif fit.surface_type == SurfaceType.CYLINDER:
        rel = centers - p["origin"]
        predicted = rel - np.outer(rel @ p["axis"], p["axis"])
    elif fit.surface_type == SurfaceType.SPHERE:
        predicted = centers - p["center"]
    elif fit.surface_type == SurfaceType.CONE:
        rel = centers - p["apex"]
        axis, alpha = p["axis"], p["half_angle"]
        radial = rel - np.outer(rel @ axis, axis)
        lengths = np.linalg.norm(radial, axis=1, keepdims=True)
        lengths[lengths < 1e-12] = 1.0
        predicted = (radial / lengths) * np.cos(alpha) - axis * np.sin(alpha)
    else:
        return 0.0
    lengths = np.linalg.norm(predicted, axis=1, keepdims=True)
    lengths[lengths < 1e-12] = 1.0
    predicted /= lengths
    cos = np.abs(np.einsum("ij,ij->i", predicted, normals)).clip(0.0, 1.0)
    return float(np.mean(np.arccos(cos)))


# ------------------------------------------------------------ refinement

def _refine_component(
    md: _MeshData,
    component: np.ndarray,
    flatness: np.ndarray,
    tol: Tolerances,
) -> list[tuple[np.ndarray, FitResult | None]]:
    """Split an unexplained component into planar + curved sub-regions.

    Handles tangent transitions (face blending into fillet) where the
    smooth-component pass merges several B-Rep faces into one region.
    """
    in_component = np.zeros(len(md.faces), dtype=bool)
    in_component[component] = True
    claimed = ~in_component  # faces outside the component count as taken

    regions: list[tuple[np.ndarray, FitResult | None]] = []

    # Extract planar sub-regions, flattest seeds first. Faces whose grow
    # attempt fizzled must not be re-seeded — without the `tried` mask
    # every failed seed re-walks its neighbourhood and the loop degrades
    # to O(n^2) on meshes with many blends.
    tried = np.zeros(len(md.faces), dtype=bool)
    seed_order = component[np.argsort(flatness[component], kind="stable")]
    for seed in seed_order:
        if claimed[seed] or tried[seed]:
            continue
        region_faces, fit = _grow_planar_region(md, seed, claimed, tol)
        if fit is None:
            tried[region_faces] = True
            continue
        claimed[region_faces] = True
        regions.append((region_faces, fit))

    # Re-classify what remains as smooth curved regions.
    remaining = in_component & ~claimed
    if remaining.any():
        for sub in _smooth_components(md, remaining):
            regions.append((sub, _classify_region(md, sub, tol)))
    return regions


def _grow_planar_region(
    md: _MeshData,
    seed: int,
    claimed: np.ndarray,
    tol: Tolerances,
) -> tuple[np.ndarray, FitResult | None]:
    """Grow one planar region from ``seed``; fit is None when too small."""
    cos_tol = np.cos(np.radians(tol.normal_angle_deg))
    normal = md.face_normals[seed].copy()
    origin = md.tri_centers[seed].copy()

    member = {seed}
    queue = deque(md.adjacency[seed])
    enqueued = {seed, *md.adjacency[seed]}
    # Geometric refit schedule: refitting every K faces is O(n^2) on big
    # patches; doubling the interval keeps total refit cost O(n log n).
    next_refit = _REFIT_INTERVAL

    while queue:
        f = queue.popleft()
        if claimed[f] or f in member:
            continue
        if md.face_normals[f] @ normal < cos_tol:
            continue
        verts = md.vertices[md.faces[f]]
        if np.max(np.abs((verts - origin) @ normal)) > tol.point_distance:
            continue
        member.add(f)
        for nb in md.adjacency[f]:
            if nb not in enqueued and not claimed[nb]:
                enqueued.add(nb)
                queue.append(nb)
        if len(member) >= next_refit:
            next_refit *= 2
            fit = _plane_of(md, member)
            origin, normal = fit.params["origin"], fit.params["normal"]
            if normal @ md.face_normals[seed] < 0:
                normal = -normal

    region_faces = np.fromiter(member, dtype=np.int64)
    region_faces.sort()
    if len(member) < _MIN_REGION_FACES:
        # Too small to be a real face; return it unfitted so the caller
        # can mark the faces as tried (re-seeding them is O(n^2) poison).
        return region_faces, None
    fit = _plane_of(md, member)
    # Keep the outward orientation the mesh triangles agree on.
    if fit.params["normal"] @ md.face_normals[seed] < 0:
        fit.params["normal"] = -fit.params["normal"]
    return region_faces, fit


def _plane_of(md: _MeshData, faces) -> FitResult:
    verts = md.vertices[np.unique(md.faces[list(faces)])]
    return fit_plane(verts)


# ----------------------------------------------------------------- merge

def _merge_compatible_regions(
    md: _MeshData,
    raw_regions: list[tuple[np.ndarray, FitResult | None]],
    tol: Tolerances,
) -> list[tuple[np.ndarray, FitResult | None]]:
    """Union-find merge of adjacent regions explained by the same surface.

    Two adjacent plane regions merge when their normals agree within
    ``tol.coplanar_angle_deg`` and their offsets along the normal agree
    within twice ``tol.point_distance``. Two adjacent cylinders merge when
    axes are parallel, radii are equal within ``tol.equal_dimension_ratio``
    and the axis lines coincide. Merged groups are refitted once.
    """
    n_regions = len(raw_regions)
    if n_regions < 2:
        return raw_regions

    face_label = np.full(len(md.faces), -1, dtype=np.int64)
    for i, (region_faces, _) in enumerate(raw_regions):
        face_label[region_faces] = i

    # Candidate pairs: regions that share at least one mesh edge.
    a_lab = face_label[md.adjacency_pairs[:, 0]]
    b_lab = face_label[md.adjacency_pairs[:, 1]]
    diff = (a_lab != b_lab) & (a_lab >= 0) & (b_lab >= 0)
    pairs = np.unique(
        np.sort(np.column_stack([a_lab[diff], b_lab[diff]]), axis=1), axis=0
    )

    parent = np.arange(n_regions)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in pairs:
        fi, fj = raw_regions[i][1], raw_regions[j][1]
        if fi is None or fj is None:
            continue
        if fi.surface_type != fj.surface_type:
            continue
        if _fits_compatible(fi, fj, tol):
            ri, rj = find(int(i)), find(int(j))
            if ri != rj:
                parent[rj] = ri

    groups: dict[int, list[int]] = {}
    for i in range(n_regions):
        groups.setdefault(find(i), []).append(i)
    if len(groups) == n_regions:
        return raw_regions

    merged: list[tuple[np.ndarray, FitResult | None]] = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(raw_regions[members[0]])
            continue
        region_faces = np.sort(np.concatenate(
            [raw_regions[m][0] for m in members]
        ))
        merged.append((region_faces, _refit_region(md, region_faces, tol)))
    return merged


def _fits_compatible(a: FitResult, b: FitResult, tol: Tolerances) -> bool:
    """Whether two same-type fits describe the same surface."""
    cos_tol = np.cos(np.radians(tol.coplanar_angle_deg))
    if a.surface_type == SurfaceType.PLANE:
        na, nb = a.params["normal"], b.params["normal"]
        if na @ nb < cos_tol:  # signed: outward orientations must agree
            return False
        gap = abs((a.params["origin"] - b.params["origin"]) @ na)
        return gap <= 2.0 * tol.point_distance
    if a.surface_type == SurfaceType.CYLINDER:
        aa, ab = a.params["axis"], b.params["axis"]
        if abs(aa @ ab) < cos_tol:
            return False
        ra, rb = a.params["radius"], b.params["radius"]
        if abs(ra - rb) > tol.equal_dimension_ratio * max(ra, rb):
            return False
        # Axis lines must coincide: perpendicular offset between them.
        d = b.params["origin"] - a.params["origin"]
        perp = d - (d @ aa) * aa
        return float(np.linalg.norm(perp)) <= 4.0 * tol.point_distance
    return False


def _refit_region(
    md: _MeshData, region_faces: np.ndarray, tol: Tolerances
) -> FitResult | None:
    """Refit a merged region; falls back to classification on failure."""
    return _classify_region(md, region_faces, tol)


# -------------------------------------------------------------- packaging

def _make_patch(
    patch_id: int,
    md: _MeshData,
    region_faces: np.ndarray,
    fit: FitResult | None,
    tol: Tolerances,
) -> SurfacePatch:
    area = float(md.face_areas[region_faces].sum())
    if fit is None:
        return SurfacePatch(
            patch_id=patch_id,
            surface_type=SurfaceType.FREEFORM,
            face_indices=region_faces,
            params={},
            confidence=Confidence(
                0.2, "no analytic surface explains this region"
            ),
            rms_error=0.0,
            area=area,
        )
    inliers = fit.inlier_ratio(tol.point_distance)
    confidence = Confidence(
        value=float(np.clip(inliers, 0.0, 1.0)),
        reason=(
            f"{inliers * 100:.0f}% of vertices within "
            f"{tol.point_distance:g} mm of fitted "
            f"{fit.surface_type.value} (rms {fit.rms:.4f} mm)"
        ),
    )
    return SurfacePatch(
        patch_id=patch_id,
        surface_type=fit.surface_type,
        face_indices=region_faces,
        params=fit.params,
        confidence=confidence,
        rms_error=fit.rms,
        area=area,
    )
