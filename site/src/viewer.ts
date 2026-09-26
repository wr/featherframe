// The frame, drawn in WebGL: the GLB lit by a neutral studio environment, its
// "screen" material an e-paper refresh between screens (epaper-refresh.ts).
// This module only draws; where the frame goes and how it is turned is the
// caller's (choreo.ts for the page, or the hero's stage on a phone).
//
// The GLB is already posed (shop scripts/addon-models/frame.mjs): metres, Y up,
// resting on y = 0, facing +Z and leaning back 12° on its kickstand. A pose
// turns it from there: `lean` 1 is the kickstand lean as authored, 0 stands it
// upright (face parallel to the screen); `yaw` turns it about the vertical;
// `pitch` tips it toward the camera, as if the camera looked down on it.
//
// Placement is 2D. The camera never moves: a small, fixed field of view looks
// straight at the frame's middle, and the projection is then scaled and
// shifted so the frame's projected bounding box lands on a rectangle of the
// canvas (contained, centred, standing on its bottom edge). Scaling and
// shifting a projection is a 2D transform of the picture, so a head-on frame
// looks the same, texel for texel, at any size and anywhere on the page —
// which is what lets the gallery wall's still images (renders of this very
// pose) take over from the live frame without a jump.
import {
  Box3, Color, DirectionalLight, Group, MathUtils, Matrix4, Mesh, MeshBasicMaterial, MeshStandardMaterial,
  NeutralToneMapping, PerspectiveCamera, PlaneGeometry, PMREMGenerator, Scene, ShaderMaterial, ShadowMaterial, SRGBColorSpace, Vector3,
  VSMShadowMap, WebGLRenderer,
} from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { createEpaperRefresh, type EpaperRefresh } from './epaper-refresh';
import type { Size } from './card';

/** `ground`: how much of its shadow on a table beneath it shows, 0–1. */
export interface Pose { yaw: number; lean: number; pitch: number; ground: number }
export interface Rect { x: number; y: number; w: number; h: number }

/** The hero's three-quarter view on its kickstand, seen from a little above. */
export const HERO: Pose = { yaw: -0.42, lean: 1, pitch: 0.1, ground: 0 };
/** Dead-on: upright, the face parallel to the screen. */
export const FLAT: Pose = { yaw: 0, lean: 0, pitch: 0, ground: 0 };
/** On the table in III, leaning back on its kickstand. */
export const TABLE: Pose = { yaw: -0.5, lean: 1, pitch: 0.16, ground: 1 };

export const lerpPose = (a: Pose, b: Pose, t: number): Pose => ({
  yaw: a.yaw + (b.yaw - a.yaw) * t,
  lean: a.lean + (b.lean - a.lean) * t,
  pitch: a.pitch + (b.pitch - a.pitch) * t,
  ground: a.ground + (b.ground - a.ground) * t,
});

