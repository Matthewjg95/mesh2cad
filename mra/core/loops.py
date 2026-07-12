"""Boundary-loop utilities shared by intent recovery and reconstruction.

Profiles are recovered from the mesh itself: the boundary edges of a planar
patch's triangle set form closed loops — the outer outline plus one inner
loop per cutout. Loops are ordered by walking edge adjacency and simplified
by collapsing collinear runs, so an 80-triangle rectangular cap becomes
exactly 4 profile points.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import trimesh


def boundary_loops_3d(
    mesh: trimesh.Trimesh, face_indices: np.ndarray
) -> list[np.ndarray]:
    """Ordered boundary loops (as 3D point arrays) of a face subset.

    Args:
        mesh: The source mesh.
        face_indices: Triangle indices forming one planar patch.

    Returns:
        One (n, 3) array per closed loop, ordered but with arbitrary
        winding, largest circumference first. Open chains (which indicate a
        broken patch) are dropped.
    """
    faces = mesh.faces[face_indices]
    counts: dict[tuple[int, int], int] = defaultdict(int)
    for tri in faces:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            counts[(min(a, b), max(a, b))] += 1
    boundary = [e for e, c in counts.items() if c == 1]
    if not boundary:
        return []

    neighbours: dict[int, list[int]] = defaultdict(list)
    for a, b in boundary:
        neighbours[a].append(b)
        neighbours[b].append(a)

    unused = {tuple(e) for e in boundary}
    loops: list[np.ndarray] = []
    while unused:
        start_edge = next(iter(unused))
        unused.discard(start_edge)
        chain = [start_edge[0], start_edge[1]]
        while True:
            here = chain[-1]
            nxt = None
            for cand in neighbours[here]:
                key = (min(here, cand), max(here, cand))
                if key in unused:
                    nxt = cand
                    unused.discard(key)
                    break
            if nxt is None:
                break
            chain.append(nxt)
        if len(chain) >= 4 and chain[0] == chain[-1]:
            loops.append(mesh.vertices[np.array(chain[:-1])])

    loops.sort(key=lambda lp: -_circumference(lp))
    return loops


def simplify_loop(points: np.ndarray, angle_tol_deg: float = 1.0) -> np.ndarray:
    """Drop points that sit on straight runs of the loop polyline.

    Args:
        points: (n, 3) ordered closed-loop points (last connects to first).
        angle_tol_deg: A vertex is kept when its two edges bend more than
            this.

    Returns:
        The reduced (m, 3) loop, m >= 3.
    """
    n = len(points)
    if n < 4:
        return points
    cos_tol = np.cos(np.radians(angle_tol_deg))
    keep = []
    for i in range(n):
        prev_pt = points[(i - 1) % n]
        here = points[i]
        next_pt = points[(i + 1) % n]
        v1 = here - prev_pt
        v2 = next_pt - here
        l1, l2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if l1 < 1e-12 or l2 < 1e-12:
            continue  # duplicate point
        if (v1 / l1) @ (v2 / l2) < cos_tol:
            keep.append(i)
    if len(keep) < 3:
        return points
    return points[np.array(keep)]


def loop_is_circle(
    points: np.ndarray, rel_tol: float = 0.02
) -> tuple[np.ndarray, float] | None:
    """Detect whether a loop is a tessellated circle.

    Equidistance from the centroid alone is not enough evidence — every
    rectangle passes that test. A circle tessellation also has many points
    and roughly uniform chord lengths.

    Returns:
        ``(center, radius)`` when the loop is circular, else None.
    """
    if len(points) < 8:
        return None
    center = points.mean(axis=0)
    radii = np.linalg.norm(points - center, axis=1)
    radius = float(radii.mean())
    if radius < 1e-9:
        return None
    if (radii.max() - radii.min()) / radius > rel_tol:
        return None
    closed = np.vstack([points, points[:1]])
    chords = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    if chords.std() > 0.25 * chords.mean():
        return None
    return center, radius


def loop_is_rounded_rect(
    points: np.ndarray,
    normal: np.ndarray,
    tol_abs: float = 0.05,
) -> dict | None:
    """Detect a rounded rectangle (the standard plate/enclosure outline).

    Works in the plane of the loop. The hypothesis is an axis-aligned
    rectangle (in the projection basis aligned with the dominant edge
    directions) with four equal corner radii; it is accepted when every
    loop point lies within ``tol_abs`` of that outline. Tessellated arc
    vertices lie exactly ON the true arc, so clean CAD exports pass with
    tolerance to spare.

    Returns:
        dict with ``center`` (3,), ``b1``/``b2`` in-plane unit axes,
        ``half_w``/``half_h`` half-extents and ``radius``; None when the
        loop is not a rounded rectangle (or just a plain rectangle —
        radius 0 is reported, callers may treat it as a polygon).
    """
    n = np.asarray(normal, dtype=np.float64)
    n = n / np.linalg.norm(n)
    helper = np.array([1.0, 0.0, 0.0])
    if abs(n @ helper) > 0.9:
        helper = np.array([0.0, 1.0, 0.0])
    b1 = np.cross(n, helper)
    b1 /= np.linalg.norm(b1)
    b2 = np.cross(n, b1)

    center3 = points.mean(axis=0)
    rel = points - center3
    uv = np.column_stack([rel @ b1, rel @ b2])

    # Align the basis with the dominant edge direction so rectangles
    # rotated in-plane still register.
    edges = np.diff(np.vstack([uv, uv[:1]]), axis=0)
    lengths = np.linalg.norm(edges, axis=1)
    if lengths.max() < 1e-9:
        return None
    dominant = edges[np.argmax(lengths)] / lengths.max()
    rot = np.array([[dominant[0], dominant[1]],
                    [-dominant[1], dominant[0]]])
    uv = uv @ rot.T

    half = (uv.max(axis=0) - uv.min(axis=0)) / 2.0
    mid = (uv.max(axis=0) + uv.min(axis=0)) / 2.0
    uv = uv - mid
    if half.min() < 4 * tol_abs:
        return None

    # Estimate the corner radius from points off the box sides.
    on_side = (
        (np.abs(np.abs(uv[:, 0]) - half[0]) <= tol_abs)
        | (np.abs(np.abs(uv[:, 1]) - half[1]) <= tol_abs)
    )
    corner_pts = uv[~on_side]
    radius = 0.0
    if len(corner_pts) > 0:
        u = half[0] - np.abs(corner_pts[:, 0])
        v = half[1] - np.abs(corner_pts[:, 1])
        s = u + v
        disc = s**2 - (u**2 + v**2)
        ok = disc >= 0
        if not np.any(ok):
            return None
        r_candidates = s[ok] - np.sqrt(disc[ok])
        radius = float(np.median(r_candidates))
        if radius < 2 * tol_abs or radius > half.min():
            return None

    # Verify every point against the rounded-rect boundary (SDF).
    q = np.abs(uv) - (half - radius)
    outside = np.linalg.norm(np.maximum(q, 0.0), axis=1)
    inside = np.minimum(np.maximum(q[:, 0], q[:, 1]), 0.0)
    sdf = outside + inside - radius
    if np.max(np.abs(sdf)) > tol_abs:
        return None

    # Report the (possibly rotated) in-plane axes in 3D.
    b1r = rot[0, 0] * b1 + rot[0, 1] * b2
    b2r = rot[1, 0] * b1 + rot[1, 1] * b2
    center3 = center3 + mid[0] * b1r + mid[1] * b2r
    return {
        "center": center3,
        "b1": b1r,
        "b2": b2r,
        "half_w": float(half[0]),
        "half_h": float(half[1]),
        "radius": radius,
    }


def region_is_through(
    mesh: trimesh.Trimesh, loop: np.ndarray, direction: np.ndarray
) -> bool:
    """Whether the region inside a boundary loop is a true through-opening.

    Samples several points across the region (centroid plus a mid-ring of
    points pulled halfway from the boundary toward the centroid) and casts
    rays both ways along ``direction``. Every sample must be clear: a
    single centroid ray is fooled by islands with a bore at their centre —
    a screw tower's outline would read as "open" straight down its own
    bore and the whole tower would be cut out as a window.
    """
    direction = np.asarray(direction, dtype=np.float64)
    centroid = loop.mean(axis=0)
    n = len(loop)
    ring_idx = np.linspace(0, n - 1, min(8, n)).astype(int)
    # Two rings: mid-region AND close to the boundary. A counterbore
    # mouth with a large bore at its centre fools the mid ring alone —
    # every sample can land inside the bore and the recess reads as a
    # through-window (observed: corner recesses re-cut as rings even
    # after the user dropped those pockets).
    samples = [centroid]
    for frac in (0.5, 0.9):
        samples += [
            centroid + (loop[i] - centroid) * frac for i in ring_idx
        ]
    origins = np.array(samples) + direction * 1e-3
    dirs = np.tile(direction, (len(origins), 1))
    if mesh.ray.intersects_any(origins, dirs).any():
        return False
    if mesh.ray.intersects_any(origins, -dirs).any():
        return False
    return True


def _circumference(loop: np.ndarray) -> float:
    return float(
        np.linalg.norm(np.diff(np.vstack([loop, loop[:1]]), axis=0), axis=1).sum()
    )
