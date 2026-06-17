# physics4.html — Physics & Performance Audit (Baseline)

This document maps the current physics engine in `physics4.html` (769 lines) to exact line ranges, then identifies measured/likely performance hotspots and a prioritized list of physics improvements. It is intended as the baseline reference for downstream optimization workers.

---

## 1. Engine Architecture Overview

The demo ships a hand-rolled rigid-body engine on top of three.js. All bodies are treated as axis-aligned boxes (AABB) with linear velocity only — no angular dynamics, no rotation integration, no continuous collision detection. The simulator runs a fixed 60 Hz step with an accumulator, an XZ spatial-hash broadphase, AABB SAT narrowphase, impulse + Coulomb-clamped friction resolution, sleeping/waking, and radial-impulse explosions that spawn pooled debris up to a hard cap.

| Subsystem | Lines |
|---|---|
| Tunable constants (gravity, restitution, friction, damping, sleep, dt, debris cap) | 202–210 |
| `Body` class (state, AABB accessors, wake, applyImpulse) | 212–242 |
| Body list + add/remove (with per-debris geometry/material dispose) | 244–252 |
| Broadphase — XZ spatial hash | 254–282 |
| `aabbOverlap` test | 284–288 |
| `resolveCollision` — AABB SAT + impulse + friction | 290–348 |
| `resolveGround` — implicit infinite ground plane | 350–362 |
| `stepPhysics` — integrate, ground, broadphase, 3 solver iters, sleep, GC, mesh sync | 364–394 |
| Building construction (shared geos, material cache, 5 floors) | 398–511 |
| Block / debris counters (full-list scan) | 513–522 |
| Bombs, debris, explosions | 524–640 |
| Bomb mode + input | 642–713 |
| Main loop (accumulator, fixed step, effects, FPS HUD) | 715–766 |

### 1.1 Tunable constants (lines 202–210)

```
GRAVITY      = -22        // m/s² on Y
RESTITUTION  = 0.05       // near-inelastic
FRICTION     = 0.55       // Coulomb-clamped tangential impulse
LINEAR_DAMP  = 0.01       // per-second, applied as (1 - LIN*dt)
SLEEP_LIN    = 0.05       // |v| threshold for sleep candidacy
SLEEP_TIME   = 0.5        // seconds below threshold before sleep
FIXED_DT     = 1/60       // physics tick
MAX_STEPS    = 4          // substeps per frame (spiral-of-death guard)
MAX_DEBRIS   = 220        // debris body cap
```

### 1.2 `Body` (lines 212–242)

Each body stores `mesh`, `size`, `half = size/2`, `mass`, `invMass`, `pos` (cloned from mesh at construction), `vel`, `isStatic` (when `mass === 0`), `sleeping`, `sleepTimer`, `onGround`, `health` / `maxHealth`, `isDebris`, `isBomb`, `life`, `age`, and a monotonic `id`.

AABB extents are computed on demand via six getters (`aabbMinX`/`aabbMaxX`/…) that always recompute `pos ± half` — no cached bounds (lines 233–238). This is one of the largest hotspots (see §3).

`applyImpulse(j)` does `vel += j * invMass` and wakes the body (line 240).

### 1.3 Broadphase — XZ spatial hash (lines 254–282)

- `CELL = 3` world units.
- Every step allocates a fresh `Map` and per-cell `Array`s (lines 257–266).
- Each body is inserted into every XZ cell its AABB overlaps; Y is ignored (the building is a vertical stack, so this *significantly* hurts — a column of bricks across 5 floors collapses into the same cells).
- Hash key: `x * 73856093 ^ z * 19349663` (line 262). This is a 32-bit XOR with no modulus and can collide (different `(x,z)` mapping to the same key) — there is no bucket disambiguation; colliding cells silently merge their occupants into one bucket, producing extra false pairs.
- Pair deduplication uses a `Set` of integer keys `min(id)*100000 + max(id)` (lines 275–277). Allocates a fresh Set per step; key collisions possible once `id ≥ 100000`.
- Static-vs-static and sleeping-vs-sleeping pairs are skipped (lines 273–274), but static-vs-sleeping and sleeping-vs-static *are* still enumerated and later resolved — wasted work, since neither can change.

