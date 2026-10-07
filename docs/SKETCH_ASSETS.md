# Sketch Asset Library

Reusable, named 2D sketch assets — hole patterns, plate outlines, device
footprints — saved to a local folder, browsable with a preview, reloadable
into the CAD workflow, and exportable to DXF and SVG.

The core (`mra.sketch_assets`: schema, library, DXF/SVG export) imports only
the Python standard library, so it runs headless and can be vendored by other
tools (e.g. ShadowScan, PlatypusOne) without OpenCascade or Qt. The CAD bridge
(`mra.sketch_assets.cad`) and the GUI dialog (`mra.gui.sketch_library`) are
thin layers on top.

## Quick start

```bash
# Reproducible demo: create -> save -> browse -> reload -> export (+ STEP)
python examples/sketch_assets/demo.py sketch_demo_output

# Headless CLI
python -m mra.sketch_assets --library ./my_lib examples          # add the 3 synthetic examples
python -m mra.sketch_assets --library ./my_lib list --query plate --tag synthetic-example
python -m mra.sketch_assets --library ./my_lib show synthetic-rounded-plate-80x50-r6
python -m mra.sketch_assets --library ./my_lib export synthetic-rounded-plate-80x50-r6 \
    --dxf plate.dxf --svg plate.svg
python -m mra.sketch_assets --library ./my_lib save some_asset.json  # validate + add
```

In the app: **Sketch Library** (toolbar) opens a browser with name/tag search
and a live preview.

- **Save Current Part…** captures the current solid's largest planar face
  (typically after **Sheet Version**) as an asset, in millimetres.
- **Load into Workspace** extrudes the selected asset to a plate (you pick
  the thickness), ready for **Validate** and **Export STEP**.
- **Export DXF… / Export SVG…** write the selected asset.
- **Change Folder…** picks the library directory (remembered).

## Python API

```python
from mra.sketch_assets import Arc, Circle, Line, Profile, SketchAsset, SketchLibrary
from mra.sketch_assets import export_dxf, export_svg

plate = SketchAsset(
    name="My bracket",
    units="mm",
    profiles=[Profile((
        Line((0, 0), (40, 0)),
        Line((40, 0), (40, 15)),
        Arc((40, 15), (30, 25), center=(30, 15)),          # ccw by default
        Line((30, 25), (0, 25)),
        Line((0, 25), (0, 0)),
    ), role="outline")],
    circles=[Circle((8, 8), 3.4, role="mounting-hole")],
    metadata={"tags": ["bracket"]},
    provenance={"source": "hand-measured", "by": "me"},
)
lib = SketchLibrary("~/sketches")   # or SketchLibrary() for the default folder
lib.save(plate)                     # refuses to overwrite unless overwrite=True
entries, problems = lib.search("brack", tags=["bracket"])
export_dxf(lib.load("my-bracket"), "bracket.dxf")

# With OpenCascade installed:
from mra.sketch_assets.cad import asset_to_plate, shape_to_asset
solid = asset_to_plate(plate, thickness_mm=3.0).shape
```

## Storage

- One asset per `<key>.sketch.json`; the key is a slug of the name
  (`"My bracket"` → `my-bracket`).
- Library folder: the `--library` / `SketchLibrary(root)` argument, else
  `$MESH2CAD_SKETCH_LIBRARY`, else `~/.mesh2cad/sketch_assets`. The GUI's
  chosen folder is stored in Qt settings.
- The file is exactly `SketchAsset.to_dict()` (no wrapper). Writes are atomic.
- Unreadable files are listed as problems (CLI stderr, red note in the GUI)
  with the reason; they are never silently skipped or "repaired".

## Schema

