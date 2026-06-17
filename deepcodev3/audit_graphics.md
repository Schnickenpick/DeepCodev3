# physics4.html — Graphics & Renderer Audit

**File:** `physics4.html` (769 lines)
**Scope:** Renderer config, lighting, shadow setup, materials, geometry sharing, camera/controls, scene atmosphere, and improvement opportunities.

---

## 1. Renderer Configuration

### Current setup (lines 66–71)

```js
const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
document.body.appendChild(renderer.domElement);
```

| Setting | Value | Line |
|---|---|---|
| `antialias` | `true` (browser MSAA on default framebuffer) | 66 |
| `powerPreference` | `'high-performance'` | 66 |
| `setPixelRatio` cap | `min(devicePixelRatio, 1.5)` | 67 |
| `shadowMap.enabled` | `true` | 69 |
| `shadowMap.type` | `THREE.PCFShadowMap` (not PCFSoft) | 70 |
| Tone mapping | **NOT SET** — defaults to `THREE.NoToneMapping` | — |
| `toneMappingExposure` | **NOT SET** — defaults to `1.0` | — |
| Output color space | **NOT SET** — defaults to `THREE.SRGBColorSpace` in r152+ (r160 in use) | 58 |
| `physicallyCorrectLights` / `useLegacyLights` | **NOT SET** — r160 default is legacy lights = `false` (i.e., physically correct) | — |

### Observations
- `antialias: true` engages browser-native MSAA on the default framebuffer; fine while no post-processing pipeline exists, but disables itself the moment an `EffectComposer` is added.
- Pixel-ratio cap of `1.5` is a reasonable perf/quality tradeoff for retina screens but is hard-coded and not adaptive.
- Shadow map type is plain `PCFShadowMap` — visibly aliased shadow edges compared to `PCFSoftShadowMap` or VSM.
- No explicit tone mapping → high-intensity values clip immediately; the bomb flash (`MeshBasicMaterial`, opacity 0.75, line 582) and any future HDR work has no headroom.
- No explicit `outputColorSpace` — relies on r160 sRGB default, which is correct but worth pinning.

---

## 2. Scene Background & Atmosphere

### Current setup (lines 73–75)

```js
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x2b2b2b);
scene.fog = new THREE.Fog(0x2b2b2b, 120, 400);
```

| Setting | Value | Line |
|---|---|---|
| Background | Flat color `0x2b2b2b` (dark grey) | 74 |
| Fog | Linear `Fog`, near 120, far 400, color matches bg | 75 |
| Environment map (`scene.environment`) | **NOT SET** | — |

### Observations
- Flat background + matching fog gives a featureless grey void. No sky, no horizon, no IBL.
- Linear fog near/far span (120 → 400) is far beyond the camera's working distance (`orbit.maxDist = 180`, line 131); fog effectively only kicks in at the edges of zoom-out. Currently underused as an atmospheric tool.
- Without `scene.environment`, all `MeshStandardMaterial` instances receive zero indirect lighting — surfaces facing away from the sun rely entirely on the hemisphere light (line 80), which is why shaded faces read as muddy.

---

## 3. Lighting

### Current setup (lines 80–93)

```js
const hemi = new THREE.HemisphereLight(0xddeeff, 0x333333, 0.55);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffffff, 1.0);
sun.position.set(40, 70, 30);
sun.castShadow = true;
sun.shadow.mapSize.set(1024, 1024);
sun.shadow.camera.left = -30;
sun.shadow.camera.right = 30;
sun.shadow.camera.top = 30;
sun.shadow.camera.bottom = -30;
sun.shadow.camera.near = 1;
sun.shadow.camera.far = 150;
sun.shadow.bias = -0.0005;
scene.add(sun);
```

| Light | Type | Color/Intensity | Line |
|---|---|---|---|
| `hemi` | `HemisphereLight` | sky `0xddeeff`, ground `0x333333`, intensity `0.55` | 80 |
| `sun` | `DirectionalLight` | white, intensity `1.0`, pos `(40,70,30)` | 82–83 |

### Shadow camera (sun)
| Property | Value | Line |
|---|---|---|
| `mapSize` | `1024 × 1024` | 85 |
| Ortho frustum L/R | `-30 / +30` | 86–87 |
| Ortho frustum T/B | `+30 / -30` | 88–89 |
| Near / Far | `1 / 150` | 90–91 |
| `bias` | `-0.0005` | 92 |
| `normalBias` | **NOT SET** (defaults to `0`) | — |
| `radius` | **NOT SET** (defaults to `1`) | — |

