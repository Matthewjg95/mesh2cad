"""Design-for-machining: cost estimation and simplification transforms.

The reconstruction recovers what the molded part IS; this module helps
decide what the machined replacement SHOULD BE. A relative cost model
(3-axis milling, aluminium) prices every recovered feature, ranks the
cost drivers, and offers transforms that trade cosmetic fidelity for
machine time — always as explicit, user-approved steps, never silently.

The dollar figures are heuristics for COMPARISON, not quotes: they get
the ranking and the relative savings right, which is what decisions need.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh

from mra.core import Feature, FeatureType, Tolerances
from mra.core.loops import boundary_loops_3d, loop_is_circle
from mra.intent import IntentResult
from mra.recognition import SegmentationResult


@dataclass
class CostModel:
    """Tunable shop-rate assumptions (aluminium, 3-axis, small shop).

    On one-off parts the quote is dominated by NON-cutting work: CAM
    programming, fixturing, inspection — all of which scale with the
    number of distinct features and setups, not with spindle minutes.
    ``programming_per_feature`` captures that; it is why deleting a
    cosmetic pocket saves real money even though it cuts in seconds.
    """

    rate_per_hour: float = 120.0
    setup_cost_per_side: float = 150.0   # fixturing + touch-off per side
    stock_and_facing: float = 60.0       # material + squaring the blank
    programming_base: float = 150.0      # CAM base for any part
    programming_per_feature: float = 12.0  # CAM+inspection per feature
    removal_cm3_per_min: float = 1.5     # adaptive roughing
    finish_mm_per_min: float = 300.0     # contour finishing feed
    drill_minutes: float = 0.4           # per hole (spot + drill)
    tap_minutes: float = 0.8             # per tapped hole
    small_tool_factor: float = 3.0       # cutting-time factor, tool < 2 mm
    small_tool_program_extra: float = 15.0  # extra CAM care per such feature
    thin_web_minutes: float = 4.0        # per fragile web (slow, careful)

    def minutes_cost(self, minutes: float) -> float:
        return minutes / 60.0 * self.rate_per_hour


@dataclass
class CostLine:
    """One priced item in the report."""

    label: str
    cost: float
    minutes: float
    note: str = ""
    feature_ids: list[int] = field(default_factory=list)


@dataclass
class CostReport:
    """Itemised machining cost estimate.

    Attributes:
        lines: Priced items, most expensive first.
        total: One-off total (setup + programming + cutting).
        per_part_cutting: The part of ``total`` repeated for every part.
        sides: Machining setups needed (1 or 2).
    """

    lines: list[CostLine] = field(default_factory=list)
    total: float = 0.0
    per_part_cutting: float = 0.0
    sides: int = 1

    def at_quantity(self, qty: int) -> float:
        """Per-part cost when ``qty`` parts share the fixed work."""
        fixed = self.total - self.per_part_cutting
        return fixed / max(qty, 1) + self.per_part_cutting

    def summary(self) -> str:
        out = [
            f"Estimated one-off cost: ${self.total:,.0f} "
            f"({self.sides} setup(s))",
            f"Per part at qty 5: ${self.at_quantity(5):,.0f}   "
            f"at qty 10: ${self.at_quantity(10):,.0f}",
        ]
        for ln in self.lines:
            out.append(
                f"  ${ln.cost:7,.0f}  {ln.minutes:5.1f} min  {ln.label}"
                + (f"  — {ln.note}" if ln.note else "")
            )
        return "\n".join(out)


def estimate_cost(
    mesh: trimesh.Trimesh,
    segmentation: SegmentationResult,
    intent: IntentResult,
    model: CostModel | None = None,
) -> CostReport:
    """Price the recovered feature set for 3-axis machining."""
    model = model or CostModel()
    report = CostReport()
    lines = report.lines

    extrusion = next(
        (f for f in intent.features
         if f.feature_type == FeatureType.EXTRUSION), None
    )
    direction = (np.asarray(extrusion.params["direction"])
                 if extrusion is not None else np.array([0.0, 0.0, 1.0]))
    height = float(extrusion.params["height"]) if extrusion else 1.0

    # Setups: two-sided when features exist on both sides.
    # Filled pockets are not machined and cost nothing.
    pockets = [f for f in intent.features
               if f.feature_type == FeatureType.POCKET
               and not f.params.get("fill")]
    pads = [f for f in intent.features if f.feature_type == FeatureType.PAD]
    sides_used = {int(f.params["side"]) for f in pockets + pads} or {1}
    report.sides = len(sides_used)
    lines.append(CostLine(
        "Stock, facing and profile", model.stock_and_facing
        + model.setup_cost_per_side * report.sides,
        0.0, note=f"{report.sides} setup(s)"))
    lines.append(CostLine("CAM programming base", model.programming_base,
                          0.0))

    # Pockets: programming dominates; cutting is volume + perimeter.
    for f in pockets:
        patch = f.patches[0]
        depth = float(f.params["depth"])
        volume_cm3 = patch.area * depth / 1000.0
        loops = boundary_loops_3d(mesh, patch.face_indices)
        perimeter = sum(_loop_length(lp) for lp in loops)
        minutes = (volume_cm3 / model.removal_cm3_per_min
                   + perimeter / model.finish_mm_per_min * depth)
        small = _needs_small_tool(loops)
        cost = model.programming_per_feature + model.minutes_cost(
            minutes * (model.small_tool_factor if small else 1.0)
        )
        if small:
            cost += model.small_tool_program_extra
        lines.append(CostLine(
            f"Pocket {depth:.2f} mm ({patch.area:.0f} mm²)",
            cost, minutes,
            note="small tool" if small else "",
            feature_ids=[f.feature_id]))

    # Pads survive as material; they cost via the surrounding pocketing
    # (already counted) — no extra line.

    # Holes: drill groups program once per diameter, cut per hole.
    holes = [f for f in intent.features
             if f.feature_type == FeatureType.HOLE]
    if holes:
        tapped = [h for h in holes
                  if "tap" in str(h.params.get("purpose", "")).lower()]
        diameters = {round(float(h.params["diameter"]), 2) for h in holes}
        minutes = (len(holes) * model.drill_minutes
                   + len(tapped) * model.tap_minutes)
        cost = (len(diameters) * model.programming_per_feature
                + model.minutes_cost(minutes))
        lines.append(CostLine(
            f"{len(holes)} holes ({len(tapped)} tapped, "
            f"{len(diameters)} sizes)",
            cost, minutes,
            feature_ids=[h.feature_id for h in holes]))

    # Through windows: each is a programmed contour, often small-tool.
    windows = find_through_windows(mesh, segmentation, intent)
    for wdw in windows:
        minutes = wdw["perimeter"] / model.finish_mm_per_min * height * 2
        small = wdw["min_width"] < 4.0
        cost = model.programming_per_feature + model.minutes_cost(
            minutes * (model.small_tool_factor if small else 1.0)
        )
        if small:
            cost += model.small_tool_program_extra
        lines.append(CostLine(
            f"Window {wdw['width']:.1f} x {wdw['height']:.1f} mm",
            cost, minutes,
            note=("small tool" if small else ""),
        ))

    lines.sort(key=lambda ln_: -ln_.cost)
    report.total = sum(ln_.cost for ln_ in lines)
    report.per_part_cutting = model.minutes_cost(
        sum(ln_.minutes for ln_ in lines)
    ) + model.stock_and_facing
    return report


def find_through_windows(
    mesh: trimesh.Trimesh,
    segmentation: SegmentationResult,
    intent: IntentResult,
) -> list[dict]:
    """Non-circular through-openings, with sizing for cost decisions."""
    extrusion = next(
        (f for f in intent.features
         if f.feature_type == FeatureType.EXTRUSION), None
    )
    if extrusion is None:
        return []
    direction = np.asarray(extrusion.params["direction"], dtype=np.float64)

    from mra.core import SurfaceType

    windows: list[dict] = []
    seen: list[np.ndarray] = []
    for patch in segmentation.patches:
        if patch.surface_type != SurfaceType.PLANE:
            continue
        if abs(np.asarray(patch.params["normal"]) @ direction) < 0.999:
            continue
        for inner in boundary_loops_3d(mesh, patch.face_indices)[1:]:
            if loop_is_circle(inner) is not None:
                continue
            centroid = inner.mean(axis=0)
            if any(np.linalg.norm(centroid - s) < 1.0 for s in seen):
                continue
            from mra.core.loops import region_is_through

            if not region_is_through(mesh, inner, direction):
                continue
            seen.append(centroid)
            rel = inner - centroid
            b1, b2 = _plane_basis(direction)
            uv = np.column_stack([rel @ b1, rel @ b2])
            size = uv.max(axis=0) - uv.min(axis=0)
            windows.append({
                "outline": inner,
                "centroid": centroid,
                "perimeter": _loop_length(inner),
                "width": float(max(size)),
                "height": float(min(size)),
                "min_width": float(min(size)),
            })
    return windows


# ---------------------------------------------------------------- helpers

def _loop_length(loop: np.ndarray) -> float:
    closed = np.vstack([loop, loop[:1]])
    return float(np.linalg.norm(np.diff(closed, axis=0), axis=1).sum())


def _plane_basis(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    helper = np.array([1.0, 0.0, 0.0])
    if abs(normal @ helper) > 0.9:
        helper = np.array([0.0, 1.0, 0.0])
    b1 = np.cross(normal, helper)
    b1 /= np.linalg.norm(b1)
    return b1, np.cross(normal, b1)


def _needs_small_tool(loops: list[np.ndarray]) -> bool:
    """True when any loop's narrow dimension calls for a tool < 2 mm."""
    for lp in loops:
        extents = lp.max(axis=0) - lp.min(axis=0)
        nonzero = sorted(float(e) for e in extents if e > 1e-6)
        if nonzero and nonzero[0] < 2.0:
            return True
    return False


