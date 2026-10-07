"""Reproducible Sketch Asset Library demo: create -> save -> browse -> reload -> export.

    python examples/sketch_assets/demo.py [OUTPUT_DIR]

Writes everything under OUTPUT_DIR (default: ./sketch_demo_output):
``library/`` (the asset JSON files), ``exports/`` (DXF + SVG per asset) and,
when OpenCascade is installed, ``exports/*.step`` plates rebuilt from the
reloaded assets. Every check below raises if a step loses information.

All three assets are SYNTHETIC examples with illustrative dimensions.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mra.sketch_assets import SketchLibrary, export_dxf, export_svg  # noqa: E402
from mra.sketch_assets.examples import all_examples  # noqa: E402
from mra.sketch_assets.geometry import asset_bounds  # noqa: E402
from mra.sketch_assets.library import asset_key  # noqa: E402


def main(out_dir: Path) -> None:
    library = SketchLibrary(out_dir / "library")
    exports = out_dir / "exports"
    exports.mkdir(parents=True, exist_ok=True)

    print("1. CREATE + SAVE")
    created = all_examples()
    for asset in created:
        path = library.save(asset, overwrite=True)
        print(f"   saved {asset.name!r} ({asset.units}, schema {asset.schema_version}) -> {path}")

    print("2. BROWSE")
    entries, problems = library.scan()
    assert not problems, problems
    for entry in entries:
        b = asset_bounds(entry.asset)
        print(f"   {entry.key:40s} {b[2] - b[0]:g} x {b[3] - b[1]:g} {entry.asset.units}"
              f"  tags={entry.asset.tags}")
    hits, _ = library.search("plate")
    print(f"   search 'plate'            -> {[e.asset.name for e in hits]}")
    hits, _ = library.search(tags=["hole-pattern"])
    print(f"   tag 'hole-pattern'        -> {[e.asset.name for e in hits]}")

    print("3. RELOAD")
    reloaded = []
    for original in created:
        asset = library.load(asset_key(original.name))
        assert asset == original, f"round trip changed {original.name}"
        reloaded.append(asset)
        print(f"   {original.name!r}: identical after reload "
              f"(units {asset.units}, provenance {asset.provenance['source']})")

    print("4. EXPORT")
    for asset in reloaded:
        key = asset_key(asset.name)
        print(f"   {export_dxf(asset, exports / f'{key}.dxf')}")
        print(f"   {export_svg(asset, exports / f'{key}.svg')}")

    print("5. RELOAD INTO THE CAD WORKFLOW (plate -> STEP)")
    try:
        from mra.export import export_step
        from mra.sketch_assets.cad import asset_to_plate
        from mra.validation import validate_shape
    except ImportError as exc:  # OCP not installed: headless core still works
        print(f"   skipped ({exc})")
        return
    for asset in reloaded:
        if not any(s.role == "outline" for s in (*asset.polygons, *asset.profiles, *asset.circles)):
            print(f"   {asset.name!r}: no outline (a bare hole pattern) - not a plate")
            continue
        plate = asset_to_plate(asset, 3.0)
        report = validate_shape(plate.shape)
        step = exports / f"{asset_key(asset.name)}.step"
        export_step(plate.shape, step)
        for line in plate.log:
            print(f"   {line}")
        print(f"   valid={report.is_valid} volume={report.volume:.3f} mm^3 -> {step}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sketch_demo_output"))