### Observations
- A 60×60 ortho footprint over a building whose plan is roughly 7×6 units and total height ~10.4 units (`FLOORS=5`, `FLOOR_H=2`, lines 405/409) — frustum is reasonably tight for the building but generous; could be tightened further or widened to also cover debris travel.
- 1024² is the floor for usable shadow resolution on a building this size; with the ortho width of 60, texel footprint is ~5.86 cm — visibly soft/aliased. Bumping to 2048² halves that.
- No `normalBias`. With `PCFShadowMap` and a negative `bias`, brick edges can self-shadow / show acne under glancing sun angles. `normalBias ~0.02` typically helps without requiring deeper `bias`.
- Hemisphere ground tone `0x333333` is very dark; combined with no environment map, undersides of slabs go nearly black.
- No fill light or rim light — single sun + hemi makes the scene visually flat.

---

## 4. Shadow Casting / Receiving Policy

| Mesh | castShadow | receiveShadow | Reference |
|---|---|---|---|
| Ground plane | — (default false) | `true` (line 100) | 98–101 |
| Bricks (walls) | `false` (default, `opts.castShadow` omitted in calls at 475, 478, 487, 488) | `true` (line 443) | 434–449 |
| Floor slabs (floors 1+) | `true` (line 461, `castShadow: true`) | `true` (line 443) | 460–461 |
| Roof slab | `true` (line 495) | `true` (line 443) | 494–495 |
| Bombs | not set → false | not set → false | 533 |
| Debris boxes | not set → false | not set → false | 559 |
| Explosion flash | `MeshBasicMaterial`, no shadows | n/a | 581–585 |
| Grid helper | n/a | n/a | 103–105 |

### Observations
- **Bricks do NOT cast shadows.** Only slabs and the roof do. This was an explicit perf decision (per project memory) but means the wall geometry is shadow-less in self-shadowing terms — windows/doors cut into walls produce no shadow detail on the floor.
- Debris and bombs casting shadows would add a lot of small-occluder cost; current omission is reasonable.
- All meshes set `receiveShadow = true` indiscriminately (line 443), including bombs and the slabs themselves — fine cost-wise but a few of these never have anything shadow them.

---

## 5. Materials & Color Palette

### Brick 5-tone palette (lines 416–430)

```js
const tones = [
  new THREE.Color(0.62, 0.27, 0.21),
  new THREE.Color(0.58, 0.24, 0.19),
  new THREE.Color(0.66, 0.30, 0.24),
  new THREE.Color(0.54, 0.22, 0.18),
  new THREE.Color(0.60, 0.26, 0.20),
];
const m = new THREE.MeshStandardMaterial({ color: tones[idx], roughness: 0.95 });
materialCache.set('brick' + idx, m);
```

- Five lazily-cached `MeshStandardMaterial` instances keyed by `'brick0'..'brick4'`. Each brick mesh shares one of five materials → good for batching / state changes.
- `roughness: 0.95`, `metalness` defaulted to `0` (none set) — dielectric brick look. No `map`, `normalMap`, `roughnessMap`, or `aoMap` → all visual variation is per-color only.

### Other materials

| Name | Type | Properties | Line |
|---|---|---|---|
| `groundMat` | `MeshStandardMaterial` | `color 0x3a3a3a`, `roughness 1` | 97 |
| `slabMat` | `MeshStandardMaterial` | `color 0x999999`, `roughness 0.9` | 431 |
| `roofMat` | `MeshStandardMaterial` | `color 0x555555`, `roughness 0.9` | 432 |
| `bombMat` | `MeshStandardMaterial` | `color 0x111111`, `roughness 0.4`, `metalness 0.6` | 529 |
| `debrisMat` | `MeshStandardMaterial` | `color 0x886655`, `roughness 0.95` | 530 |
| Flash | `MeshBasicMaterial` | `color 0xffaa33`, transparent, opacity 0.75 | 582 |

### Observations
- **Debris materials are NOT cached.** `spawnDebris` (line 558) allocates a **fresh** `MeshStandardMaterial` per debris piece (typically 2–4 per destroyed block, up to `MAX_DEBRIS = 220`). At cap that's up to ~220 unique materials → 220 unique shader-state buckets → uniform uploads & no batching.
- `getBrickMaterial` (line 416) picks a tone randomly *per brick at build time*; the random pick is fine, but the function allocates only on first miss (good).
- No PBR maps anywhere — scene is entirely color + roughness. `MeshStandardMaterial` cost is paid without visual gain over `MeshLambertMaterial` / `MeshPhongMaterial`.
- `bombMat.metalness 0.6` with no environment map means the metallic component contributes essentially nothing (metals require IBL to look metallic).