| Version | Contents |
|---|---|
| `1.0` | `name`, `units`, `origin`, `circles`, `polygons`, `metadata`, `provenance` (PRs #7–#9) |
| `1.1` | everything in 1.0 plus `profiles`: closed loops of `line` / `arc` segments |

- An asset without profiles is written as `1.0`, so strict 1.0 readers still
  read it; an asset with profiles is `1.1`. An explicit `schema_version` is
  kept as given; `1.0` with profiles is rejected.
- Reading is strict: unknown fields, unknown versions, unsupported segment
  types, NaN/Infinity, duplicate JSON keys, non-finite or non-numeric
  coordinates, degenerate shapes, and arcs whose start/end are not
  equidistant from the center all fail with a message naming the problem.
- Profile segments must meet within `GEOMETRY_TOL = 1e-6` asset units.
  Larger gaps are rejected, never closed.
- Arc segment: `{"type": "arc", "start", "end", "center", "ccw": bool}`.
  Full circles are `circles`, not arcs.
- Tags live in `metadata["tags"]` (list of non-empty strings).
- Roles are free text. `outline` marks the plate boundary. When an asset is
  built as a plate, roles containing `hole`, `cutout` or `slot` are cut
  through. Any other role (e.g. `keep-out`) is kept in the asset and
  exports, and is reported as "not cut" in the build log.

## Units and coordinates

- Each asset declares `units` (`mm` or `in`). Persistence, DXF and SVG
  never scale coordinates.
- The convention is right-handed X right / Y up. `origin` records where the
  local frame sits in its producer's frame. It is stored and exported as
  metadata but **not** applied to coordinates.
- **CAD bridge:** the STEP workflow is millimetres. Loading an `in` asset
  multiplies by exactly 25.4 and says so in the build log; the asset is
  unchanged. Captured assets are `mm`, and `provenance.sketch_plane` records
  the 3D plane (`origin`, `normal`, `x_axis`, `y_axis`). Each 2D point
  `(u, v)` maps back to 3D as `origin + u·x_axis + v·y_axis`.

### DXF

- The dialect is R12 / AC1009 ASCII.
- `$INSUNITS` is 4 (mm) or 1 (in), and `$MEASUREMENT` is set to match.
- Circles become `CIRCLE`, arcs become `ARC`, and straight edges become
  `LINE`. Each role becomes a layer (upper-cased, sanitised).
- DXF is Y-up like the asset, so no flip is applied. DXF arcs always run
  CCW from angle 50 to 51, so a clockwise arc is written with its angles
  swapped (the same curve).
- Floats are written with full `repr` precision.

### SVG

SVG is Y-down; the asset is Y-up. The exporter handles this as follows:

- **Numbers unchanged.** Every coordinate is written exactly as stored,
  inside `<g transform="scale(1 -1)">`. The only flip is that transform.
- **viewBox.** It is the flipped bounds plus a 4 % margin: the top edge is
  `-(max_y + margin)`.
- **True scale.** The root `width`/`height` carry the asset unit (e.g.
  `84.2mm`) and one user unit equals one asset unit, so the file prints and
  imports at true size.
- **Arcs.** Arcs are `A r r 0 large sweep x y` commands. The sweep flag is
  evaluated in the group's local (Y-up) frame, so `sweep=1` means CCW, the
  same as `ccw: true` in the asset.
- **Metadata.** `<metadata>` holds JSON with the name, units, schema
  version, origin, provenance and tags. Every element carries `data-role`.
- The GUI preview renders this same SVG.

## Synthetic examples

`examples/sketch_assets/library/` holds three **synthetic** assets with
round, illustrative numbers. They do not describe any real product:

- a 4 × Ø3.4 mm hole pattern on 50 × 30 mm (schema 1.0, no outline);
- an 80 × 50 mm R6 rounded plate with four holes and an obround slot
  (schema 1.1);
- a 2.0 × 3.5 in device outline with R0.25 top corners, two Ø0.125 in holes
  and a `keep-out` zone (inches, schema 1.1).

A test checks that the committed files match `mra/sketch_assets/examples.py`.

## Limitations

- **Geometry types.** Only lines, circular arcs, circles and polygons are
  supported. Capture rejects B-spline, ellipse and other edges rather than
  approximating them.
- **No self-intersection check.** Profiles are not checked for
  self-intersection, or for containment of cutouts within the outline.
  The plate builder warns when a cut misses the plate.
- **Capture picks one face.** It takes the single largest planar face. On a
  fully reconstructed part with pockets, that may not be the face you want;
  run **Sheet Version** first for a clean footprint.
- **Loading builds plates only.** Loading creates an extruded plate. It does
  not merge the asset into an existing solid.
- **DXF/SVG are export-only.** There is no DXF or SVG import yet.
- **Search.** Search is a substring match over names and tags.
