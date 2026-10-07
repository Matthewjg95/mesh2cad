"""Bridge between sketch assets and the OpenCascade workflow.

This module needs OCP and is deliberately NOT imported by
``mra.sketch_assets`` itself, so the schema, library and exporters stay
usable without a CAD kernel (e.g. from ShadowScan or PlatypusOne).

- :func:`shape_to_asset` captures the largest planar face of a B-Rep
  (typically the Sheet Version footprint) as an asset. Lines and circular
  edges stay analytic; any other edge type is rejected.
- :func:`asset_to_plate` rebuilds an asset as an extruded plate solid so it
  flows into the existing Validate / Export STEP steps.

The STEP workflow is millimetre-based. Inch assets are converted with an
explicit x25.4 factor that is reported in the build log; the asset itself is
never modified.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line, GeomAbs_Plane
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape, TopoDS_Wire
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from mra import __version__
from mra.sketch_assets.models import (
    Arc,
    Circle,
    Line,
    Point2D,
    Polygon,
    Profile,
    SketchAsset,
)

MM_PER_UNIT = {"mm": 1.0, "in": 25.4}


class UnsupportedGeometryError(ValueError):
    """The shape contains geometry a sketch asset cannot represent exactly."""


def is_cut_role(role: str) -> bool:
    """Roles that become through-cuts when an asset is built as a plate."""
    lowered = role.lower()
    return any(word in lowered for word in ("hole", "cutout", "slot"))


# --------------------------------------------------------- asset -> solid


@dataclass
class PlateBuild:
    """Result of :func:`asset_to_plate`."""

    shape: TopoDS_Shape
    log: list[str] = field(default_factory=list)


def _pnt(p: Point2D, scale: float) -> gp_Pnt:
    return gp_Pnt(p[0] * scale, p[1] * scale, 0.0)


def _wire(shape: Circle | Polygon | Profile, scale: float) -> TopoDS_Wire:
    maker = BRepBuilderAPI_MakeWire()
    if isinstance(shape, Circle):
        circ = gp_Circ(
            gp_Ax2(_pnt(shape.center, scale), gp_Dir(0.0, 0.0, 1.0)),
            shape.diameter / 2.0 * scale,
        )
        maker.Add(BRepBuilderAPI_MakeEdge(circ).Edge())
    elif isinstance(shape, Polygon):
        pts = shape.points
        for a, b in zip(pts, pts[1:] + pts[:1]):
            maker.Add(BRepBuilderAPI_MakeEdge(_pnt(a, scale), _pnt(b, scale)).Edge())
    else:
        segments = shape.segments
        for index, segment in enumerate(segments):
            # Reuse the next segment's start as this segment's end so edges
            # share exact vertices (the schema allows GEOMETRY_TOL gaps).
            start = _pnt(segment.start, scale)
            end = _pnt(segments[(index + 1) % len(segments)].start, scale)
            if isinstance(segment, Line):
                maker.Add(BRepBuilderAPI_MakeEdge(start, end).Edge())
            else:
                arc = GC_MakeArcOfCircle(start, _pnt(segment.point_at(0.5), scale), end)
                if not arc.IsDone():
                    raise ValueError(f"could not build arc for role {shape.role!r}")
                maker.Add(BRepBuilderAPI_MakeEdge(arc.Value()).Edge())
    if not maker.IsDone():
        raise ValueError(f"could not build a closed wire for role {shape.role!r}")
    return maker.Wire()


def _prism(shape: Circle | Polygon | Profile, scale: float, z0: float, height: float) -> TopoDS_Shape:
    face = BRepBuilderAPI_MakeFace(_wire(shape, scale), True)
    if not face.IsDone():
        raise ValueError(f"could not build a face for role {shape.role!r}")
    prism = BRepPrimAPI_MakePrism(face.Face(), gp_Vec(0.0, 0.0, height))
    built = prism.Shape()
    if z0:
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(0.0, 0.0, z0))
        built = BRepBuilderAPI_Transform(built, move, True).Shape()
    return built


def _volume(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def asset_to_plate(asset: SketchAsset, thickness_mm: float) -> PlateBuild:
    """Extrude an asset into a plate solid (millimetres, +Z, base at z=0).

    Exactly one primitive must have role ``outline``. Primitives whose role
    names a hole, cutout or slot are cut through; any other role (e.g.
    ``keep-out``) is listed in the log as not cut, never silently dropped.

    Raises:
        ValueError: no/multiple outlines, a non-positive thickness, or a
            primitive OpenCascade cannot build.
    """
    if not (isinstance(thickness_mm, (int, float)) and math.isfinite(thickness_mm)
            and thickness_mm > 0):
        raise ValueError("plate thickness must be a positive finite number of mm")
    scale = MM_PER_UNIT[asset.units]
    log: list[str] = []
    if scale != 1.0:
        log.append(
            f"Asset units are '{asset.units}'; geometry converted x{scale} to mm "
            "for the STEP workflow (the stored asset is unchanged)."
        )

    shapes = [*asset.polygons, *asset.profiles, *asset.circles]
    outlines = [s for s in shapes if s.role == "outline"]
    if len(outlines) != 1:
        raise ValueError(
            f"asset must have exactly one 'outline' primitive to build a plate "
            f"(found {len(outlines)})"
        )
    solid = _prism(outlines[0], scale, 0.0, thickness_mm)
    cuts = 0
    for shape in shapes:
        if shape is outlines[0]:
            continue
        if not is_cut_role(shape.role):
            log.append(f"Not cut: {type(shape).__name__.lower()} with role '{shape.role}'.")
            continue
        before = _volume(solid)
        cutter = _prism(shape, scale, -thickness_mm, 3.0 * thickness_mm)
        op = BRepAlgoAPI_Cut(solid, cutter)
        op.Build()
        if not op.IsDone():
            raise ValueError(f"cut failed for role {shape.role!r}")
        solid = op.Shape()
        if _volume(solid) >= before - 1e-9:
            log.append(f"Warning: {shape.role} at {_where(shape)} lies outside the outline.")
        else:
            cuts += 1
    log.append(
        f"Built '{asset.name}' as a {thickness_mm:g} mm plate with {cuts} through-cut(s)."
    )
    return PlateBuild(solid, log)


def _where(shape: Circle | Polygon | Profile) -> str:
    if isinstance(shape, Circle):
        return f"({shape.center[0]:g}, {shape.center[1]:g})"
    first = shape.points[0] if isinstance(shape, Polygon) else shape.segments[0].start
    return f"({first[0]:g}, {first[1]:g})"


# --------------------------------------------------------- solid -> asset


def _largest_planar_face(shape: TopoDS_Shape) -> TopoDS_Face:
    best, best_area = None, 0.0
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        if BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane:
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            if props.Mass() > best_area:
                best, best_area = face, props.Mass()
        explorer.Next()
    if best is None:
        raise UnsupportedGeometryError("shape has no planar face to capture as a sketch")
    return best


def _sketch_frame(face: TopoDS_Face) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Deterministic (origin, normal, x_axis, y_axis) for a planar face.

    The normal is oriented toward +Z (else +Y, else +X) so a plate is seen
    from above; the X axis is world X projected into the plane (world Y if
    X is the normal); the origin is the world origin projected onto it.
    """
    plane = BRepAdaptor_Surface(face).Plane()
    d = plane.Axis().Direction()
    n = np.array([d.X(), d.Y(), d.Z()])
    for axis in (2, 1, 0):
        if abs(n[axis]) > 1e-9:
            if n[axis] < 0:
                n = -n
            break
    x = np.array([1.0, 0.0, 0.0])
    if abs(n @ x) > 0.9:
        x = np.array([0.0, 1.0, 0.0])
    x = x - (x @ n) * n
    x /= np.linalg.norm(x)
    y = np.cross(n, x)
    loc = plane.Location()
    p0 = np.array([loc.X(), loc.Y(), loc.Z()])
    origin = (p0 @ n) * n
    return origin, n, x, y


