// Copied from wells/shop src/lib/epaper-refresh.ts (24 Sep 2026); additions: onShown, onArriving, holdMs.
// An e-paper frame refreshing between plates, for a model's "screen" material
// (the Featherframe). Each refresh is drawn the way the panel paints: not a
// crossfade but a waveform — a fixed sequence of whole-sheet drive phases,
// during which each pixel shows whatever its particles are doing right then.
//
//   gc16      the 10.3" 16-gray panel's full refresh (IT8951, GC16): the old
//             picture's negative, a dark flash, white, the new picture's
//             negative, the new picture — each step fading into the next, as
//             the panel's does, rather than blinking. About a second.
//   spectra6  the 13.3" Spectra 6 colour panel, 12–20 s on the panel, told
//             in about 5½: the old picture driven out as its negative, a
//             black/white shake and a sweep of colour across the sheet, the
//             new picture as a silhouette and its negative, then the inks in
//             the order they arrive — red and yellow while blue and green are
//             still dark, a washed-out yellow flicker, blue and green, dull
//             and then settled.
//
// A phase maps the ink each pixel is headed for (or came from) to the colour
// it shows during that phase, so regions bound for different inks pass
// through different colours at once — the "right picture, wrong colours"
// look of a real colour refresh. Each phase change is a global, stepped flash
// — every pixel crosses together over the same short blend — the way the
// whole sheet actually drives at once; gc16's mono step alone gets a little
// per-cell spread (see WaveformSpec.spread) so its fade looks soft rather
// than a hard cut. The colour refresh carries no per-pixel randomness: its
// only fine texture is the plates' own static dither.
//
// The glass is a render target the size of the plates, redrawn only while a
// refresh runs (and at most every other frame); between refreshes nothing is
// drawn and the viewer's loop can park. The plates are sampled texel for texel
// (no filtering), so the dither survives into the ink snap — give or take the
// JPEG's softening at ink edges — and the target's mipmaps keep that dither
// from shimmering when the frame is small on screen.

import {
  LinearFilter,
  LinearMipmapLinearFilter,
  Mesh,
  NearestFilter,
  NoColorSpace,
  OrthographicCamera,
  PlaneGeometry,
  Scene,
  SRGBColorSpace,
  ShaderMaterial,
  Texture,
  TextureLoader,
  WebGLRenderTarget,
  type WebGLRenderer,
} from 'three';

export type Waveform = 'gc16' | 'spectra6';

/** What the product frontmatter's `screenRefresh` says: the waveform, and the
 *  plates to refresh to after the still, in order (the loop returns to it). */
export interface ScreenRefresh {
  waveform: Waveform;
  plates: string[];
}

export interface EpaperRefresh {
  /** The glass as it looks now — the screen material's map and emissive map. */
  readonly texture: Texture;
  /** Advance to `now` (performance.now() ms). `changed`: the glass was redrawn;
   *  `busy`: a refresh is under way, so keep the loop running. */
  tick(now: number): { changed: boolean; busy: boolean };
  /** Dev and tests: hold the glass `ms` into the refresh to the next plate, or
   *  `null` to let it run again. */
  freeze(ms: number | null): void;
  dispose(): void;
}

type RGB = readonly [number, number, number];
/** One drive phase. `from`: whose inks the map reads, the picture going
 *  (`old`) or coming (`new`). `map`: the colour shown for each ink — for
 *  spectra6 the six inks in INKS order, for gc16 two entries, what black and
 *  white show (grays fall between). `settled`: the plate's own texels. */
interface Phase {
  ms: number;
  from: 'old' | 'new';
  map?: RGB[];
  settled?: boolean;
  /** The new picture is first recognisable as itself from this phase on:
   *  when the refresh clock reaches its start, onArriving fires. One per waveform. */
  arrives?: boolean;
}
interface WaveformSpec {
  phases: Phase[];
  /** How long one pixel takes to cross from one phase's colour to the next. */
  blend: number;
  /** How far early or late a pixel may cross a phase boundary, in all — gc16
   *  only (see the shader's SPECTRA6 branch); 0 for a waveform whose phases
   *  are a global flash instead. Every phase must last at least blend +
   *  spread: a pixel is only ever drawn between the two phases either side of
   *  the nearest boundary. */
  spread: number;
}