### 1.4 Narrowphase + resolution (lines 284–348)

- `aabbOverlap` is six comparisons on the on-demand AABB getters (lines 284–288) — each call re-derives 6 floats per body it touches.
- `resolveCollision` (lines 290–348):
  - Recomputes overlap manually via penetration on each axis (`px`, `py`, `pz`).
  - Picks the minimum-penetration axis as the contact normal (classic AABB SAT MTV, lines 300–303).
  - Positional correction split by inverse-mass ratio (lines 310–315).
  - Normal impulse with restitution (lines 317–330).
  - Tangential impulse with `Math.hypot` (line 335) and Coulomb clamp (lines 336–347).
  - Both bodies are unconditionally woken on every contact (line 308) — even resting stacks. This defeats sleeping for the entire building (see §3).
- `resolveGround` (lines 350–362): clamps `minY` to 0, reflects `vy` with restitution, scales `vx`/`vz` by `(1 - FRICTION*0.5)`. Sets `onGround = true` but never sets it back to false except at the top of the next integration step.

### 1.5 `stepPhysics` (lines 364–394)

One fixed tick performs, in order:

1. Integrate active dynamic bodies: age, life expiry mark, gravity, linear damping, `pos += vel*dt`, clear `onGround` (lines 365–374).
2. Ground resolution for every non-static non-sleeping body (line 375).
3. Broadphase (allocates Map, Sets, arrays, pair list — line 376).
4. **3 solver iterations** over the pair list (lines 377–379) — fixed iteration count regardless of contact count or stack height.
5. Sleep state machine: if `speed < SLEEP_LIN && onGround`, accumulate `sleepTimer`, sleep when `> SLEEP_TIME` (lines 380–389). `Body.vel.length()` calls `Math.sqrt` even when a squared comparison would suffice.
6. Reverse-walk `_dead` removal with `splice` (lines 390–392) — O(n) per removal due to `bodies.indexOf` + `splice` inside `removeBody` (lines 247–248).
7. Mesh sync: every body copies `pos → mesh.position` every tick (line 393), including sleeping bodies.

### 1.6 Building (lines 398–511)

- Shared `brickGeo` / `brickGeoRot` `BoxGeometry`s for the two brick orientations (lines 411–412); ad-hoc geometries only when sizes don't match (line 438). Good — but allocations still happen for slabs/roof (different sizes).
- `getBrickMaterial()` (lines 416–430): 5-tone palette via a `Map` cache. Sound material-batching strategy, but each brick is still its own `Mesh` (no `InstancedMesh`), so draw calls scale with brick count.
- Building config: 5 floors × (7×6 footprint) × 4 rows = on the order of 300–400 brick bodies + 5 slabs + roof. Confirm via HUD `blockCount` at startup.
- `clearBuilding` (lines 498–508) has a confusing predicate `!b.isStatic === true` (line 501) — semantically removes only non-static bodies, but the comment says "remove all non-ground bodies", which is wrong because the building's own bricks are dynamic (`mass > 0` ⇒ `isStatic = false`). On a reset path this works only because the subsequent `buildingGroup.children` purge tears the meshes out anyway.

### 1.7 Debris + explosions (lines 524–640)

- Bomb body: `Sphere(0.35, 12, 8)` geometry, shared material (lines 528–529, 532–543). The body uses a `Box(0.7,0.7,0.7)` AABB even though the visual is a sphere.
- `spawnDebris` (lines 551–575):
  - Calls `debrisCount()` which walks the entire body list every spawn (lines 545–549, 553).
  - Allocates **fresh `BoxGeometry` and fresh `MeshStandardMaterial` for every debris chunk** (lines 557–558). Disposed individually on removal (lines 250–251). High allocation churn under sustained explosions.
