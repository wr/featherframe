// Copied from wells/shop src/lib/epaper-refresh.ts (24 Sep 2026); additions: onShown, onArriving, holdMs, show,
// speed, and spectra6 as the panel really refreshes.
// An e-paper frame refreshing between plates, for a model's "screen" material
// (the Featherframe). Each refresh is drawn the way the panel paints: not a
// crossfade but a waveform — a fixed sequence of whole-sheet drive phases,
// during which each pixel shows whatever its particles are doing right then.
//
//   gc16      the 10.3" 16-gray panel's full refresh (IT8951, GC16): the old
//             picture's negative, a dark flash, white, the new picture's
//             negative, the new picture — each step fading into the next, as
//             the panel's does, rather than blinking. About a second.
//   spectra6  the 13.3" Spectra 6 colour panel, about 15½ s, as a video of
//             one refreshing shows it: the new picture's negative in blue,
//             the sheet flickering dark blue and yellow, a pale wash, the
//             picture in sepia shimmering against brown, then its colours
//             under a mauve cast that lifts as it settles.
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
export type ShowHow = 'panel' | 'quick' | 'instant';

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
  /** Leave the cycle for `src` (an image the size of the plates): refresh to
   *  it at once and hold it until told otherwise. `null` goes back to the
   *  cycle, refreshing to the plate it left. `how`: 'panel' refreshes at the
   *  panel's own pace, as the cycle does (a new detection); 'quick' runs the
   *  panel's own waveform in about QUICK_MS, for a change a scroll asks for;
   *  'instant' skips the waveform, for a frame nobody can see change. */
  show(src: string | null, how?: ShowHow): void;
  /** Load `src` ahead of a show(), so an instant one has it to hand. */
  prepare(src: string): void;
  /** The show()n picture fully on the glass now, or null (the cycle, or on its way). */
  showing(): string | null;
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
   *  only (see the shader's spectra6 branch); 0 for a waveform whose phases
   *  are a global flash instead. Every phase must last at least blend +
   *  spread: a pixel is only ever drawn between the two phases either side of
   *  the nearest boundary. */
  spread: number;
}

