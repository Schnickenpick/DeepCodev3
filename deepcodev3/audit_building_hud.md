# physics4.html — Baseline Audit: Building, Debris, HUD, Orbit Controls

File audited: `physics4.html` (769 lines, single-file three.js demo).
This document maps the *current* implementation with exact line references and ends with a prioritized improvement list for the integration agent.

---

## 1. Building Generation

### 1.1 Constants and dimensions — lines 402–409

```
BRICK_W       = 1.0   // line 402
BRICK_H       = 0.5   // line 403
BRICK_D       = 0.6   // line 404
FLOORS        = 5     // line 405
BRICKS_PER_ROW = 7    // line 406  (X axis, front/back walls)
BRICKS_PER_COL = 6    // line 407  (Z axis, side walls)
ROWS_PER_FLOOR = 4    // line 408
FLOOR_H        = ROWS_PER_FLOOR * BRICK_H = 2.0 // line 409
```

Building footprint:
- X span: `7 * 1.0 = 7.0` units
- Z span: `6 * 1.0 = 6.0` units
- Total height (including slabs + roof): `5 * 2.0 + 0.2 (roof slab top) ≈ 10.2` units

### 1.2 Geometry / material reuse — lines 411–432

- **Two shared brick geometries** are created once and reused:
  - `brickGeo` (W×H×D = 1.0×0.5×0.6) — line 411
  - `brickGeoRot` (D×H×W = 0.6×0.5×1.0) for side walls — line 412
- **5-tone brick material cache** in `getBrickMaterial()` — lines 416–430. Bricks are randomly bucketed into one of 5 `MeshStandardMaterial` instances (roughness 0.95), enabling material sharing across hundreds of meshes.
- Dedicated shared materials:
  - `slabMat` — gray 0x999999, roughness 0.9 (line 431)
  - `roofMat` — darker gray 0x555555, roughness 0.9 (line 432)

### 1.3 `makeBlock(...)` — lines 434–449

Picks `brickGeo` / `brickGeoRot` when sizes match brick dimensions; otherwise allocates a new `BoxGeometry` (used for slabs and roof). Sets `castShadow` only when opts says so; `receiveShadow` is always true. Mass defaults to `sx*sy*sz*50`; default health 55.

### 1.4 `buildBuilding()` — lines 451–496