- `explode` (lines 579–625):
  - Allocates a new flash sphere geo + material per detonation (lines 581–582); cleaned up after 0.4 s in the `effects` array.
  - Linear scan of all bodies for radius test (lines 604–619). Uses `r2 = radius²` for the cull, then `Math.sqrt` for normalization (line 611).
  - Allocates a `THREE.Vector3` per affected body for the impulse, calls `.normalize()` (which is another sqrt) (line 614). Two sqrt's per affected body.
  - Builds `toRemove` array, then for each killed body: `spawnDebris(...)` (more allocations) + `removeBody(...)` (O(n) splice). For an explosion damaging 50 blocks that's 50 × `indexOf`+`splice` = O(n²) on the bodies array.
- `tickBombs` (lines 627–640): **O(B × N)** nested scan — every bomb tests every other body via `aabbOverlap`, with no broadphase reuse. Triggers on first hit, on ground touch, or near end-of-life.

### 1.8 Main loop (lines 715–766)

- Accumulator with `MAX_STEPS = 4`: at 60 Hz target a single rAF that exceeds 4/60 ≈ 66.7 ms (i.e., sustained <15 fps) silently zeros the accumulator — wall clock and sim clock diverge (line 735).
- Effects ticked on a sliced clone (line 738) — `slice()` per frame allocates.
- FPS HUD updates every 0.5 s and calls `updateDebrisCount()` (another full-list scan).

---

## 2. Static measurements / fixed costs

| Quantity | Value | Source |
|---|---|---|
| Brick bodies at startup | ~300–400 dynamic + 5 slabs + 1 roof + 0 ground (ground is not a body) | building loop 451–496 |
| Draw calls per brick | 1 (no instancing) | line 440 |
| Shared materials | 5 brick tones + slab + roof + bomb + debrisMat (debris re-allocates) | 416–430, 431–432, 529–530 |
| Shared geometries | `brickGeo`, `brickGeoRot`, slab/roof reuse `BoxGeometry` for matching sizes | 411–412, 436–438 |
| Solver iterations per substep | 3 (hard-coded) | 377 |
| Max substeps per frame | 4 | 209, 729 |
| Debris cap | 220 | 210, 553 |
| Shadow map | 1024² PCF, ortho 60×60×149 frustum | 70, 85–92 |
| Pixel ratio cap | 1.5 | 67 |

---

## 3. Performance Hotspots (likely, ranked)

With no in-app profiler the ranking is based on Big-O × call frequency × allocation pressure. Order is the recommended attack order.

### H1 — Broadphase is 2D (XZ) on a vertical building. **Highest impact.**
*Lines 254–282.* The building is a 5-floor stack with bricks vertically aligned. Hashing on XZ only forces every brick in a vertical column (≈20 bricks) to share buckets, then the inner `O(k²)` pair enumeration explodes. Expected pair count is several × higher than a true 3D hash would produce. Fix: hash on `(x, y, z)` cells with the same scheme, or sort-and-sweep on Y after XZ.

