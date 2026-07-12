"""STL (and general mesh) import.

Wraps trimesh loading with the behaviour the pipeline needs: always returns a
single ``trimesh.Trimesh`` (scenes are flattened), never silently processes
away data the repair stage wants to diagnose first.
"""

from __future__ import annotations

from pathlib import Path

import trimesh


def load_mesh(path: str | Path) -> trimesh.Trimesh:
    """Load a mesh file and return an unprocessed ``Trimesh``.

    ``process=False`` matters: trimesh's default processing merges vertices
    and drops degenerate faces on load, which would hide the very defects the
    repair report must describe to the user.

    Args:
        path: Path to an STL / OBJ / PLY / 3MF file.

    Returns:
        The loaded mesh with original vertex/face data.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the file contains no triangle geometry.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"mesh file not found: {path}")

    loaded = trimesh.load(path, process=False, force="mesh")

    if isinstance(loaded, trimesh.Scene):
        geometries = [
            g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)
        ]
        if not geometries:
            raise ValueError(f"no triangle geometry in {path}")
        loaded = trimesh.util.concatenate(geometries)

    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise ValueError(f"no triangle geometry in {path}")

    return loaded
