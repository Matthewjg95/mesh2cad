"""Portable 2D engineering-interface schema.

This module intentionally knows nothing about meshes, CAD kernels, Fusion,
FreeCAD, or the Mesh2CAD GUI. Producers translate their source geometry into
these primitives; consumers export or index them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from numbers import Real
from typing import Any, Literal

Point2D = tuple[float, float]
Units = Literal["mm", "in"]

#: Schema versions this module reads and writes. 1.0 is the original
#: circles + polygons contract; 1.1 is a strict superset that adds
#: ``profiles`` (closed loops of analytic line and arc segments).
SUPPORTED_SCHEMA_VERSIONS = ("1.0", "1.1")

#: Coincidence tolerance, in the asset's own units, for profile segment
#: continuity and arc radius consistency. It absorbs floating-point noise
#: from computed coordinates (cos/sin), never real gaps: anything larger is
#: rejected rather than snapped closed.
GEOMETRY_TOL = 1e-6


def _validate_number(value: object, label: str) -> None:
    """Require a finite real number; booleans are not coordinates."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a real number")
    if not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite")


def _validate_point(point: object, label: str) -> None:
    """Validate the canonical V1 two-value tuple representation."""
    if not isinstance(point, tuple) or len(point) != 2:
        raise ValueError(f"{label} must be a two-value tuple")
    _validate_number(point[0], f"{label} x")
    _validate_number(point[1], f"{label} y")


