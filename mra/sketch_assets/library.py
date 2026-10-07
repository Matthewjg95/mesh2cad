"""Local, file-based sketch asset library.

One asset per ``<key>.sketch.json`` file in a plain directory, so a library
can be versioned, synced by the user's own tools, or shared by copying the
folder. The file content is exactly ``SketchAsset.to_dict()`` — no wrapper,
no hidden fields — so any consumer of the schema can read it directly.

The directory is chosen, in order, by the ``root`` argument, the
``MESH2CAD_SKETCH_LIBRARY`` environment variable, or
``~/.mesh2cad/sketch_assets``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mra.sketch_assets.models import SketchAsset

LIBRARY_ENV_VAR = "MESH2CAD_SKETCH_LIBRARY"
FILE_SUFFIX = ".sketch.json"


def default_library_dir() -> Path:
    """The library directory used when none is given explicitly."""
    configured = os.environ.get(LIBRARY_ENV_VAR, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".mesh2cad" / "sketch_assets"


def asset_key(name: str) -> str:
    """Filesystem-safe key derived from an asset name."""
    key = re.sub(r"[^\w]+", "-", name.strip().casefold()).strip("-_")
    if not key:
        raise ValueError(f"asset name {name!r} has no usable characters for a file name")
    return key


def validate_tags(asset: SketchAsset) -> None:
    """``metadata["tags"]``, when present, must be a list of non-empty strings."""
    if "tags" not in asset.metadata:
        return
    tags = asset.metadata["tags"]
    if not isinstance(tags, list) or not all(
        isinstance(tag, str) and tag.strip() for tag in tags
    ):
        raise ValueError("asset metadata 'tags' must be a list of non-empty strings")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(token: str) -> float:
    raise ValueError(f"non-finite number {token} is not allowed")


def read_asset_file(path: str | Path) -> SketchAsset:
    """Strictly parse one asset JSON file.

    Raises:
        ValueError: on malformed JSON, duplicate keys, NaN/Infinity, or any
            schema violation (the message names the problem).
    """
    path = Path(path)
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: invalid JSON ({exc})") from exc
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    try:
        asset = SketchAsset.from_dict(payload)
        validate_tags(asset)
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    return asset


def asset_json(asset: SketchAsset) -> str:
    """Canonical on-disk text for an asset."""
    return json.dumps(asset.to_dict(), indent=2, allow_nan=False) + "\n"


@dataclass(frozen=True)
class LibraryEntry:
    """A successfully loaded library asset."""

    key: str
    path: Path
    asset: SketchAsset


@dataclass(frozen=True)
class LibraryProblem:
    """A library file that could not be loaded, with the reason."""

    path: Path
    error: str


class SketchLibrary:
    """A directory of sketch assets."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root else default_library_dir()

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}{FILE_SUFFIX}"

    def save(self, asset: SketchAsset, *, overwrite: bool = False) -> Path:
        """Persist ``asset`` and return its file path.

        Refuses to replace an existing file unless ``overwrite`` is true.
        The write is atomic (temp file + rename) so a crash never leaves a
        half-written asset.
        """
        validate_tags(asset)
        key = asset_key(asset.name)
        path = self.path_for(key)
        if path.exists() and not overwrite:
            raise FileExistsError(
                f"an asset with key {key!r} already exists at {path}; "
                "choose another name or save with overwrite"
            )
        text = asset_json(asset)
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-", suffix=FILE_SUFFIX)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path

    def load(self, key: str) -> SketchAsset:
        """Load one asset by key (see :func:`asset_key`)."""
        path = self.path_for(key)
        if not path.is_file():
            raise KeyError(f"no asset {key!r} in {self.root}")
        return read_asset_file(path)

    def scan(self) -> tuple[list[LibraryEntry], list[LibraryProblem]]:
        """Load every asset; malformed files are reported, never hidden."""
        entries: list[LibraryEntry] = []
        problems: list[LibraryProblem] = []
        if not self.root.is_dir():
            return entries, problems
        for path in sorted(self.root.glob(f"*{FILE_SUFFIX}")):
            if path.name.startswith(".tmp-"):
                continue
            key = path.name[: -len(FILE_SUFFIX)]
            try:
                entries.append(LibraryEntry(key, path, read_asset_file(path)))
            except (ValueError, OSError) as exc:
                problems.append(LibraryProblem(path, str(exc)))
        entries.sort(key=lambda e: e.asset.name.casefold())
        return entries, problems

    def search(
        self, query: str = "", tags: Iterable[str] = ()
    ) -> tuple[list[LibraryEntry], list[LibraryProblem]]:
        """Filter by name/tag text and required tags.

        ``query`` matches case-insensitively as a substring of the name or
        of any tag. Every entry in ``tags`` must equal one of the asset's
        tags (case-insensitive).
        """
        entries, problems = self.scan()
        needle = query.strip().casefold()
        required = {t.strip().casefold() for t in tags if t.strip()}
        matches = []
        for entry in entries:
            asset_tags = {t.casefold() for t in entry.asset.tags}
            if required - asset_tags:
                continue
            if needle and needle not in entry.asset.name.casefold() and not any(
                needle in t for t in asset_tags
            ):
                continue
            matches.append(entry)
        return matches, problems