- Computes `halfW = 3.5`, `halfD = 3.0`, slab size `7.0 × 0.2 × 6.0` — lines 452–455.
- **Floor loop** (`floor = 0..4`) — line 457.
  - **Slab floors** (`floor > 0`) — lines 459–462. A single `7 × 0.2 × 6` slab placed at `baseY + 0.1`, material `slabMat`, mass ≈ `7*0.2*6*80 = 672`, health 300, casts shadow. Floor 0 has no slab (ground serves as floor).
  - **Brick walls**: 4 rows per floor, running-bond pattern via `offset = (row % 2) * (BRICK_W/2)` — line 467.
    - **Front/back walls (Z = ±halfD ∓ BRICK_D/2)** — lines 469–480.
      - Ground floor (floor 0), rows 0–2, bricks where `|x| < BRICK_W` are omitted to form a centered door on the front face (line 472). The door is **front-face only**; the back face is solid (lines 477–479 always place the back brick unless it's a window).
      - Upper floors (floor > 0), rows 1–2, every 3rd brick (`i % 3 === 1`) is a window — lines 473, 477.
    - **Side walls (X = ±halfW ∓ BRICK_D/2)** — lines 482–490. Loop `j = 1..BRICKS_PER_COL-2` (skips corners to avoid double-placing with front/back). Side walls use `brickGeoRot`. Upper floors get the same windowing rule on rows 1–2 (line 485).
- **Roof slab** — lines 494–495. Single `7 × 0.2 × 6` block at `y = FLOORS * FLOOR_H + 0.1 = 10.1`, material `roofMat`, health 300, casts shadow.

### 1.5 Approximate counts (per floor, ignoring removed door/window bricks)

- Bricks per row, front+back: `2 * 7 = 14` (running bond may drop the offset-row edge brick — line 471 — bringing some rows to 13).
- Bricks per row, sides: `2 * (BRICKS_PER_COL - 2) = 8`.
- Per row total ≈ 21–22 bricks; per floor ≈ `4 * 21 = 84` bricks before subtractions.
- Removals per floor:
  - Ground floor: door (~3 bricks on front face only).
  - Upper floors: ~4 window cutouts per row on rows 1–2 → ~8 cutouts/floor.
- Total brick bodies ≈ **~390–410**, plus **4 floor slabs + 1 roof slab = 5 slab bodies**. Static ground plane is not added to `bodies[]`.

### 1.6 `clearBuilding()` / `resetScene()` — lines 498–508, 700–713

- `clearBuilding` (lines 498–508) iterates and removes non-static bodies and the buildingGroup children. The condition `!b.isStatic === true` (line 501) is a **bug-shaped expression** equivalent to `b.isStatic === false`; it works because no body in the scene is actually static (the ground plane is not a `Body`), but it is misleading.
- `resetScene` (lines 700–713) does a clean wipe by removing **all** bodies, clearing the group, and calling `buildBuilding()` again. This is the path bound to `R`.

---

## 2. Debris System

### 2.1 Cap and reuse — lines 210, 545–575

- `MAX_DEBRIS = 220` — line 210.
- `debrisCount()` (lines 545–549) scans `bodies[]` linearly each spawn. `spawnDebris` early-outs when over the cap (line 553).
- Per destroyed block, **2–4 debris cubes** are emitted (`n = 2 + floor(rand*3)` — line 554).
- Each debris cube is **0.18–0.32** on a side (line 556).

### 2.2 Geometry / material reuse — lines 530, 557–558

- Shared `debrisMat` is declared (line 530) but **not actually reused**: `spawnDebris` allocates a fresh `BoxGeometry` and a fresh `MeshStandardMaterial` per debris cube (lines 557–558), copying the source block's material color when available (passed in from `explode` at line 621).
- This is the single biggest hot-path allocation in the file: up to `MAX_DEBRIS = 220` geometry+material pairs live concurrently, and each is disposed individually in `removeBody` (lines 250–251).

### 2.3 Auto-expiry — lines 229, 367–368, 390–392, 566–567

- Each debris body gets `life: 5` seconds at spawn (line 567).
- `stepPhysics` ages every dynamic body and flags `_dead` when `b.age > b.life` (lines 367–368).
- Dead bodies are removed in-place at the end of `stepPhysics` (lines 390–392) via `removeBody`, which disposes geometry + material when `isDebris` is true (lines 250–251).
- Debris also receive initial outward velocity (lines 568–572): horizontal ±4 m/s, vertical +2..+8 m/s.

### 2.4 HUD readout — lines 110, 518–522, 757

- `hudDebris` element bound at line 110.
- `updateDebrisCount()` (lines 518–522) does another linear scan of `bodies[]`.
- Refresh cadence: **once every ~0.5 s** from the main loop's FPS block (line 757), not on every spawn/expire.

---

## 3. HUD / UI

### 3.1 Markup and styling — lines 9–53

- `#hud` (top-left) — readouts: bombs fired, blocks remaining, debris, FPS (lines 40–45).
- `#help` (top-right) — control legend including LMB/MMB drag, wheel zoom, R reset, B bomb mode, F frame, hold-LMB-to-charge (lines 46–51).
- `#crosshair` — CSS-only red cross, hidden by default, shown only in bomb mode (lines 28–36, 52, 655).
- `#charge` — bottom-center progress bar, 260×10 px, fills green→yellow→red via gradient (lines 21–27, 53).
- All HUD elements are `pointer-events: none` so they never steal mouse input.

### 3.2 Bomb mode toggle (`B`) — lines 645–658, 687–691

- `bombMode` starts `true` (line 645); `setBombMode(on)` toggles crosshair visibility and canvas cursor (lines 653–657).
- Keybind: `KeyB` calls `setBombMode(!bombMode)` (line 689).
- When bomb mode is **on**, LMB-down is intercepted by `startCharge()` instead of starting orbit drag (lines 156–160).
- When bomb mode is **off**, LMB drags the camera (orbit).

### 3.3 Charge mechanic — lines 649–651, 660–664, 675–685, 741–744

- Constants: `MAX_CHARGE = 1.6 s`, `MIN_POWER = 22`, `MAX_POWER = 75` (lines 649–651).
- `startCharge()` records `chargeStart = performance.now()` and shows the bar (lines 660–664).
- Per-frame the loop updates `chargeFill.style.width` from `(held / MAX_CHARGE) * 100%` (lines 741–744).
- `releaseCharge(e)` (lines 675–685): clamps held time to `MAX_CHARGE`, lerps power `MIN_POWER → MAX_POWER`, converts cursor NDC to a world-space direction via `screenToWorldDir` (lines 666–673), and spawns the bomb 1.5 units in front of the camera.

### 3.4 LMB fire flow — lines 154–164, 186–191

- `mousedown` on canvas:
  - If bomb mode + LMB → `startCharge()` and **early return** (lines 156–160), so orbit drag never engages.
  - Otherwise: LMB → `dragging = 'orbit'`, MMB → `dragging = 'pan'`, RMB → `dragging = 'pan'` (lines 161–163).
- `mouseup` on window: if charging + LMB → `releaseCharge(e)`; always clears `dragging` (lines 186–191).

### 3.5 Reset / frame keys — lines 687–698

- `R` → `resetScene()` (rebuilds building, zeros bomb counter).
- `F` → `frameBuilding()` (lines 693–698): re-centers `orbit.target` at `(0, FLOORS*FLOOR_H/2 = 5, 0)`, distance 35, yaw `0.25π`, pitch `0.18π`.

### 3.6 On-screen readouts and update cadence

| Readout | Element | Updated by | Cadence |
|---|---|---|---|
| Bombs fired | `#bombCount` (line 41) | `spawnBomb` (line 541) | Per shot |
| Blocks remaining | `#blockCount` (line 42) | `updateBlockCount()` (lines 513–517, called at 511, 624, 711) | After build + each explosion |
| Debris | `#debrisCount` (line 43) | `updateDebrisCount()` (lines 518–522, called at 712, 757) | ~0.5 s |
| FPS | `#fps` (line 44) | Main loop (lines 752–759) | ~0.5 s |

FPS is computed as `frames / elapsed` over the last ~0.5 s window (lines 754–759).

---

## 4. Blender-Style Orbit Controls

### 4.1 State — lines 125–134

```
orbit = {
  target:   Vector3(0, 4, 0),
  distance: 35,
  yaw:      0.25π,
  pitch:    0.18π,
  minDist:  4,   maxDist:  180,
  minPitch: -π/2 + 0.05, maxPitch: π/2 - 0.05,
}
```

### 4.2 Camera placement — `updateOrbitCamera()` lines 136–147

Spherical-to-cartesian around `orbit.target`:
```
pos = target + distance * (cos(pitch)*sin(yaw), sin(pitch), cos(pitch)*cos(yaw))
camera.lookAt(target)
```
Called once per frame from the main loop (line 747).

### 4.3 Input bindings

- **Right-click context menu suppressed** (line 152).
- **`mousedown`** (lines 154–164):
  - Bomb-mode LMB → charge (return).
  - LMB → orbit drag.
  - MMB (button 1) → pan drag (with `preventDefault` to stop browser middle-click autoscroll).
  - RMB (button 2) → pan drag.
  - *Note: `help` text advertises “MMB drag pan” but RMB also pans.*
- **`mousemove`** (window, lines 166–184):
  - Orbit: `yaw -= dx * 0.005`, `pitch += dy * 0.005`, pitch clamped (lines 171–174).
  - Pan: scale `= distance * 0.0015`. Extracts camera basis via `camera.matrix.extractBasis(right, up, _)` (line 180) and offsets `orbit.target` along screen-space right/up (lines 175–183). *Dy uses `+`, so dragging the mouse down moves the target up on screen — this matches the user’s feel of grabbing the world and pulling it.*
- **`mouseup`** (window, lines 186–191): releases charge if applicable, clears `dragging`.
- **`wheel`** (canvas, lines 193–197): `distance *= exp(deltaY * 0.001)`, clamped to `[minDist, maxDist]`. `passive: false` so `preventDefault` can stop page scroll.
- **Keyboard** (lines 687–691):
  - `R` → `resetScene()`
  - `B` → toggle bomb mode
  - `F` → `frameBuilding()` re-centers orbit on the building.

### 4.4 Behavior notes / minor quirks

- Pan basis is extracted from `camera.matrix` (world matrix), so it’s correct as long as `updateOrbitCamera` has run at least once before the first pan. It is called at line 765 before the loop starts, so this is safe.
- The third arg to `extractBasis` (line 180) is the forward vector and is intentionally discarded.
- No damping/inertia — motion stops the instant the mouse stops.
- No touch / pointer-event support — desktop mouse only.

---

## 5. Cross-Cutting Improvement List (Prioritized)

Ordered by impact-per-effort for the integration agent. Each item is concrete and references the lines above.

### P0 — High impact, low risk

1. **Reuse debris geometry + material** (lines 530, 557–558). Replace per-cube `new BoxGeometry` / `new MeshStandardMaterial` with a small pool (e.g. 3–4 shared sizes + a color-bucketed material cache mirroring `getBrickMaterial`). Removes the largest GC / dispose churn in the demo; also lets `removeBody` skip `dispose()` for debris (lines 250–251). Expected: smoother frames under heavy explosions.
2. **Stop linear scans for HUD counts**. `updateBlockCount` (513–517), `updateDebrisCount` (518–522), and `debrisCount` (545–549) each walk the entire `bodies[]`. Maintain two integer counters (`blockCount`, `debrisCount`) updated in `addBody` / `removeBody` and on type transitions. Update HUD on change instead of every 0.5 s.
3. **Add perf counters to HUD**: FPS already shown. Add **draw calls** (`renderer.info.render.calls`), **triangles** (`renderer.info.render.triangles`), **active (awake) bodies**, **sleeping bodies**, **physics ms / frame** (wrap `stepPhysics` with `performance.now()` deltas), and **debris / cap** as `n / MAX_DEBRIS`. All cheap; surfaces the cost of every other improvement.
4. **Pool bomb-flash spheres** (lines 581–600) — currently `new SphereGeometry` + `new MeshBasicMaterial` per blast. One shared geo + material, scale + fade via an effect record.

### P1 — Visible quality wins

5. **Structural variety in `buildBuilding`** (lines 451–496):
   - Vary `BRICKS_PER_ROW` / `BRICKS_PER_COL` per floor for setbacks.
   - Add interior partition walls on every 2nd floor (one wall splitting the floor in half).
   - Add a balcony slab on floor 2 or 3 (overhanging cantilever — naturally fun to collapse).
   - Lintels above doors/windows so the opening doesn’t look like a clean gap.
   - Randomize brick tone selection by position (hash of x,y,z) instead of `Math.random()` (line 418) so resets look the same — better for A/B testing perf changes.
6. **Better debris shapes**. Mix cubes with thin slabs (brick fragments), small tetrahedra (chips), and triangular wedges. Three or four pooled `BufferGeometry`s with the existing AABB physics still work because the broadphase / narrowphase are AABB-based (lines 256–348); only the visual mesh changes. Add a small random rotation on spawn and freeze rotation in physics (engine has no angular state anyway).
7. **Breakable joints / hinge-style constraints**. The current engine has no constraints. A lightweight first pass:
   - Tag adjacent bricks at build time as a “bond” pair with a `bondStrength` value.
   - Each tick, if both bonded bricks are awake and the impulse between them exceeded `bondStrength` last step, break the bond; otherwise apply a soft positional constraint pulling them together (a fraction of the penetration solver) so walls hold up under small loads.
   - Doors and slab-to-wall seams get higher `bondStrength`; this fixes the current behavior where slabs can drift before being hit.
   - Hinge variant: door bricks can be replaced by a single door body bonded along one vertical edge only — breaks into a swinging slab when shoved.
8. **Anti-aliased crosshair / aim feedback**. Cursor stays as a CSS cross (lines 28–36); add a faint ring that pulses while charging (drive it from the same `held / MAX_CHARGE` value used at line 743).

### P2 — UX polish

9. **Camera damping**. Lerp `orbit.yaw`, `orbit.pitch`, `orbit.distance`, and `orbit.target` toward target values each frame (e.g. 15 % per frame at 60 Hz). Removes the “snap” feel of the current controls (lines 171–183, 195–196).
10. **Touch + PointerEvent support**. Replace mouse listeners with `pointerdown/move/up` + pinch-to-zoom (two-pointer distance delta → `orbit.distance`).
11. **Help-text accuracy**: line 47 says “MMB drag pan” but RMB also pans (line 163). Either document it or drop RMB-pan to free RMB for, say, a quick deselect / cancel-charge.
12. **Cancel-charge with Esc or RMB** while holding LMB in bomb mode (currently the only way to abort is to fire).
13. **Aim line preview**. While charging, render a thin line or small arc from the camera-forward spawn point along `screenToWorldDir(mouseX, mouseY)` (lines 666–673). Cheap (one `Line` reused) and dramatically improves aim feel.
14. **Pause / slow-mo key** (e.g. `Space`): freezes `stepPhysics` calls (line 730) — useful for inspecting collapses and great for screenshots.
15. **Mini-axis gizmo** in a HUD corner (3 short colored lines reflecting camera orientation) — Blender-style affordance that matches the orbit-control vocabulary.

### P3 — Engine-level (larger lifts)

16. **Angular dynamics**. The engine is purely linear (`Body` has only `pos`, `vel` — lines 219–220). Adding orientation + angular velocity would let bricks tumble realistically and is a prerequisite for convincing hinge constraints (item 7).
17. **Sleeping island propagation**. Currently a body wakes only on direct contact (lines 308, 240). When a slab is destroyed, the bricks resting on it should wake immediately. Either explicitly wake everything within the explosion radius (already done at line 615 via `applyImpulse → wake`) *and* their neighbors, or implement contact islands.
18. **Replace `bodies.indexOf` + `splice`** in `removeBody` (lines 247–248). Use swap-remove (`bodies[i] = bodies[last]; bodies.pop()`) plus a stored `b._index`. Removes O(n) cost from every destroyed brick and debris expiry.
19. **Persistent broadphase cells**. `broadphase()` (lines 256–282) rebuilds the entire `Map` every step and allocates new arrays. Reuse a flat `Int32Array` keyed grid; skip sleeping-only cells.
20. **Tighten shadow camera dynamically**. The ortho frustum is fixed at ±30 (lines 86–89). As debris flies, anything outside the frustum loses shadows. Either accept that (current behavior) or expand to ±50 — but only after measuring the cost via the new perf counters (item 3).

---

## Quick reference: key line ranges

| Subsystem | Lines |
|---|---|
| HUD markup + styles | 6–53 |
| Renderer / scene / lights | 65–93 |
| Ground + grid | 95–105 |
| HUD JS refs | 107–114 |
| Orbit camera state + update | 125–147 |
| Orbit mouse / wheel input | 152–197 |
| Physics constants | 202–210 |
| `Body` class | 212–242 |
| Broadphase | 254–282 |
| Narrowphase / resolve | 284–348 |
| Ground resolve | 350–362 |
| `stepPhysics` | 364–394 |
| Building constants + materials | 402–432 |
| `makeBlock` | 434–449 |
| `buildBuilding` | 451–496 |
| `clearBuilding` | 498–508 |
| Block / debris counts | 513–522 |
| Bomb spawn | 532–543 |
| Debris spawn | 545–575 |
| Explosion | 579–625 |
| Bomb tick | 627–640 |
| Bomb mode + charge | 645–685 |
| Keybinds (R/B/F) | 687–698 |
| Reset scene | 700–713 |
| Main loop | 718–766 |
