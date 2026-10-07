"""Pure-Python geometry helpers shared by the exporters and the preview.

Nothing here changes an asset: these functions only measure it.
"""

from __future__ import annotations

import math

from mra.sketch_assets.models import Arc, Circle, Line, Point2D, Polygon, Profile, SketchAsset

Bounds = tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)


def arc_extreme_points(arc: Arc) -> list[Point2D]:
    """Endpoints plus every axis-extreme point the arc actually sweeps over."""
    points = [arc.start, arc.end]
    sign = 1.0 if arc.ccw else -1.0
    sweep = arc.sweep
    for k in range(4):
        axis_angle = k * math.pi / 2.0
        # Angular distance from the start to this axis direction, measured
        # along the arc's direction of travel.
        travel = (sign * (axis_angle - arc.start_angle)) % (2.0 * math.pi)
        if travel < sweep:
            points.append(
                (
                    arc.center[0] + arc.radius * math.cos(axis_angle),
                    arc.center[1] + arc.radius * math.sin(axis_angle),
                )
            )
    return points


def _shape_points(shape: Circle | Polygon | Profile) -> list[Point2D]:
    if isinstance(shape, Circle):
        r = shape.diameter / 2.0
        cx, cy = shape.center
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if isinstance(shape, Polygon):
        return list(shape.points)
    points: list[Point2D] = []
    for segment in shape.segments:
        if isinstance(segment, Line):
            points += [segment.start, segment.end]
        else:
            points += arc_extreme_points(segment)
    return points


def asset_bounds(asset: SketchAsset) -> Bounds | None:
    """Tight bounding box of all geometry in asset-local coordinates.

    Returns None for an asset with no geometry.
    """
    points: list[Point2D] = []
    for shape in (*asset.polygons, *asset.profiles, *asset.circles):
        points += _shape_points(shape)
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def iter_shapes(asset: SketchAsset) -> list[Circle | Polygon | Profile]:
    """All closed shapes in a stable order: polygons, profiles, circles."""
    return [*asset.polygons, *asset.profiles, *asset.circles]