---

## 6. Geometry Sharing

### Shared geometries

| Geometry | Size | Line |
|---|---|---|
| `brickGeo` | `BoxGeometry(1.0, 0.5, 0.6)` | 411 |
| `brickGeoRot` | `BoxGeometry(0.6, 0.5, 1.0)` (sideways for end-walls) | 412 |
| `bombGeo` | `SphereGeometry(0.35, 12, 8)` — low-poly | 528 |
| `groundGeo` | `PlaneGeometry(600, 600)` | 96 |

### Per-instance geometry

| Geometry | Where | Line |
|---|---|---|
| Slabs / roof | `new THREE.BoxGeometry(sx, sy, sz)` in `makeBlock` fallback | 438, used at 460, 494 |
| Debris cubes | `new THREE.BoxGeometry(s, s, s)` per debris — **not shared** | 557 |
| Explosion flash | `new THREE.SphereGeometry(radius * 0.7, 16, 12)` per blast | 581 |

### Observations
- Wall bricks all share `brickGeo` or `brickGeoRot` ✅ — good.
- Floor slabs and roof each allocate their **own** `BoxGeometry` (line 438) because their dimensions don't match the brick presets. Only 6 such allocations total (5 floors + roof), negligible.
- **Debris allocates fresh geometry per piece** (line 557). At 220 debris cap that's 220 box buffers in memory; combined with the 220 fresh materials (above) this is the biggest avoidable allocation in the scene.
- Bomb sphere is shared and uses very low segments (12×8) — appropriate for a small projectile.
- Each `makeBlock` mesh is a separate `THREE.Mesh` — no `InstancedMesh` is used anywhere. A fully-built tower has on the order of (5 floors × 4 rows × ~20 bricks/row) ≈ 400+ draw calls before slabs, debris, bombs.

---

## 7. Camera & Orbit Controls

### Camera (line 77)

```js
const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.1, 1000);
```

| Property | Value |
|---|---|
| FOV | 50° |
| Aspect | window |
| Near / Far | 0.1 / 1000 |

### Orbit state (lines 125–134)

```js
const orbit = {
  target: new THREE.Vector3(0, 4, 0),
  distance: 35,
  yaw: Math.PI * 0.25,
  pitch: Math.PI * 0.18,
  minDist: 4,
  maxDist: 180,
  minPitch: -Math.PI/2 + 0.05,
  maxPitch: Math.PI/2 - 0.05,
};
```

- Custom orbit (Blender-style), not `OrbitControls` — pure spherical-coord rig in `updateOrbitCamera` (lines 136–147).
- Pan uses screen-space basis from `camera.matrix.extractBasis` scaled by `orbit.distance * 0.0015` (lines 177–182).
- Wheel zoom is exponential: `Math.exp(e.deltaY * 0.001)` (line 195).
- `F` reframes (lines 693–698): target `(0, FLOORS*FLOOR_H*0.5, 0)`, distance 35, yaw `π·0.25`, pitch `π·0.18`.

### Observations
- Near plane `0.1` is fine; far plane `1000` is overkill given `maxDist=180` and fog far `400` — could be tightened to `~500` to improve depth precision marginally.
- Camera FOV of 50° is a sensible mid-tele framing.
- No damping/inertia on orbit/pan/zoom — feel is crisp but stops dead. Could add easing for cinematic feel.
- No frustum-culling concerns in the controls themselves; three.js handles per-mesh culling automatically (every `Mesh` has `frustumCulled = true` by default).

---

## 8. Misc Visual Elements

- `GridHelper(200, 40, 0x555555, 0x444444)` at `y=0.01` (lines 103–105) — adds a few hundred line segments, very cheap. Reads as “diagnostic” more than aesthetic; could be hidden in-screen for screenshots.
- Explosion flash uses transparent `MeshBasicMaterial` (line 582); fine. Geometry/material disposed after expiry (lines 594–595) ✅.
- Bombs use `MeshStandardMaterial` with metalness 0.6 but no env map (line 529) — appears matte black.

---

# Improvement Opportunities

Grouped by impact and difficulty. Line references point at the code that would change.

## A. Quick wins (low effort, visible improvement)

