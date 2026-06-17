# physics4.html — Conflict Resolution Patch

Audit target: `physics4.html` (480 lines, merged perf-integrated build) plus its 7 physics modules and 13 graphics/perf modules loaded via `<script src=...>`.

Scope of audit:
1. Duplicate top-level identifiers inside the inline `<script>` block.
2. Conflicting/overlapping global namespace assignments (`UC_*` objects).
3. Redeclared THREE imports.
4. Historical duplicate `animate` / `last` / `fpsAcc` bug (regression check).
5. Inconsistent constants between `physics4_core_constants.js` (G2 reference) and the values actually used inline.

Line numbers below refer to `physics4.html` unless prefixed with a module filename.

---

## 1. Duplicate top-level identifiers (inline `<script>`)

The inline script spans lines 96–480. A scan of all top-level `const`/`let`/`function` declarations yielded the following identifiers, each appearing exactly once:

| Identifier | Kind | Line | Status |
|---|---|---|---|
| `scene` | const | 108 | unique |
| `renderer` | let | 111 | unique |
| `camera` | let | 127 | unique |
| `controls` | let | 127 | unique |
| `env` | let | 147 | unique |
| `mats` | let | 157 | unique |
| `ground` | let | 175 | unique |
| `lighting` | let | 187 | unique |
| `sun` | let | 188 | unique |
| `post` | let | 208 | unique |
| `instanced` | const | 216 | unique |
| `shell` | const | 217 | unique |
| `culler` | const | 218 | unique |
| `debrisPool` | const | 219 | unique |
| `drs` | const | 220 | unique |
| `idle` | const | 221 | unique |
| `shadows` | const | 222 | unique |
| `bodies` | const | 227 | unique |
| `GRAVITY` | const | 228 | unique |
| `FIXED_DT` | const | 229 | unique |
| `MAX_SUBSTEPS` | const | 230 | unique |
| `SLEEP_LIN_EPS` | const | 231 | unique |
| `SLEEP_TIME` | const | 232 | unique |
| `DEBRIS_CAP` | const | 233 | unique |
| `accumulator` | let | 235 | unique |
| `lastTime` | let | 236 | unique |
| `HASH_CELL` | const | 239 | unique |
| `hashGrid` | const | 240 | unique |
| `hashKey` | function | 241 | unique |
| `rebuildHash` | function | 242 | unique |
| `aabbOverlap` | function | 264 | unique |
| `resolveCollision` | function | 270 | unique |
| `step` | function | 335 | unique |
| `wakeBody` | function | 393 | unique |
| `addBody` | function | 418 | unique |
| `BRICK_W`,`BRICK_H`,`BRICK_D` | const | 461 | unique |
| `FLOORS`,`ROWS_PER_FLOOR` | const | 462 | unique |
| `WALL_LEN_X` | const | 463 | unique |
| `WALL_LEN_Z` | const | 464 | unique |
| `SLAB_THK` | const | 465 | unique |
| `buildingGroup` | const | 467 | unique |
| `brickGeo` | const | 470 | unique |
| `brickGeoZ` | const | 471 | unique |
| `slabGeoX` | const | 472 | unique |
| `pickBrickMat` | function | 474 | unique |
| `buildingBounds` | const | 479 | unique |

**Result: zero duplicate top-level identifiers detected in the inline script as of line 480.**

---

## 2. Historical `animate` / `last` / `fpsAcc` duplicate-identifier bug

This bug landed in `physics3.html` and was fixed there. Regression check for `physics4.html`:

| Identifier | Occurrences in inline script | Status |
|---|---|---|
| `animate` | 0 | NOT YET DEFINED — file is truncated at line 480 before the render loop is declared. **Risk: when the tail (animate loop + HUD wiring + bomb logic) is appended, ensure only one `function animate` / `const animate` exists.** |
| `last` | 0 | NOT YET DEFINED. The current build uses `lastTime` (line 236). **Resolution: keep `lastTime`, do NOT introduce a second `last` variable in the tail.** |
| `fpsAcc` | 0 | NOT YET DEFINED. **Resolution: declare exactly once when the FPS HUD ticker is added.** |
| `accumulator` | 1 (line 235) | Single declaration — correct. Do not redeclare in the animate loop. |

