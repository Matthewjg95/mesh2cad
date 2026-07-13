"""Reproducible bug-report bundles — the community data pipeline.

Every part a user runs, together with the tool's own stage-by-stage
analysis and the user's note on what went wrong, is a labeled example.
:func:`build_repro_bundle` packages one such example into a single zip a
user can drag onto a GitHub issue: the mesh, a machine-readable
``report.json``, and a human-readable ``report.md``.

Collected at scale these bundles become a regression corpus (each is a
fixture with expected outputs) and, later, training data for the AI
module described in the project brief. Nothing is uploaded here — the
bundle is written locally and the user chooses to attach it.
"""

from __future__ import annotations

import json
import platform
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from mra import __version__
from mra.intent import IntentResult
from mra.meshproc import compute_stats
from mra.reconstruction import BuildResult
from mra.recognition import SegmentationResult

SCHEMA_VERSION = 1


@dataclass
class ReproReport:
    """Structured summary of one reconstruction run + the user's verdict.

    The dict form (``to_dict``) is the durable, diffable record; it is what
    turns a pile of bug reports into a dataset.
    """

    user_notes: str = ""
    part_class: str = ""            # e.g. "bracket", "enclosure", "unknown"
    stage_where_wrong: str = ""     # repair|recognize|reconstruct|export|none
    expected: str = ""
    actual: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(
        self,
        mesh: trimesh.Trimesh,
        segmentation: SegmentationResult | None,
        intent: IntentResult | None,
        build: BuildResult | None,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": __version__,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "user": {
                "part_class": self.part_class,
                "stage_where_wrong": self.stage_where_wrong,
                "expected": self.expected,
                "actual": self.actual,
                "notes": self.user_notes,
                **self.extra,
            },
            "mesh": _mesh_summary(mesh),
        }
        if segmentation is not None:
            report["recognition"] = _recognition_summary(segmentation)
        if intent is not None:
            report["intent"] = _intent_summary(intent)
        if build is not None:
            report["build"] = _build_summary(build, mesh)
        return report


def build_repro_bundle(
    out_path: str | Path,
    mesh: trimesh.Trimesh,
    report: ReproReport,
    segmentation: SegmentationResult | None = None,
    intent: IntentResult | None = None,
    build: BuildResult | None = None,
) -> Path:
    """Write a self-contained repro zip and return its path.

    Contents:
      * ``mesh.stl``   — the exact geometry the tool saw
      * ``report.json``— machine-readable stage analysis + user verdict
      * ``report.md``  — the same, human-readable, ready to paste

    Args:
        out_path: Destination ``.zip`` path (suffix added if missing).
        mesh: The repaired/isolated mesh that was reconstructed.
        report: The user's verdict and part metadata.
        segmentation, intent, build: Whatever stages have run.
    """
    out_path = Path(out_path)
    if out_path.suffix.lower() != ".zip":
        out_path = out_path.with_suffix(".zip")

    data = report.to_dict(mesh, segmentation, intent, build)
    stl_bytes = trimesh.exchange.stl.export_stl(mesh)
    md = _render_markdown(data)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mesh.stl", stl_bytes)
        zf.writestr("report.json", json.dumps(data, indent=2, default=_json))
        zf.writestr("report.md", md)
    return out_path


# ----------------------------------------------------------- stage summaries

def _mesh_summary(mesh: trimesh.Trimesh) -> dict[str, Any]:
    s = compute_stats(mesh)
    return {
        "vertices": s.vertex_count,
        "triangles": s.face_count,
        "extents_mm": [round(float(e), 3) for e in s.extents],
        "surface_area_mm2": round(s.surface_area, 2),
        "volume_mm3": round(s.volume, 2),
        "watertight": s.is_watertight,
        "bodies": s.body_count,
    }


def _recognition_summary(seg: SegmentationResult) -> dict[str, Any]:
    by_type = Counter(p.surface_type.value for p in seg.patches)
    confs = [p.confidence.value for p in seg.patches]
    low = [
        {"id": p.patch_id, "type": p.surface_type.value,
         "conf": round(p.confidence.value, 2),
         "area_mm2": round(p.area, 1)}
        for p in seg.patches if p.confidence.value < 0.55
    ]
    return {
        "patch_count": len(seg.patches),
        "by_type": dict(by_type),
        "coverage": round(seg.coverage(), 4),
        "mean_confidence": round(float(np.mean(confs)), 3) if confs else 0.0,
        "low_confidence_patches": low[:25],
    }


