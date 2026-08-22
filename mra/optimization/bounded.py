"""Deterministic bounded coordinate search for CAD parameters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

import numpy as np
import trimesh

from mra.optimization.metrics import GeometricScore, score_meshes

CandidateBuilder = Callable[[Mapping[str, float]], trimesh.Trimesh]
CandidateValidator = Callable[[trimesh.Trimesh], bool]


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    initial: float
    minimum: float
    maximum: float
    step: float
    locked: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("parameter name cannot be empty")
        if self.minimum > self.maximum:
            raise ValueError(f"invalid bounds for {self.name}")
        if not self.minimum <= self.initial <= self.maximum:
            raise ValueError(f"initial value for {self.name} is outside bounds")
        if self.step <= 0:
            raise ValueError(f"step for {self.name} must be positive")


@dataclass(frozen=True)
class OptimizationConfig:
    max_passes: int = 12
    max_evaluations: int = 100
    sample_count: int = 2048
    seed: int = 0
    volume_weight: float = 0.25
    minimum_improvement: float = 1e-6
    step_shrink: float = 0.5
    minimum_step: float = 1e-4

    def __post_init__(self) -> None:
        if self.max_passes <= 0 or self.max_evaluations <= 0:
            raise ValueError("evaluation limits must be positive")
        if not 0 < self.step_shrink < 1:
            raise ValueError("step_shrink must be between zero and one")


@dataclass(frozen=True)
class Trial:
    index: int
    parameters: dict[str, float]
    score: GeometricScore | None
    valid: bool
    accepted: bool
    reason: str


@dataclass
class OptimizationResult:
    initial_parameters: dict[str, float]
    best_parameters: dict[str, float]
    initial_score: GeometricScore
    best_score: GeometricScore
    trials: list[Trial] = field(default_factory=list)
    stop_reason: str = ""


def _default_validator(mesh: trimesh.Trimesh) -> bool:
    return (
        isinstance(mesh, trimesh.Trimesh)
        and not mesh.is_empty
        and len(mesh.faces) > 0
        and np.isfinite(mesh.vertices).all()
    )


def refine_parameters(
    source_mesh: trimesh.Trimesh,
    parameters: list[ParameterSpec],
    build_candidate: CandidateBuilder,
    *,
    validate_candidate: CandidateValidator | None = None,
    config: OptimizationConfig | None = None,
) -> OptimizationResult:
    """Refine named dimensions without changing feature structure.

    The search is deterministic coordinate descent.  It never mutates caller
    data, never changes locked parameters, and records failed candidates as
    trials rather than allowing them to interrupt the run.
    """
    if not parameters:
        raise ValueError("at least one parameter is required")
    names = [item.name for item in parameters]
    if len(set(names)) != len(names):
        raise ValueError("parameter names must be unique")

    cfg = config or OptimizationConfig()
    validator = validate_candidate or _default_validator
    specs = {item.name: item for item in parameters}
    initial = {item.name: float(item.initial) for item in parameters}
    current = dict(initial)
    steps = {item.name: float(item.step) for item in parameters}
    trials: list[Trial] = []
    evaluations = 0

    def evaluate(values: dict[str, float]) -> tuple[GeometricScore, int] | None:
        nonlocal evaluations
        if evaluations >= cfg.max_evaluations:
            return None
        index = evaluations
        evaluations += 1
        try:
            mesh = build_candidate(dict(values))
            if not validator(mesh):
                trials.append(Trial(index, dict(values), None, False, False,
                                    "candidate validation failed"))
                return None
            score = score_meshes(
                source_mesh,
                mesh,
                sample_count=cfg.sample_count,
                seed=cfg.seed,
                volume_weight=cfg.volume_weight,
            )
            trials.append(Trial(index, dict(values), score, True, False,
                                "scored"))
            return score, len(trials) - 1
        except Exception as exc:  # candidate failures are experiment data
            trials.append(Trial(index, dict(values), None, False, False,
                                f"candidate error: {type(exc).__name__}: {exc}"))
            return None

    initial_evaluation = evaluate(current)
    if initial_evaluation is None:
        raise ValueError("initial candidate is invalid or could not be scored")
    initial_score, initial_trial_index = initial_evaluation
    best_score = initial_score
    initial_trial = trials[initial_trial_index]
    trials[initial_trial_index] = Trial(
        initial_trial.index,
        initial_trial.parameters,
        initial_trial.score,
        initial_trial.valid,
        True,
        "initial candidate",
    )

    stop_reason = "maximum passes reached"
    for _pass in range(cfg.max_passes):
        pass_improved = False
        for name in names:
            spec = specs[name]
            if spec.locked or steps[name] < cfg.minimum_step:
                continue
            candidates: list[tuple[GeometricScore, dict[str, float], int]] = []
            for direction in (-1.0, 1.0):
                proposal = dict(current)
                proposal[name] = min(
                    spec.maximum,
                    max(spec.minimum, current[name] + direction * steps[name]),
                )
                if proposal[name] == current[name]:
                    continue
                evaluation = evaluate(proposal)
                if evaluation is not None:
                    score, trial_index = evaluation
                    candidates.append((score, proposal, trial_index))
                if evaluations >= cfg.max_evaluations:
                    break
            if candidates:
                candidate_score, proposal, trial_index = min(
                    candidates, key=lambda item: item[0].loss
                )
                improvement = best_score.loss - candidate_score.loss
                if improvement > cfg.minimum_improvement:
                    current = proposal
                    best_score = candidate_score
                    old = trials[trial_index]
                    trials[trial_index] = Trial(
                        old.index, old.parameters, old.score, old.valid, True,
                        f"accepted; loss improved by {improvement:.6g}",
                    )
                    pass_improved = True
            if evaluations >= cfg.max_evaluations:
                stop_reason = "evaluation budget reached"
                break
        if evaluations >= cfg.max_evaluations:
            break
        if not pass_improved:
            for name in names:
                if not specs[name].locked:
                    steps[name] *= cfg.step_shrink
            if all(
                specs[name].locked or steps[name] < cfg.minimum_step
                for name in names
            ):
                stop_reason = "minimum step reached"
                break

    return OptimizationResult(
        initial_parameters=initial,
        best_parameters=dict(current),
        initial_score=initial_score,
        best_score=best_score,
        trials=trials,
        stop_reason=stop_reason,
    )
