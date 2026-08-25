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


@dataclass
class SketchAsset:
    """A reusable, source-agnostic 2D engineering interface.

    Coordinates are expressed relative to the origin in the selected units.
    Geometry is intentionally minimal in V1: circles and polygons cover board
    outlines, mounting holes, simple cutouts, and keep-out regions.
    """

    name: str
    units: Units = "mm"
    origin: Point2D = (0.0, 0.0)
    circles: list[Circle] = field(default_factory=list)
    polygons: list[Polygon] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

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
        if not isinstance(self.schema_version, str) or not self.schema_version.strip():
            raise ValueError("schema version must not be empty")

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
        _reject_unknown_fields(payload, allowed, "asset")
        schema_version = payload.get("schema_version", "1.0")
        if schema_version != "1.0":
            raise ValueError(f"unsupported sketch asset schema: {schema_version!r}")

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
            schema_version="1.0",
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-serialisable representation."""
        return asdict(self)
