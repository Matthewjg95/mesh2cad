# Research-to-Experiment Roadmap: Hybrid Mesh-to-CAD Recovery

Status: design note; no runtime behavior changed  
Added: 2026-08-22

## Why this belongs in Mesh2CAD

Mesh2CAD's durable advantage is the workflow around reconstruction: inspect imperfect evidence, expose engineering assumptions, recover explicit design intent, build analytic B-Rep geometry, validate it, and let the user correct uncertain choices. Better open-source recognizers and generative models can become interchangeable inputs to that workflow rather than replacements for it.

The architecture should therefore separate:

1. **Evidence** — mesh, point samples, photos, patches, normals and dimensional hints.
2. **Intent hypothesis** — named features, references, parameters, constraints, confidence and provenance.
3. **Executable reconstruction** — deterministic OCC/CadQuery-style operations that produce a valid solid.
4. **Geometric evaluation** — comparisons between the source evidence and candidate reconstruction.
5. **Revision policy** — bounded parameter changes or feature edits that must improve the score and remain valid.
6. **Human decision** — accept, edit, lock or reject uncertain intent.

This keeps Mesh2CAD useful as stronger open-source models appear: new systems can propose evidence labels or intent hypotheses while Mesh2CAD owns validation, iteration, explainability and export.

## How CADFit works

Reference: [CADFit: Precise Mesh-to-CAD Program Generation with Hybrid Optimization](https://arxiv.org/abs/2605.01171)

CADFit treats reverse engineering as a search over **structured CAD programs**, not as direct mesh conversion.

At a high level:

1. A source mesh is supplied as the target geometry.
2. A candidate CAD construction sequence is generated incrementally.
3. The candidate program is executed to produce geometry.
4. The result is converted to a comparable representation.
5. Geometric agreement is measured, principally using volumetric Intersection over Union (IoU), with Chamfer distance used as another fidelity measure.
6. Operation parameters are optimized using that geometric feedback.
7. Proposed operations are retained only when they execute successfully and improve the reconstruction.
8. The process continues across a richer operation vocabulary than sketch/extrude alone, including revolutions, fillets and chamfers.

The useful idea is not any single model. It is the division of labor:

- discrete search chooses **which operations exist and in what order**;
- continuous optimization tunes **dimensions and placements**;
- a CAD kernel enforces **executability and solid validity**;
- geometric scoring supplies **objective feedback**.

This avoids asking one model to infer the entire construction history perfectly in one pass.

## Translation into the current Mesh2CAD pipeline

Current modules already provide most of the boundary:

| CADFit concept | Mesh2CAD location | Proposed responsibility |
|---|---|---|
| Target geometry | `mra.meshproc` | Preserve source mesh and normalized samples |
| Initial program hypothesis | `mra.recognition` + `mra.intent` | Produce named features and initial parameters |
| Program execution | `mra.reconstruction` | Build analytic OCC B-Rep from an intent model |
| Validity gate | `mra.validation` | Reject invalid, empty, disconnected or non-manifold candidates |
| Geometric feedback | new `mra.optimization` package | Compute reproducible candidate scores |
| Iteration history | `mra.diagnostics` extension | Record trials, scores, accepted changes and stop reason |
| User control | `mra.gui` | Lock features/parameters, run refinement, compare before/after |

Proposed flow:

```text
Import -> Inspect/Repair -> Recognize -> Intent Model -> Initial B-Rep
                                                   |
                                                   v
                                      Optional Refinement Loop
                                  propose -> build -> validate -> score
                                      ^                         |
                                      +------ accept/reject -----+
                                                   |
                                                   v
                                      Review -> DFM -> Export
```

## Minimum viable optimization layer

Do not begin with learned operation generation. First prove that geometric feedback improves an already-correct feature sequence.

### Inputs

- immutable source mesh;
- current `IntentModel`;
- list of explicitly optimizable parameters;
- locked parameters/features;
- tolerances and evaluation budget.

### Candidate lifecycle

1. Clone the intent model.
2. Change one bounded parameter or a small parameter group.
3. Rebuild through the existing deterministic reconstruction path.
4. Run the existing structural validation.
5. Tessellate/sample the valid B-Rep in a deterministic way.
6. Compute metrics.
7. Accept only a valid candidate that improves the selected objective by more than a minimum threshold.
8. Append the complete trial to diagnostics.
9. Stop at convergence, evaluation budget, cancellation or repeated invalid candidates.

### Initial metrics

Use multiple metrics because each catches a different failure:

- **symmetric surface Chamfer distance:** local surface mismatch;
- **volumetric IoU:** missing or extra material;
- **volume error:** inexpensive coarse guardrail;
- **validity and solid-count gates:** manufacturability prerequisites;
- **complexity penalty:** discourages needless features or fragmented programs.

A first objective can be:

```text
loss = normalized_chamfer
     + w_volume * relative_volume_error
     + w_complexity * added_feature_count
```

Volumetric IoU can be added after a stable voxelization method is selected. Invalid candidates receive no numeric reward and are rejected.

### Safety and trust rules

- Refinement is opt-in.
- The source mesh and initial intent model are immutable.
- User-locked dimensions and features are never changed.
- Bounds come from `mra.core.Tolerances` or explicit experiment configuration.
- No candidate silently changes feature type in the parameter-only experiment.
- Every accepted change records before/after values, score delta and evidence.
- The initial model remains recoverable and exportable.
- Optimization failure must leave the existing workflow unchanged.

## Design-intent contract

Zero-to-CAD reinforces that readable parametric programs are more useful than opaque coordinate chains. Extend the intent representation toward this stable contract:

| Field | Purpose |
|---|---|
| `feature_id` | Stable identity across revisions |
| `feature_type` | Plane, extrusion, hole, pocket, boss, revolve, fillet, chamfer, etc. |
| `semantic_name` | Human-readable role when known |
| `references` | Parent plane, axis, face or earlier feature |
| `parameters` | Named dimensions and placements with units |
| `constraints` | Concentric, symmetric, equal, coincident, pattern relationship |
| `confidence` | Strength of the inference |
| `provenance` | Mesh patch, photo region, user input or imported model |
| `locked` | Whether automated refinement may modify it |
| `alternatives` | Plausible interpretations retained for user review |

Export backends consume this contract. Future recognizers, photo pipelines or learned systems produce it. This prevents one research model from becoming the architecture.

## Future experiments

Each experiment is tracked as a separate GitHub issue with explicit acceptance criteria.

### E1 — CADFit-inspired bounded parameter refinement

Prove that a fixed Mesh2CAD feature sequence can be made more accurate by tuning dimensions against the source mesh. This is the highest-value and lowest-risk experiment.

### E2 — CADReasoner-inspired discrepancy report and iterative editing

Generate actionable spatial evidence—missing material, excess material and largest mismatch regions—then test one-operation-at-a-time revisions. Include simulated scan defects to prevent tuning only for clean synthetic meshes.

### E3 — Zero-to-CAD-inspired design-intent interchange contract

Make intent readable, backend-agnostic, versioned and round-trippable before adding more model-driven reconstruction.

### E4 — CADDreamer-inspired primitive hypothesis map for ShadowScan

Treat photo reconstruction first as evidence labeling. Produce a correctable map of primitive regions and unknown/occluded areas before generating geometry.

## Recommended order

1. E1 parameter refinement.
2. E3 intent contract.
3. E2 discrepancy-driven editing.
4. E4 photo primitive map.

E1 establishes the feedback loop. E3 makes it portable. E2 expands from continuous parameter tuning to discrete feature edits. E4 brings imperfect photographic evidence into the same workflow without coupling Mesh2CAD to a particular image model.
