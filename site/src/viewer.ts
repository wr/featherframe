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
  ACESFilmicToneMapping, Box3, Color, DirectionalLight, Group, MathUtils, Matrix4, Mesh, MeshStandardMaterial,
  PCFShadowMap, PerspectiveCamera, PlaneGeometry, PMREMGenerator, Scene, ShadowMaterial, SRGBColorSpace, Vector3,
  WebGLRenderer,
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
// RoomEnvironment is a bright white room: at full strength it bleaches the
// walnut to oak and, with the screen's glow, washes the picture out under ACES.
// These keep the wood walnut and the screen's paper level with the white mat.
const EXPOSURE = 0.9;
const ENVIRONMENT = 0.7;
// The table's shadow: a light that lights nothing (so the frame looks as it
// does everywhere else) but casts the frame onto a shadow-only floor, from
// above and in front, so it falls back toward the wall, crisp where the frame
// meets the table. SHADOW is its darkness at full `ground`.
const SHADOW = 0.2;
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
   *  its box) fitted to `rect`, in canvas CSS pixels; `null` draws nothing. */
  draw(rect: Rect | null, pose: Pose, sway?: number): void;
  /** True once something has really been drawn. */
  readonly drawn: boolean;
  dispose(): void;
}

export async function loadFrame(size: Size, opts: {
  holdMs?: number;
  onShown?: (i: number) => void;
  /** Called when the screen needs the loop to run again (a hold is up, a screen loaded). */
  wake: () => void;
  /** Keep the drawing buffer, for the poster and wall renders' screenshots. */
  keep?: boolean;
}): Promise<Frame3D> {
  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: !!opts.keep });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.setClearColor(0x000000, 0);
  renderer.toneMappingExposure = EXPOSURE;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = PCFShadowMap;
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
  let screen: Mesh | undefined;
  model.traverse((o) => {
    const m = o as Mesh;
    if (m.isMesh && (m.material as MeshStandardMaterial).name === 'screen') screen = m;
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

  model.traverse((o) => { if ((o as Mesh).isMesh) o.castShadow = true; });
  const shadowMaterial = new ShadowMaterial({ opacity: 0, depthWrite: false });
  const floor = new Mesh(new PlaneGeometry(2, 2), shadowMaterial);
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -centre.y - 0.0005; // the GLB rests on y = 0
  floor.receiveShadow = true;
  floor.visible = false;
  yaw.add(floor);
  const sun = new DirectionalLight(0xffffff, 0);
  sun.position.set(-0.2, 1.6, 0.3);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.radius = 3;
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
  const mat = screen.material as MeshStandardMaterial;
  mat.map = refresh.texture;
  mat.color.set(0xffffff);
  mat.emissiveMap = refresh.texture;
  mat.emissive = new Color(0xffffff);
  mat.emissiveIntensity = 0.3;
  mat.needsUpdate = true;

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
    setSize(w, h) {
      width = Math.max(1, w);
      height = Math.max(1, h);
      renderer.setSize(width, height, false);
    },
    draw(rect, pose, sway = 0) {
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
      floor.visible = pose.ground > 0.001;
      shadowMaterial.opacity = SHADOW * pose.ground;
      // Drawn only while the floor shows — but once before anything else, so
      // the shadow map exists for every material's shadow sampler.
      renderer.shadowMap.needsUpdate = floor.visible || !drawn;
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
      sun.shadow.dispose();
      disposeAll();
      renderer.forceContextLoss();
      canvas.remove();
    },
  };
}