/** A 'quick' show(): the panel's waveform, sped up to about this long (never slowed). */
const QUICK_MS = 5000;
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
    // Timed from a video of the panel refreshing (Wells, 26 Sep 2026), about
    // 15½ s from the old picture to the new one settled, its colours corrected
    // for the camera's blue cast. The phases are flat washes and stepped
    // flickers, so every crossing is short.
    blend: 45,
    // No per-pixel spread: the panel drives its whole sheet at once, so each
    // phase change is a global crossing over `blend` ms, not a jittered one.
    spread: 0,
    // map order: K W R Y B G
    phases: [
      // the new picture's negative in blue: its darks near white, its lights deep blue…
      { ms: 1700, from: 'new', map: [hex(0xe6ecf4), hex(0x2f5fb0), hex(0x6f8fc8), hex(0x4a74c0), hex(0xd0dcef), hex(0x9fb3d6)] },
      // …fading to a paler negative
      { ms: 1600, from: 'new', map: [hex(0xd4dde8), hex(0x6d90c8), hex(0x93acd4), hex(0x7e9ccc), hex(0xc9d6ea), hex(0xb0c2df)] },
      // the whole sheet flickering between dark blue and yellow, about three times a second
      { ms: 200, from: 'new', map: all(hex(0x25508f)) },
      { ms: 150, from: 'new', map: all(hex(0xf0dc6e)) },
      { ms: 200, from: 'new', map: all(hex(0x25508f)) },
      { ms: 150, from: 'new', map: all(hex(0xf0dc6e)) },
      { ms: 200, from: 'new', map: all(hex(0x25508f)) },
      { ms: 150, from: 'new', map: all(hex(0xf0dc6e)) },
      { ms: 200, from: 'new', map: all(hex(0x25508f)) },
      { ms: 150, from: 'new', map: all(hex(0xf0dc6e)) },
      { ms: 200, from: 'new', map: all(hex(0x25508f)) },
      { ms: 150, from: 'new', map: all(hex(0xf0dc6e)) },
      { ms: 200, from: 'new', map: all(hex(0x25508f)) },
      { ms: 150, from: 'new', map: all(hex(0xf0dc6e)) },
      // a pale wash with the picture's ghost in it
      { ms: 500, from: 'new', map: [hex(0xd6d6c6), hex(0xf2f0d8), hex(0xe6dcc8), hex(0xf2f0d8), hex(0xdcdcd0), hex(0xe4e4d0)] },
      // dark blue, then a muddy brown
      { ms: 300, from: 'new', map: all(hex(0x2a5596)) },
      { ms: 300, from: 'new', map: all(hex(0x8a7a60)) },
      // the picture, in sepia: the first phase that shows it the right way round
      // (the sepia picture shimmering against a flat brown, a little brighter each second)
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xc8a8a4), hex(0xa8504c), hex(0xb89070), hex(0x584058), hex(0x6a5a4a)], arrives: true },
      { ms: 90, from: 'new', map: all(hex(0x9a5e5e)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xc8a8a4), hex(0xa8504c), hex(0xb89070), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0x9a5e5e)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xc8a8a4), hex(0xa8504c), hex(0xb89070), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0x9a5e5e)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xc8a8a4), hex(0xa8504c), hex(0xb89070), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0x9a5e5e)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xc8a8a4), hex(0xa8504c), hex(0xb89070), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0x9a5e5e)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xd4b4ae), hex(0xb84c44), hex(0xcc9c64), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0xa86a66)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xd4b4ae), hex(0xb84c44), hex(0xcc9c64), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0xa86a66)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xd4b4ae), hex(0xb84c44), hex(0xcc9c64), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0xa86a66)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xd4b4ae), hex(0xb84c44), hex(0xcc9c64), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0xa86a66)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xd4b4ae), hex(0xb84c44), hex(0xcc9c64), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0xa86a66)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xdcc0b8), hex(0xc44a3e), hex(0xdcac58), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0xb07470)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xdcc0b8), hex(0xc44a3e), hex(0xdcac58), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0xb07470)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xdcc0b8), hex(0xc44a3e), hex(0xdcac58), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0xb07470)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xdcc0b8), hex(0xc44a3e), hex(0xdcac58), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0xb07470)) },
      { ms: 110, from: 'new', map: [hex(0x3a2a2c), hex(0xdcc0b8), hex(0xc44a3e), hex(0xdcac58), hex(0x584058), hex(0x6a5a4a)] },
      { ms: 90, from: 'new', map: all(hex(0xb07470)) },
      // the colours in, under a mauve cast: reds and yellows right, the lights mauve, greens still blue
      { ms: 3900, from: 'new', map: [hex(0x2a2040), hex(0xc8b8d8), hex(0xc04040), hex(0xd8c050), hex(0x4050a0), hex(0x506080)] },
      // the cast lifts: washed out, then true
      { ms: 800, from: 'new', map: [hex(0x3a3448), hex(0xe4e0ea), mix(R, W, 0.2), mix(Y, W, 0.2), mix(B, W, 0.2), mix(G, B, 0.35)] },
      { ms: 800, from: 'new', map: [mix(K, W, 0.08), mix(W, K, 0.04), R, Y, mix(B, W, 0.08), mix(G, B, 0.12)] },
      { ms: 500, from: 'new', settled: true },
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
uniform float uSpectra;
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

