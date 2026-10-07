"""SVG export of a sketch asset — also the preview format for browsing.

Coordinate and unit handling (SVG is Y-down, the asset is Y-up):

- Every coordinate is written **unchanged**, in asset-local units, inside a
  ``<g transform="scale(1 -1)">`` group. The single Y flip happens in that
  transform, never in the numbers, so values read back from the file equal
  the asset's values exactly.
- The ``viewBox`` is the flipped geometry bounds plus a small margin, and
  the root ``width``/``height`` carry the asset unit (``mm`` or ``in``):
  one user unit is one asset unit, so the drawing prints at true scale.
- Arcs are SVG elliptical-arc commands with equal radii (true arcs).
  Because the sweep flag is evaluated in the group's local (Y-up) frame,
  ``sweep-flag=1`` means counter-clockwise exactly as in the asset.
- ``origin`` is recorded in metadata but not applied, matching DXF export.
- Name, units, schema version, origin and provenance are embedded as JSON in
  ``<metadata>``; each primitive carries its role in ``data-role``.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from mra.sketch_assets.geometry import asset_bounds
from mra.sketch_assets.models import Arc, Line, Profile, SketchAsset

_STROKES = {"outline": "#1f2937"}
_CUT_STROKE = "#c0392b"
_OTHER_STROKE = "#2471a3"


def _num(value: float) -> str:
    return repr(float(value))


def _stroke(role: str) -> str:
    if role in _STROKES:
        return _STROKES[role]
    lowered = role.lower()
    if any(word in lowered for word in ("hole", "cutout", "slot")):
        return _CUT_STROKE
    return _OTHER_STROKE


def _profile_path(profile: Profile) -> str:
    first = profile.segments[0].start
    parts = [f"M {_num(first[0])} {_num(first[1])}"]
    for segment in profile.segments:
        x, y = segment.end
        if isinstance(segment, Line):
            parts.append(f"L {_num(x)} {_num(y)}")
        else:
            assert isinstance(segment, Arc)
            r = _num(segment.radius)
            large = 1 if segment.sweep > 3.141592653589793 else 0
            sweep = 1 if segment.ccw else 0
            parts.append(f"A {r} {r} 0 {large} {sweep} {_num(x)} {_num(y)}")
    parts.append("Z")
    return " ".join(parts)


def svg_text(asset: SketchAsset) -> str:
    """Render the asset as a standalone SVG document."""
    bounds = asset_bounds(asset) or (0.0, 0.0, 1.0, 1.0)
    min_x, min_y, max_x, max_y = bounds
    width = max(max_x - min_x, 1e-9)
    height = max(max_y - min_y, 1e-9)
    margin = 0.04 * max(width, height)
    stroke_width = max(width, height) / 400.0

    vb_x = min_x - margin
    vb_y = -(max_y + margin)  # Y flip: top of the viewBox is -max_y
    vb_w = width + 2 * margin
    vb_h = height + 2 * margin

    metadata = json.dumps(
        {
            "name": asset.name,
            "units": asset.units,
            "schema_version": asset.schema_version,
            "origin": list(asset.origin),
            "coordinate_system": "asset-local, Y-up, flipped by group transform",
            "provenance": asset.provenance,
            "tags": asset.tags,
        },
        sort_keys=True,
        default=str,
    )

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'width="{_num(vb_w)}{asset.units}" height="{_num(vb_h)}{asset.units}" '
        f'viewBox="{_num(vb_x)} {_num(vb_y)} {_num(vb_w)} {_num(vb_h)}" '
        f'data-units="{asset.units}" data-schema-version="{escape(asset.schema_version)}">',
        f"  <title>{escape(asset.name)}</title>",
        f"  <metadata>{escape(metadata)}</metadata>",
        f'  <g transform="scale(1 -1)" fill="none" '
        f'stroke-width="{_num(stroke_width)}" stroke-linejoin="round">',
    ]

    def attrs(role: str) -> str:
        return f'stroke="{_stroke(role)}" data-role={quoteattr(role)}'

    for polygon in asset.polygons:
        pts = " ".join(f"{_num(x)},{_num(y)}" for x, y in polygon.points)
        lines.append(f'    <polygon points="{pts}" {attrs(polygon.role)}/>')
    for profile in asset.profiles:
        lines.append(f'    <path d="{_profile_path(profile)}" {attrs(profile.role)}/>')
    for circle in asset.circles:
        cx, cy = circle.center
        lines.append(
            f'    <circle cx="{_num(cx)}" cy="{_num(cy)}" '
            f'r="{_num(circle.diameter / 2.0)}" {attrs(circle.role)}/>'
        )
    lines += ["  </g>", "</svg>", ""]
    return "\n".join(lines)


def export_svg(asset: SketchAsset, path: str | Path) -> Path:
    """Write the asset to ``path`` as SVG and return the path."""
    path = Path(path)
    path.write_text(svg_text(asset), encoding="utf-8")
    return path
