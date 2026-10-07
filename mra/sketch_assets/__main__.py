"""Headless command line for the sketch asset library.

    python -m mra.sketch_assets [--library DIR] list [--query Q] [--tag T ...]
    python -m mra.sketch_assets [--library DIR] show KEY
    python -m mra.sketch_assets [--library DIR] save FILE.json [--overwrite]
    python -m mra.sketch_assets [--library DIR] export KEY [--dxf P] [--svg P]
    python -m mra.sketch_assets [--library DIR] examples [--overwrite]
"""

from __future__ import annotations

import argparse
import sys

from mra.sketch_assets.examples import all_examples
from mra.sketch_assets.export_dxf import export_dxf
from mra.sketch_assets.export_svg import export_svg
from mra.sketch_assets.geometry import asset_bounds
from mra.sketch_assets.library import SketchLibrary, asset_json, read_asset_file


def _summary(asset) -> str:
    bounds = asset_bounds(asset)
    size = (
        f"{bounds[2] - bounds[0]:g} x {bounds[3] - bounds[1]:g} {asset.units}"
        if bounds else "empty"
    )
    counts = (
        f"{len(asset.circles)} circle(s), {len(asset.polygons)} polygon(s), "
        f"{len(asset.profiles)} profile(s)"
    )
    return f"{size}; {counts}; schema {asset.schema_version}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mra.sketch_assets")
    parser.add_argument("--library", help="library directory (default: "
                        "$MESH2CAD_SKETCH_LIBRARY or ~/.mesh2cad/sketch_assets)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list", help="browse/search assets")
    p_list.add_argument("--query", default="", help="substring of name or tag")
    p_list.add_argument("--tag", action="append", default=[], help="required tag")
    p_show = sub.add_parser("show", help="print an asset's JSON")
    p_show.add_argument("key")
    p_save = sub.add_parser("save", help="validate a JSON asset file and add it")
    p_save.add_argument("file")
    p_save.add_argument("--overwrite", action="store_true")
    p_export = sub.add_parser("export", help="export an asset to DXF and/or SVG")
    p_export.add_argument("key")
    p_export.add_argument("--dxf")
    p_export.add_argument("--svg")
    p_examples = sub.add_parser("examples", help="add the synthetic examples")
    p_examples.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    library = SketchLibrary(args.library)
    try:
        if args.command == "list":
            entries, problems = library.search(args.query, args.tag)
            print(f"Library: {library.root}")
            for entry in entries:
                tags = ", ".join(entry.asset.tags) or "-"
                print(f"  {entry.key}\n    {entry.asset.name} | {_summary(entry.asset)} | tags: {tags}")
            if not entries:
                print("  (no matching assets)")
            for problem in problems:
                print(f"  ! unreadable {problem.path.name}: {problem.error}", file=sys.stderr)
        elif args.command == "show":
            print(asset_json(library.load(args.key)), end="")
        elif args.command == "save":
            asset = read_asset_file(args.file)
            print(library.save(asset, overwrite=args.overwrite))
        elif args.command == "export":
            if not (args.dxf or args.svg):
                parser.error("export needs --dxf and/or --svg")
            asset = library.load(args.key)
            if args.dxf:
                print(export_dxf(asset, args.dxf))
            if args.svg:
                print(export_svg(asset, args.svg))
        elif args.command == "examples":
            for asset in all_examples():
                print(library.save(asset, overwrite=args.overwrite))
    except (ValueError, KeyError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
