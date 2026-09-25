// The hero's frame: the GLB on its kickstand, lit by a neutral studio
// environment, swaying slowly and turnable by drag; its "screen" material is an
// e-paper refresh between the size's screens (epaper-refresh.ts).
//
// The GLB is already posed (shop scripts/addon-models/frame.mjs): metres, Y up,
// resting on y = 0, facing +Z and leaning back 12° on its kickstand. So it is
// shown as authored — turning its screen to face +Z would undo the lean and
// lift the kickstand off the ground.
import {
  ACESFilmicToneMapping, Box3, Color, Group, MathUtils, Mesh, MeshStandardMaterial, PerspectiveCamera,
  PMREMGenerator, Scene, SRGBColorSpace, Vector3, WebGLRenderer,
} from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { createEpaperRefresh } from './epaper-refresh';
import type { Size } from './card';

const YAW = -0.42;        // three-quarter view, radians: the frame's right side toward the camera
const SWAY = 0.06;        // idle sway amplitude, radians
const SWAY_PERIOD = 14;   // seconds
const DRAG_LIMIT = 0.9;   // radians either side
const PITCH = 0.1;        // the camera looks down this much, radians: a frame on a table, seen standing
const FILL = 0.94;        // the share of the stage the frame may reach at its widest
// How long each picture holds: well over twice the colour refresh (about
// 5.5 s), so the frame reads as a picture that sometimes changes, not as a
// frame forever refreshing. `?hold=` overrides it for tests.
const HOLD_MS = 14000;
// RoomEnvironment is a bright white room: at full strength it bleaches the
// walnut to oak and, with the screen's glow, washes the picture out under ACES.
// These keep the wood walnut and the screen's paper level with the white mat.
const EXPOSURE = 0.9;
const ENVIRONMENT = 0.7;

const loadImage = (src: string) => new Promise<HTMLImageElement>((ok, no) => {
  const img = new Image();
  img.decoding = 'async';
  img.onload = () => ok(img);
  img.onerror = no;
  img.src = src;
});

/** Sample points of the model (every vertex, thinned to about `max`), in its
 *  own space centred on `centre`: enough to fit the camera to the real
 *  silhouette rather than to a bounding box turned through the sway. */