def _intent_summary(intent: IntentResult) -> dict[str, Any]:
    from mra.core import FeatureType

    by_type = Counter(f.feature_type.value for f in intent.features)
    holes = [f for f in intent.features
             if f.feature_type == FeatureType.HOLE]
    return {
        "feature_count": len(intent.features),
        "by_type": dict(by_type),
        "wall_thickness_mm": (round(intent.wall_thickness, 3)
                              if intent.wall_thickness else None),
        "symmetry_planes": len(intent.symmetry_planes),
        "open_questions": len(intent.questions),
        "hole_diameters_mm": sorted(
            round(float(h.params.get("diameter", 0)), 2) for h in holes
        ),
    }


def _build_summary(build: BuildResult, mesh: trimesh.Trimesh) -> dict[str, Any]:
    out: dict[str, Any] = {
        "succeeded": build.shape is not None,
        "applied_feature_count": len(build.applied_features),
        "skipped_feature_count": len(build.skipped_features),
        "log": list(build.log),
    }
    if build.shape is not None:
        from mra.validation import validate_shape

        ref = float(mesh.volume) if mesh.is_watertight else None
        v = validate_shape(build.shape, reference_volume=ref)
        vol_err = None
        if ref:
            vol_err = round((v.volume - ref) / ref * 100, 2)
        out["validation"] = {
            "is_valid": v.is_valid,
            "solids": v.solid_count,
            "open_edges": v.open_edge_count,
            "volume_mm3": round(v.volume, 2),
            "volume_error_pct": vol_err,
            "ready_for_export": v.ready_for_export,
            "problems": v.problems,
        }
    return out


# --------------------------------------------------------------- rendering

def _render_markdown(data: dict[str, Any]) -> str:
    u = data["user"]
    m = data["mesh"]
    lines = [
        f"# Repro bundle — mesh2cad v{data['tool_version']}",
        "",
        f"*Created {data['created_utc']} on {data['platform']}*",
        "",
        "## What went wrong",
        f"- **Part class:** {u['part_class'] or '(unspecified)'}",
        f"- **Stage:** {u['stage_where_wrong'] or '(unspecified)'}",
        f"- **Expected:** {u['expected'] or '(unspecified)'}",
        f"- **Actual:** {u['actual'] or '(unspecified)'}",
        "",
        u["notes"] or "_(no additional notes)_",
        "",
        "## Mesh",
        f"- {m['triangles']:,} triangles, {m['bodies']} body(ies), "
        f"{'watertight' if m['watertight'] else 'NOT watertight'}",
        f"- Size {m['extents_mm']} mm, volume {m['volume_mm3']:,} mm³",
    ]
    if "recognition" in data:
        r = data["recognition"]
        lines += [
            "",
            "## Recognition",
            f"- {r['patch_count']} patches, {r['coverage'] * 100:.0f}% "
            f"coverage, mean conf {r['mean_confidence']}",
            f"- By type: {r['by_type']}",
        ]
    if "intent" in data:
        it = data["intent"]
        lines += [
            "",
            "## Intent",
            f"- {it['feature_count']} features: {it['by_type']}",
            f"- Wall {it['wall_thickness_mm']} mm, "
            f"{it['symmetry_planes']} symmetry plane(s), "
            f"{it['open_questions']} question(s)",
        ]
    if "build" in data:
        b = data["build"]
        lines += ["", "## Build", f"- succeeded: {b['succeeded']}"]
        if "validation" in b:
            v = b["validation"]
            lines.append(
                f"- {v['solids']} solid(s), valid={v['is_valid']}, "
                f"volume error {v['volume_error_pct']}%, "
                f"ready={v['ready_for_export']}"
            )
    lines += ["", "_Attach the whole zip (mesh.stl + report.json) to the "
              "GitHub issue._"]
    return "\n".join(lines)


def _json(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return str(obj)