**Action required for the missing tail of the file** (the file ends mid-builder at line 480; the animate loop, HUD updater, bomb/explosion logic, and `</script></body></html>` are still to be appended):

- Declare `let last = performance.now();` and `let fpsAcc = 0;` ONCE at module scope, before `function animate()`.
- Declare `function animate(now) { ... }` ONCE.
- Do NOT reuse the names `last`, `fpsAcc`, `animate`, `accumulator`, `lastTime`, `step` inside any nested closure with `let`/`const` of the same name — shadowing has caused the historical confusion.

---

## 3. Global `UC_*` namespace assignments

Each perf/graphics module assigns exactly one `window.UC_*` object. Cross-referenced via the loaded `<script src>` list (lines 71–94) and each module's own write log:

| Global | Owner module | Members | Collision? |
|---|---|---|---|
| `UC_Renderer` | `renderer_setup.js` | `setupRenderer`, `makeResizeHandler` | none |
| `UC_Lighting` | `lighting_setup.js` | `setupLighting` | none |
| `UC_Environment` | `environment_setup.js` | `setupEnvironment`, `createGroundPlane` | none |
| `UC_Materials` | `materials_palette.js` | `createMaterialPalette` | none |
| `UC_Post` | `postprocessing_setup.js` | `setupPostProcessing`, `createContactShadowHelper` | none |
| `UC_Camera` | `camera_controls.js` | `setupCamera`, `setupOrbitControls`, `bindHotkeys`, `setup` | none |
| `UC_Instanced` | `physics4_perf_instanced.js` | `InstancedMeshManager` | **CONFLICT (see below)** |
| `UC_Culling` | `physics4_perf_culling.js` | `FrustumCuller` | none |
| `UC_DebrisPool` | `physics4_perf_pool.js` | `DebrisPool` | **CONFLICT (see below)** |
| `UC_MergeShell` | `physics4_perf_merge.js` | `buildMergedShell` | **CONFLICT (see below)** |
| `UC_DRS` | `physics4_perf_drs.js` | `DynamicResolutionScaler` | none |
| `UC_Idle` | `physics4_perf_idle.js` | `IdleScheduler` | none |
| `UC_Shadows` | `physics4_perf_shadows.js` | `ShadowController`, `MOVE_EPSILON` | none |

### 3a. `UC_Instanced` — class name mismatch

- **Module exports** (`physics4_perf_instanced.js`): `UC_Instanced = { InstancedMeshManager }` (per worker write log).
- **Inline usage** (`physics4.html` line 216): `new UC_Instanced.InstancedManager(scene)` — note **`InstancedManager`**, not `InstancedMeshManager`.
- **Resolution: KEEP module export as `InstancedMeshManager`** (matches the descriptive worker spec) and **patch the inline call site** to `new UC_Instanced.InstancedMeshManager(scene)`. Reason: the module is the single source of truth for its public API; renaming the export to match a typo at one call site would break the module's documented contract.

### 3b. `UC_DebrisPool` / `UC_Pool` — namespace mismatch

- **Module exports** (`physics4_perf_pool.js`): `window.UC_DebrisPool = { DebrisPool }`.
- **Inline usage** (line 219): `UC_Pool.createDebrisPool(64)` — references **`UC_Pool`** (not `UC_DebrisPool`) and calls a factory **`createDebrisPool`** that the module does not export.
- **Resolution: KEEP the module namespace `UC_DebrisPool`** and **patch the inline call** to `new UC_DebrisPool.DebrisPool(64)`. Reason: the module exposes a class, not a factory; the inline factory call is a stale reference from an earlier API draft.