function hull(model: Group, centre: Vector3, max = 6000): Vector3[] {
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

/** How far back the camera must stand, along its view direction, for every
 *  point to fit the view at every yaw in [lo, hi]. */
function fitDistance(points: Vector3[], lo: number, hi: number, fov: number, aspect: number): number {
  const tanV = Math.tan(MathUtils.degToRad(fov / 2)) * FILL;
  const tanH = tanV * aspect;
  const back = new Vector3(0, Math.sin(PITCH), Math.cos(PITCH)); // from the target toward the camera
  const up = new Vector3(0, Math.cos(PITCH), -Math.sin(PITCH));
  const p = new Vector3();
  let d = 0;
  for (let k = 0; k <= 8; k++) {
    const yaw = lo + ((hi - lo) * k) / 8;
    const c = Math.cos(yaw), s = Math.sin(yaw);
    for (const q of points) {
      p.set(q.x * c + q.z * s, q.y, -q.x * s + q.z * c);
      const z = p.dot(back);
      d = Math.max(d, z + Math.abs(p.x) / tanH, z + Math.abs(p.dot(up)) / tanV);
    }
  }
  return d;
}

export async function startViewer(
  stage: HTMLElement, size: Size,
  opts: { holdMs?: number; onShown: (i: number) => void; onArriving?: (i: number) => void; poster?: boolean },
): Promise<{ dispose(): void }> {
  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: !!opts.poster });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.setClearColor(0x000000, 0);
  renderer.toneMappingExposure = EXPOSURE;

  const scene = new Scene();
  const pmrem = new PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const env = pmrem.fromScene(room, 0.04).texture;
  room.dispose();
  scene.environment = env;
  scene.environmentIntensity = ENVIRONMENT;
  const camera = new PerspectiveCamera(24, 1, 0.01, 20);

  // The first screen seeds the glass; the refresh loads the rest as each nears.
  const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  const [gltf, first] = await Promise.all([loader.loadAsync(size.model), loadImage(size.screens[0])]);
  const model = gltf.scene;
  let screen: Mesh | undefined;
  model.traverse((o) => {
    const m = o as Mesh;
    if (m.isMesh && (m.material as MeshStandardMaterial).name === 'screen') screen = m;
  });
  if (!screen) throw new Error('no screen material');

  // Turn about the frame's own middle, and fit the camera to it at every sway.
  const box = new Box3().setFromObject(model);
  const centre = box.getCenter(new Vector3());
  const points = hull(model, centre);
  model.position.sub(centre);
  const pivot = new Group();
  pivot.add(model);
  scene.add(pivot);

  const refresh = createEpaperRefresh({
    renderer,
    first,
    spec: { waveform: size.waveform, plates: size.screens.slice(1) },
    anisotropy: Math.min(4, renderer.capabilities.getMaxAnisotropy()),
    wake: () => {},
    motionOk: () => !document.hidden,
    holdMs: opts.holdMs ?? HOLD_MS,
    onShown: opts.onShown,
    onArriving: opts.onArriving,
  });
  const mat = screen.material as MeshStandardMaterial;
  mat.map = refresh.texture;
  mat.color.set(0xffffff);
  mat.emissiveMap = refresh.texture;
  mat.emissive = new Color(0xffffff);
  mat.emissiveIntensity = 0.3;
  mat.needsUpdate = true;

  let drawn = false;
  const resize = () => {
    const { width, height } = stage.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    const d = fitDistance(points, YAW - SWAY, YAW + SWAY, camera.fov, camera.aspect);
    camera.position.set(0, Math.sin(PITCH) * d, Math.cos(PITCH) * d);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    // setSize clears the canvas; once the frame is showing, draw again at
    // once, before the browser paints, so a resize never flashes an empty stage.
    if (drawn && !document.hidden) renderer.render(scene, camera);
  };
  const ro = new ResizeObserver(resize);
  ro.observe(stage);
  resize();

  // Drag to turn; it eases back to the three-quarter view when let go.
  let drag = 0, dragTarget = 0, startX = 0, dragging = false;
  canvas.addEventListener('pointerdown', (e) => { dragging = true; startX = e.clientX - dragTarget * 300; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', (e) => {
    if (dragging) dragTarget = Math.max(-DRAG_LIMIT, Math.min(DRAG_LIMIT, (e.clientX - startX) / 300));
  });
  const release = () => { dragging = false; dragTarget = 0; };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);

  let visible = true;
  const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; });
  io.observe(stage);

  let raf = 0;
  // The stage turns live (poster out, canvas in) only once a frame has really
  // been drawn — the model and the first screen on the canvas — and then on
  // the rAF after it, once that frame is on screen. A throttled or hidden page
  // may run rAFs without drawing; revealing on a rAF count alone showed an
  // empty stage. Cancelled by dispose: a viewer superseded before then must
  // not hide the poster over a stage with no canvas.
  let reveal = 0;
  const t0 = performance.now();
  const frame = (now: number) => {
    raf = requestAnimationFrame(frame);
    if (!visible || document.hidden) return;
    refresh.tick(now);
    drag += (dragTarget - drag) * 0.12;
    const sway = opts.poster ? 0 : Math.sin(((now - t0) / 1000) * (2 * Math.PI / SWAY_PERIOD)) * SWAY;
    pivot.rotation.set(0, YAW + sway + drag, 0);
    renderer.render(scene, camera);
    if (!drawn) {
      drawn = true;
      reveal = requestAnimationFrame(() => stage.classList.add('live'));
    }
  };
  raf = requestAnimationFrame(frame);

  stage.appendChild(canvas);

  return {
    dispose() {
      cancelAnimationFrame(raf);
      cancelAnimationFrame(reveal);
      ro.disconnect();
      io.disconnect();
      refresh.dispose();
      env.dispose();
      pmrem.dispose();
      renderer.dispose();
      canvas.remove();
      stage.classList.remove('live');
    },
  };
}
