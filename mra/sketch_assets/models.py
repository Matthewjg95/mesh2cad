"""Portable 2D engineering-interface schema.

This module intentionally knows nothing about meshes, CAD kernels, Fusion,
FreeCAD, or the Mesh2CAD GUI. Producers translate their source geometry into
these primitives; consumers export or index them.
"""

from __future__ import annotations

import math
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

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return asdict(self)
