"""Durable JSON reports for bounded refinement runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mra.optimization.bounded import OptimizationResult

REPORT_SCHEMA_VERSION = 1


def _score(score) -> dict[str, float] | None:
    if score is None:
        return None
    return {
        "normalized_chamfer": score.normalized_chamfer,
        "relative_volume_error": score.relative_volume_error,
        "loss": score.loss,
    }


def result_to_dict(result: OptimizationResult) -> dict[str, Any]:
    """Convert a result into a stable, diffable experiment record."""
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "initial_parameters": result.initial_parameters,
        "best_parameters": result.best_parameters,
        "initial_score": _score(result.initial_score),
        "best_score": _score(result.best_score),
        "stop_reason": result.stop_reason,
        "trials": [
            {
                "index": trial.index,
                "parameters": trial.parameters,
                "score": _score(trial.score),
                "valid": trial.valid,
                "accepted": trial.accepted,
                "reason": trial.reason,
            }
            for trial in result.trials
        ],
    }


def write_result_report(
    result: OptimizationResult, path: str | Path
) -> Path:
    """Write a JSON refinement report and return its final path."""
    path = Path(path)
    if path.suffix.lower() != ".json":
        path = path.with_suffix(".json")
    path.write_text(json.dumps(result_to_dict(result), indent=2) + "\n")
    return path
