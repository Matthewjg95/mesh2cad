"""Machinist-facing hole schedule accompanying a STEP export.

Plain STEP (AP214/AP242 geometry) carries only the bore CYLINDER — there
is no native "this is an M3 tapped hole" in the geometry itself; thread
callouts live in PMI annotations (rarely written or read outside high-end
CAD) or, in practice, on a drawing / hole table. This module writes that
hole table, so the purpose the user assigned in the hole wizard reaches
the machine shop.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from mra.core import FeatureType
from mra.intent import IntentResult


def write_hole_schedule(
    intent: IntentResult, step_path: str | Path
) -> Path | None:
    """Write ``<step stem>_holes.txt`` next to the STEP file.

    Positions are given in the STEP file's own coordinate system so the
    machinist can verify against the model directly.

    Returns:
        The schedule path, or None when the intent has no holes.
    """
    holes = [f for f in intent.features
             if f.feature_type == FeatureType.HOLE]
    if not holes:
        return None

    step_path = Path(step_path)
    out = step_path.with_name(step_path.stem + "_holes.txt")

    lines = [
        f"HOLE SCHEDULE for {step_path.name}",
        "Coordinates in model space (mm). Ø as modelled; PURPOSE is the",
        "engineering intent — thread per purpose, not per modelled Ø.",
        "",
        f"{'#':>2}  {'X':>8}  {'Y':>8}  {'Z':>8}  {'Ø':>6}  "
        f"{'DEPTH':>6}  PURPOSE",
    ]
    for i, h in enumerate(sorted(
        holes, key=lambda f: float(f.params["diameter"])
    ), start=1):
        c = np.asarray(h.params["center"], dtype=float)
        d = float(h.params["diameter"])
        depth = float(h.params["depth"])
        through = bool(h.params.get("through", False))
        purpose = str(h.params.get("purpose", "as measured"))
        lines.append(
            f"{i:>2}  {c[0]:8.2f}  {c[1]:8.2f}  {c[2]:8.2f}  {d:6.2f}  "
            f"{'THRU' if through else f'{depth:5.2f}'}  {purpose}"
        )
    lines.append("")
    lines.append(
        "NOTE: STEP geometry cannot carry thread specs; tapped holes are"
    )
    lines.append(
        "modelled at tap-drill diameter. Tap per PURPOSE column."
    )
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
