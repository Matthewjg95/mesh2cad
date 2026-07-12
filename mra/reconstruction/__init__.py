"""Stage 5: build true analytic B-Rep solids from recovered intent."""

from mra.reconstruction.builder import BuildResult, build_sheet, build_solid
from mra.reconstruction.tessellate import shape_to_trimesh

__all__ = ["BuildResult", "build_sheet", "build_solid", "shape_to_trimesh"]