def _wire_to_primitive(
    wire: TopoDS_Wire, face: TopoDS_Face, frame: tuple, role: str
) -> Circle | Polygon | Profile:
    origin, n, x_axis, y_axis = frame

    def uv(p: gp_Pnt) -> Point2D:
        v = np.array([p.X(), p.Y(), p.Z()]) - origin
        return (float(v @ x_axis), float(v @ y_axis))

    segments: list[Line | Arc] = []
    full_circle: Circle | None = None
    circle_keys: set[tuple[float, float, float]] = set()
    explorer = BRepTools_WireExplorer(wire, face)
    while explorer.More():
        edge = explorer.Current()
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        reversed_ = edge.Orientation() == TopAbs_REVERSED
        p_start, p_end = curve.Value(first), curve.Value(last)
        if reversed_:
            p_start, p_end = p_end, p_start
        kind = curve.GetType()
        if kind == GeomAbs_Line:
            segments.append(Line(uv(p_start), uv(p_end)))
        elif kind == GeomAbs_Circle:
            circ = curve.Circle()
            center = uv(circ.Location())
            radius = circ.Radius()
            circle_keys.add((round(center[0], 9), round(center[1], 9), round(radius, 9)))
            if abs((last - first) - 2.0 * math.pi) < 1e-9:
                full_circle = Circle(center=center, diameter=2.0 * radius, role=role)
            else:
                a = circ.Axis().Direction()
                param_ccw = (np.array([a.X(), a.Y(), a.Z()]) @ n) > 0
                segments.append(
                    Arc(uv(p_start), uv(p_end), center=center, ccw=bool(param_ccw != reversed_))
                )
        else:
            raise UnsupportedGeometryError(
                f"edge of curve type {kind} cannot be stored exactly in a sketch "
                "asset (only lines and circular arcs are supported); nothing was captured"
            )
        explorer.Next()

    if full_circle is not None:
        if segments:
            raise UnsupportedGeometryError("wire mixes a full circle with other edges")
        return full_circle
    if segments and all(isinstance(s, Arc) for s in segments) and len(circle_keys) == 1:
        arc0 = segments[0]
        return Circle(center=arc0.center, diameter=2.0 * arc0.radius, role=role)
    if segments and all(isinstance(s, Line) for s in segments):
        return Polygon(points=tuple(s.start for s in segments), role=role)
    return Profile(segments=tuple(segments), role=role)