### H2 — Sleeping is defeated by unconditional wake on contact.
*Line 308 in `resolveCollision`.* Every solver iteration wakes both bodies on every overlap, including resting stacks. A static building therefore never settles into the cheap sleeping path; the sleep state machine (lines 380–389) keeps re-arming on the next contact pass. Fix: only wake when relative speed along the normal exceeds a threshold, or when the impulse magnitude is non-trivial. Also: do not wake the static partner (statics don't need waking).

### H3 — Allocation churn in hot loops.
Per **physics tick** the engine allocates:
- one `Map` and one `Set` plus N small `Array`s (broadphase, 257–266, 269);
- one pair array of length up to `O(k²)` (line 268);
- per pair: two-tuple `[a,b]` arrays (line 278).

Per **explosion**:
- one flash geometry + material + mesh (581–584);
- per affected body: a `THREE.Vector3` (line 614) and a `.normalize()` allocation-free but sqrt-heavy operation;
- per killed body: 2–4 fresh `BoxGeometry` + fresh `MeshStandardMaterial` (557–558).

The GC pauses these produce show up as frame spikes, not steady-state slow-down. Fix: keep a persistent `Map`, persistent reusable arrays for cell occupants, a flat pair buffer (`Float64Array`/typed pair list of ids), and a debris pool of pre-allocated meshes with a shared material (re-tint via `mesh.material = sharedTintedMaterial[i]` from a small palette, same trick as bricks).

### H4 — `Math.sqrt` and `Vector3.length()` where squared compares would do.
- Sleep check `b.vel.length() < SLEEP_LIN` (line 382) → compare `vel.lengthSq() < SLEEP_LIN²`.
- Tangential friction `Math.hypot(tvx,tvy,tvz)` (line 335) — unavoidable for the *direction*, but the early-out `if (tlen > 1e-4)` can use `tlenSq > 1e-8` first and short-circuit before the sqrt.
- Explosion: line 611 `Math.sqrt(d2)` plus line 614 `.normalize()` → one sqrt is enough; build the unit vector inline as `(dx/d, dy/d+0.4/d_eff, dz/d)`.
- `aabbOverlap` itself is sqrt-free, but it is called from `tickBombs` (627–640) in O(B·N).

### H5 — Bomb collision check is O(B × N).
*Lines 627–640.* Each tick, each bomb scans the entire body list. With even a handful of bombs and ~400 bodies this is the second-largest narrowphase cost after the main solver. Fix: piggyback on the broadphase pair list — a bomb's overlap candidates are already in `pairs`.

### H6 — Redundant AABB recomputation.
Each access to `aabbMinX()`/`aabbMaxX()`/… (lines 233–238) recomputes `pos.x ± half.x`. In a single broadphase pass each body's bounds are read 6 times for cell insertion (line 259–260) plus 6 times per overlap test in `aabbOverlap` (lines 285–287) plus implicitly in `resolveCollision` (which fortunately re-derives via `dx/dy/dz` and halves, not the getters). Cache `minX/maxX/minY/maxY/minZ/maxZ` on the body, recompute once per integration step right after `pos += vel*dt`. Statics + sleeping bodies don't recompute at all.

### H7 — Fixed solver iteration count (3) regardless of stack depth.
*Line 377.* Tall stacks (5 floors) need more iterations to dissipate penetration; isolated bodies need fewer. With 3 iterations, the floor under a heavy slab can stay penetrated for several frames, manifesting as visible jitter and "sponginess". Fix: either bump to 4–6 for the stacked baseline, or do early-out when no positional correction exceeds a tolerance.

### H8 — `removeBody` is O(n) and called in bulk after explosions.
*Lines 246–252.* `indexOf` + `splice` is fine for one-offs, terrible after a 50-body kill. Fix: mark `_dead` and do a single end-of-tick compaction (swap-with-last + pop). Already partly done at lines 390–392, but `removeBody` is *also* called directly from `explode` (line 622) outside the compaction path.

### H9 — Debris lifecycle.
Five-second life (line 567), 220 cap (line 210), per-piece geometry+material disposal (lines 250–251). Two issues: (a) `debrisCount()` scan on every spawn (line 553) is O(n) and is called inside a hot loop after explosions; (b) the cap is enforced *only at spawn time* — already-airborne debris can outlive the cap during a chain reaction because nothing removes excess. Track `debrisAlive` as an integer maintained in add/remove. Optionally remove oldest first when full (FIFO) so chains of explosions don't get silently swallowed.

### H10 — Mesh sync for sleeping bodies.
*Line 393.* `for (const b of bodies) mesh.position.copy(b.pos)` runs for every body every tick, including statics (whose position never changes) and sleeping bodies (whose `pos` is unchanged since they slept). Skip both.

### H11 — Pair deduplication key collisions.
*Line 275.* `a.id * 100000 + b.id` collides once any id reaches 100000. In a long session with continuous debris churn this is reachable. Use a `Set` keyed by a string `a.id+","+b.id` (slow) or split into nested `Map<number, Set<number>>`, or just accept duplicate pair work and remove the Set entirely (the dedup cost may exceed the duplicate-work cost for typical bucket sizes).

### H12 — Per-frame `effects.slice()`.
*Line 738.* Allocates an array every frame to safely iterate while items remove themselves. Reverse-iterate the original array instead.

### H13 — Spiral-of-death zero-out.
*Line 735.* Hiding sim time when frame time spikes makes long pauses (alt-tab return, GC) silently lose physics time. Acceptable for a toy demo, but if substep tuning is increased (see P-2 below), consider scaling `dt` instead of dropping it.

### H14 — Shadow map cost vs. coverage.
*Lines 85–92.* 1024² PCF is reasonable, but the 60×60 ortho frustum is narrower than the 7×6 brick footprint × 5 floors of debris radius after explosions; debris flying outside ±30 loses shadows abruptly. Either widen the frustum (cheap visually, free perf) or tighten it to the building only and disable shadows on debris (debris currently doesn't cast shadows — `castShadow` defaults to false at line 442 — but slabs do, line 461).

---

## 4. Prioritized Physics Improvement Plan

Priorities are ranked by `(expected frame-time win) × (implementation risk inverse)`. P-1 through P-4 should land first; they are the high-leverage, low-risk wins.

### P-1. Cache AABB extents on the body, recompute once per tick.
- Replace `aabbMinX()`/etc. with cached `minX, maxX, minY, maxY, minZ, maxZ` fields.
- Recompute at the end of integration (after `pos += vel*dt`) for active bodies only.
- Statics compute once at construction.
- `aabbOverlap` reads cached fields.
- Lines touched: 212–242, 256–266, 284–288.
- Expected win: 10–20% of physics step time, eliminates ~12 multiplies/adds per body per overlap test.

### P-2. 3D spatial hash (or XZ + Y-sort).
- Extend the hash to `(x,y,z)` with the same prime-XOR pattern using a third prime (e.g. `83492791`).
- Halves expected bucket size for the 5-floor stack; pair count drops roughly linearly.
- Lines touched: 254–282.
- Expected win: 30–50% of broadphase + narrowphase time on the stacked-building baseline.

### P-3. Fix sleeping wakes.
- In `resolveCollision` (line 308), only wake when `|velAlongN| > SLEEP_LIN * 2` *and* don't wake static bodies.
- In `applyImpulse` (line 240) wake unconditionally (this is the explosion path — correct).
- Add a `lengthSq` sleep check (line 382) so the comparison is `vel.lengthSq() < SLEEP_LIN*SLEEP_LIN`.
- Skip sync of sleeping/static meshes (line 393).
- Lines touched: 240, 308, 382, 393.
- Expected win: 40–70% of total physics cost once the building has settled — a settled tower should approach zero physics cost.

### P-4. Persistent broadphase buffers + sweep-and-prune option.
- Reuse a module-level `Map` for cells (`.clear()` per step) and a per-cell pooled array.
- Reuse a flat pair buffer (two parallel `Uint32Array`s of `aId/bId`, grown geometrically).
- Drop the dedup `Set`; either accept duplicate-pair work or switch to sweep-and-prune on the X axis (typically wins over hashing for ≤2000 bodies).
- Lines touched: 254–282.
- Expected win: removes a major GC source; 5–15% steady frame-time, larger spike reduction.

### P-5. Debris pool with shared materials.
- Pre-allocate `MAX_DEBRIS` boxes at startup, each with a shared `MeshStandardMaterial` per tint bucket (reuse the brick palette).
- `spawnDebris` pulls from a free list, recycles on `removeBody` instead of disposing.
- Maintain `debrisAlive` as an integer; drop the `debrisCount()` scan (line 553) and the one at line 519.
- FIFO eviction when full so explosions never get suppressed.
- Lines touched: 545–575, 246–252, 513–522.
- Expected win: removes the biggest GC spike source during heavy demolition; 1–3 ms saved on explosion frames.

### P-6. Bomb collision via broadphase pairs.
- Drop the O(B·N) loop (lines 627–640).
- During pair iteration, if either body is a bomb and they overlap, mark the bomb for detonation.
- Lines touched: 627–640, with a small additional flag check inside the resolver loop or a dedicated post-pass over `pairs`.
- Expected win: scales bomb cost from O(B·N) to ~O(B·k) where k ≈ avg pair density per bomb cell.

### P-7. Compact-remove instead of indexOf+splice.
- Add a single end-of-tick compaction: swap-with-last + pop for every `_dead`.
- Make `removeBody` set `_dead` only (when called outside the compaction).
- Apply to explosion kills (line 622) too.
- Lines touched: 246–252, 390–392, 620–623.
- Expected win: explosion frames drop from O(killed × N) to O(killed) + one O(N) compaction.

### P-8. Tune substeps and solver iterations.
- Bump solver iterations to 4 for the first 0.5 s after an explosion (when stacks are unstable), drop back to 2 once everything is sleeping.
- Consider `MAX_STEPS = 5` so a 100 ms frame doesn't desync sim clock.
- Lines touched: 377, 729, 735.
- Expected win: better stack stability (visible quality) at small cost.

### P-9. Sqrt audit.
- Lines 335, 382, 611, 614: replace with `lengthSq` early-outs and inline the single sqrt for normalize.
- Expected win: small but free (~2–5% of solver inner loop).

### P-10. Instanced rendering for bricks.
- Replace per-brick `Mesh` with one `InstancedMesh` per material tone (6 instanced meshes total).
- Update `instanceMatrix` only for non-sleeping bodies (the sync optimization in P-3 already feeds this).
- Lines touched: 411–449, 393, plus removal-on-destroy bookkeeping.
- Expected win: large GPU/CPU-side win on draw calls — currently every brick is a draw call (~400). InstancedMesh collapses to 5–6.
- Risk: medium — requires reworking the brick → body → mesh mapping (instance index instead of mesh).

### P-11. Effects array iteration.
- Replace `effects.slice()` (line 738) with a reverse loop and in-place removal.
- Trivial; removes a small per-frame allocation.

### P-12. Per-explosion flash reuse.
- Keep one flash mesh, show/scale/fade it per explosion; if multiple overlap, queue or reset.
- Lines touched: 579–600.
- Small steady-state win, mostly a code-cleanliness improvement.

---

## 5. What this audit explicitly does *not* change

- The physics is still AABB-only — no rotation. Adding angular dynamics is a separate, larger workstream and is out of scope for performance work.
- Continuous collision detection: bombs at high power can tunnel through thin slabs at large `power`. Not addressed here; a swept-AABB pass at integration time is the standard fix but is its own task.
- Material/visual upgrades (normal maps, bricks-as-instanced + per-instance color) are part of the graphics workstream, not this audit.

---

## 6. Suggested measurement harness (for downstream workers)

Before/after numbers should be captured with:

1. A fixed reset scene + a fixed bomb script (e.g. 3 bombs at t=1.0, 2.0, 3.5 s from canonical camera positions) — deterministic enough by seeding `Math.random` once via a tiny PRNG.
2. Per-frame timers around `stepPhysics` (broadphase, integrate, solver, sleep, sync) and around `renderer.render`.
3. HUD additions: physics ms, render ms, pair count, awake count, debris count.
4. Record the first 10 s of the scripted run; report median + p95 frame time and per-section ms.

With that harness, each P-item above can be landed and measured independently.