### A1. Switch to PCFSoftShadowMap
Line 70: `renderer.shadowMap.type = THREE.PCFShadowMap;` → `THREE.PCFSoftShadowMap`.
Cost: negligible. Result: noticeably softer shadow edges.

### A2. Add `normalBias` to the sun shadow
After line 92 add `sun.shadow.normalBias = 0.02;`. Reduces shadow acne on brick faces and lets you relax the negative `bias` toward `-0.0001`, sharpening contact shadows.

### A3. Enable tone mapping
After line 70 add:
```js
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
```
Gives the bomb flash and any future bright sources headroom; warms the overall image. Pair with slightly brighter sun (e.g., `1.4`) to compensate.

### A4. Pin output color space
After line 70 add `renderer.outputColorSpace = THREE.SRGBColorSpace;` — explicit even though it's the r160 default.

### A5. Bump shadow map to 2048²
Line 85: `sun.shadow.mapSize.set(2048, 2048);`. Quadruples shadow memory (still only ~16 MB) and roughly halves visible shadow stair-stepping. Combine with tightening the ortho frustum to `±20` on X/Z and far `100` to claw back texel density.

### A6. Tighten far plane / fog
Line 77 far: `1000` → `500`. Line 75 fog: pull near to `~50` and far to `~200` so the dark void recedes into atmospheric falloff during zoom-out.

### A7. Cache debris materials & geometry
Lines 557–558: instead of `new BoxGeometry(s,s,s)` and a new `MeshStandardMaterial` per piece, pick from a small pool (e.g., 3 size buckets × 5 tones = 15 shared (geo, mat) pairs). Eliminates the 220-allocation worst case.

## B. Mid-effort, big visual upgrades

### B1. Environment map / IBL
Add a `PMREMGenerator` + `RoomEnvironment` (built into three.js examples):
```js
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
```
Immediate gains: bomb metalness reads correctly, shaded brick sides pick up indirect light, slab undersides stop going pitch black. Zero per-frame cost.

### B2. Replace flat background with sky / gradient
Options:
- `Sky` from `three/addons/objects/Sky.js` (Hosek-Wilkie, tied to `sun.position`).
- A simple vertical gradient via a fullscreen shader or large `MeshBasicMaterial` sphere with vertex colors.
Greatly improves perceived production value at modest cost.

### B3. PBR brick texture set
A single small atlas (`map`, `normalMap`, `roughnessMap`, `aoMap`) on one shared brick material would lift visual quality far more than any other change. Keep the 5-tone palette by tinting via `material.color` or by storing per-instance color in `InstancedMesh.setColorAt`.

### B4. InstancedMesh for bricks
Replace per-brick `THREE.Mesh` (line 440) with one `InstancedMesh` per (geometry, material) pair. With 5 brick materials × 2 brick orientations that's 10 instanced meshes total instead of ~400 individual meshes. Draw call count drops by an order of magnitude.

Key adjustments:
- The physics `Body` only needs `pos`/`size`/`vel` — no Mesh required. Keep a `(instanceId → matrix)` map and write the matrix each frame in the same loop currently at line 393.
- On block destruction (line 622) set the instance matrix to a hidden/zero-scale matrix and mark the slot free for reuse.
- Slabs and roof remain individual meshes — they're few and have unique sizes.

### B5. InstancedMesh for debris
Same idea: one `InstancedMesh` of capacity `MAX_DEBRIS=220` (line 210) with shared `BoxGeometry(1,1,1)` scaled per-instance. Pair with `setColorAt` for per-piece tint.

## C. Post-processing & AA strategy

### C1. Decision point: MSAA vs FXAA/SMAA
Current state: relying on `antialias: true` → browser MSAA on default framebuffer. Works **only** if no `EffectComposer` is attached. Two paths:

**Path 1 — Stay simple, no post.** Keep `antialias: true`. Quality is fine for this scene. Add only screen-space outline or vignette as one-off shaders via direct render passes.

**Path 2 — Add post-processing.** Move to `EffectComposer`. At that point default MSAA is lost; options:
- `WebGLRenderTarget` with `samples: 4` (WebGL2 MSAA on render targets — supported in r160). Best quality.
- `SMAAPass` (better than FXAA for edge AA at modest cost).
- `FXAAPass` (cheapest, somewhat blurry).

Recommendation: if any post is added, go with multisampled `RenderTarget` (`samples: 4`) + an `SMAAPass` for sub-pixel edges; skip FXAA.

