"""Least-squares analytic surface fits.

Each ``fit_*`` function takes sample points (and, where useful, normals) and
returns a ``FitResult`` holding the surface parameters, the RMS distance of
the points to the fitted surface, and per-point residuals so callers can
compute inlier ratios against their tolerance.

Conventions:
  * All direction vectors returned are unit length.
  * Cylinder/cone axes have arbitrary sign; callers must not rely on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import least_squares

from mra.core import SurfaceType


@dataclass
class FitResult:
    """Outcome of one analytic fit.

    Attributes:
        surface_type: Which primitive was fitted.
        params: Parameter dict matching ``SurfacePatch.params`` conventions.
        residuals: Per-point unsigned distances to the fitted surface.
        rms: Root-mean-square of ``residuals``.
    """

    surface_type: SurfaceType
    params: dict[str, Any]
    residuals: np.ndarray
    rms: float

    def inlier_ratio(self, distance_tol: float) -> float:
        """Fraction of points within ``distance_tol`` of the surface."""
        if len(self.residuals) == 0:
            return 0.0
        return float(np.mean(self.residuals <= distance_tol))


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    if n < 1e-12:
        raise ValueError("zero-length direction vector")
    return v / n


# Iterative refinement uses at most this many points; final residuals are
# always evaluated on the full set. A cylinder is over-determined a
# thousandfold by 3k points — more just burns time in the Jacobian.
_MAX_FIT_POINTS = 3000


def _subsample(points: np.ndarray) -> np.ndarray:
    if len(points) <= _MAX_FIT_POINTS:
        return points
    idx = np.random.default_rng(0).choice(
        len(points), _MAX_FIT_POINTS, replace=False
    )
    return points[idx]


def _finish(surface_type: SurfaceType, params: dict[str, Any],
            residuals: np.ndarray) -> FitResult:
    residuals = np.abs(np.asarray(residuals, dtype=np.float64))
    rms = float(np.sqrt(np.mean(residuals**2))) if len(residuals) else np.inf
    return FitResult(surface_type, params, residuals, rms)


# ------------------------------------------------------------------ plane

def fit_plane(points: np.ndarray) -> FitResult:
    """Fit a plane by SVD of the centred point cloud.

    params: ``origin`` (centroid), ``normal`` (unit).
    """
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 3:
        raise ValueError("plane fit needs >= 3 points")
    centroid = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - centroid, full_matrices=False)
    normal = _unit(vt[-1])
    residuals = (points - centroid) @ normal
    return _finish(
        SurfaceType.PLANE,
        {"origin": centroid, "normal": normal},
        residuals,
    )


# --------------------------------------------------------------- cylinder

def _axis_from_normals(normals: np.ndarray) -> np.ndarray:
    """Cylinder axis estimate: surface normals are perpendicular to the
    axis, so the axis is the direction of least normal variance (smallest
    eigenvector of the normal covariance matrix)."""
    normals = np.asarray(normals, dtype=np.float64)
    cov = normals.T @ normals
    eigvals, eigvecs = np.linalg.eigh(cov)
    return _unit(eigvecs[:, 0])


def _cylinder_residuals(points: np.ndarray, origin: np.ndarray,
                        axis: np.ndarray, radius: float) -> np.ndarray:
    rel = points - origin
    radial = rel - np.outer(rel @ axis, axis)
    return np.linalg.norm(radial, axis=1) - radius


def fit_cylinder(points: np.ndarray,
                 normals: np.ndarray | None = None) -> FitResult:
    """Fit an infinite cylinder.

    Seeds the axis from triangle normals when available (robust for CAD
    tessellations), projects points to the plane perpendicular to the axis,
    fits a circle algebraically, then refines everything with
    Levenberg-Marquardt on true orthogonal distances.

    params: ``origin`` (point on axis), ``axis`` (unit), ``radius``.
    """
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 6:
        raise ValueError("cylinder fit needs >= 6 points")

    if normals is not None and len(normals) >= 3:
        axis0 = _axis_from_normals(normals)
    else:
        # Fall back: longest extent direction of the point cloud.
        centroid = points.mean(axis=0)
        _, _, vt = np.linalg.svd(points - centroid, full_matrices=False)
        axis0 = _unit(vt[0])

    centroid = points.mean(axis=0)
    center0, radius0 = _circle_in_plane(points, centroid, axis0)

    # Parameterise axis by two rotation angles around the seed to keep the
    # optimisation well-conditioned (unit constraint built in).
    b1, b2 = _orthonormal_basis(axis0)
    fit_points = _subsample(points)

    def unpack(x: np.ndarray):
        ox, oy, oz, a1, a2, r = x
        axis = _unit(axis0 + a1 * b1 + a2 * b2)
        return np.array([ox, oy, oz]), axis, r

    def resid(x: np.ndarray) -> np.ndarray:
        origin, axis, r = unpack(x)
        return _cylinder_residuals(fit_points, origin, axis, r)

    x0 = np.array([*center0, 0.0, 0.0, radius0])
    sol = least_squares(resid, x0, method="lm", max_nfev=200)
    origin, axis, radius = unpack(sol.x)
    radius = abs(float(radius))
    # Anchor the reported origin at the projection of the centroid onto the
    # axis so downstream code gets a stable, meaningful point.
    origin = origin + ((centroid - origin) @ axis) * axis
    return _finish(
        SurfaceType.CYLINDER,
        {"origin": origin, "axis": axis, "radius": radius},
        _cylinder_residuals(points, origin, axis, radius),
    )


def _orthonormal_basis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors orthogonal to ``axis`` and each other."""
    helper = np.array([1.0, 0.0, 0.0])
    if abs(axis @ helper) > 0.9:
        helper = np.array([0.0, 1.0, 0.0])
    b1 = _unit(np.cross(axis, helper))
    b2 = np.cross(axis, b1)
    return b1, b2


