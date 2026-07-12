# Mechanical Reverse Engineering Assistant

Turn an STL into an **editable, manufacturable CAD model** — by recovering
the design intent, not by wrapping triangles.

This is not an STL-to-STEP converter. It's a desktop reverse-engineering
workbench that looks at a mesh the way a mechanical engineer would: it finds
the planes, cylinders, holes, pockets, bosses and walls the original designer
intended, rebuilds them as true analytic B-Rep geometry (real circles, real
arcs — never tessellation), and exports a clean AP242/AP214 STEP you can
machine, laser-cut, or redesign.

## Real result

The part that drove development: the backplate of an M5Stack Tab5, scanned
from the product assembly STL.

| Route | Quote (SendCutSend, qty 1) |
|---|---|
| 3-axis CNC machining of the faithful reconstruction | **$1,188** |
| Flat **sheet version** produced by this tool + $3 of standoffs | **$12.05** |

The sheet function measured the part's own towers (3.2 mm, Ø5.7), dropped
all depth features, kept the profile and every through-hole as true arcs,
and wrote a hole schedule telling the shop which holes serve which screws.

## What it does

- **Stage 1 – Repair**: weld duplicate vertices, fix winding/normals, fill
  small holes, split multi-body assemblies (with a body picker in the GUI).
- **Stage 2 – Recognize**: segment the mesh into analytic surfaces (plane /
  cylinder / cone / sphere) with least-squares fits, normal-consistency
  gates, and per-patch confidence. Handles 100k+ triangle meshes in seconds.
- **Stage 3 – Intent**: infer the engineering features — base extrusion,
  holes (with pattern detection), pockets/terraces, pads/towers, fillets,
  wall thickness, mirror symmetry. Nearly-equal dimensions snap to common
  values; ambiguity becomes a question, never a silent guess.
- **Stage 4 – Interactive**: hole wizard (assign M2–M4 clearance / cut-tap /
  roll-tap purposes, or fill holes in), machining-cost savings menu (drop
  cosmetic recesses, eliminate whole CNC setups with tradeoffs spelled out),
  numbered feature list with click-to-highlight.
- **Stage 5 – Rebuild**: true B-Rep solids via OpenCascade — profile wires
  with recovered arcs and rounded rectangles, analytic cylinder holes,
  terrace pockets with islands, prismatic ribs for molded blends.
- **Stage 6 – Validate**: closed-solid check, open edges, slivers, volume
  cross-checked against the scan.
- **Stage 7 – Export**: STEP AP242/AP214 plus a machinist-facing
  `*_holes.txt` schedule (positions, diameters, thread purposes — because
  STEP geometry cannot carry thread callouts).
- **Sheet Version**: one click flattens any plate-class part to a
  laser/waterjet-cuttable sheet at your stock gauge.
- **DFM cost model**: per-feature machining cost estimate (setups and CAM
  programming dominate one-offs, not cutting time) with quantity
  amortization.

## Install

Requires Python 3.12+ on Windows/Linux/macOS.

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt     # Linux/macOS
```

## Run

```bash
python -m mra
```

(Windows: double-click `MRA.bat`.)

Workflow: **Import STL → (Isolate Body) → Repair Mesh → Recognize Features →
Reconstruct CAD** (wizards ask about holes and cost tradeoffs) **→ Validate →
Export STEP** — or **Sheet Version** for the flat-cut variant.

## Tests

```bash
.venv/Scripts/python -m pytest tests -q
```

85 tests cover every stage on synthetic parts built with boolean CSG, plus
STEP round-trips verifying the output is analytic (real `CIRCLE` entities,
never point soup).

## Architecture

```
mra/
  core/            Datatypes: patches, features, tolerances, questions, loops
  meshproc/        Stage 1 — import, repair, stats, body splitting
  recognition/     Stage 2 — segmentation + surface fitting
  intent/          Stage 3 — feature/intent recovery
  reconstruction/  Stage 5 — B-Rep building, bridges, fillets, sheet
  validation/      Stage 6 — solid health checks
  export/          Stage 7 — STEP writer + hole schedules
  dfm.py           Machining cost model & simplification transforms
  gui/             PySide6 workbench (viewport, wizards, dialogs)
```

Every non-GUI module is importable and testable headless. `PROGRESS.md` is
the full development log, including the dead ends and why they died.

## Honest limitations

- Optimized for the *extruded plate / enclosure* part class (plates,
  brackets, covers, housings). Organic shapes reconstruct as honest
  FREEFORM regions and questions, not fake precision.
- Molded blends/gussets are converted to simple machinable ribs by design
  ("prefer manufacturable geometry over triangle fidelity").
- Complex thin-wall molded shells (wraparound enclosure bodies) are not yet
  reconstructable — the tool tells you so instead of producing garbage.

## License

MIT — see [LICENSE](LICENSE). Built for the makers.