/** How long a plate stays on the glass before the next refresh starts. */
const HOLD_MS = 6000;
/** Redraw the glass at most this often during a refresh: every other frame at
 *  60 Hz (a 33 ms cap would land either side of two frames' jittered gap and
 *  judder between 30 and 20 fps). The panel's own frame rate is about that. */
const DRAW_EVERY_MS = 25;

const hex = (h: number): RGB => [((h >> 16) & 255) / 255, ((h >> 8) & 255) / 255, (h & 255) / 255];
const mix = (a: RGB, b: RGB, t: number): RGB => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const all = (c: RGB): RGB[] => [c, c, c, c, c, c];

// The six inks, as scripts/addon-models/plates.py dithers the colour plates.
const K = hex(0x000000);
const W = hex(0xffffff);
const R = hex(0xce302a);
const Y = hex(0xe8be38);
const B = hex(0x2c4e9e);
const G = hex(0x30844e);
const INKS: RGB[] = [K, W, R, Y, B, G];
/** The gray panel's flash: its particles never pack down to full black in
 *  the few frames the waveform gives them, so the sheet dims rather than goes
 *  out — and so does the dark of each negative. */
const DIM = hex(0x4a4a4a);

const WAVEFORMS: Record<Waveform, WaveformSpec> = {
  gc16: {
    // a long crossing and little spread: each step fades into the next
    blend: 120,
    spread: 30,
    phases: [
      { ms: 150, from: 'old', map: [W, DIM] }, // the old picture's negative
      { ms: 170, from: 'old', map: [DIM, DIM] }, // the dark flash
      { ms: 170, from: 'old', map: [W, W] }, // white
      { ms: 150, from: 'new', map: [W, DIM] }, // the new picture's negative
      // the new picture. Its negative just before is only 150 ms of inverted
      // grays, so the picture is first itself here.
      { ms: 320, from: 'new', settled: true, arrives: true }, // the new picture
    ],
  },
  spectra6: {
    blend: 70,
    // No per-pixel spread: the panel drives its whole sheet at once, so each
    // phase change is a global flash over `blend` ms, not a jittered one.
    spread: 0,
    // map order: K W R Y B G
    phases: [
      // the old picture driven out: its negative, colours to their opposite inks
      { ms: 400, from: 'old', map: [W, K, G, B, Y, R] },
      // the shake: the whole sheet swung between full black and full white…
      { ms: 330, from: 'old', map: all(K) },
      { ms: 330, from: 'old', map: all(W) },
      { ms: 330, from: 'old', map: all(K) },
      { ms: 330, from: 'old', map: all(W) },
      // …and a sweep of the colour pigments across it
      { ms: 240, from: 'old', map: all(R) },
      { ms: 240, from: 'old', map: all(Y) },
      // the new picture as a silhouette — every dark ink black, every light one paper…
      { ms: 460, from: 'new', map: [K, W, K, W, K, K] },
      // …then its negative
      { ms: 350, from: 'new', map: [W, K, W, K, W, W] },
      // the warm pigments land first; blue and green are still dark. The first
      // phase that shows the new picture the right way round and in its own
      // inks (the silhouette and negative before it are the picture's shape,
      // not the picture), so a viewer recognises it from here.
      { ms: 620, from: 'new', map: [K, W, R, Y, K, mix(K, Y, 0.3)], arrives: true },
      // a washed-out yellow flicker as the blue is pulled through
      { ms: 270, from: 'new', map: [mix(K, R, 0.35), mix(W, Y, 0.55), Y, W, mix(K, B, 0.5), mix(Y, G, 0.5)] },
      // blue and green arrive, dull
      { ms: 540, from: 'new', map: [K, mix(W, K, 0.12), R, Y, mix(B, K, 0.45), mix(G, K, 0.45)] },
      // one last light flicker…
      { ms: 240, from: 'new', map: [mix(K, W, 0.5), W, mix(R, W, 0.5), mix(Y, W, 0.5), mix(B, W, 0.5), mix(G, W, 0.5)] },
      // …and it settles
      { ms: 700, from: 'new', settled: true },
    ],
  },
};

const VERTEX = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