def _circle_in_plane(points: np.ndarray, plane_origin: np.ndarray,
                     plane_normal: np.ndarray) -> tuple[np.ndarray, float]:
    """Algebraic (Kasa) circle fit of points projected onto a plane.

    Returns the circle centre in 3D and its radius.
    """
    b1, b2 = _orthonormal_basis(plane_normal)
    rel = points - plane_origin
    uv = np.column_stack([rel @ b1, rel @ b2])
    a_mat = np.column_stack([2 * uv, np.ones(len(uv))])
    rhs = (uv**2).sum(axis=1)
    (cu, cv, c), *_ = np.linalg.lstsq(a_mat, rhs, rcond=None)
    radius = float(np.sqrt(c + cu**2 + cv**2))
    center3d = plane_origin + cu * b1 + cv * b2
    return center3d, radius


# ----------------------------------------------------------------- sphere

def fit_sphere(points: np.ndarray) -> FitResult:
    """Algebraic sphere fit (linear least squares), exact for clean data.

    params: ``center``, ``radius``.
    """
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 4:
        raise ValueError("sphere fit needs >= 4 points")
    a_mat = np.column_stack([2 * points, np.ones(len(points))])
    rhs = (points**2).sum(axis=1)
    (cx, cy, cz, c), *_ = np.linalg.lstsq(a_mat, rhs, rcond=None)
    center = np.array([cx, cy, cz])
    radius = float(np.sqrt(c + center @ center))
    residuals = np.linalg.norm(points - center, axis=1) - radius
    return _finish(
        SurfaceType.SPHERE,
        {"center": center, "radius": radius},
        residuals,
    )


# ------------------------------------------------------------------- cone

def fit_cone(points: np.ndarray, normals: np.ndarray) -> FitResult:
    """Fit a cone using point + normal constraints.

    For a cone of half-angle ``alpha``, every surface normal makes the angle
    ``90 deg - alpha`` with the axis. The axis and angle are seeded from the
    normal distribution, the apex from ray intersection, then all seven
    parameters are refined on orthogonal distances.

    params: ``apex``, ``axis`` (unit, pointing from apex into the material),
    ``half_angle`` (radians).
    """
    points = np.asarray(points, dtype=np.float64)
    normals = np.asarray(normals, dtype=np.float64)
    if len(points) < 6:
        raise ValueError("cone fit needs >= 6 points")

    # Seed apex: every tangent plane of a cone passes through the apex,
    # so solve n_i . x = n_i . p_i in least squares. (Rank-deficient for
    # cylinders — lstsq still returns a finite point; LM sorts it out.)
    rhs = np.einsum("ij,ij->i", normals, points)
    apex0, *_ = np.linalg.lstsq(normals, rhs, rcond=None)

    # Seed axis and half-angle: outward normals satisfy n . a = sin(alpha),
    # a constant — the smallest singular vector of [N | -1] gives (a, s)
    # up to scale.
    m = np.column_stack([normals, -np.ones(len(normals))])
    _, _, vt = np.linalg.svd(m, full_matrices=False)
    a_s = vt[-1]
    axis0 = _unit(a_s[:3])
    sin_alpha0 = float(np.clip(a_s[3] / np.linalg.norm(a_s[:3]), -0.999, 0.999))
    if sin_alpha0 < 0:
        axis0, sin_alpha0 = -axis0, -sin_alpha0
    half_angle0 = float(np.clip(np.arcsin(sin_alpha0), 0.02, np.pi / 2 - 0.02))

    # The residual model assumes the axis points from the apex toward the
    # data; the SVD seed has arbitrary sign, so orient it now.
    if (points.mean(axis=0) - apex0) @ axis0 < 0:
        axis0 = -axis0

    b1, b2 = _orthonormal_basis(axis0)
    fit_points = _subsample(points)

    def unpack(x: np.ndarray):
        ax, ay, az, a1, a2, alpha = x
        axis = _unit(axis0 + a1 * b1 + a2 * b2)
        return np.array([ax, ay, az]), axis, alpha

    def resid(x: np.ndarray) -> np.ndarray:
        apex, axis, alpha = unpack(x)
        return _cone_residuals(fit_points, apex, axis, alpha)

    x0 = np.array([*apex0, 0.0, 0.0, half_angle0])
    lo = [-np.inf] * 3 + [-1.0, -1.0, 0.005]
    hi = [np.inf] * 3 + [1.0, 1.0, np.pi / 2 - 0.005]
    sol = least_squares(resid, x0, method="trf", bounds=(lo, hi),
                        max_nfev=300)
    apex, axis, half_angle = unpack(sol.x)
    half_angle = abs(float(half_angle))
    # Point the axis toward the data.
    if (points.mean(axis=0) - apex) @ axis < 0:
        axis = -axis
    return _finish(
        SurfaceType.CONE,
        {"apex": apex, "axis": axis, "half_angle": half_angle},
        _cone_residuals(points, apex, axis, half_angle),
    )


def _cone_residuals(points: np.ndarray, apex: np.ndarray, axis: np.ndarray,
                    half_angle: float) -> np.ndarray:
    rel = points - apex
    along = rel @ axis
    radial = np.linalg.norm(rel - np.outer(along, axis), axis=1)
    # Signed distance from point to the cone surface (exact for a line cone).
    return radial * np.cos(half_angle) - along * np.sin(half_angle)


