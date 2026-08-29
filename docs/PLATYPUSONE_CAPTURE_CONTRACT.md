# PlatypusOne Capture Contract for Mesh2CAD

Status: proposed cross-repository interface  
Date: 2026-08-28

## Purpose

PlatypusOne and Mesh2CAD solve different halves of the same engineering workflow:

```text
physical object
  -> PlatypusOne captures calibrated evidence
  -> evidence bundle preserves raw observations and provenance
  -> Mesh2CAD recovers editable design intent
  -> CAD kernel rebuilds and validates the model
  -> discrepancy report tells the user what remains uncertain
```

PlatypusOne is not required to run full mesh-to-CAD reconstruction on the handheld. Its durable role is to collect better evidence than an arbitrary phone photograph or damaged STL: controlled views, known scale, pose/range metadata, illumination state, and explicit user-confirmed dimensions. Mesh2CAD owns the heavier reconstruction, optimization, discrepancy analysis, and CAD export.

This boundary prevents the PlatypusOne BOM from acquiring an unnecessary GPU while ensuring that camera, ToF, IMU, illumination, storage, controls, and mechanical mounting are selected for reconstruction quality rather than feature-count marketing.

## Versioned evidence bundle

A PlatypusOne capture intended for Mesh2CAD should export a directory or archive containing:

```text
capture.json
raw/
  view-000.*
  view-001.*
  view-002.*
derived/
  silhouettes/
  edge-maps/
  optional-point-cloud-or-mesh.*
calibration/
  camera-intrinsics.json
  sensor-extrinsics.json
  target-observations.*
notes/
  user-confirmed-dimensions.json
```

Minimum manifest fields:

| Field | Meaning |
|---|---|
| `schema_version` | Contract version |
| `capture_id`, `device_id`, `software_revision` | Traceability |
| `timestamp`, `units` | Evidence context |
| `views[]` | File, pose label, image size, exposure/focus state and validity |
| `camera_intrinsics` | Calibration reference and distortion model |
| `sensor_extrinsics` | Rigid transforms among camera, ToF and IMU frames |
| `range_samples` | ToF data with timestamps and validity |
| `orientation_samples` | IMU orientation with timestamps and validity |
| `illumination_state` | Which controlled lights were active |
| `scale_evidence` | Fiducial, known dimension or calibrated range source |
| `user_constraints[]` | Locked dimensions or relationships confirmed during capture |
| `derived_artifacts[]` | Transform, settings, confidence and source links |
| `limitations[]` | Occlusion, blur, saturation, calibration expiry or missing view |

Raw evidence remains immutable. Derived silhouettes, meshes, primitive hypotheses and CAD intent reference their sources rather than replacing them.

## Capture profile: mechanical interface

The first bounded profile is intended for brackets, plates, covers, housings and mounting interfaces:

1. Place a scale/fiducial target in the same working plane as the part.
2. Guide the user through front, top and right views or three equivalent orthogonal views.
3. Lock focus and exposure for the view set when supported.
4. Record ToF range and IMU orientation for each deliberate trigger.
5. Capture controlled-light variants when a silhouette or edge is ambiguous.
6. Ask for at least one user-confirmed critical dimension.
7. Preview the evidence bundle before acceptance.
8. Export the bundle without requiring a cloud account.

The profile does not promise automatic recovery of hidden geometry. Missing or conflicting evidence becomes an explicit Mesh2CAD question.

## How the research maps into the contract

| Research direction | Contract consequence | Mesh2CAD experiment |
|---|---|---|
| CADIR | Preserve stable feature IDs, dependencies and geometric signatures across rebuilds | Reconnect faces/edges after bounded parameter changes |
| CADFit | Preserve enough scale and surface evidence for positive/negative residual analysis | Split missing/excess material into connected components |
| CADReasoner | Store multi-view and spatial discrepancy evidence, not only one scalar score | Red/blue overlays and feature-linked discrepancy reports |
| Ortho2CAD | Prefer standardized multi-view evidence with known dimensions over unconstrained photos | Rectified three-view import with locked dimensions |
| CADBench | Evaluate clean, noisy and incomplete captures with multiple metrics | Small deterministic robustness benchmark |

## BOM-derived capability requirements

The PlatypusOne BOM should satisfy capabilities rather than assume a specific vendor part:

- camera: Linux-accessible still capture, repeatable 10–30 cm focus, calibration support, and exposure/focus locking or reproducible reporting;
- ToF: timestamped valid range; multi-zone depth is preferred when schedule and driver maturity permit;
- IMU: orientation available at each capture without making host-side sensor fusion the contest critical path;
- illumination: at least two independently controllable sources with fixed, documented geometry relative to the camera;
- mechanical datum: camera, ToF and illumination mounted rigidly enough that calibration remains meaningful after ordinary handling;
- scale accessory: inexpensive printed fiducial/checkerboard and at least one known physical dimension;
- deliberate trigger and stable support: one-handed trigger plus tripod/kickstand provision;
- storage/export: raw and derived artifacts remain user-owned and can move to a workstation intact;
- compute: no dedicated reconstruction accelerator is required for Rev A.

## Acceptance gate

The handoff is useful when one simple physical bracket can be captured on PlatypusOne, imported into Mesh2CAD, reconstructed with at least one locked dimension, and returned with:

- source-linked intent;
- a valid editable CAD result;
- missing/excess material regions;
- explicit uncertainty for unsupported geometry; and
- a repeatable evidence package that produces the same import result twice.