void main() {
  vec3 oldC = texture2D(uOld, vUv).rgb;
  vec3 newC = texture2D(uNew, vUv).rgb;
  vec3 srcA = mix(oldC, newC, uFromA);
  vec3 srcB = mix(oldC, newC, uFromB);
  vec3 lookA;
  vec3 lookB;
  float w;
  if (uSpectra > 0.5) {
    inkLook(srcA, srcB, lookA, lookB);
  } else {
    float lumA = dot(srcA, vec3(0.299, 0.587, 0.114));
    float lumB = dot(srcB, vec3(0.299, 0.587, 0.114));
    lookA = mix(uMapA[0], uMapA[1], lumA);
    lookB = mix(uMapB[0], uMapB[1], lumB);
  }
  lookA = mix(lookA, srcA, uSettledA);
  lookB = mix(lookB, srcB, uSettledB);
  if (uSpectra > 0.5) {
    // A global flash: every texel crosses together over the same short blend,
    // so a phase change reads as one stepped event across the whole sheet.
    w = smoothstep(0.0, 1.0, uDt / uBlend + 0.5);
  } else {
    // When this pixel crosses: mostly a property of its cell, a little of the step.
    vec2 cell = floor(vUv * uSize);
    float n = 0.7 * cellHash(cell) + 0.3 * cellHash(cell + uStep * 17.0);
    w = smoothstep(0.0, 1.0, (uDt + uSpread * (0.5 - n)) / uBlend + 0.5);
  }
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
   *  picture is first recognisable (about 2.4 s before it settles on
   *  spectra6; on gc16, whose refresh is a second long, at its last phase).
   *  `freeze()` does not call it. */
  onArriving?: (index: number) => void;
  /** How long each plate holds before the next refresh (default HOLD_MS). */
  holdMs?: number;
  /** Run every refresh this many times faster (tests). */
  speed?: number;
}): EpaperRefresh {
  const { renderer, first, spec, wake, motionOk } = opts;
  const hold = opts.holdMs ?? HOLD_MS;
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
  // show()'s pictures are appended after the cycle's plates, one slot per src.
  const sources: (string | null)[] = [null, ...spec.plates];
  const cycle = plates.length;
  const loading = new Set<number>();
  /** Plates that wouldn't load: the rotation skips them. */
  const failed = new Set<number>();
  const loader = new TextureLoader();
  let disposed = false;
  const ensure = (i: number) => {
    if (plates[i] || loading.has(i)) return;
    loading.add(i);
    loader.load(
      sources[i]!,
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
        if (i === instantTo) settle(i);
        else if (i === next()) preload(tex);
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
    uniforms: {
      uOld: { value: plates[0] },
      uNew: { value: plates[0] },
      uSize: { value: [width, height] },
      uDt: { value: 0 },
      uStep: { value: 0 },
      uBlend: { value: 0 },
      uSpread: { value: 0 },
      uSpectra: { value: 0 },
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
  interface Run { name: Waveform; spec: WaveformSpec; steps: Phase[]; starts: number[]; total: number; arriveAt: number; rate: number }
  const timing = (name: Waveform, rate = 1): Run => {
    const w = WAVEFORMS[name];
    const starts: number[] = [];
    // The first boundary waits until even the earliest pixel's crossing starts
    // after 0, so a refresh opens on the old plate rather than halfway out of it.
    let total = (w.blend + w.spread) / 2;
    let arriveAt = -1;
    for (const p of w.phases) {
      if (p.arrives) arriveAt = total;
      starts.push(total);
      total += p.ms;
    }
    if (arriveAt < 0) arriveAt = total;
    return { name, spec: w, steps: [{ ms: 0, from: 'old', settled: true }, ...w.phases], starts, total, arriveAt, rate };
  };
  const speed = opts.speed ?? 1;
  /** The panel's own refresh (the cycle's and a 'panel' show()), and its quick one (a 'quick' show()). */
  const own = timing(spec.waveform, speed);
  const quick = timing(spec.waveform, Math.max(speed, own.total / QUICK_MS));
  /** How the show()n picture on its way is to arrive. */
  let how: ShowHow = 'quick';
  let run = own;
  const useRun = (r: Run) => {
    run = r;
    const u = material.uniforms;
    u.uBlend.value = r.spec.blend;
    u.uSpread.value = r.spec.spread;
    u.uSpectra.value = r.name === 'spectra6' ? 1 : 0;
  };
  useRun(own);
  const setPhase = (suffix: 'A' | 'B', p: Phase) => {
    const u = material.uniforms;
    u[`uFrom${suffix}`].value = p.from === 'new' ? 1 : 0;
    u[`uSettled${suffix}`].value = p.settled ? 1 : 0;
    const arr = u[`uMap${suffix}`].value as Float32Array;
    arr.fill(0);
    (p.map ?? []).forEach((c, i) => arr.set(c, i * 3));
  };

  const draw = (t: number) => {
    const { steps, starts, total } = run;
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

  let current = 0;
  /** The show()n picture's slot, while there is one. */
  let pinned: number | null = null;
  /** The cycle's plate when show() took the glass over: where null returns. */
  let left = 0;
  /** A slot waiting to load, to be put on the glass without a refresh. */
  let instantTo = -1;
  let mode: 'hold' | 'refresh' = 'hold';
  let holdSince = performance.now();
  let clock = 0; // ms into the refresh, advanced only while ticking
  let lastTick = -1;
  let lastDraw = -Infinity;
  let frozen: number | null = null;
  let timer = 0;
  let arrived = false;
  /** The plate this refresh is headed for, fixed when it starts. */
  let incoming = 0;
  /** The plate after the current one, skipping any that failed to load
   *  (the current one again when every other has). */
  const next = () => {
    if (pinned !== null) return failed.has(pinned) ? current : pinned;
    if (current >= cycle) return left;
    for (let k = 1; k < cycle; k++) {
      const i = (current + k) % cycle;
      if (!failed.has(i)) return i;
    }
    return current;
  };
  /** Off the cycle, a picture is refreshed to at once rather than after a hold. */
  const urgent = () => pinned !== null ? pinned !== current : current >= cycle;
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
  draw(Infinity);
  startHold(holdSince);

  /** show()'s slot for `src`, made on first use. */
  function slotFor(src: string) {
    let i = sources.indexOf(src, cycle);
    if (i < 0) {
      i = plates.length;
      plates.push(null);
      sources.push(src);
    }
    return i;
  }

  /** Put slot `i` on the glass as it is, no waveform. */
  function settle(i: number) {
    instantTo = -1;
    mode = 'hold';
    current = i;
    material.uniforms.uOld.value = plates[i];
    material.uniforms.uNew.value = plates[i];
    draw(Infinity);
    trim();
    startHold(performance.now());
    wake();
  }

  return {
    texture: target.texture,
    tick(now) {
      const dt = lastTick < 0 ? 0 : Math.min(now - lastTick, 100);
      lastTick = now;
      if (disposed || plates.length < 2) return { changed: false, busy: false };
      if (frozen !== null) return { changed: false, busy: false };
      if (mode === 'hold') {
        if ((now - holdSince < hold && !urgent()) || next() === current || !motionOk()) return { changed: false, busy: false };
        const upNext = plates[next()];
        if (!upNext) {
          ensure(next()); // still loading, or the one before it failed
          return { changed: false, busy: false };
        }
        // The refresh opens at 0 whatever the gap since the loop last ran.
        mode = 'refresh';
        // a show()n picture comes in as show() was asked; the cycle by the panel's own pace
        useRun(urgent() && how === 'quick' ? quick : own);
        clock = 0;
        arrived = false;
        incoming = next();
        lastDraw = -Infinity;
        material.uniforms.uOld.value = plates[current];
        material.uniforms.uNew.value = upNext;
      } else {
        clock += dt * run.rate;
      }
      if (!arrived && clock >= run.arriveAt) {
        arrived = true;
        opts.onArriving?.(incoming);
      }
      if (clock >= run.total) {
        draw(Infinity);
        current = incoming;
        if (current < cycle) opts.onShown?.(current);
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
      useRun(own);
      draw(Math.min(ms, run.total));
      wake();
    },
    prepare(src) {
      ensure(slotFor(src));
    },
    show(src, asked = 'quick') {
      let i: number;
      if (src === null) {
        if (pinned === null) return;
        pinned = null;
        i = left;
      } else {
        i = slotFor(src);
        if (pinned === null && current < cycle) left = current;
        pinned = i;
      }
      how = asked;
      // a scroll doesn't wait out the panel's pace: a refresh under way hurries to its end
      if (mode === 'refresh' && how === 'quick' && run === own) useRun(quick);
      if (how === 'instant') {
        if (plates[i]) settle(i);
        else {
          instantTo = i;
          ensure(i);
        }
        return;
      }
      instantTo = -1;
      if (!plates[i]) ensure(i);
      wake();
    },
    showing() {
      return current >= cycle && mode === 'hold' ? sources[current] : null;
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