def shape_to_asset(
    shape: TopoDS_Shape,
    name: str,
    *,
    metadata: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> SketchAsset:
    """Capture the largest planar face of ``shape`` as a millimetre asset.

    The outer boundary becomes the ``outline``; inner circular loops become
    ``hole`` circles and other inner loops ``cutout`` primitives. The 2D
    frame is recorded in ``provenance["sketch_plane"]`` so every point maps
    back to 3D as ``origin + u * x_axis + v * y_axis``.

    Raises:
        UnsupportedGeometryError: no planar face, or an edge that is not a
            line or circular arc (e.g. a B-spline).
    """
    face = _largest_planar_face(shape)
    frame = _sketch_frame(face)
    outer = BRepTools.OuterWire_s(face)

    primitives = [_wire_to_primitive(outer, face, frame, "outline")]
    explorer = TopExp_Explorer(face, TopAbs_WIRE)
    while explorer.More():
        wire = TopoDS.Wire_s(explorer.Current())
        if not wire.IsSame(outer):
            primitives.append(_wire_to_primitive(wire, face, frame, "cutout"))
        explorer.Next()

    circles, polygons, profiles = [], [], []
    for prim in primitives:
        if isinstance(prim, Circle):
            if prim.role == "cutout":
                prim = Circle(center=prim.center, diameter=prim.diameter, role="hole")
            circles.append(prim)
        elif isinstance(prim, Polygon):
            polygons.append(prim)
        else:
            profiles.append(prim)

    origin, n, x_axis, y_axis = frame
    record: dict[str, Any] = {
        "producer": "mesh2cad",
        "producer_version": __version__,
        "method": "largest-planar-face",
        "model_units": "mm",
        "sketch_plane": {
            "origin": [float(v) for v in origin],
            "normal": [float(v) for v in n],
            "x_axis": [float(v) for v in x_axis],
            "y_axis": [float(v) for v in y_axis],
        },
    }
    record.update(provenance or {})
    return SketchAsset(
        name=name,
        units="mm",
        circles=circles,
        polygons=polygons,
        profiles=profiles,
        metadata=dict(metadata or {}),
        provenance=record,
    )


__all__ = [
    "MM_PER_UNIT",
    "PlateBuild",
    "UnsupportedGeometryError",
    "asset_to_plate",
    "is_cut_role",
    "shape_to_asset",
]