### C2. Useful post-process effects for this scene
- **Bloom** (`UnrealBloomPass`) — would make the bomb flash actually glow. Threshold ~0.9, strength ~0.6, radius ~0.4.
- **SSAO** (`SSAOPass` or `GTAOPass`) — adds crevice shading between bricks and under slabs; significant cost but solves the “muddy underside” problem more cheaply than full GI.
- **Outline / EdgeDetect** — would add a Blender-viewport feel given the orbit controls.
- **Vignette + filmic curve** — already partially achieved by `ACESFilmicToneMapping` (A3).

### C3. Shadow improvements beyond A1/A2/A5
- **Cascaded shadow maps (CSM)** via `three/addons/csm/CSM.js`: would only matter once the camera zooms way in/out across a much larger scene. For a single building it's overkill — the single ortho already covers the asset.
- **VSM (`VSMShadowMap`)**: trades acne for soft, light-leak-prone shadows; useful only if you also add a blur pass. Skip in favor of PCFSoft + normalBias.
- **Contact-hardening shadows (PCSS)**: requires a custom shader; high effort, marginal payoff here.

## D. Performance hardening

### D1. Adaptive pixel ratio
Replace line 67 cap with a feedback loop: monitor `fpsAcc` (line 755), and if FPS drops below 50 for >1 s lower the cap to `1.0`, restore at `1.5` when above 58. Smoother than a hard cap.

### D2. Frustum culling is already on
Every `Mesh` has `frustumCulled = true` by default — no action needed. If `InstancedMesh` is adopted (B4), set the instanced mesh's `boundingSphere` correctly so culling works for the whole batch.

### D3. Disable `receiveShadow` on bombs and debris
Line 443 sets `receiveShadow = true` for everything created via `makeBlock`. Bombs (line 533) and debris (line 559) don't go through `makeBlock`, so they're already shadow-receive false ✅. No action needed there — but explicitly setting `castShadow = false` on debris/bombs would future-proof if `makeBlock` is generalized.

### D4. Pre-allocate debris pool
With `MAX_DEBRIS=220` (line 210), pre-build a pool of 220 inert `InstancedMesh` slots at startup; `spawnDebris` (line 551) just activates the next free slot. Eliminates per-explosion allocations entirely.

### D5. Reduce overdraw from explosion flash
Flash sphere has 16×12 segments (line 581) — fine. But it's transparent and can stack: if two bombs detonate adjacent, overdraw doubles. Cap concurrent flashes or fade faster (line 589 currently uses `0.4 s`).

### D6. Sphere geometry for bomb
Line 528: `SphereGeometry(0.35, 12, 8)` — already low-poly. No change.

---

## Summary — Prioritized Recommendations

| # | Change | Effort | Visual Impact | Perf Impact |
|---|---|---|---|---|
| 1 | PCFSoftShadowMap + `normalBias 0.02` (A1, A2) | trivial | medium | ~0 |
| 2 | `ACESFilmicToneMapping` + exposure, pin `outputColorSpace` (A3, A4) | trivial | medium | ~0 |
| 3 | Shadow map 2048² + tighter ortho (A5) | trivial | medium | minor mem |
| 4 | `scene.environment` via PMREM + RoomEnvironment (B1) | low | **high** | ~0 |
| 5 | Cache debris geo+mat (A7) | low | none | **high** (alloc) |
| 6 | InstancedMesh for bricks (B4) | medium | none | **very high** (draw calls) |
| 7 | InstancedMesh for debris pool (B5, D4) | medium | none | high |
| 8 | PBR brick texture atlas (B3) | medium | **very high** | small (more samplers) |
| 9 | Sky background (B2) | low | high | ~0 |
| 10 | `EffectComposer` + MSAA RT + SMAA + Bloom + GTAO (C1, C2) | high | very high | medium-high |
| 11 | Adaptive pixel ratio (D1) | low | none | high on weak GPUs |
| 12 | Tighten far plane + fog reshape (A6) | trivial | low-medium | tiny |

**Recommended first pass (≈30 minutes of work, no API surface changes):** items 1, 2, 3, 4, 5, 9, 12 — all drop-in, no refactor of physics code, and together they transform the look from “dev test” to “demo-ready.”

**Recommended second pass (architectural):** items 6 + 7 — instancing — for an order-of-magnitude draw-call reduction that unlocks bigger buildings and more debris.

**Recommended third pass (polish):** items 8 + 10 — texture atlas + post pipeline — for final production quality.
