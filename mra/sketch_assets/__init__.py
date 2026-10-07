"""Portable sketch assets for reusable mechanical interfaces.

The public API is kernel-independent: the schema, the local library, and
DXF/SVG export import only the standard library, so other tools can reuse
them without OpenCascade or Qt. The OpenCascade bridge lives in
``mra.sketch_assets.cad`` and must be imported explicitly.
"""

from mra.sketch_assets.export_dxf import export_dxf
from mra.sketch_assets.export_svg import export_svg
from mra.sketch_assets.library import (
    LibraryEntry,
    LibraryProblem,
    SketchLibrary,
    default_library_dir,
)
from mra.sketch_assets.models import Arc, Circle, Line, Polygon, Profile, SketchAsset

__all__ = [
    "Arc",
    "Circle",
    "LibraryEntry",
    "LibraryProblem",
    "Line",
    "Polygon",
    "Profile",
    "SketchAsset",
    "SketchLibrary",
    "default_library_dir",
    "export_dxf",
    "export_svg",
]
