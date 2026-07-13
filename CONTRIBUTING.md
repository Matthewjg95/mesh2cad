# Contributing to mesh2cad

The most valuable thing you can contribute is **a part** — one that broke,
or one that worked. Every real part makes the reconstruction smarter and
guards it against regressions. You do not need to write any code.

## Submit a part in 30 seconds

1. Run your part through the app (Import → Recognize → Reconstruct, or
   Sheet Version).
2. Click **Report Part**. It writes a `.zip` next to your file containing
   `mesh.stl` and `report.json` — the geometry plus the tool's own
   stage-by-stage analysis. **Nothing is uploaded** — it just writes the
   file.
3. Open a [new issue](https://github.com/Matthewjg95/mesh2cad/issues/new/choose)
   — "Reconstruction issue" if it went wrong, "Part submission" if it went
   well — and drag the zip in.

That's it. A repro bundle lets a maintainer rebuild your exact result and
fix the actual cause instead of guessing.

## Why this matters

Each bundle is a **labeled example**: the mesh, the recovered features,
the confidence scores, the build log, and your verdict on what should have
happened. Collected across many parts and part classes, these become:

- a **regression corpus** — fixtures that fail loudly if a change breaks a
  part class that used to work, and
- training data for the design-intent **AI module** on the roadmap.

The `report.json` schema is versioned (`schema_version`) so old bundles
stay usable as the tool evolves.

## Please don't upload geometry you can't share

By submitting a part you confirm you have the right to share it and license
it under the repo's MIT terms as a test fixture. Don't submit proprietary
or NDA'd product geometry. (Development was done against parts kept out of
this repo for exactly this reason.)

## Contributing code

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest tests -q      # keep it green
```

- Every non-GUI module is importable and testable headless; add a test with
  your change (see `tests/` — parts are built with boolean CSG so no
  fixture files are needed).
- Match the surrounding style: type hints, docstrings on public functions,
  comments that explain *why*.
- The pipeline is staged (`meshproc → recognition → intent → reconstruction
  → validation → export`); keep new logic in the stage it belongs to.
- `PROGRESS.md` is the running design log, including dead ends and why they
  died — skim it before a large change.

## Good first issues

- New part-class recognizers (ribs, snap-fits, vents) in `mra/intent`.
- A `.exe` / app-bundle build so non-Python users can run it.
- Bundle-driven regression harness: load every corpus zip and assert its
  recorded outputs still hold.