def _point_from_data(value: object, label: str) -> Point2D:
    """Convert a JSON-style point array to the canonical tuple."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must contain exactly two values")
    point = (value[0], value[1])
    _validate_point(point, label)
    return point  # type: ignore[return-value]


def _reject_unknown_fields(
    payload: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    unknown = set(payload) - allowed
    if unknown:
        names = ", ".join(sorted(str(name) for name in unknown))
        raise ValueError(f"{label} contains unknown fields: {names}")


def _validate_role(role: object) -> None:
    if not isinstance(role, str) or not role.strip():
        raise ValueError("primitive role must not be empty")


@dataclass(frozen=True)
class Circle:
    """Circular sketch primitive, typically a mounting hole or cutout."""

    center: Point2D
    diameter: float
    role: str = "hole"

    def __post_init__(self) -> None:
        _validate_point(self.center, "circle center")
        _validate_number(self.diameter, "circle diameter")
        if self.diameter <= 0:
            raise ValueError("circle diameter must be positive")
        _validate_role(self.role)


@dataclass(frozen=True)
class Polygon:
    """Closed polygonal primitive such as an outline, cutout, or keep-out."""

    points: tuple[Point2D, ...]
    role: str = "outline"

    def __post_init__(self) -> None:
        if not isinstance(self.points, tuple) or len(self.points) < 3:
            raise ValueError("polygon requires at least three points")
        for index, point in enumerate(self.points):
            _validate_point(point, f"polygon point {index}")

        # Repeated vertices and collinear point sets cannot define an area.
        if len(set(self.points)) < 3:
            raise ValueError("polygon requires at least three distinct points")
        twice_area = sum(
            x1 * y2 - x2 * y1
            for (x1, y1), (x2, y2) in zip(
                self.points, self.points[1:] + self.points[:1]
            )
        )
        if twice_area == 0:
            raise ValueError("polygon must enclose a non-zero area")
        _validate_role(self.role)


@dataclass(frozen=True)
class Line:
    """Straight profile segment from ``start`` to ``end``."""

    start: Point2D
    end: Point2D

    def __post_init__(self) -> None:
        _validate_point(self.start, "line start")
        _validate_point(self.end, "line end")
        if math.dist(self.start, self.end) <= GEOMETRY_TOL:
            raise ValueError("line segment must have non-zero length")


@dataclass(frozen=True)
class Arc:
    """Analytic circular-arc profile segment.

    The arc runs from ``start`` to ``end`` about ``center``,
    counter-clockwise when ``ccw`` is true (Y-up convention), clockwise
    otherwise. Full circles are not arcs: use :class:`Circle`.
    """

    start: Point2D
    end: Point2D
    center: Point2D
    ccw: bool = True

    def __post_init__(self) -> None:
        _validate_point(self.start, "arc start")
        _validate_point(self.end, "arc end")
        _validate_point(self.center, "arc center")
        if not isinstance(self.ccw, bool):
            raise ValueError("arc ccw must be true or false")
        r_start = math.dist(self.center, self.start)
        r_end = math.dist(self.center, self.end)
        if r_start <= GEOMETRY_TOL:
            raise ValueError("arc radius must be positive")
        if abs(r_start - r_end) > GEOMETRY_TOL * max(1.0, r_start):
            raise ValueError(
                "arc start and end are not equidistant from its center "
                f"(radii {r_start!r} vs {r_end!r})"
            )
        if math.dist(self.start, self.end) <= GEOMETRY_TOL:
            raise ValueError(
                "arc start and end coincide; use a Circle for a full circle"
            )

    @property
    def radius(self) -> float:
        return math.dist(self.center, self.start)

    @property
    def start_angle(self) -> float:
        """Angle of ``start`` about ``center`` in radians, (-pi, pi]."""
        return math.atan2(
            self.start[1] - self.center[1], self.start[0] - self.center[0]
        )

    @property
    def end_angle(self) -> float:
        return math.atan2(
            self.end[1] - self.center[1], self.end[0] - self.center[0]
        )

    @property
    def sweep(self) -> float:
        """Swept angle in radians, always in (0, 2*pi), along ``ccw``."""
        delta = self.end_angle - self.start_angle
        if not self.ccw:
            delta = -delta
        return delta % (2.0 * math.pi)

    def point_at(self, fraction: float) -> Point2D:
        """Point on the arc at ``fraction`` (0 = start, 1 = end) of sweep."""
        sign = 1.0 if self.ccw else -1.0
        angle = self.start_angle + sign * self.sweep * fraction
        return (
            self.center[0] + self.radius * math.cos(angle),
            self.center[1] + self.radius * math.sin(angle),
        )


Segment = Line | Arc


def _segment_signed_area_term(segment: Segment) -> float:
    """Twice the signed-area contribution of one boundary segment.

    The chord contributes its shoelace term; an arc additionally adds (ccw)
    or removes (cw) the circular segment between chord and arc.
    """
    (x1, y1), (x2, y2) = segment.start, segment.end
    term = x1 * y2 - x2 * y1
    if isinstance(segment, Arc):
        theta = segment.sweep
        bulge = segment.radius**2 * (theta - math.sin(theta))
        term += bulge if segment.ccw else -bulge
    return term


@dataclass(frozen=True)
class Profile:
    """Closed loop of analytic line and arc segments (schema 1.1).

    Used for outlines and cutouts whose arcs must stay true arcs, such as a
    rounded-rectangle plate. Consecutive segments must meet within
    :data:`GEOMETRY_TOL`; gaps are rejected, never bridged.
    """

    segments: tuple[Segment, ...]
    role: str = "outline"

    def __post_init__(self) -> None:
        if not isinstance(self.segments, tuple) or len(self.segments) < 2:
            raise ValueError("profile requires at least two segments")
        for index, segment in enumerate(self.segments):
            if not isinstance(segment, (Line, Arc)):
                raise ValueError(
                    f"profile segment {index} must be a Line or Arc"
                )
        for index, segment in enumerate(self.segments):
            following = self.segments[(index + 1) % len(self.segments)]
            gap = math.dist(segment.end, following.start)
            if gap > GEOMETRY_TOL:
                raise ValueError(
                    f"profile is not closed: segment {index} ends "
                    f"{gap:.6g} away from the start of the next segment"
                )
        if abs(self.signed_area()) <= GEOMETRY_TOL**2:
            raise ValueError("profile must enclose a non-zero area")
        _validate_role(self.role)

    def signed_area(self) -> float:
        """Enclosed area, positive for counter-clockwise loops."""
        return 0.5 * sum(_segment_signed_area_term(s) for s in self.segments)


@dataclass
class SketchAsset:
    """A reusable, source-agnostic 2D engineering interface.

    Coordinates are expressed relative to the origin in the selected units.
    Geometry is intentionally minimal in V1: circles and polygons cover board
    outlines, mounting holes, simple cutouts, and keep-out regions; schema
    1.1 adds ``profiles`` with true arcs.

    Convention: right-handed X right / Y up (CAD sketch convention), all
    lengths in ``units``. ``origin`` records where this local frame sits in
    the producer's frame; geometry coordinates are never offset by it.
    """

    name: str
    units: Units = "mm"
    origin: Point2D = (0.0, 0.0)
    circles: list[Circle] = field(default_factory=list)
    polygons: list[Polygon] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    #: Left unset, the lowest version able to hold the geometry is chosen
    #: ("1.0" without profiles, "1.1" with). An explicit version is kept.
    schema_version: str = None  # type: ignore[assignment]
    profiles: list[Profile] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("asset name must not be empty")
        if self.units not in ("mm", "in"):
            raise ValueError("units must be 'mm' or 'in'")
        _validate_point(self.origin, "asset origin")
        if not isinstance(self.circles, list) or not all(
            isinstance(circle, Circle) for circle in self.circles
        ):
            raise ValueError("asset circles must be a list of Circle objects")
        if not isinstance(self.polygons, list) or not all(
            isinstance(polygon, Polygon) for polygon in self.polygons
        ):
            raise ValueError("asset polygons must be a list of Polygon objects")
        if not isinstance(self.metadata, dict):
            raise ValueError("asset metadata must be a dictionary")
        if not isinstance(self.provenance, dict):
            raise ValueError("asset provenance must be a dictionary")
        if not isinstance(self.profiles, list) or not all(
            isinstance(profile, Profile) for profile in self.profiles
        ):
            raise ValueError("asset profiles must be a list of Profile objects")
        if self.schema_version is None:
            self.schema_version = "1.1" if self.profiles else "1.0"
        if not isinstance(self.schema_version, str) or not self.schema_version.strip():
            raise ValueError("schema version must not be empty")
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported sketch asset schema: {self.schema_version!r}"
            )
        if self.profiles and self.schema_version == "1.0":
            raise ValueError("profiles require schema_version '1.1'")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SketchAsset":
        """Reconstruct a V1 asset from decoded JSON-compatible data.

        Unknown fields and unsupported schema versions fail explicitly so a
        future schema cannot be silently misread as V1.
        """
        if not isinstance(payload, Mapping):
            raise ValueError("asset payload must be a mapping")

        allowed = {
            "name",
            "units",
            "origin",
            "circles",
            "polygons",
            "metadata",
            "provenance",
            "schema_version",
        }
        schema_version = payload.get("schema_version", "1.0")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported sketch asset schema: {schema_version!r}")
        if schema_version != "1.0":
            allowed.add("profiles")
        elif "profiles" in payload:
            raise ValueError(
                "asset contains unknown fields: profiles "
                "(profiles require schema_version '1.1')"
            )
        _reject_unknown_fields(payload, allowed, "asset")

        raw_circles = payload.get("circles", [])
        if not isinstance(raw_circles, list):
            raise ValueError("asset circles must be an array")
        circles: list[Circle] = []
        for index, item in enumerate(raw_circles):
            if not isinstance(item, Mapping):
                raise ValueError(f"circle {index} must be an object")
            _reject_unknown_fields(item, {"center", "diameter", "role"}, f"circle {index}")
            if "center" not in item or "diameter" not in item:
                raise ValueError(f"circle {index} requires center and diameter")
            circles.append(
                Circle(
                    center=_point_from_data(item["center"], f"circle {index} center"),
                    diameter=item["diameter"],
                    role=item.get("role", "hole"),
                )
            )

        raw_polygons = payload.get("polygons", [])
        if not isinstance(raw_polygons, list):
            raise ValueError("asset polygons must be an array")
        polygons: list[Polygon] = []
        for index, item in enumerate(raw_polygons):
            if not isinstance(item, Mapping):
                raise ValueError(f"polygon {index} must be an object")
            _reject_unknown_fields(item, {"points", "role"}, f"polygon {index}")
            raw_points = item.get("points")
            if not isinstance(raw_points, list):
                raise ValueError(f"polygon {index} points must be an array")
            polygons.append(
                Polygon(
                    points=tuple(
                        _point_from_data(point, f"polygon {index} point {point_index}")
                        for point_index, point in enumerate(raw_points)
                    ),
                    role=item.get("role", "outline"),
                )
            )

        raw_profiles = payload.get("profiles", [])
        if not isinstance(raw_profiles, list):
            raise ValueError("asset profiles must be an array")
        profiles = [
            _profile_from_data(item, f"profile {index}")
            for index, item in enumerate(raw_profiles)
        ]

        raw_metadata = payload.get("metadata", {})
        raw_provenance = payload.get("provenance", {})
        if not isinstance(raw_metadata, Mapping):
            raise ValueError("asset metadata must be an object")
        if not isinstance(raw_provenance, Mapping):
            raise ValueError("asset provenance must be an object")

        return cls(
            name=payload.get("name"),  # type: ignore[arg-type]
            units=payload.get("units", "mm"),  # type: ignore[arg-type]
            origin=_point_from_data(payload.get("origin", (0.0, 0.0)), "asset origin"),
            circles=circles,
            polygons=polygons,
            metadata=deepcopy(dict(raw_metadata)),
            provenance=deepcopy(dict(raw_provenance)),
            schema_version=schema_version,
            profiles=profiles,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-serialisable representation.

        ``profiles`` is emitted only for schema 1.1 so a 1.0 asset stays
        readable by strict 1.0 consumers.
        """
        payload: dict[str, Any] = {
            "name": self.name,
            "units": self.units,
            "origin": self.origin,
            "circles": [asdict(circle) for circle in self.circles],
            "polygons": [asdict(polygon) for polygon in self.polygons],
            "metadata": deepcopy(self.metadata),
            "provenance": deepcopy(self.provenance),
            "schema_version": self.schema_version,
        }
        if self.schema_version != "1.0":
            payload["profiles"] = [
                {
                    "segments": [_segment_to_data(s) for s in profile.segments],
                    "role": profile.role,
                }
                for profile in self.profiles
            ]
        return payload

    @property
    def tags(self) -> list[str]:
        """Search tags, stored by convention as ``metadata["tags"]``."""
        raw = self.metadata.get("tags", [])
        if isinstance(raw, list):
            return [tag for tag in raw if isinstance(tag, str)]
        return []


