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


## 2026-08-28 research increment

This increment adds the strongest practical findings from the current geometry-reconstruction brief and maps them onto the existing experiments.

### CADIR — stable construction graphs and geometric signatures

Reference: [CADIR](https://arxiv.org/abs/2608.00891) and its Apache-2.0 [SimpleCADAPI artifact](https://github.com/NiJingzhe/SimpleCADAPI).

CADIR reinforces two additions to the versioned intent contract:

- explicit operation dependencies rather than a flat feature list;
- topology references backed by geometric signatures so a face or edge can be reidentified after a parameter change or backend replay.

New experiment: rebuild a synthetic bracket at two parameter sets and measure whether every referenced face/edge can be reconnected without relying on transient OCC entity order.

### CADFit — spatial residual decomposition

CADFit's most useful next step beyond bounded parameter tuning is its separation of positive and negative residual material.

Extend E2 to:

1. voxelize or otherwise classify target-versus-candidate occupancy;
2. compute missing material (`target - candidate`) and excess material (`candidate - target`);
3. split each class into connected components;
4. record component volume, bounds, centroid and nearest intent feature;
5. preserve this as evidence before any feature edit is proposed.

License boundary: CADFit's published implementation is CC BY-NC 4.0 and the authors report a provisional patent. Mesh2CAD may independently test the general engineering idea, but implementation must not copy the reference code.

### CADReasoner — multi-view discrepancy evidence

A single score is insufficient for deciding what to edit. E2 should additionally emit six orthographic red/blue overlays and feature-linked mismatch measurements. The Apache-2.0 CADReasoner repository is a useful reference for representation and testing, but its trained VLM is not required for the first implementation.

### Ortho2CAD — standardized capture beats unconstrained imagery

Reference: [Ortho2CAD](https://arxiv.org/abs/2607.08891).

Before pursuing a large image-to-CAD model, accept a small engineering-evidence bundle: rectified front/top/right views, known scale, locked dimensions, capture pose/range and explicit occlusion. The cross-repository interface is defined in [PlatypusOne Capture Contract](PLATYPUSONE_CAPTURE_CONTRACT.md).

New experiment: import three controlled views of one bracket, lock its bounding dimensions and compare the result with a single-photo input.

Large-compute boundary: reproducing Ortho2CAD's full learned system requires fine-tuning an 8B VLM on roughly 150,000 samples. That is not a current Mesh2CAD dependency.

### CADBench — robustness and multi-metric evaluation

Reference: [CADBench](https://arxiv.org/abs/2605.10873).

Add a 12-part microbenchmark:

- four clean mechanical meshes;
- four deterministic noisy/decimated variants;
- four cropped or missing-patch variants.

Report volumetric IoU, surface alignment, Chamfer distance, valid-solid rate and operation count separately. Do not collapse them into a single marketing score.

## Revised execution order

1. Finish E1's real-mesh/OCC gate.
2. Add positive/negative residual components and feature association to E2.
3. Compare E3's contract with CADIR dependency graphs and geometric signatures.
4. Establish the 12-part robustness microbenchmark.
5. Test the PlatypusOne three-view evidence import.
6. Add GUI discrepancy overlays only after the headless report is useful.

The highest-value immediate test remains spatial residual decomposition because it converts the optimizer's scalar improvement into an actionable engineering correction.