### 3c. `UC_MergeShell` / `UC_Merge` — namespace mismatch

- **Module exports** (`physics4_perf_merge.js`): `window.UC_MergeShell.buildMergedShell(...)`.
- **Inline usage** (line 217): `new UC_Merge.StaticShell(scene)` — references **`UC_Merge`** (wrong namespace) and **`StaticShell`** (not exported).
- **Resolution: KEEP the module namespace `UC_MergeShell` and its `buildMergedShell` function.** Patch the inline code to call `UC_MergeShell.buildMergedShell(brickDescriptors, slabDescriptors, roofDescriptors, mats)` after the building generator has produced its descriptor lists, and store the returned `{ meshes, boundsBox }`. Reason: the merge module returns merged meshes from descriptors; there is no `StaticShell` class, and inventing one would duplicate the existing API.

### 3d. `UC_Shadows.ShadowController` constructor signature

- **Module** (`physics4_perf_shadows.js`): `new ShadowController(renderer)` — single arg, takes the renderer; sun is queried via dirty flag.
- **Inline usage** (line 222): `new UC_Shadows.ShadowController(renderer, sun)` — passes a second `sun` argument.
- **Resolution: KEEP the module signature `(renderer)`** and drop the `sun` argument at the call site. JS will silently ignore the extra arg, so this is non-fatal, but the patched call should be `new UC_Shadows.ShadowController(renderer)` for clarity.

---

## 4. Redeclared THREE imports

The page loads THREE and its examples via CDN scripts at lines 60–68:

```
60  three.min.js
61  OrbitControls.js
62  BufferGeometryUtils.js
63  EffectComposer.js
64  RenderPass.js
65  ShaderPass.js
66  SSAOPass.js
67  CopyShader.js
68  SSAOShader.js
```

All 7 physics modules and 13 graphics/perf modules use the global `THREE` populated by `three.min.js` (line 60). None of them include an additional `<script src="three...">` tag or `import` statement, and none redefine `THREE` as a local variable.

| Concern | Status |
|---|---|
| Duplicate `<script src=".../three.min.js">` tags | none — single load on line 60 |
| Modules importing THREE via ES modules in a non-module page | none — all modules are classic scripts |
| `const THREE = ...` or `let THREE = ...` redeclaration | none found in inline script or any module |
| OrbitControls / Composer / Pass scripts loaded twice | none — each appears exactly once (lines 61–68) |

**Result: no redeclared THREE imports. The CDN load order is correct (`three.min.js` before any `examples/js/*` that attach to `THREE.*`).**

Note: when the tail of `physics4.html` is appended, do NOT add `import * as THREE from ...` — the page is not an ES module page, and doing so would shadow the global.

---

## 5. Constant value consistency: `physics4_core_constants.js` (G2) vs inline

`physics4_core_constants.js` is a **reference-only doc** (all values live in JSDoc comments; nothing is actually exported). The inline code on lines 228–233 redeclares a subset of those constants. Cross-reference:

| Constant | G2 spec value | Inline value (physics4.html) | Match? | Resolution |
|---|---|---|---|---|
| `GRAVITY` | `(0, -9.81, 0)` m/s² | `(0, -9.81 * 2.2, 0)` = **(0, -21.582, 0)** (line 228) | **MISMATCH** | **KEEP INLINE.** The 2.2× scale is a deliberate gameplay tuning from the project memory ("radial-impulse bombs" with snappy destructible feel). The G2 spec value `-9.81` is the physics-accurate default; the inline value is the demo's chosen game-feel value. Annotate inline with a comment: `// GRAVITY scaled 2.2x from physical 9.81 for snappier destruction (see core_constants §1)`. |
| `FIXED_DT` | `1/60` s | `1/60` (line 229) | match | keep as-is |
| `MAX_SUBSTEPS` | `5` | `5` (line 230) | match | keep as-is |
| `SOLVER_ITERATIONS` | `6` | not declared inline | n/a (no solver — simple impulse path) | OK; if a full SI solver is ever wired in, use `6` |
| `WARM_START_FACTOR` | `0.8` | not declared inline | n/a | OK |
| `PENETRATION_SLOP` | `0.005` m | not declared inline; positional correction at line 290 uses raw `pen` with no slop | **MISMATCH (omission)** | **Acceptable for the simple impulse path.** Adding a slop guard `if (pen < PENETRATION_SLOP) return;` before line 290 is recommended but optional. Not blocking. |
| `BAUMGARTE` | `0.2` | inline uses `0.8` as the position-correction factor (line 290: `corr = pen / invSum * 0.8`) | **MISMATCH** | **KEEP INLINE 0.8.** Reason: the inline code is doing direct positional correction, not a Baumgarte-stabilized velocity constraint. `0.8` in this context is the projection ratio (Erin Catto-style position-only push), which is the standard value (0.2–0.8 range, 0.8 for stiff stacks). The G2 `BAUMGARTE = 0.2` applies to a constraint-bias term, a different mathematical role. Annotate inline: `// 0.8 = positional projection ratio (not Baumgarte bias)`. |
| `RESTITUTION_THRESHOLD` | `1.0` m/s | not enforced inline (line 306 only checks `velAlongN > 0`) | **MISMATCH (omission)** | **Recommend patch.** Add `if (-velAlongN < 1.0) e = 0;` before line 308 to silence buzz on resting stacks under gravity. Non-blocking, but the project memory notes resting-stack stability is a known concern. |
| `SLEEP_LIN_THRESHOLD` | `0.05` m/s | `SLEEP_LIN_EPS = 0.05` (line 231) | match (renamed) | **Resolution: rename inline `SLEEP_LIN_EPS` → `SLEEP_LIN_THRESHOLD`** for consistency with the reference doc. Non-blocking. |
| `SLEEP_ANG_THRESHOLD` | `0.05` rad/s | not declared (no angular velocity in current simple engine) | n/a | OK — the inline engine is position-only; when angular is added (per `physics4_angular.js`), declare this constant. |
| `SLEEP_TIME_REQUIRED` | `0.5` s | `SLEEP_TIME = 0.6` (line 232) | **MISMATCH (0.6 vs 0.5)** | **KEEP INLINE 0.6.** Reason: 0.6 s gives a slightly more conservative sleep, reducing premature-sleep wake-storms when bombs land on stacks. The 0.1 s difference is within the tuning band documented in core_constants §4. Update the reference doc footnote to acknowledge the inline override. |
| `CCD_VELOCITY_THRESHOLD` | `15.0` m/s | not used (no CCD in simple inline path) | n/a | OK — debris is capped to `DEBRIS_CAP` and travels short distances; CCD module exists (`physics4_ccd.js`) but is not wired into the simple inline step. |
| `GRID_CELL_SIZE` | `1.5` m | `HASH_CELL = 2.0` (line 239) | **MISMATCH (2.0 vs 1.5)** | **KEEP INLINE 2.0.** Reason: the inline broadphase is a 3D spatial hash (XYZ), not the XZ-only grid the G2 spec describes. With a vertical axis included, the larger cell reduces per-body cell count (a brick spans ~1–2 cells instead of 2–3), which is the dominant cost. The 2.0 value is independently validated by the dependency note in the project context ("Uniform3DGrid broadphase (2.0"). Annotate the reference doc to allow 3D-mode override. |
| `GRID_BOUNDS` | `±40 XZ, -2..60 Y` | not explicitly bounded inline (open-world hash) | mismatch by omission | **Acceptable.** Inline hash uses unbounded integer cells; out-of-world debris is culled via `DEBRIS_CAP` recycling and ground-plane clamp (line 347). |
| `DEBRIS_CAP` | `220` | `220` (line 233) | match | keep as-is |