# ---------------------------------------------------------------- setups

@dataclass
class SetupPlan:
    """What one machining side costs and what dropping it would take.

    Attributes:
        side: +1 (top of the extrusion) or -1 (bottom/outer face).
        pockets: POCKET features machined from this side.
        pads: PAD features on this side (these BLOCK elimination — a pad
            is material that must be milled around from this side).
        cost: Setup fee plus this side's pocket feature costs.
        eliminable: True when no pads require the side.
        tradeoffs: Human-readable consequences of dropping each pocket
            (material stays: screws sit proud, recesses become flush).
    """

    side: int
    pockets: list[Feature] = field(default_factory=list)
    pads: list[Feature] = field(default_factory=list)
    cost: float = 0.0
    eliminable: bool = True
    tradeoffs: list[str] = field(default_factory=list)


def setup_plans(
    intent: IntentResult, model: CostModel | None = None
) -> list[SetupPlan]:
    """Per-side machining plan: the input for setup-elimination decisions.

    Dropping every pocket on an eliminable side removes that whole setup
    (fixture, touch-off, flip) from the job — usually the single biggest
    saving available on a one-off part. The tradeoff is functional, not
    cosmetic: an un-recessed screw sits proud by the recess depth, so the
    user compensates with a longer screw or accepts the proud head.
    """
    model = model or CostModel()
    plans: list[SetupPlan] = []
    for side in (+1, -1):
        plan = SetupPlan(side=side)
        for f in intent.features:
            if f.feature_type == FeatureType.POCKET \
                    and int(f.params["side"]) == side \
                    and not f.params.get("fill"):
                plan.pockets.append(f)
                depth = float(f.params["depth"])
                area = f.patches[0].area if f.patches else 0.0
                plan.tradeoffs.append(
                    f"Pocket {depth:.2f} mm x {area:.0f} mm² stays "
                    f"unmachined: anything seated there sits "
                    f"{depth:.2f} mm proud (longer screw / spacer)"
                )
            elif f.feature_type == FeatureType.PAD \
                    and int(f.params["side"]) == side:
                plan.pads.append(f)
        plan.eliminable = not plan.pads
        if plan.pockets or plan.pads:
            plan.cost = model.setup_cost_per_side + sum(
                model.programming_per_feature for _ in plan.pockets
            )
        plans.append(plan)
    return plans