// Colour maths happens on the plates' own sRGB values (they're loaded as
// NoColorSpace) and the result is decoded to linear for the sRGB target, which
// the GPU re-encodes — so an inversion is the visual one, not a linear-light one.
const FRAGMENT = /* glsl */ `
uniform sampler2D uOld;
uniform sampler2D uNew;
uniform vec2 uSize;
uniform float uDt;
uniform float uStep;
uniform float uBlend;
uniform float uSpread;
uniform float uFromA;
uniform float uFromB;
uniform float uSettledA;
uniform float uSettledB;
uniform vec3 uMapA[6];
uniform vec3 uMapB[6];
uniform vec3 uInks[6];
varying vec2 vUv;

// An integer hash of a texel's cell (Hoskins): uncorrelated between
// neighbours. Used only by gc16's per-cell crossing spread below — the mono
// panel's own step doesn't snap cleanly, so a little of this softens the cut
// into a fade. spectra6 doesn't use it: its flashes are a whole-sheet event,
// not a per-pixel one, and a hash here reads as static, not as the panel's
// texture.
float cellHash(vec2 p) {
  uvec2 v = uvec2(p) * uvec2(1597334673u, 3812015801u);
  uint n = (v.x ^ v.y) * 1597334673u;
  return float(n) / 4294967295.0;
}

vec3 toLinear(vec3 c) {
  return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c));
}

#ifdef SPECTRA6
// The colour each phase shows for this texel of its source picture: snap the
// texel to its nearest ink and read that ink's entry.
void inkLook(vec3 srcA, vec3 srcB, out vec3 a, out vec3 b) {
  float bestA = 1e9;
  float bestB = 1e9;
  for (int i = 0; i < 6; i++) {
    vec3 da = srcA - uInks[i];
    vec3 db = srcB - uInks[i];
    float ea = dot(da, da);
    float eb = dot(db, db);
    if (ea < bestA) { bestA = ea; a = uMapA[i]; }
    if (eb < bestB) { bestB = eb; b = uMapB[i]; }
  }
}
#endif

void main() {
  vec3 oldC = texture2D(uOld, vUv).rgb;
  vec3 newC = texture2D(uNew, vUv).rgb;
  vec3 srcA = mix(oldC, newC, uFromA);
  vec3 srcB = mix(oldC, newC, uFromB);
  vec3 lookA;
  vec3 lookB;
#ifdef SPECTRA6
  inkLook(srcA, srcB, lookA, lookB);
#else
  float lumA = dot(srcA, vec3(0.299, 0.587, 0.114));
  float lumB = dot(srcB, vec3(0.299, 0.587, 0.114));
  lookA = mix(uMapA[0], uMapA[1], lumA);
  lookB = mix(uMapB[0], uMapB[1], lumB);
#endif
  lookA = mix(lookA, srcA, uSettledA);
  lookB = mix(lookB, srcB, uSettledB);
#ifdef SPECTRA6
  // A global flash: every texel crosses together over the same short blend,
  // so a phase change reads as one stepped event across the whole sheet.
  float w = smoothstep(0.0, 1.0, uDt / uBlend + 0.5);
#else
  // When this pixel crosses: mostly a property of its cell, a little of the step.
  vec2 cell = floor(vUv * uSize);
  float n = 0.7 * cellHash(cell) + 0.3 * cellHash(cell + uStep * 17.0);
  float w = smoothstep(0.0, 1.0, (uDt + uSpread * (0.5 - n)) / uBlend + 0.5);
#endif
  gl_FragColor = vec4(toLinear(mix(lookA, lookB, w)), 1.0);
}
`;

/**
 * Start a frame refreshing between `first` (the still already decoded for the
 * screen) and `spec.plates`, holding each plate before the next refresh.
 * `wake` is called when a hold ends, so a parked render loop comes back to run
 * the refresh; `motionOk` is asked before each refresh starts.
 */