const LEAN = MathUtils.degToRad(12); // the authored kickstand lean
const FOV = 10;                      // degrees: near enough orthographic that a head-on frame is flat
const DISTANCE = 3;                  // metres from the frame's middle; the frame is under 0.4 m tall
// RoomEnvironment is a bright white room. Under ACES it bleached the walnut to
// oak and lifted the picture's blacks and greyed its colours (the screen was a
// lit, glossy surface reflecting that room): the render is tone-mapped with
// Khronos PBR Neutral, which leaves colours as they are below the highlights,
// and the screen is not lit at all (SCREEN_WHITE, below).
const EXPOSURE = 1;
const ENVIRONMENT = 0.7;
// The walnut: the room's reflection at WALNUT_ENV of its strength (a glossy
// white room on it read milky), a touch rougher, its texture darkened to
// WALNUT_TINT — the shop's render's dark walnut. The glass reflects the room at GLASS_ENV.
const WALNUT_ENV = 0.5;
const WALNUT_ROUGHNESS = 0.55;
const WALNUT_TINT = 0.72;
const GLASS_ENV = 0.7;
// The screen shows its picture as it is — unlit, not tone-mapped — at this
// level (linear RGB): its paper exactly the mat's white round it (the mat
// renders at #f6f8fa), so no box of paper shows through the opening, and its
// inks as dark and as saturated as the picture's own.
const SCREEN_WHITE = [0.922, 0.939, 0.956] as const;
// The table's shadow, for the table's still only (`?wall=table`, opts.floor):
// a light that lights nothing (so the frame looks as it does everywhere else)
// but casts the frame onto a shadow-only floor, from above and in front, so it
// falls back toward the wall, crisp where the frame meets the table. SHADOW is
// its darkness at full `ground`; soft (a variance shadow map, blurred wide).
// The page's live frame has no floor: on some GPUs the floor's shadow map
// showed as a hatched square round the frame, so on the page the one shadow is
// the CSS contact under the table's frame (styles.css .contact, --land).
const SHADOW = 0.13;
// The glass's sheen: a soft diagonal highlight, as a window across the room
// would leave on it, that slides as the frame moves up the screen. The wall's
// HTML frames draw the same band in CSS (styles.css .sheen, sheen.ts), so a
// hand-off between them does not jump. SHEEN is its strength.
export const SHEEN = 0.16;
// The studio light's bar (the shop's STUDIO_RIG key: a long, thin softbox,
// its edges falling off, turned 24° about its face), mirrored in the glass as
// a soft bar of neutral white light. At the centre stop the frame holds still
// while the page scrolls on, as if it were travelling down past the light: the
// bar sweeps up the glass with the scroll, twice (`bar`, the dwell 0 … 1; each
// half is one pass, from below the glass to above it, so the wrap is unseen).
// BAR is its strength; BAR_ROLL its tilt.
export const BAR = 0.5;
const BAR_ROLL = MathUtils.degToRad(-24);
export { sheenAt } from './sheen-at';
/** Where the bar is in its pass (0 below the glass … 1 above) at `t` through the dwell: two passes. */
export const barPass = (t: number) => { const b = 2 * Math.max(0, Math.min(1, t)); return b <= 1 ? b : b - 1; };
// How long each picture holds in the hero's cycle: well over twice the colour
// refresh (about 5.5 s), so the frame reads as a picture that sometimes
// changes, not as a frame forever refreshing. `?hold=` overrides it for tests.
const HOLD_MS = 14000;

export const loadImage = (src: string) => new Promise<HTMLImageElement>((ok, no) => {
  const img = new Image();
  img.decoding = 'async';
  img.onload = () => ok(img);
  img.onerror = no;
  img.src = src;
});

/** Sample points of the model (every vertex, thinned to about `max`), in its
 *  own space centred on `centre`: its real silhouette, for the fit. */
function hull(model: Group, centre: Vector3, max = 4000): Vector3[] {
  const all: Vector3[] = [];
  model.updateMatrixWorld(true);
  model.traverse((o) => {
    const m = o as Mesh;
    if (!m.isMesh) return;
    const pos = m.geometry.getAttribute('position');
    for (let i = 0; i < pos.count; i++) all.push(new Vector3().fromBufferAttribute(pos, i).applyMatrix4(m.matrixWorld).sub(centre));
  });
  const step = Math.max(1, Math.floor(all.length / max));
  return all.filter((_, i) => i % step === 0);
}

export interface Frame3D {
  readonly canvas: HTMLCanvasElement;
  readonly refresh: EpaperRefresh;
  /** Width over height of the frame's projected box in `pose`. */
  aspect(pose: Pose): number;
  /** The canvas's size in CSS pixels. */
  setSize(width: number, height: number): void;
  /** Draw the frame in `pose` (plus `sway` radians of yaw, which does not move
   *  its box) fitted to `rect`, in canvas CSS pixels; `null` draws nothing.
   *  `sheen`: where the glass's highlight crosses it (sheenAt), or none;
   *  `bar`: where the studio light's bar crosses it (0 … 1, up the glass), or none. */
  draw(rect: Rect | null, pose: Pose, sway?: number, sheen?: number | null, bar?: number | null): void;
  /** True once something has really been drawn. */
  readonly drawn: boolean;
  /** Whether it draws the table's shadow-only floor. */
  readonly floor: boolean;
  dispose(): void;
}