def flatten_hole_recesses(
    intent: IntentResult, hole_ids: set[int], radius: float = 7.0
) -> list[str]:
    """Fill the stepped recesses around the given holes (both machining
    sides), turning each into a plain through-hole in a flat face.

    A recess step near a mounting hole is a POCKET feature that may
    classify onto either side, so a per-side "fill" misses some — this
    is hole-centric: any pocket whose region falls within ``radius`` mm
    of a flagged hole's axis is marked to FILL (reuses the tested pocket-
    fill path; the hole itself is re-cut afterward). Solves stepped
    counterbores where "uncheck the outer side" left the inner-classified
    step behind.
    """
    holes = [f for f in intent.features
             if f.feature_type == FeatureType.HOLE
             and f.feature_id in hole_ids]
    if not holes:
        return []
    centers = np.array([np.asarray(h.params["center"]) for h in holes])
    axis = np.asarray(holes[0].params["axis"], dtype=np.float64)

    log: list[str] = []
    for f in intent.features:
        if f.feature_type != FeatureType.POCKET or f.params.get("fill"):
            continue
        ctr = f.params.get("centroid")
        if ctr is None:
            continue
        ctr = np.asarray(ctr, dtype=np.float64)
        rel = centers - ctr
        radial = np.linalg.norm(
            rel - np.outer(rel @ axis, axis), axis=1
        )
        if radial.min() <= radius:
            f.params["fill"] = True
            f.user_resolved = True
            log.append(
                f"Flattened {float(f.params['depth']):.2f} mm recess step "
                "near a mounting hole (fills to a flat face)"
            )
    return log