export function createEpaperRefresh(opts: {
  renderer: WebGLRenderer;
  first: HTMLImageElement | ImageBitmap;
  spec: ScreenRefresh;
  anisotropy: number;
  wake: () => void;
  motionOk: () => boolean;
  /** Called with the plate's index once a refresh to it has settled. */
  onShown?: (index: number) => void;
  /** Called once per refresh with the incoming plate's index, when the
   *  refresh reaches the waveform's `arrives` phase: the moment the new
   *  picture is first recognisable, well before it settles. */
  onArriving?: (index: number) => void;
  /** How long each plate holds before the next refresh (default HOLD_MS). */
  holdMs?: number;
}): EpaperRefresh {
  const { renderer, first, spec, wake, motionOk } = opts;
  const hold = opts.holdMs ?? HOLD_MS;
  const wave = WAVEFORMS[spec.waveform];
  const width = first.width;
  const height = first.height;

  const target = new WebGLRenderTarget(width, height, {
    depthBuffer: false,
    generateMipmaps: true,
    minFilter: LinearMipmapLinearFilter,
    magFilter: LinearFilter,
    colorSpace: SRGBColorSpace,
  });
  target.texture.anisotropy = opts.anisotropy;

  const plateTexture = (image: HTMLImageElement | ImageBitmap) => {
    const t = new Texture(image);
    t.colorSpace = NoColorSpace;
    t.minFilter = NearestFilter;
    t.magFilter = NearestFilter;
    t.generateMipmaps = false;
    t.needsUpdate = true;
    return t;
  };
  // Slot 0 is the still; the rest load one at a time, each as its turn nears.
  const plates: (Texture | null)[] = [plateTexture(first), ...spec.plates.map(() => null)];
  const loading = new Set<number>();
  /** Plates that wouldn't load: the rotation skips them. */
  const failed = new Set<number>();
  const loader = new TextureLoader();
  let disposed = false;
  const ensure = (i: number) => {
    if (plates[i] || loading.has(i)) return;
    loading.add(i);
    loader.load(
      spec.plates[i - 1],
      (tex) => {
        loading.delete(i);
        if (disposed) {
          tex.dispose();
          return;
        }
        tex.colorSpace = NoColorSpace;
        tex.minFilter = NearestFilter;
        tex.magFilter = NearestFilter;
        tex.generateMipmaps = false;
        plates[i] = tex;
        if (i === next()) preload(tex);
        wake();
      },
      undefined,
      () => {
        loading.delete(i);
        failed.add(i);
        wake();
      },
    );
  };

  const mapUniform = () => ({ value: new Float32Array(18) });
  const material = new ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    defines: spec.waveform === 'spectra6' ? { SPECTRA6: '' } : {},
    uniforms: {
      uOld: { value: plates[0] },
      uNew: { value: plates[0] },
      uSize: { value: [width, height] },
      uDt: { value: 0 },
      uStep: { value: 0 },
      uBlend: { value: wave.blend },
      uSpread: { value: wave.spread },
      uFromA: { value: 0 },
      uFromB: { value: 0 },
      uSettledA: { value: 1 },
      uSettledB: { value: 1 },
      uMapA: mapUniform(),
      uMapB: mapUniform(),
      uInks: { value: new Float32Array(INKS.flat()) },
    },
    depthTest: false,
    depthWrite: false,
    toneMapped: false,
  });
  const quad = new Mesh(new PlaneGeometry(2, 2), material);
  quad.frustumCulled = false;
  const quadScene = new Scene();
  quadScene.add(quad);
  const quadCamera = new OrthographicCamera(-1, 1, 1, -1, 0, 1);

  // The steps a refresh walks: the old plate as it was, then the phases. A
  // pixel is only ever between two neighbouring steps, so the shader gets the
  // pair around the nearest boundary.
  const steps: Phase[] = [{ ms: 0, from: 'old', settled: true }, ...wave.phases];
  const starts: number[] = [];
  // The first boundary waits until even the earliest pixel's crossing starts
  // after 0, so a refresh opens on the old plate rather than halfway out of it.
  let total = (wave.blend + wave.spread) / 2;
  let arriveAt = -1;
  for (const p of wave.phases) {
    if (p.arrives) arriveAt = total;
    starts.push(total);
    total += p.ms;
  }
  if (arriveAt < 0) arriveAt = total;
  const setPhase = (suffix: 'A' | 'B', p: Phase) => {
    const u = material.uniforms;
    u[`uFrom${suffix}`].value = p.from === 'new' ? 1 : 0;
    u[`uSettled${suffix}`].value = p.settled ? 1 : 0;
    const arr = u[`uMap${suffix}`].value as Float32Array;
    arr.fill(0);
    (p.map ?? []).forEach((c, i) => arr.set(c, i * 3));
  };

  const draw = (t: number) => {
    // steps[k] starts at starts[k - 1]; find the boundary nearest t
    let k = 1;
    let best = Infinity;
    for (let i = 0; i < starts.length; i++) {
      const d = Math.abs(t - starts[i]);
      if (d < best) {
        best = d;
        k = i + 1;
      }
    }
    const u = material.uniforms;
    if (t >= total) {
      setPhase('A', steps[steps.length - 1]);
      setPhase('B', steps[steps.length - 1]);
      u.uDt.value = 0;
    } else {
      setPhase('A', steps[k - 1]);
      setPhase('B', steps[k]);
      u.uDt.value = t - starts[k - 1];
    }
    u.uStep.value = k;
    const previous = renderer.getRenderTarget();
    renderer.setRenderTarget(target);
    renderer.render(quadScene, quadCamera);
    renderer.setRenderTarget(previous);
  };

  const count = plates.length;
  let current = 0;
  let mode: 'hold' | 'refresh' = 'hold';
  let holdSince = performance.now();
  let clock = 0; // ms into the refresh, advanced only while ticking
  let lastTick = -1;
  let lastDraw = -Infinity;
  let frozen: number | null = null;
  let timer = 0;
  let arrived = false;
  /** The plate after the current one, skipping any that failed to load
   *  (the current one again when every other has). */
  const next = () => {
    for (let k = 1; k < count; k++) {
      const i = (current + k) % count;
      if (!failed.has(i)) return i;
    }
    return current;
  };
  /** Upload a plate a moment from now, mid-hold, so neither a refresh's first
   *  frame nor the frame that ends one pays for it. */
  const preload = (tex: Texture) => {
    window.setTimeout(() => {
      if (!disposed && plates[next()] === tex) renderer.initTexture(tex);
    }, 250);
  };

  /** Keep the plates on the GPU to the two a refresh needs (a trimmed one
   *  keeps its image and uploads again when it's next up). */
  const trim = () => {
    plates.forEach((p, i) => {
      if (p && i !== current && i !== next()) p.dispose();
    });
  };
  /** Wake the loop when the hold is up (re-arming if the timer fires early). */
  const whenHeld = () => {
    const left = hold - (performance.now() - holdSince);
    if (left > 0) timer = window.setTimeout(whenHeld, left + 5);
    else wake();
  };
  const startHold = (now: number) => {
    mode = 'hold';
    holdSince = now;
    const upNext = plates[next()];
    if (upNext) preload(upNext);
    else ensure(next());
    window.clearTimeout(timer);
    whenHeld();
  };

  // The glass starts as the still.
  material.uniforms.uOld.value = plates[0];
  material.uniforms.uNew.value = plates[0];
  draw(total);
  startHold(holdSince);

  return {
    texture: target.texture,
    tick(now) {
      const dt = lastTick < 0 ? 0 : Math.min(now - lastTick, 100);
      lastTick = now;
      if (disposed || count < 2) return { changed: false, busy: false };
      if (frozen !== null) return { changed: false, busy: false };
      if (mode === 'hold') {
        if (now - holdSince < hold || next() === current || !motionOk()) return { changed: false, busy: false };
        const upNext = plates[next()];
        if (!upNext) {
          ensure(next()); // still loading, or the one before it failed
          return { changed: false, busy: false };
        }
        // The refresh opens at 0 whatever the gap since the loop last ran.
        mode = 'refresh';
        clock = 0;
        arrived = false;
        lastDraw = -Infinity;
        material.uniforms.uOld.value = plates[current];
        material.uniforms.uNew.value = upNext;
      } else {
        clock += dt;
      }
      if (!arrived && clock >= arriveAt) {
        arrived = true;
        opts.onArriving?.(next());
      }
      if (clock >= total) {
        draw(total);
        current = next();
        opts.onShown?.(current);
        trim();
        startHold(now);
        return { changed: true, busy: false };
      }
      if (now - lastDraw < DRAW_EVERY_MS) return { changed: false, busy: true };
      lastDraw = now;
      draw(clock);
      return { changed: true, busy: true };
    },
    freeze(ms) {
      frozen = ms;
      if (ms === null) {
        startHold(performance.now() - hold);
        wake();
        return;
      }
      const upNext = plates[next()];
      if (!upNext) {
        ensure(next());
        return;
      }
      material.uniforms.uOld.value = plates[current];
      material.uniforms.uNew.value = upNext;
      draw(Math.min(ms, total));
      wake();
    },
    dispose() {
      disposed = true;
      window.clearTimeout(timer);
      plates.forEach((p) => p?.dispose());
      material.dispose();
      quad.geometry.dispose();
      target.dispose();
    },
  };
}