export async function loadFrame(size: Size, opts: {
  holdMs?: number;
  onShown?: (i: number) => void;
  /** Called when the screen needs the loop to run again (a hold is up, a screen loaded). */
  wake: () => void;
  /** Keep the drawing buffer, for the poster and wall renders' screenshots. */
  keep?: boolean;
  /** Draw the table's shadow-only floor (the table's still render only). */
  floor?: boolean;
}): Promise<Frame3D> {
  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: !!opts.keep });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = NeutralToneMapping;
  renderer.setClearColor(0x000000, 0);
  renderer.toneMappingExposure = EXPOSURE;
  const withFloor = !!opts.floor;
  renderer.shadowMap.enabled = withFloor;
  renderer.shadowMap.type = VSMShadowMap;
  renderer.shadowMap.autoUpdate = false;

  const scene = new Scene();
  const pmrem = new PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const env = pmrem.fromScene(room, 0.04).texture;
  room.dispose();
  scene.environment = env;
  scene.environmentIntensity = ENVIRONMENT;

  const disposeAll = () => {
    env.dispose();
    pmrem.dispose();
    renderer.dispose();
  };

  let gltf, first: HTMLImageElement;
  try {
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    [gltf, first] = await Promise.all([loader.loadAsync(size.model), loadImage(size.screens[0])]);
  } catch (e) {
    disposeAll();
    throw e;
  }
  const model = gltf.scene;
  let screen: Mesh | undefined, glass: Mesh | undefined;
  model.traverse((o) => {
    const m = o as Mesh;
    if (!m.isMesh) return;
    const name = (m.material as MeshStandardMaterial).name;
    if (name === 'screen') screen = m;
    if (name === 'featherframe_glass') glass = m;
    if (name === 'featherframe_walnut') {
      const w = m.material as MeshStandardMaterial;
      w.envMap = env; // (so its own envMapIntensity counts)
      w.envMapIntensity = WALNUT_ENV;
      w.roughness = WALNUT_ROUGHNESS;
      w.color.setScalar(WALNUT_TINT);
    }
    if (name === 'featherframe_glass') {
      const g = m.material as MeshStandardMaterial;
      g.envMap = env;
      g.envMapIntensity = GLASS_ENV;
    }
    // The steel clips holding the panel showed through the mat as faint ticks
    // on a dead-on frame; they are never seen from the front, so they are not drawn.
    if (m.name === 'featherframe_silver') m.visible = false;
  });
  if (!screen) {
    disposeAll();
    throw new Error('no screen material');
  }

  // pitch ∘ yaw ∘ lean, each about the frame's own middle
  const centre = new Box3().setFromObject(model).getCenter(new Vector3());
  const points = hull(model, centre);
  model.position.sub(centre);
  const lean = new Group();
  const yaw = new Group();
  const pitch = new Group();
  lean.add(model);
  yaw.add(lean);
  pitch.add(yaw);
  scene.add(pitch);

  model.traverse((o) => { if ((o as Mesh).isMesh) o.castShadow = withFloor; });

  // The sheen: a second skin on the glass, drawn over it. Its band is laid out
  // in the glass's own box (x across, y up), so it sits on the glass whatever the pose.
  let sheen: Mesh | undefined, sheenMaterial: ShaderMaterial | undefined;
  if (glass) {
    const g = glass.geometry;
    g.computeBoundingBox();
    const bb = g.boundingBox!;
    sheenMaterial = new ShaderMaterial({
      transparent: true,
      depthWrite: false,
      toneMapped: false,
      uniforms: {
        uMin: { value: bb.min.clone() }, uSize: { value: bb.getSize(new Vector3()) }, uC: { value: 0.5 }, uI: { value: 0 },
        uBar: { value: 0 }, uBarI: { value: 0 }, uAspect: { value: bb.max.x - bb.min.x > 0 ? (bb.max.x - bb.min.x) / Math.max(1e-6, bb.max.y - bb.min.y) : 0.8 },
        uTan: { value: Math.tan(BAR_ROLL) },
      },
      vertexShader: /* glsl */ `
        uniform vec3 uMin;
        uniform vec3 uSize;
        varying vec2 vP;
        void main() {
          vP = vec2((position.x - uMin.x) / uSize.x, 1.0 - (position.y - uMin.y) / uSize.y);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`,
      // sheen.ts's CSS gradient, in the same terms: q runs 0 at the top-left to 1 at the bottom-right
      fragmentShader: /* glsl */ `
        uniform float uC;
        uniform float uI;
        uniform float uBar;
        uniform float uBarI;
        uniform float uAspect;
        uniform float uTan;
        varying vec2 vP;
        void main() {
          float q = (vP.x + 0.8 * vP.y) / 1.8;
          float a = uI * (exp(-pow((q - uC) / 0.11, 2.0)) + 0.45 * exp(-pow((q - uC - 0.2) / 0.035, 2.0)));
          // the bar: a softbox's flat middle and falling edges, a wide faint glow round it,
          // tilted, rising from below the glass (uBar 0) to above it (uBar 1)
          float y = 1.18 - 1.36 * uBar;
          float d = (vP.y - y) + uTan * (vP.x - 0.5) * uAspect;
          float b = smoothstep(0.075, 0.03, abs(d)) + 0.22 * exp(-pow(d / 0.2, 2.0));
          float ends = smoothstep(-0.25, 0.2, vP.x) * smoothstep(1.25, 0.8, vP.x);
          b *= uBarI * ends;
          float alpha = clamp(a + b, 0.0, 1.0);
          gl_FragColor = vec4(vec3(1.0), alpha);
        }`,
    });
    sheen = new Mesh(g, sheenMaterial);
    sheen.renderOrder = 10;
    sheen.castShadow = false;
    glass.add(sheen);
  }
  const shadowMaterial = new ShadowMaterial({ opacity: 0, depthWrite: false });
  const floor = new Mesh(new PlaneGeometry(2, 2), shadowMaterial);
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -centre.y - 0.0005; // the GLB rests on y = 0
  floor.receiveShadow = true;
  floor.visible = false;
  yaw.add(floor);
  const sun = new DirectionalLight(0xffffff, 0);
  sun.position.set(-0.2, 1.6, 0.3);
  sun.castShadow = withFloor;
  sun.shadow.mapSize.set(1024, 1024);
  sun.shadow.radius = 14;
  sun.shadow.blurSamples = 24;
  sun.shadow.bias = -0.0004;
  Object.assign(sun.shadow.camera, { left: -0.45, right: 0.45, top: 0.45, bottom: -0.45, near: 0.2, far: 3 });
  yaw.add(sun, sun.target);

  // The fixed camera, and its projection at aspect 1: the picture every fit scales and shifts.
  const camera = new PerspectiveCamera(FOV, 1, DISTANCE - 1, DISTANCE + 1);
  camera.position.set(0, 0, DISTANCE);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  const base = camera.projectionMatrix.clone();
  const focal = 1 / Math.tan(MathUtils.degToRad(FOV / 2));

  const refresh = createEpaperRefresh({
    renderer,
    first,
    spec: { waveform: size.waveform, plates: size.screens.slice(1) },
    anisotropy: Math.min(4, renderer.capabilities.getMaxAnisotropy()),
    wake: opts.wake,
    motionOk: () => !document.hidden,
    holdMs: opts.holdMs ?? HOLD_MS,
    onShown: opts.onShown,
  });
  (screen.material as MeshStandardMaterial).dispose();
  screen.material = new MeshBasicMaterial({ map: refresh.texture, color: new Color(...SCREEN_WHITE), toneMapped: false });

  const rotation = new Matrix4();
  const tmp = new Matrix4();
  const p = new Vector3();
  /** The pose's projected box, in the aspect-1 picture's units (focal-scaled x/y over depth). */
  const bounds = (pose: Pose) => {
    rotation.makeRotationX(pose.pitch)
      .multiply(tmp.makeRotationY(pose.yaw))
      .multiply(tmp.makeRotationX(LEAN * (1 - pose.lean)));
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const q of points) {
      p.copy(q).applyMatrix4(rotation);
      const k = focal / (DISTANCE - p.z);
      const u = p.x * k, v = p.y * k;
      if (u < x0) x0 = u;
      if (u > x1) x1 = u;
      if (v < y0) y0 = v;
      if (v > y1) y1 = v;
    }
    return { x0, x1, y0, y1 };
  };

  let width = 1, height = 1, drawn = false;
  const projection = new Matrix4();
  return {
    canvas,
    refresh,
    aspect(pose) {
      const b = bounds(pose);
      return (b.x1 - b.x0) / (b.y1 - b.y0);
    },
    get drawn() { return drawn; },
    floor: withFloor,
    setSize(w, h) {
      width = Math.max(1, w);
      height = Math.max(1, h);
      renderer.setSize(width, height, false);
    },
    draw(rect, pose, sway = 0, at = null, bar = null) {
      if (!rect || rect.w <= 0 || rect.h <= 0) {
        renderer.clear();
        return;
      }
      const b = bounds(pose);
      // canvas px = o + k · picture units (y down): contained, centred, on the rect's bottom
      const k = Math.min(rect.w / (b.x1 - b.x0), rect.h / (b.y1 - b.y0));
      const ox = rect.x + (rect.w - k * (b.x1 - b.x0)) / 2 - k * b.x0;
      const oy = rect.y + rect.h + k * b.y0;
      const sx = (2 * k) / width, tx = (2 * ox) / width - 1;
      const sy = (2 * k) / height, ty = 1 - (2 * oy) / height;
      const e = base.elements; // column-major
      const row = (r: number) => [e[r], e[4 + r], e[8 + r], e[12 + r]];
      const [r0, r1, r2, r3] = [row(0), row(1), row(2), row(3)];
      projection.set(
        sx * r0[0] + tx * r3[0], sx * r0[1] + tx * r3[1], sx * r0[2] + tx * r3[2], sx * r0[3] + tx * r3[3],
        sy * r1[0] + ty * r3[0], sy * r1[1] + ty * r3[1], sy * r1[2] + ty * r3[2], sy * r1[3] + ty * r3[3],
        r2[0], r2[1], r2[2], r2[3],
        r3[0], r3[1], r3[2], r3[3],
      );
      camera.projectionMatrix.copy(projection);
      camera.projectionMatrixInverse.copy(projection).invert();
      pitch.rotation.set(pose.pitch, 0, 0);
      yaw.rotation.set(0, pose.yaw + sway, 0);
      lean.rotation.set(LEAN * (1 - pose.lean), 0, 0);
      if (sheenMaterial) {
        sheenMaterial.uniforms.uI.value = at === null ? 0 : SHEEN;
        if (at !== null) sheenMaterial.uniforms.uC.value = at;
        sheenMaterial.uniforms.uBarI.value = bar === null ? 0 : BAR;
        // two passes: each half of the dwell sweeps the bar once from below the glass to above it
        if (bar !== null) sheenMaterial.uniforms.uBar.value = barPass(bar);
      }
      if (sheen) sheen.visible = at !== null || bar !== null;
      floor.visible = withFloor && pose.ground > 0.001;
      shadowMaterial.opacity = SHADOW * pose.ground;
      // Drawn only while the floor shows — but once before anything else, so
      // the shadow map exists for every material's shadow sampler.
      renderer.shadowMap.needsUpdate = withFloor && (floor.visible || !drawn);
      renderer.render(scene, camera);
      drawn = true;
    },
    dispose() {
      refresh.dispose();
      model.traverse((o) => {
        const m = o as Mesh;
        if (!m.isMesh) return;
        m.geometry.dispose();
        (m.material as MeshStandardMaterial).dispose();
      });
      floor.geometry.dispose();
      shadowMaterial.dispose();
      sheenMaterial?.dispose();
      sun.shadow.dispose();
      disposeAll();
      renderer.forceContextLoss();
      canvas.remove();
    },
  };
}
