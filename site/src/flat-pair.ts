// The e-paper section's pair wherever the journey is not drawing it (a phone, reduced motion, a desktop whose 3D
// frame failed): each frame a flat picture of itself dead-on with its screen a hole (scripts/wall.mjs hole), over a
// small canvas of its own where the glass refreshes exactly as the 3D frame's does (epaper-refresh.ts), at the
// panel's own pace. No models: only the two pictures each frame refreshes between. The pair refreshes once by itself
// when it is well in view (never with reduced motion), and again on Refresh. Without WebGL the section's stills stay.
import { Color, Mesh, MeshBasicMaterial, OrthographicCamera, PlaneGeometry, Scene, SRGBColorSpace, WebGLRenderer } from 'three';
import { createEpaperRefresh, SCREEN_WHITE, type EpaperRefresh } from './epaper-refresh';
import { MODELS, pairPictures, pairUI, type Model } from './pair-ui';
import type { SiteData } from './card';

/** How long the pair holds still, in view, before it refreshes by itself. */
const BEAT_MS = 700;

const loadImage = (src: string) => new Promise<HTMLImageElement>((ok, no) => {
  const img = new Image();
  img.decoding = 'async';
  img.onload = () => ok(img);
  img.onerror = no;
  img.src = src;
});

interface Glass {
  refresh: EpaperRefresh;
  draw(): void;
  dispose(): void;
}

async function glass(canvas: HTMLCanvasElement, src: string, waveform: 'gc16' | 'spectra6', speed: number, wake: () => void): Promise<Glass> {
  const renderer = new WebGLRenderer({ canvas, antialias: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = SRGBColorSpace;
  let first: HTMLImageElement;
  try {
    first = await loadImage(src);
  } catch (e) {
    renderer.dispose();
    throw e;
  }
  const refresh = createEpaperRefresh({
    renderer, first, firstSrc: src, spec: { waveform, plates: [] },
    anisotropy: Math.min(4, renderer.capabilities.getMaxAnisotropy()),
    wake, motionOk: () => !document.hidden, holdMs: 1e9, speed,
  });
  const material = new MeshBasicMaterial({ map: refresh.texture, color: new Color(...SCREEN_WHITE), toneMapped: false });
  const quad = new Mesh(new PlaneGeometry(2, 2), material);
  const scene = new Scene();
  scene.add(quad);
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0, 1);
  let w = 0, h = 0;
  return {
    refresh,
    draw() {
      const cw = canvas.clientWidth, ch = canvas.clientHeight;
      if (cw !== w || ch !== h) renderer.setSize(w = cw, h = ch, false);
      renderer.render(scene, camera);
    },
    dispose() {
      refresh.dispose();
      material.dispose();
      quad.geometry.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
    },
  };
}

export async function startFlatPair(data: SiteData, opts: { reduced: boolean; speed?: number }): Promise<{ dispose(): void }> {
  const none = { dispose() {} };
  const found = pairUI();
  if (!found) return none;
  const ui = found;
  try {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) return none;
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  } catch { return none; }

  let raf = 0, beat = 0, ran = false, seen = false, disposed = false;
  const request = () => { if (!raf && !disposed) raf = requestAnimationFrame(tick); };
  const added: HTMLElement[] = [];
  const glasses = {} as Record<Model, Glass>;
  try {
    await Promise.all(MODELS.map(async (m) => {
      const slot = ui.el.querySelector<HTMLElement>(`.ep.s${m} .slot`)!;
      const flat = document.createElement('div');
      flat.className = 'flat';
      flat.setAttribute('aria-hidden', 'true');
      const canvas = document.createElement('canvas');
      const body = new Image();
      body.alt = '';
      body.src = `img/pair/${m}-frame.webp`;
      flat.append(canvas, body);
      const [g] = await Promise.all([glass(canvas, pairPictures(data.sizes[m])[1], data.sizes[m].waveform, opts.speed ?? 1, request), body.decode()]);
      glasses[m] = g;
      g.refresh.prepare(pairPictures(data.sizes[m])[0]);
      slot.append(flat);
      added.push(flat);
    }));
  } catch (e) {
    for (const g of Object.values(glasses)) g.dispose();
    for (const el of added) el.remove();
    console.warn('featherframe: the e-paper pair is unavailable', e);
    return none;
  }
  for (const m of MODELS) glasses[m].draw();
  ui.live(true);

  const idle = () => MODELS.every((m) => !glasses[m].refresh.progress() && !ui.watch[m].running);
  /** Both to whichever of the two pictures the 10.3-inch is not showing, at the panel's own pace. */
  const run = () => {
    if (!idle()) return;
    const k = glasses['10'].refresh.onGlass() === pairPictures(data.sizes['10'])[0] ? 1 : 0;
    for (const m of MODELS) {
      const src = pairPictures(data.sizes[m])[k];
      if (glasses[m].refresh.onGlass() !== src) ui.watch[m].start();
      glasses[m].refresh.show(src, 'panel');
    }
    ui.ready(false);
    request();
  };
  const offRefresh = ui.onRefresh(run);

  function tick(now: number) {
    raf = 0;
    let busy = false;
    for (const m of MODELS) {
      const g = glasses[m];
      const r = g.refresh.tick(now);
      if (r.changed) g.draw();
      busy ||= r.busy;
      ui.watch[m].update(g.refresh.progress(), now);
      busy ||= ui.watch[m].running;
    }
    ui.ready(idle());
    if (busy) request();
  }

  // Once, by itself, when the pair is well in view and has held still a moment there.
  const io = new IntersectionObserver(([e]) => {
    seen = e.isIntersecting;
    clearTimeout(beat);
    if (seen && !ran && !opts.reduced) beat = window.setTimeout(() => { if (seen && !ran && idle()) { ran = true; run(); } }, BEAT_MS);
  }, { threshold: 0.6 });
  io.observe(ui.el.querySelector('.duo') ?? ui.el);
  const onResize = () => { for (const m of MODELS) glasses[m].draw(); };
  addEventListener('resize', onResize);
  ui.ready(true);

  return {
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf);
      clearTimeout(beat);
      io.disconnect();
      offRefresh();
      removeEventListener('resize', onResize);
      for (const m of MODELS) glasses[m].dispose();
      for (const el of added) el.remove();
      ui.live(false);
      ui.ready(false);
    },
  };
}
