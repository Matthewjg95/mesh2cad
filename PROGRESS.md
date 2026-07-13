# Build Progress Log

Purpose: session-crash-proof record of what is done, what is in flight, and
the exact next step. Update after every meaningful step. If a session dies,
resume from "NEXT STEP" below.

## Current state (2026-07-02)

### Done
- [x] Project scaffolding: `requirements.txt`, `README.md`, `mra/` package
- [x] `mra/core/` — Tolerances, Confidence, SurfacePatch, Feature, enums
- [x] `mra/meshproc/loader.py` — STL import (`process=False`, scene flatten)
- [x] `mra/meshproc/stats.py` — MeshStats for sidebar
- [x] `mra/meshproc/repair.py` — diagnose + repair + RepairReport
- [x] `tests/test_meshproc.py` — 15/15 passing
- [x] `mra/gui/viewport.py` — pyqtgraph GL viewport, 3 layers
      (original / patches / preview), offscreen-tested
- [x] `mra/gui/sidebar.py` — stats, repair report, features list,
      tolerance spin boxes with tolerances_changed signal
- [x] `mra/gui/app.py` — MainWindow; Import STL + Repair Mesh functional,
      later-stage buttons disabled with tooltips. Offscreen smoke test OK.

### Environment
- Python 3.12.10 at `python` (system)
- Venv at `.venv\` — working, pip 26.1.2.
- Project has its OWN git repo now (`git init` done, identity configured).
  NOTE: a stray git repo exists at C:\Users\matth\.git (home dir) — user
  should decide whether to delete it; it slows git and confuses tools.

### Install plan (small foreground steps, one at a time)
1. [x] `numpy scipy networkx` (numpy 2.5.0, scipy 1.18.0, networkx 3.6.1)
2. [x] `trimesh rtree shapely` (trimesh 4.12.2)
3. [x] `pytest` 9.1.1 — Stage-1 suite: 15/15 PASSING
4. [x] `PySide6` 6.11.1 — import OK
5. [x] `cadquery-ocp` — OCP BRepPrimAPI_MakeBox verified working
6. [ ] `open3d` — DEFERRED (optional; only if Stage-2 needs its RANSAC)
7. [x] `pyqtgraph` 0.14.0 + `PyOpenGL` — for the 3D viewport

- [x] `mra/recognition/fitting.py` — LSQ fits: plane (SVD), cylinder
      (normal-covariance axis seed + Kasa circle + LM refine), sphere
      (algebraic), cone (tangent-plane apex seed + [N|-1] SVD axis seed +
      bounded TRF refine). All exact on synthetic data.
- [x] `mra/recognition/segmentation.py` — smooth-component decomposition
      first, whole-component classification w/ simplicity tie-break, planar
      region-growing fallback inside unexplained components.
- [x] `tests/test_recognition.py` — 13 tests. FULL SUITE: 28/28 PASSING.

### Key lesson learned (do not regress)
Planar-region-growing FIRST shreds curved surfaces (locally-flat patches
get stolen from spheres/cylinder walls). Smooth-components-first is the
correct order; planes are just one candidate fit per component.

- [x] GUI "Recognize Features" wired (QThread worker, patch colours).
- [x] `mra/core/questions.py` — Question dataclass for Stage 4.
- [x] `mra/intent/recover.py` — Stage 3: extrusion detection, hole/boss/
      fillet classification (angular coverage + concavity), hole-diameter
      equalisation w/ user questions, linear+circular patterns, wall
      thickness (weighted plane-pair mode), mirror symmetry (KDTree),
      freeform questions. `tests/test_intent.py` 12 tests.
      FULL SUITE: 40/40 PASSING.
- [x] `manifold3d` installed (trimesh boolean engine for test parts).

### More lessons learned (do not regress)
- Plane normals from SVD fits need orienting against mesh face normals,
  else opposite caps look parallel-same-direction and extrusion fails.
- Position residuals cannot distinguish short tube vs sphere (both rms 0
  on 2-rim tessellations) — the NORMAL-deviation gate is load-bearing.
- Feature depth/extent must come from patch VERTICES not triangle centres.

- [x] `mra/reconstruction/` — profiles.py (boundary-loop extraction,
      collinear simplification, strict circle detection), builder.py
      (prism base + analytic hole cuts + boss fuses + non-circular cutout
      prisms), tessellate.py (OCP -> trimesh preview).
- [x] `mra/validation/checks.py` — BRepCheck, open edges, slivers, tiny
      edges, volume vs scan reference.
- [x] `mra/export/step.py` — AP214/AP242 writer + reader (round-trip).
- [x] GUI: ALL SIX BUTTONS FUNCTIONAL (Import / Repair / Recognize /
      Reconstruct / Validate / Export). Stage-4 question dialogs with
      answer application (incl. measured-diameter restore).
- [x] End-to-end offscreen test: plate + 2 holes -> STEP, volume exact.
- [x] FULL SUITE: 52/52 PASSING.

### MILESTONE: v0.1 pipeline complete (2026-07-02)
The app reconstructs the "extruded part" class (plates, brackets, simple
housings bases): STL -> repair -> analytic patches -> intent (extrusion,
holes, bosses, patterns, symmetry, wall thickness) -> B-Rep -> validated
AP242 STEP with true CYLINDRICAL_SURFACEs.

### Performance work (2026-07-03, post-v0.1)
Real-mesh testing: l_bracket.stl reconstructs perfectly in 0.2 s.
bit-organizer.stl (145k tris) went from >10 min hang to 42 s total:
- _MeshData bundle: hoist all trimesh arrays once (property access
  re-hashes the vertex buffer inside loops — was ~1/3 of runtime).
- scipy csgraph connected_components instead of Python BFS.
- `tried` mask in refinement (failed grow seeds re-walked -> O(n^2)).
- Geometric (doubling) plane-refit schedule in region growing.
- _MAX_FIT_POINTS=3000 subsampling inside LM/TRF fits.
- _MIN_CURVED_FACES=12: no cylinder/cone LM on tiny fragments.
- Early-exit fit cascade (near-perfect simple fit skips cone TRF).
- Intent: cap candidate planes (500) + vectorised cap search;
  wall-thickness pairs capped at 200 largest planes.
Organizer result: 109 holes, 235 fillets, wall 2.008 mm — plausible.
KNOWN ISSUE: fragmented meshes yield 26k patches (25k tiny planes);
needs a coplanar-adjacent merge pass (future).

### Enclosure milestone (2026-07-03)
- [x] Patch merge pass: union-find over adjacent compatible regions
      (plane: normal+offset; cylinder: axis+radius+axis-line), refit after
      merge. tests/test_recognition.py::TestMerge.
- [x] mra/core/loops.py — boundary-loop utils moved from reconstruction
      (mra.reconstruction.profiles is now a shim) so intent can use them.
- [x] Extrusion cap selection by SILHOUETTE MATCH: top cap = farthest
      plane whose outer boundary extents match the base outline (5%).
      Fixes both failure modes: boss-top swallowing AND cavity-floor-
      as-top. Bounded to 20 largest candidates for fragmented meshes.
- [x] INTERNAL_CAVITY detection (large same-facing plane between caps)
      + builder cuts rim inner loops down to the cavity floor.
- [x] Open-top enclosure + enclosure-with-bosses reconstruct with exact
      volume; STEP round-trip. FULL SUITE: 58/58 PASSING.
- Organizer regression: 60 s total (silhouette candidates add ~18 s on
  fragmented meshes — optimise boundary_loops_3d later if needed).

### Enclosure features round 2 (2026-07-03)
- [x] Boss-before-hole ordering in builder (screw-boss pilot holes were
      being filled back in by the boss fuse) + test.
- [x] CONNECTOR_CUTOUT: non-circular inner loops on side-wall planes
      detected in intent, cut through the wall (1.5x thickness) in the
      builder + USB-cutout test. FULL SUITE: 60/60 PASSING.

### M5 Tab5 backplate milestone (2026-07-03) — FIRST REAL DELIVERABLE
User goal: CNC-machinable CAD of the Tab5 backplate.
- m5tabsolidworks/Tab5.stl = full assembly, unwelded (every triangle its
  own body). After weld: 23 bodies. Backplate = the 125.9 x 77.9 x 2.85
  plate at the z=0 face (body index 1 by area). Saved as
  m5tabsolidworks/backplate_extracted.stl (use this in the GUI).
- .SLDPRT files are unreadable by the open-source stack (proprietary).
- NEW: terrace/pocket reconstruction (replaces cavity-specific path):
  * Extrusion silhouette = cap with LARGEST OUTLINE on either side;
    base prism spans lowest bottom plane -> largest-outline top plane.
  * POCKET features: any cap-parallel plane below its silhouette face,
    cut region = the patch's own loops (inner loops stay as islands,
    so bosses survive). Ray "open sky" test rejects lateral-opening
    ledges (USB cutouts stay cutouts).
  * Analytic wires: circles and rounded rectangles (4 lines + 4 true
    arcs) recovered from tessellated loops; polygon fallback.
- RESULT: m5tabsolidworks/backplate_final.step — 1 solid, 81 faces,
  12 CIRCLE entities, volume +0.26% vs scan, scan->CAD max deviation
  0.0088 mm. 62/62 tests passing.
- Known cosmetic: 12 sliver faces / 24 tiny edges (pocket loops nearly
  coincident with silhouette corners) — harmless, could ShapeFix later.

### Launch experience (2026-07-03)
- [x] MRA.bat in project root — double-click, no console (pythonw).
- [x] Desktop shortcut "Mechanical RE Assistant.lnk" with generated icon
      (assets/mra.ico); app window/taskbar icon wired in gui/app.py.
- User priority noted: ease of access / seamless UX.

### Correction + rear panels (2026-07-03, evening)
- USER CORRECTION: the 125.9x77.9x2.85 plate at z=0 is the FRONT/screen
  plate (files renamed frontplate_*). Front of assembly = z=0.
- Tab5 rear = two panels: rear_panel_large (79x76.3) + rear_panel_small
  (38.2x70.4); rear_shell_housing (128x80x12, 1 mm walls) is the molded
  wraparound body — NOT reconstructable yet (+395%), STL kept, STEP
  deleted. Which part the user actually wants to CNC: unconfirmed —
  STEPs provided for both rear panels + front plate.
- [x] PAD features: planes ABOVE their silhouette face fuse as raised
  plateaus (mirror of pockets, circle tops left to bosses).
  rear_panel_large -0.75% / rear_panel_small +2.2% vs scan. 63/63.

### Body 5 + through-cut gate (2026-07-03, late)
- User identified their target as body 5 (28.0 x 3.5 x 9.2 mm rail/clip,
  z -11..-1.8). Exposed a bug: non-circular inner loops on the base cap
  were always tunnelled through; blind recesses lost 35% volume. Fixed
  with a ray gate (_loop_region_open) — through-cut only when the loop
  region is actually open. body5 now +0.04%, body4 (79x36.4x4, exported
  too) -0.65%. Files named by dimensions in m5tabsolidworks/. 63/63.

### Body isolation in GUI (2026-07-03, late)
- USER CONFIRMED: rear_panel_large (79 x 76.3 x 5.2) is the CNC target.
- [x] mra/meshproc/bodies.py — split_bodies (weld-then-split, debris
      filter, largest first) + BodyInfo labels.
- [x] mra/gui/body_picker.py — dialog with live viewport preview per
      row; auto-opens on multi-body import; "Isolate Body" toolbar
      button re-opens it. Verified against Tab5.stl (65 bodies, rear
      panel = row 2). 67/67 tests.
- Remaining rear_panel_large gaps (-0.75% vol, ~1.8 mm max dev spots):
  6 unapplied fillets + drafted (tapered) pad sides -> roadmap 1.

### Viewer shading + validation gate (2026-07-03, late)
- [x] Viewport shader "tab5TwoLight" ported from the user's Tab5
      renderer (AI Camera Project/Tab5 3D Render/src/renderer.h,
      _shade_color): warm key (0.577,0.577,-0.577)/(180,175,155), cool
      fill (-0.577,-0.3,0.577)/(30,28,60), ambient (30,30,40). Written
      GLSL-ES style (u_mvp/a_normal — pyqtgraph 0.14 has no legacy
      built-ins). Near-white base material, 1.3x framing. Verified by
      real-display screenshot.
- [x] Validation: ready_for_export now requires EXACTLY ONE solid;
      builder prunes <0.1%-volume boolean slivers and warns about real
      disjoint pieces. rear_panel_large: 9 disjoint solids -> correctly
      NOT ready (missing fillet/blend bridges — roadmap 1). 68/68.

### Single-solid backplate: bridges + fillet application (2026-07-03)
- [x] mra/reconstruction/bridges.py — graph-targeted blend/gusset
      reconstruction: map floating solids to mesh patches (bbox
      assignment), hull their adjacency-ring connectors (phase 1), and
      for pieces connected by thin planar webs, hull the piece + its
      small web neighbours together (phase 2). Hulls inflated 0.1 mm
      for fuse penetration; every fuse guarded (sane OCP volume, never
      lose volume, degenerate hulls rejected).
- [x] mra/reconstruction/fillets.py — BRepFilletAPI application of
      recognised FILLET features: edges matched by patch bbox + axis
      parallelism, per-edge/per-fillet failure tolerance, validity
      check before accepting.
- RESULT: rear_panel_large = ONE SOLID, ready_for_export, exported.
  Volume +3.39% (bridge hulls slightly overfill moats near ribs —
  an honest, logged trade-off). 73/73 tests.
- The 6 recognised fillets on this part found no matching straight
  edges (blend geometry consumed by bridges) — left sharp, logged.
- DECISION on manual connect tool: automatic bridging suffices for
  this part class; a Stage-4 "bridge two faces" click tool remains on
  the roadmap as the escape hatch for ambiguous cases (e.g. when
  filling a clearance channel would be wrong).

### Reference-photo audit + unclaimed round openings (2026-07-03)
- references/M5Tab Backplate Bottom View.jpeg (user photo, inside view)
  used to audit the reconstruction. Revealed: NO hole features were
  detected on the panel (hole walls tessellate below _MIN_CURVED_FACES,
  so no cylinder patches), and the builder skipped circular cap loops
  assuming hole features would cut them -> mounting holes vanished.
- [x] _cut_unclaimed_round_openings: scans ALL cap-parallel planes for
  circular inner loops that are through (ray gate both ways), unclaimed
  by hole features, dedup across faces -> analytic cylinder cuts.
- Backplate: 4x D3.00 corner mounting holes + 4x D2.20 insert bores
  recovered (matches photo 1:1). Still 1 solid, +3.09% vol. 73/73.
- Insert bores are D2.2 (brass heat-set in the molded part) — for CNC
  metal the machinist drills/taps instead; spec in photo.

### User feedback round: highlighting, hole wizard, missing geometry
### (2026-07-03, late night)
- [x] Outline-derived round openings promoted to first-class HOLE
      features in intent (conf 0.70, visible in sidebar, wizard-editable).
- [x] _cut_unclaimed_openings: non-circular through-windows cut on ANY
      cap-parallel plane (was bottom-cap only) — backplate recovered 20
      through windows incl. the rectangular ones the user flagged.
- [x] Debris prune threshold now ABSOLUTE (1e-3 mm^3): the old 0.1%-of-
      part threshold silently deleted real thin webs/ribs. (User asked
      if thin sections were removed for machinability — no, it was this
      bug; the tool never intentionally deletes geometry.)
- [x] Sidebar feature list -> viewport highlight: clicking a row lights
      its patches orange, everything else dims; deselect restores.
- [x] mra/gui/hole_wizard.py — Stage-4 hole purpose dialog, grouped by
      diameter: keep measured / M2-M4 clearance/tap-drill / fill-in.
      Runs during Reconstruct CAD. User context: Tab5 inserts are M3
      (heat-set in plastic; Ø2.2 molded bores -> M3 tap drill Ø2.5 for
      CNC metal).
- Backplate: 1 solid, +1.04% volume (was +3.09 — windows removed the
  bridge overfill), re-exported. 73/73 tests.

### DFM cost advisor (2026-07-03, night)
- Context: shop quoted ~$1,400 for the backplate. mra/dfm.py cost model
  lands at $1,254 one-off — structure validated: ~$510 setups+CAM base,
  ~$28/feature programming+inspection, cutting time trivial.
- [x] mra/dfm.py — CostModel/CostReport (qty amortisation), per-feature
  pricing (pockets, holes by size-groups, windows w/ small-tool
  penalties), find_through_windows, transforms: drop_shallow_pockets,
  unify_pocket_depths.
- [x] mra/gui/dfm_dialog.py — savings-menu dialog in Reconstruct flow
  (report + opt-in transforms). tests/test_dfm.py, 77/77 passing.
- [x] rear_panel_large_SIMPLIFIED.step exported (M3 tap bores, cosmetic
  pockets dropped): $1,254 -> $1,200 one-off; qty5 $316, qty10 $199.
- KEY INSIGHT for user: safe auto-simplifications save ~$50; the big
  levers are QUANTITY (qty5 = 4x cheaper/part) and dropping/enlarging
  the ~15 small-tool windows (~$28-43 each) — functionality decisions
  only the user can make (window keep/drop UI on roadmap).

### Plugged-bore hunt + hole schedules (2026-07-04, early)
- User report: wizard tap selection showed holes filled in. Root causes
  found by stage-tracing (scratchpad/trace_plug.py pattern):
  1. Outline-derived through holes used depth = extrusion HEIGHT (the
     base slab, 1.99 mm) — cutters never reached bores inside towers.
     Fix: depth = full part span along the extrusion direction.
  2. Single-centroid ray "openness" test was fooled by islands with a
     bore at centre: a screw tower outline read as a through-window and
     the whole tower column was cut out. Fix: core.loops.region_is_
     through — multi-sample (centroid + mid-ring) rays both ways.
  3. Pipeline reordered: material (base/pads/pockets/bosses) -> bridges
     -> fillets -> ALL CUTS LAST (holes/windows/cutouts cut once,
     through final material; bridge hulls can never plug them).
  4. Bridges now fuse via ONE multi-tool fuzzy fuse (SetFuzzyValue 1e-5)
     — chained fuses left coincident-face debris; cuts also fuzzy.
- RESULT: backplate 1 solid, volume -0.02% (best yet), all 8 bores
  verified open by B-Rep point classification AND tessellation rays.
  16 true windows (4 fake "tower windows" eliminated). 77/77 tests.
- [x] mra/export/schedule.py — hole schedule txt written next to STEP
  (positions, Ø, THRU, purpose). STEP geometry cannot natively carry
  thread specs (that is PMI territory); the schedule carries intent.

### Setup-elimination tradeoff tool (2026-07-04)
- User insight: outer face could skip machining entirely; recesses at
  the mounting holes compensated with longer screws.
- [x] dfm.setup_plans: per-side plan (pockets, pads-block-elimination,
  cost incl. setup fee, per-pocket tradeoff text) + drop_pockets
  (selective, by feature id).
- [x] DfmDialog rebuilt: per-side sections with keep/drop checkbox per
  recess (tooltip = tradeoff, click highlights patch in viewport);
  dropping ALL recesses of an eliminable side removes the whole setup.
  Pads on a side block elimination and say so.
- Backplate result: outer side = 6 pockets, 0 pads -> eliminable.
  Dropping them: $1,204/2 setups -> $895/1 setup (SAVES $309, 26%);
  qty5 $246. Tradeoff: 4 corner screws sit 2.0 mm proud (use +2 mm
  screws), 2x 1.5 mm recesses flush. Exported
  rear_panel_large_SINGLE_SETUP.step + hole schedule (M3 roll Ø2.75).
  80/80 tests.

### Recess FILL semantics + boolean hardening (2026-07-04)
- User: unchecked all outer-side recesses; they did not fill in.
  Stage-trace with corner-box volume probes revealed: corner-tab
  recesses are NOT cuts — the base silhouette is NOTCHED there and the
  tabs are pads floating above the notch. Skipping a cut fills nothing.
- [x] drop_pockets now FLAGS pockets fill=True (kept as features);
  builder fuses the floor region out to the face (face_with_holes, so
  bores stay open). Identical result for real cut-recesses; adds the
  missing material for emergent ones. drop_shallow_pockets same path.
  Cost model/setup plans skip filled pockets.
- [x] Boolean hardening (combo of many fills broke later cuts to an
  EMPTY-but-"successful" result): _guarded_fuse (fuse never loses
  volume), cut guard in _boolean (cut can never remove more than the
  tool volume; per-site try/except skips + logs), _unify_faces cleanup
  runs BEFORE the cut phase as well as at the end.
- Backplate single-setup: $1,191/2 setups -> $882/1 setup, 1 solid,
  all 8 bores open, exported + hole schedule. 81/81 tests.

### Flatten-recess option + stepped-hole finding (2026-07-04)
- User: two-step recesses down to corner mounting holes; only one step
  filled when unchecking "outer side".
- Root cause (diagnosed via BRepClass3d column probes): the recess steps
  near a mounting hole are (a) OFFSET from the bore (floor at 78.2,21 vs
  bore 75.9,18.9 — 3mm off, NOT a concentric counterbore) and (b)
  classified onto DIFFERENT machining sides, so per-side "fill" misses
  some. A concentric-cylinder plug (first attempt) was the wrong model +
  failed silently on bridged corner topology.
- [x] Pockets now store "centroid" in params (set in _detect_pockets).
- [x] dfm.flatten_hole_recesses(intent, hole_ids, radius): marks every
  pocket whose centroid is within radius of a flagged hole's axis as
  fill=True (reuses tested pocket-fill path, side-agnostic). Wired to a
  per-group "flatten recess" checkbox in the hole wizard (apply() now
  returns (removed, flatten_ids)). 83/83 tests.
- REMAINING (documented limitation, NOT a bug): at the (76,*) corners
  the deepest "step" is not a pocket at all — the mounting TAB is
  genuinely thinner stock there (ends z=-10.16, plate face z=-12), so
  there is no feature to fill. Flatten catches detected recess steps;
  a tab that is simply thin is a CAD judgment call. Recommendation to
  user: the residual single counterbore step is machining-harmless
  (one extra Z-level on an already-milled face, ~no cost) — leave it,
  or thicken that tab by hand on the STEP if a flush face is required.

### Artifact audit + attempted architecture fix (2026-07-04) — REVERTED
- User (SendCutSend renders): spokes (Large), gaps at mounting holes
  (SINGLE_SETUP), overhangs (SIMPLIFIED). Asked to be methodical.
- ROOT CAUSE (confirmed, scratchpad/audit.py): reconstruction is ADDITIVE
  (thin base + fused pads). Base built from ONE cap outline (3627 mm²) vs
  the true full footprint mesh.projected = 4909 mm² / 22 openings. Pads
  land off the base and FRAGMENT the body (1 -> 9 solids); bridge_
  disconnected reconnects with convex-hull WEBS = the spokes; partial
  closes = the gaps. Overhangs = the real towers (separate DFM matter).
- ATTEMPTED (plan swirling-finding-snail): footprint base + full-column
  pads + subtractive full-height stock block with top-relief carve.
  FINDINGS:
  * Footprint base + full-column pads DID connect pads (base->pads = 1
    solid), but POCKET cuts then fragment the thin slab (1 -> 13).
  * Net fragmentation got WORSE (13 vs 9) -> more bridging, not less.
  * Subtractive relief cut (top-cap region has 20 tower-hole islands) is
    too fragile for OCC — the 20-island face produces an invalid cut.
  * REVERTED builder.py to committed state (known-good 9->1 bridge,
    validated single solid). Did NOT ship the regression.
- CONCLUSION: proper fix is a real subtractive rewrite where the top
  relief is done as block MINUS per-tower prisms (NOT one 20-island
  face) so each cut is simple/robust. That is a dedicated effort, not a
  quick patch. Alternative smaller win: replace convex-hull bridge webs
  with clean prismatic gap-fills (cosmetic only).
- Known-good outputs remain: rear_panel_large*.step (1 solid each,
  validated, bores open). The spokes/gaps are cosmetic-ish artifacts of
  bridging, not holes in the part.

### Cosmetic bridge-web fix: prismatic ribs (2026-07-04)
- User: revisit the connector-slot section (window at (73,41), 5.6x38.8mm,
  flanked by thin frame rails — pads 1.1 & 2.8mm wide x 35mm long — plus
  end bosses); "already flat one side but gets messed up". Approved the
  cosmetic bridge-web fix.
- ROOT of "messed up" = the spokes: bridge_disconnected filled connector
  regions with 3D CONVEX HULLS that span diagonally boss->plate = struts.
- [x] bridges.bridge_disconnected now takes `direction`; connector
  regions are filled with `_prismatic_web` = the region's 2D footprint
  (shapely convex hull, +0.15mm buffer) extruded STRAIGHT through the
  part thickness -> a clean vertical machinable rib, flush to the flat
  faces, no spokes. Falls back to hull if prism fails. build_solid passes
  the extrusion direction.
- Slot section render now closely matches the STL (rails + both bosses +
  slot; squared ribs replace the molded rounded gussets — expected DFM
  simplification). All 8 bores still open. 83/83 tests.
- TRADEOFF: full-thickness ribs add material — volume +7.4..8.5% (was
  ~+1%). Cosmetically clean but heavier; could extrude only local gap
  thickness later to trim. All 3 deliverable STEPs regenerated (1 solid
  each) + hole schedules.

### Local-extent ribs (2026-07-04) — "extra walls" fix
- User (SCS upload): extra walls extending one way (the full-thickness
  ribs), edge wall a little thick, a slot in the right wall that could be
  solid. SCS 3D preview blank but it QUOTED ($1,188 qty1) — STEP is valid
  (BRepCheck: 1 solid, 1 shell, 0 invalid faces), so blank preview is a
  SCS viewer issue, not a defect.
- [x] _prismatic_web now spans only the connector region's OWN along-axis
  extent (+0.3 margin), clamped to the envelope — was spanning the full
  part thickness = tall extra walls. Volume +8.5% -> +0.5..1.7% across
  variants; 83/83 tests; all bores open; deliverables regenerated.
- STILL TO PINPOINT (need exact location from user): "edge wall a little
  thick" and "slot in the right wall that should be solid" — localized
  accuracy tweaks (likely a mis-detected pocket/window on a side wall or
  an over-thick rim). Ask user to click the feature in the GUI list to
  identify, then adjust.

### Sheet-metal flat version (2026-07-04)
- User idea: a function that gives a flat sheet version — drop all depth
  besides the actual thickness. Laser/waterjet sheet cutting costs a
  fraction of 3-axis machining on SendCutSend.
- [x] reconstruction.build_sheet(mesh, seg, intent, thickness=None):
  full 2D footprint (outline + real through-openings; degenerate shapely
  interior slivers filtered by area) extruded to one thickness. Default
  thickness = detected wall thickness -> extrusion height -> 2 mm.
  Wizard-resized hole diameters re-cut analytically. _footprint_face
  shared helper in builder.py.
- [x] GUI "Sheet Version" toolbar button (enabled after Recognize):
  QInputDialog thickness prompt -> preview -> Validate/Export enabled.
- [x] Deliverable: rear_panel_large_SHEET_2mm.step + hole schedule
  (1 valid solid, all 20 openings, M3 roll-tap bores). 85/85 tests.
- NOTE: sheet drops towers — user must add standoffs or spacers for the
  M3 inserts (schedule carries positions); that's inherent to the
  flat-sheet tradeoff, not a bug.

### Open-sourced as mesh2cad (2026-07-12)
- Sheet parts ordered: 6061 T6 .080" $12.05 + 5052 H32 .080" $10.47,
  total $32.21 shipped, arriving ~Jul 16 (vs $1,188 machined quote).
- PUBLIC REPO: https://github.com/Matthewjg95/mesh2cad (MIT).
  Published as a FRESH single-commit snapshot: tool code + tests +
  PROGRESS only. m5tabsolidworks/, m5tabsolidworks.zip, references/
  EXCLUDED (M5Stack-derived geometry; user chose to keep private) and
  are in the public .gitignore.
- THIS local repo remains the private working repo with full history
  incl. part files. TO RELEASE UPDATES to the public repo: re-copy the
  tree (minus .git/.venv/m5tabsolidworks*/references/.claude) into a
  clone of mesh2cad, commit, push — or set up a sanitized branch later.
  NEVER push local history to the public remote (it contains the part
  files in history).

### Community data pipeline (2026-07-12)
- Vision: crowd-sourced parts (mesh + tool analysis + user verdict) become
  a regression corpus AND training data for the roadmap AI module.
- [x] mra/diagnostics.py — ReproReport + build_repro_bundle(): writes a
  zip (mesh.stl + versioned report.json + report.md) capturing mesh
  stats, recognition (patch counts/coverage/confidence/low-conf list),
  intent (feature counts, wall, symmetry, questions, hole Øs), build
  (log, validation, volume error). schema_version=1. Nothing uploaded.
- [x] GUI "Report Part" button (enabled after import): verdict dialog ->
  save zip -> points to issues/new/choose.
- [x] .github/ISSUE_TEMPLATE/ forms (reconstruction-issue.yml,
  part-submission.yml, config.yml) that ask for the bundle; CONTRIBUTING.md.
- [x] tests/test_diagnostics.py. FULL SUITE 88/88.
- Backplate bundle verified: 313 patches, all features, 1 solid 0.46%.

### NEXT STEP (future sessions)
0a. PyInstaller single-folder .exe distribution (user wants a real
    executable; OCP+PySide6 bundling is finicky — budget a session).
0b. ShapeFix cleanup pass for sliver faces / tiny edges before export.
1. Fillet application in Stage 5 (BRepFilletAPI_MakeFillet on matching
   edges) — fillets are recognised+reported but not built.
2. Chamfer recognition (narrow planar strips at ~45 deg).
3. Screw-boss composite feature (boss + concentric hole -> SCREW_BOSS
   with sidebar description; geometry already reconstructs correctly).
4. Stage-4 viewport highlighting of questioned patches (click-to-select).
5. Try on a real M5Stack STL; tune tolerances.
6. GUI: run reconstruct/validate in worker thread too (currently only
   recognition is threaded; build is fast but OCC booleans can spike).
7. Optimise boundary_loops_3d (defaultdict walk) — adds ~18 s on
   fragmented 145k-tri meshes via silhouette matching.

## Decisions made
- trimesh loads with `process=False` so repair report sees real defects.
- Repair fixes only unambiguous problems; large holes / multi-body are
  reported, not silently "fixed".
- All tolerances live in `mra.core.Tolerances`; nothing hard-coded.
- open3d is optional (only needed for Stage-2 RANSAC alternatives);
  guard imports so app runs without it.

## Crash workarounds
- Sessions have crashed repeatedly. Mitigations:
  - No long-running background shell tasks; foreground with timeout.
  - Install dependencies in small chunks, checkpoint here after each.
  - `git commit` after every completed module.
  - `PYTHONIOENCODING=utf-8` on any pio/pip-heavy output (Windows cp1252).
