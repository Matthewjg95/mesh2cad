"""Portable 2D engineering-interface schema.

This module intentionally knows nothing about meshes, CAD kernels, Fusion,
FreeCAD, or the Mesh2CAD GUI.  Producers translate their source geometry into
these primitives; consumers export or index them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Point2D = tuple[float, float]
Units = Literal["mm", "in"]


@dataclass(frozen=True)
class Circle:
    """Circular sketch primitive, typically a mounting hole or cutout."""

    center: Point2D
    diameter: float
    role: str = "hole"

    def __post_init__(self) -> None:
        if self.diameter <= 0:
            raise ValueError("circle diameter must be positive")


@dataclass(frozen=True)
class Polygon:
    """Closed polygonal primitive such as an outline, cutout, or keep-out."""

    points: tuple[Point2D, ...]
    role: str = "outline"

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError("polygon requires at least three points")


@dataclass
class SketchAsset:
    """A reusable, source-agnostic 2D engineering interface.

    Coordinates are expressed relative to ``origin`` in ``units``.  Geometry
    is intentionally minimal in V1: circles and polygons cover board outlines,
    mounting holes, simple cutouts, and keep-out regions.
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
        if not self.name.strip():
            raise ValueError("asset name must not be empty")
        if self.units not in ("mm", "in"):
            raise ValueError("units must be 'mm' or 'in'")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return asdict(self)
