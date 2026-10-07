"""DXF (R12 / AC1009 ASCII) export of a sketch asset.

R12 is the most widely readable DXF dialect (laser/waterjet services, CAM,
LibreCAD, Inkscape, FreeCAD, Fusion). Geometry is written 1:1 in the
asset's local coordinates and units:

- circles become CIRCLE entities and arcs become ARC entities (true
  analytic geometry, never polylines);
- polygon and line edges become LINE entities;
- each primitive's role becomes its layer (sanitised, upper-cased).

DXF and the asset share the same Y-up convention, so no axis flip is
needed. Units are declared in ``$INSUNITS`` (1 = inches, 4 = millimetres)
and ``$MEASUREMENT``; the coordinates themselves are never scaled.
Coordinates are written with ``repr`` so every float survives exactly.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from mra.sketch_assets.geometry import asset_bounds
from mra.sketch_assets.models import Arc, Circle, Line, Polygon, Profile, SketchAsset

_INSUNITS = {"mm": 4, "in": 1}
_MEASUREMENT = {"mm": 1, "in": 0}


def layer_name(role: str) -> str:
    """Map a free-form role onto a legal DXF layer name."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", role.strip()).strip("_").upper()
    return name or "SKETCH"


def _num(value: float) -> str:
    return repr(float(value))


def _comment_safe(text: str) -> str:
    return " ".join(str(text).split())


def dxf_text(asset: SketchAsset) -> str:
    """Render the asset as DXF R12 text."""
    out: list[str] = []

    def pair(code: int, value: object) -> None:
        out.append(str(code))
        out.append(str(value))

    pair(999, f"mesh2cad sketch asset: {_comment_safe(asset.name)}")
    pair(999, f"units={asset.units} schema_version={asset.schema_version} "
              f"origin={asset.origin[0]!r},{asset.origin[1]!r} (not applied)")

    bounds = asset_bounds(asset) or (0.0, 0.0, 0.0, 0.0)
    pair(0, "SECTION")
    pair(2, "HEADER")
    pair(9, "$ACADVER")
    pair(1, "AC1009")
    pair(9, "$INSUNITS")
    pair(70, _INSUNITS[asset.units])
    pair(9, "$MEASUREMENT")
    pair(70, _MEASUREMENT[asset.units])
    pair(9, "$EXTMIN")
    pair(10, _num(bounds[0]))
    pair(20, _num(bounds[1]))
    pair(30, "0.0")
    pair(9, "$EXTMAX")
    pair(10, _num(bounds[2]))
    pair(20, _num(bounds[3]))
    pair(30, "0.0")
    pair(0, "ENDSEC")

    shapes = [*asset.polygons, *asset.profiles, *asset.circles]
    layers = sorted({layer_name(s.role) for s in shapes} or {"SKETCH"})
    pair(0, "SECTION")
    pair(2, "TABLES")
    pair(0, "TABLE")
    pair(2, "LTYPE")
    pair(70, 1)
    pair(0, "LTYPE")
    pair(2, "CONTINUOUS")
    pair(70, 0)
    pair(3, "Solid line")
    pair(72, 65)
    pair(73, 0)
    pair(40, "0.0")
    pair(0, "ENDTAB")
    pair(0, "TABLE")
    pair(2, "LAYER")
    pair(70, len(layers))
    for name in layers:
        pair(0, "LAYER")
        pair(2, name)
        pair(70, 0)
        pair(62, 7)
        pair(6, "CONTINUOUS")
    pair(0, "ENDTAB")
    pair(0, "ENDSEC")

    pair(0, "SECTION")
    pair(2, "ENTITIES")

    def line(layer: str, a: tuple[float, float], b: tuple[float, float]) -> None:
        pair(0, "LINE")
        pair(8, layer)
        pair(10, _num(a[0]))
        pair(20, _num(a[1]))
        pair(30, "0.0")
        pair(11, _num(b[0]))
        pair(21, _num(b[1]))
        pair(31, "0.0")

    def arc(layer: str, segment: Arc) -> None:
        # DXF arcs always run counter-clockwise from 50 to 51, so a
        # clockwise segment is written with its angles swapped (same curve).
        start, end = segment.start_angle, segment.end_angle
        if not segment.ccw:
            start, end = end, start
        pair(0, "ARC")
        pair(8, layer)
        pair(10, _num(segment.center[0]))
        pair(20, _num(segment.center[1]))
        pair(30, "0.0")
        pair(40, _num(segment.radius))
        pair(50, _num(math.degrees(start) % 360.0))
        pair(51, _num(math.degrees(end) % 360.0))

    for shape in shapes:
        layer = layer_name(shape.role)
        if isinstance(shape, Circle):
            pair(0, "CIRCLE")
            pair(8, layer)
            pair(10, _num(shape.center[0]))
            pair(20, _num(shape.center[1]))
            pair(30, "0.0")
            pair(40, _num(shape.diameter / 2.0))
        elif isinstance(shape, Polygon):
            pts = shape.points
            for a, b in zip(pts, pts[1:] + pts[:1]):
                line(layer, a, b)
        elif isinstance(shape, Profile):
            for segment in shape.segments:
                if isinstance(segment, Line):
                    line(layer, segment.start, segment.end)
                else:
                    arc(layer, segment)

    pair(0, "ENDSEC")
    pair(0, "EOF")
    return "\n".join(out) + "\n"


def export_dxf(asset: SketchAsset, path: str | Path) -> Path:
    """Write the asset to ``path`` as DXF R12 and return the path."""
    path = Path(path)
    path.write_text(dxf_text(asset), encoding="ascii", errors="replace")
    return path