def drop_pockets(
    intent: IntentResult, feature_ids: set[int]
) -> list[str]:
    """Mark the selected pockets as FILLED (the face comes out flat).

    The feature is kept but flagged: the builder FUSES the pocket floor's
    region out to the outer face instead of cutting. This matters because
    some recesses are not cuts at all — e.g. a recessed corner tab is a
    pad floating over a notch in the base silhouette, and merely skipping
    a cut would leave the void. Filling is explicit material addition and
    covers both cases identically.

    Returns log lines describing what was filled.
    """
    log: list[str] = []
    for f in intent.features:
        if f.feature_type == FeatureType.POCKET \
                and f.feature_id in feature_ids:
            f.params["fill"] = True
            f.user_resolved = True
            depth = float(f.params["depth"])
            side = "outer" if int(f.params["side"]) < 0 else "inner"
            log.append(
                f"Filled {side}-side recess ({depth:.2f} mm) — face is "
                "flat there, compensate at assembly"
            )
    return log


# ------------------------------------------------------------- transforms

def drop_shallow_pockets(
    intent: IntentResult, max_depth: float = 0.5
) -> list[str]:
    """Remove cosmetic pockets shallower than ``max_depth`` (mm).

    Molded parts carry decorative insets and mold-relief steps that a
    machined replacement does not need. Returns log lines.
    """
    shallow = {
        f.feature_id for f in intent.features
        if f.feature_type == FeatureType.POCKET
        and float(f.params["depth"]) <= max_depth
        and not f.params.get("fill")
    }
    log = drop_pockets(intent, shallow)
    return [ln.replace("recess", "cosmetic recess") for ln in log]


def unify_pocket_depths(
    intent: IntentResult, rel_tol: float = 0.15
) -> list[str]:
    """Snap near-equal pocket depths (per side) to a common value.

    Fewer distinct Z-levels means fewer tool paths and less prone-to-
    error programming. Returns log lines.
    """
    log: list[str] = []
    for side in (+1, -1):
        pockets = [f for f in intent.features
                   if f.feature_type == FeatureType.POCKET
                   and int(f.params["side"]) == side
                   and not f.params.get("fill")]
        pockets.sort(key=lambda f: float(f.params["depth"]))
        cluster: list[Feature] = []
        clusters: list[list[Feature]] = []
        for f in pockets:
            if cluster and (
                float(f.params["depth"])
                - float(cluster[0].params["depth"])
            ) > rel_tol * float(cluster[0].params["depth"]):
                clusters.append(cluster)
                cluster = []
            cluster.append(f)
        if cluster:
            clusters.append(cluster)
        for group in clusters:
            if len(group) < 2:
                continue
            depths = [float(f.params["depth"]) for f in group]
            unified = max(depths)  # deepest wins: never leaves material
            if max(depths) - min(depths) < 1e-6:
                continue
            for f in group:
                f.params["depth"] = unified
            log.append(
                f"Unified {len(group)} pocket depths "
                f"{min(depths):.2f}-{max(depths):.2f} -> {unified:.2f} mm"
            )
    return log