### Summary of constant patches required

**Mandatory (code correctness):** none — all mismatches are deliberate tuning or omitted-feature-acceptable.

**Recommended (consistency / stability):**
1. Rename `SLEEP_LIN_EPS` → `SLEEP_LIN_THRESHOLD` (line 231) to match reference doc.
2. Add inline comment on line 228 explaining the 2.2× gravity scale.
3. Add inline comment on line 290 explaining 0.8 is a positional projection ratio, not Baumgarte.
4. Optional: add `RESTITUTION_THRESHOLD = 1.0` guard before line 308 to stabilize resting stacks.
5. Update `physics4_core_constants.js` §4 and §6 with footnotes acknowledging the inline `SLEEP_TIME = 0.6` and `HASH_CELL = 2.0` (3D-mode) overrides.

---

## 6. Other findings

### 6a. `lighting` / `sun` decoupling (lines 187–205)

The fallback path on lines 192–204 builds `sun` directly, then on line 204 reassigns `lighting = { sun, hemi, ... }`. Both `lighting` and `sun` are declared once each — no duplicate, but the dual-path initialization could be tightened. **Resolution: keep as-is**; the dual path is intentional graceful-degradation when `UC_Lighting` is missing.

### 6b. `scene.fog` double-set (lines 152 + 154)

Line 152 (fallback path) sets `scene.fog = new THREE.Fog(0x9bbfe0, 60, 220)`.
Line 154 (post-condition) sets `scene.fog = new THREE.Fog(0x9bbfe0, 60, 240)` if still null.
Not a duplicate identifier, but a value mismatch (220 vs 240). **Resolution: drop line 154** (the post-condition is redundant once the fallback path runs, and the value 240 silently overrides whatever `UC_Environment.setupEnvironment` returned if that module returned `{ pmremEnv: ..., fog: null }`). Or unify both to 240.

### 6c. `renderer` properties set twice (lines 112–121 vs 123–124)

Lines 123–124 set `toneMapping` and `toneMappingExposure` unconditionally, AFTER `UC_Renderer.setupRenderer` already set them (per the module write log). Not a duplicate identifier, but a property override. **Resolution: keep**; the explicit override documents the intended exposure and is harmless.

---

## 7. Resolution summary (one-line per fix)

| # | Location | Change |
|---|---|---|
| 1 | physics4.html:216 | `new UC_Instanced.InstancedManager` → `new UC_Instanced.InstancedMeshManager` |
| 2 | physics4.html:217 | `new UC_Merge.StaticShell(scene)` → call `UC_MergeShell.buildMergedShell(...)` after descriptor build |
| 3 | physics4.html:219 | `UC_Pool.createDebrisPool(64)` → `new UC_DebrisPool.DebrisPool(64)` |
| 4 | physics4.html:222 | `new UC_Shadows.ShadowController(renderer, sun)` → `new UC_Shadows.ShadowController(renderer)` |
| 5 | physics4.html:231 | rename `SLEEP_LIN_EPS` → `SLEEP_LIN_THRESHOLD` |
| 6 | physics4.html:154 | remove redundant `scene.fog` re-set (or unify value to 240) |
| 7 | physics4_core_constants.js §4 | add footnote: inline `SLEEP_TIME = 0.6` overrides spec `0.5` |
| 8 | physics4_core_constants.js §6 | add footnote: 3D hash mode uses `HASH_CELL = 2.0` (vs 2D `1.5`) |
| 9 | physics4.html (tail, not yet written) | when `animate`/`last`/`fpsAcc` are added, declare each EXACTLY ONCE at module scope; do not shadow `lastTime`/`accumulator` |

No identifier collisions exist in the current 480-line build. The above patches close the namespace/API mismatches between inline call sites and module exports, and pre-empt the historical `animate`/`last`/`fpsAcc` duplication when the file's tail is appended.