def _segment_to_data(segment: Segment) -> dict[str, Any]:
    if isinstance(segment, Line):
        return {"type": "line", "start": segment.start, "end": segment.end}
    return {
        "type": "arc",
        "start": segment.start,
        "end": segment.end,
        "center": segment.center,
        "ccw": segment.ccw,
    }


def _segment_from_data(item: object, label: str) -> Segment:
    if not isinstance(item, Mapping):
        raise ValueError(f"{label} must be an object")
    kind = item.get("type")
    if kind == "line":
        _reject_unknown_fields(item, {"type", "start", "end"}, label)
        if "start" not in item or "end" not in item:
            raise ValueError(f"{label} requires start and end")
        return Line(
            start=_point_from_data(item["start"], f"{label} start"),
            end=_point_from_data(item["end"], f"{label} end"),
        )
    if kind == "arc":
        _reject_unknown_fields(item, {"type", "start", "end", "center", "ccw"}, label)
        if not all(key in item for key in ("start", "end", "center", "ccw")):
            raise ValueError(f"{label} requires start, end, center and ccw")
        return Arc(
            start=_point_from_data(item["start"], f"{label} start"),
            end=_point_from_data(item["end"], f"{label} end"),
            center=_point_from_data(item["center"], f"{label} center"),
            ccw=item["ccw"],
        )
    raise ValueError(f"{label} has unsupported segment type {kind!r}")


def _profile_from_data(item: object, label: str) -> Profile:
    if not isinstance(item, Mapping):
        raise ValueError(f"{label} must be an object")
    _reject_unknown_fields(item, {"segments", "role"}, label)
    raw_segments = item.get("segments")
    if not isinstance(raw_segments, list):
        raise ValueError(f"{label} segments must be an array")
    return Profile(
        segments=tuple(
            _segment_from_data(segment, f"{label} segment {index}")
            for index, segment in enumerate(raw_segments)
        ),
        role=item.get("role", "outline"),
    )
