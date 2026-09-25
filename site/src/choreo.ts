// The frame's journey down the page (desktop, motion welcome, WebGL there):
// one WebGL frame on one fixed canvas behind the text, placed on each scroll
// by the stops it travels between.
//
//   hero      the cover's frame, three-quarter on its kickstand, cycling species
//   centre    dead-on, ~80% of the page wide, drifting up at 0.3× the scroll
//   art       dead-on in the art spread's left half, showing the flamingo
//   wall 1    the gallery wall's empty first place: the still takes over there
//   wall 12   the wall's last frame tears off where the still hands back…
//   table     …and leans back on its kickstand on III's table, showing the cardinal
//
// Every stop is an invisible slot on the page with the frame's own aspect, so
// the layout decides where the frame goes and the frame only follows it. A
// stop holds over a range of scroll; between two stops the frame's box and
// pose ease (smoothstep) from one to the other, each end still moving with the
// page as it does at rest. Layout is read only on resize and font load; each
// frame reads scrollY alone. Nothing is drawn while nothing changes.
import { FLAT, HERO, TABLE, lerpPose, loadFrame, type Frame3D, type Pose, type Rect } from './viewer';
import type { Size } from './card';

const SWAY = 0.06;        // the hero's idle sway, radians
const SWAY_PERIOD = 14;   // seconds
const DRIFT = 0.3;        // the centre stop moves at this share of the scroll

// The poster's canvas is 1200 × 1400 with the frame at 111,108 → 1086,1352.
export const heroRect = (stage: DOMRect | Rect, offsetY = 0): Rect => {
  const s = 'width' in stage ? { x: stage.x, y: stage.y, w: stage.width, h: stage.height } : stage;
  return { x: s.x + (s.w * 111) / 1200, y: s.y + offsetY + (s.h * 108) / 1400, w: (s.w * 975) / 1200, h: (s.h * 1244) / 1400 };
};

const smooth = (t: number) => t * t * (3 - 2 * t);
const clamp01 = (t: number) => Math.max(0, Math.min(1, t));
const lerpRect = (a: Rect, b: Rect, t: number): Rect => ({
  x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, w: a.w + (b.w - a.w) * t, h: a.h + (b.h - a.h) * t,
});

/** What the frame shows. */
type Screen = 'cycle' | 'flamingo' | 'oriole' | 'cardinal';

interface Stop {
  /** The frame's box on screen at scroll `s`. */
  rect(s: number): Rect;
  pose: Pose;
  /** The scroll range it holds over. */
  s0: number;
  s1: number;
}

interface Layout {
  stops: Stop[];
  /** Stop index after which the frame is the wall's still, not the canvas… */
  landAt: number;
  /** …until this stop, where it tears off again. */
  tearAt: number;
  vw: number;
  vh: number;
}

/** A slot's box in page coordinates. */
const pageBox = (el: Element): Rect => {
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y + scrollY, w: r.width, h: r.height };
};
const scrolled = (b: Rect) => (s: number): Rect => ({ x: b.x, y: b.y - s, w: b.w, h: b.h });

function measure(els: Els): Layout {
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const stops: Stop[] = [];
  const hero = heroRect(els.stage.getBoundingClientRect(), scrollY);
  stops.push({ rect: scrolled(hero), pose: HERO, s0: -Infinity, s1: 0 });
  let landAt = Infinity, tearAt = Infinity;
  const after = (s: number, min: number) => Math.max(s, stops[stops.length - 1].s1 + min * vh);

  if (els.centre) {
    // Its size and x from the slot; its height on screen from the viewport's middle, drifting.
    const b = pageBox(els.centre);
    const spacer = pageBox(els.centre.parentElement!);
    const s0 = after(spacer.y - 0.1 * vh, 0.3);
    const s1 = after(spacer.y + 0.4 * vh, 0);
    const mid = (s0 + s1) / 2;
    const y = (vh - b.h) / 2;
    stops.push({
      rect: (s) => ({ x: b.x, y: y - DRIFT * (Math.max(s0, Math.min(s1, s)) - mid) - Math.max(0, s - s1), w: b.w, h: b.h }),
      pose: FLAT, s0, s1,
    });
  }
  if (els.art) {
    const b = pageBox(els.art);
    const s0 = after(b.y + b.h / 2 - vh / 2, 0.3);
    stops.push({ rect: scrolled(b), pose: FLAT, s0, s1: s0 + 0.15 * vh });
  }
  if (els.first && els.last) {
    const b1 = pageBox(els.first);
    const s = after(b1.y - 0.22 * vh, 0.3);
    stops.push({ rect: scrolled(b1), pose: FLAT, s0: s, s1: s });
    landAt = stops.length - 1;
    const b12 = pageBox(els.last);
    const t = after(b12.y + b12.h / 2 - vh / 2, 0.2);
    stops.push({ rect: scrolled(b12), pose: FLAT, s0: t, s1: t });
    tearAt = stops.length - 1;
    if (els.table) {
      const b = pageBox(els.table);
      stops.push({ rect: scrolled(b), pose: TABLE, s0: after(b.y + b.h / 2 - vh / 2, 0.3), s1: Infinity });
    }
  }
  const last = stops[stops.length - 1];
  if (last.s1 < Infinity) last.s1 = Infinity;
  return { stops, landAt, tearAt, vw, vh };
}

interface State {
  rect: Rect | null;
  pose: Pose;
  /** How much of the hero's sway is on, 0–1. */
  sway: number;
  landed: boolean;
  torn: boolean;
  /** What the glass should show, or null to leave it as it is (hysteresis). */
  screen: Screen | null;
  /** 0 at the hero, 1 once the frame has left it. */
  lift: number;
  /** 0 until the frame sets off for the table, 1 once it is on it. */
  land: number;
}

function at(l: Layout, s: number): State {
  const { stops, landAt, tearAt } = l;
  let i = 0;
  while (i < stops.length - 1 && s > stops[i].s1) i++;
  // now s <= stops[i].s1: holding at i, or travelling into it from i - 1
  const stop = stops[i];
  const landed = i > landAt || (i === landAt && s >= stop.s0);
  const torn = i > tearAt || (i === tearAt && s >= stop.s0);
  let rect: Rect | null, pose: Pose, t = 1;
  if (s >= stop.s0 || i === 0) {
    rect = stop.rect(s);
    pose = stop.pose;
  } else {
    const prev = stops[i - 1];
    t = smooth(clamp01((s - prev.s1) / (stop.s0 - prev.s1)));
    rect = lerpRect(prev.rect(s), stop.rect(s), t);
    pose = lerpPose(prev.pose, stop.pose, t);
  }
  const fromHero = i === 0 ? 0 : i === 1 && s < stop.s0 ? t : 1;
  if (landed && !torn) rect = null;
  // The glass: the species cycle at the hero, the flamingo from the centre to
  // the wall, the wall's last print when it tears off, the cardinal on the table.
  let screen: Screen | null;
  if (i === 0 || (i === 1 && s < stop.s0)) screen = fromHero >= 0.5 ? 'flamingo' : fromHero <= 0.3 ? 'cycle' : null;
  else if (!torn) screen = landed ? null : 'flamingo';
  else if (i > tearAt + 1 || (i === tearAt + 1 && s >= stop.s0)) screen = 'cardinal';
  else if (i === tearAt) screen = 'oriole';
  else screen = t >= 0.75 ? 'cardinal' : t <= 0.5 ? 'oriole' : null;
  const land = i > tearAt + 1 ? 1 : i === tearAt + 1 ? (s >= stop.s0 ? 1 : t) : 0;
  return { rect, pose, sway: 1 - fromHero, landed, torn, screen, lift: fromHero, land };
}

interface Els {
  stage: HTMLElement;
  centre: HTMLElement | null;
  art: HTMLElement | null;
  wall: HTMLElement | null;
  first: HTMLElement | null;
  last: HTMLElement | null;
  table: HTMLElement | null;
}

/**
 * Run the page's choreography. The layout and the wall's classes start at
 * once; the frame joins when its model has loaded. Rejects (and undoes
 * itself) if the frame cannot be drawn.
 */
export async function startPage(size: Size, opts: {
  holdMs?: number; onShown: (i: number) => void; poster?: boolean;
}): Promise<{ dispose(): void }> {
  const root = document.documentElement;
  const slots = [...document.querySelectorAll<HTMLElement>('.wall .slot')];
  const els: Els = {
    stage: document.getElementById('stage')!,
    centre: document.getElementById('centre-slot'),
    art: document.getElementById('art-slot'),
    wall: document.querySelector('.wall'),
    first: slots[0] ?? null,
    last: slots[slots.length - 1] ?? null,
    table: document.getElementById('table-slot'),
  };
  const screens: Record<Exclude<Screen, 'cycle'>, string | undefined> = {
    flamingo: size.wall?.[0],
    oriole: size.wall?.[size.wall.length - 1],
    cardinal: size.wall?.find((f) => f.includes('cardinal')),
  };

  let layout = measure(els);
  let frame: Frame3D | null = null;
  let raf = 0, reveal = 0, dirty = true, lastKey = '', shownScreen: Screen | 'hidden' | undefined;
  const t0 = performance.now();
  const request = () => { if (!raf) raf = requestAnimationFrame(tick); };

  const applyClasses = (st: State) => {
    els.wall?.classList.toggle('landed', st.landed);
    els.wall?.classList.toggle('torn', st.torn);
  };
  const applyScreen = (st: State) => {
    if (!frame) return;
    const want: Screen | 'hidden' | null = st.rect ? st.screen : 'hidden';
    if (want === null || want === shownScreen) return;
    // Out of sight (or not yet drawn), the glass changes without a refresh.
    const instant = shownScreen === undefined || shownScreen === 'hidden';
    shownScreen = want;
    if (want === 'hidden') return;
    const src = want === 'cycle' ? null : screens[want];
    if (src !== undefined) frame.refresh.show(src, instant);
  };

  function tick(now: number) {
    raf = 0;
    const st = at(layout, scrollY);
    applyClasses(st);
    if (!frame) return;
    // A hidden page runs no rAFs; one that says it is hidden but runs them
    // (a throttled tab) keeps checking back without drawing.
    if (document.hidden) { request(); return; }
    applyScreen(st);
    const r = frame.refresh.tick(now);
    const sway = opts.poster || !st.sway ? 0 : Math.sin(((now - t0) / 1000) * (2 * Math.PI / SWAY_PERIOD)) * SWAY * st.sway;
    const key = st.rect ? `${st.rect.x},${st.rect.y},${st.rect.w},${st.rect.h},${st.pose.yaw},${st.pose.lean},${st.pose.pitch},${st.pose.ground},${sway}` : '';
    if (r.changed || dirty || key !== lastKey) {
      frame.draw(st.rect, st.pose, sway);
      frame.canvas.classList.toggle('empty', !st.rect);
      dirty = false;
      lastKey = key;
      root.style.setProperty('--lift', st.lift.toFixed(3));
      root.style.setProperty('--land', st.land.toFixed(3));
      if (frame.drawn && !reveal) {
        // Live (poster out, canvas in) on the rAF after the first real draw, once it is on screen.
        reveal = requestAnimationFrame(() => {
          els.stage.classList.add('live');
          frame?.canvas.classList.add('live');
        });
      }
    }
    if (r.busy || sway !== 0) request();
  }

  const relayout = () => {
    layout = measure(els);
    if (frame) frame.setSize(layout.vw, layout.vh);
    dirty = true;
    request();
  };
  const ro = new ResizeObserver(relayout);
  ro.observe(document.body);
  addEventListener('resize', relayout);
  addEventListener('scroll', request, { passive: true });
  const onVisible = () => { if (!document.hidden) request(); };
  document.addEventListener('visibilitychange', onVisible);
  void document.fonts?.ready.then(relayout);
  request();

  const undo = () => {
    cancelAnimationFrame(raf);
    cancelAnimationFrame(reveal);
    ro.disconnect();
    removeEventListener('resize', relayout);
    removeEventListener('scroll', request);
    document.removeEventListener('visibilitychange', onVisible);
    els.wall?.classList.remove('landed', 'torn');
    els.stage.classList.remove('live');
    root.style.removeProperty('--lift');
    root.style.removeProperty('--land');
    frame?.dispose();
    frame = null;
  };

  try {
    frame = await loadFrame(size, { holdMs: opts.holdMs, onShown: opts.onShown, wake: request, keep: opts.poster });
  } catch (e) {
    undo();
    throw e;
  }
  for (const src of Object.values(screens)) if (src) frame.refresh.prepare(src);
  frame.canvas.className = 'ff3d';
  frame.setSize(layout.vw, layout.vh);
  document.body.prepend(frame.canvas);
  dirty = true;
  request();
  return { dispose: undo };
}

/**
 * A phone's hero: the frame in the cover's stage, three-quarter, swaying and
 * turnable by drag, cycling species — no journey.
 */
export async function startStage(stage: HTMLElement, size: Size, opts: {
  holdMs?: number; onShown: (i: number) => void;
}): Promise<{ dispose(): void }> {
  let raf = 0, reveal = 0, visible = true;
  const request = () => { if (!raf) raf = requestAnimationFrame(tick); };
  const frame = await loadFrame(size, { holdMs: opts.holdMs, onShown: opts.onShown, wake: request });
  const { canvas } = frame;
  let box: Rect = { x: 0, y: 0, w: 1, h: 1 };
  const resize = () => {
    const r = stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    frame.setSize(r.width, r.height);
    box = heroRect({ x: 0, y: 0, w: r.width, h: r.height });
    // setSize clears the canvas; once the frame is showing, draw again at
    // once, before the browser paints, so a resize never flashes an empty stage.
    if (frame.drawn && !document.hidden) draw(performance.now());
  };
  const ro = new ResizeObserver(resize);
  ro.observe(stage);
  const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) request(); });
  io.observe(stage);

  // Drag to turn; it eases back to the three-quarter view when let go.
  let drag = 0, dragTarget = 0, startX = 0, dragging = false;
  canvas.addEventListener('pointerdown', (e) => { dragging = true; startX = e.clientX - dragTarget * 300; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', (e) => {
    if (dragging) dragTarget = Math.max(-0.9, Math.min(0.9, (e.clientX - startX) / 300));
  });
  const release = () => { dragging = false; dragTarget = 0; };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);

  const t0 = performance.now();
  const draw = (now: number) => {
    const sway = Math.sin(((now - t0) / 1000) * (2 * Math.PI / SWAY_PERIOD)) * SWAY;
    frame.draw(box, HERO, sway + drag);
  };
  function tick(now: number) {
    raf = requestAnimationFrame(tick);
    if (!visible || document.hidden) return;
    frame.refresh.tick(now);
    drag += (dragTarget - drag) * 0.12;
    draw(now);
    if (!reveal) reveal = requestAnimationFrame(() => stage.classList.add('live'));
  }
  stage.appendChild(canvas);
  resize();
  request();
  return {
    dispose() {
      cancelAnimationFrame(raf);
      cancelAnimationFrame(reveal);
      ro.disconnect();
      io.disconnect();
      frame.dispose();
      stage.classList.remove('live');
    },
  };
}

/**
 * `?wall=<index>` (or `?wall=table`): one still for the page, rendered by the
 * live frame itself — the frame dead-on showing the wall's `index`th screen
 * (or on the table showing the cardinal), filling the viewport. scripts/wall.mjs
 * sizes the viewport to the pose's aspect and captures it; the page says
 * `data-wall="ready"` on <html> once it has drawn.
 */
/** Around the table's still, as shares of the frame's box: left, top, right,
 *  bottom (styles.css places the image by the same numbers). */
export const TABLE_PAD = [0.15, 0.15, 0.15, 0.05];

export async function startWallRender(size: Size, which: string): Promise<void> {
  const table = which === 'table';
  const src = table ? size.wall.find((f) => f.includes('cardinal'))! : size.wall[Number(which)];
  let raf = 0;
  const request = () => { if (!raf) raf = requestAnimationFrame(tick); };
  const frame = await loadFrame(size, { wake: request, keep: true, holdMs: 1e9 });
  const pose = table ? TABLE : FLAT;
  const [l, t, r, b] = table ? TABLE_PAD : [0, 0, 0, 0];
  document.documentElement.dataset.aspect = (frame.aspect(pose) * (1 + l + r) / (1 + t + b)).toFixed(5);
  frame.canvas.className = 'ff3d live';
  document.body.prepend(frame.canvas);
  frame.refresh.show(src, true);
  let settled = 0;
  function tick(now: number) {
    raf = 0;
    const w = document.documentElement.clientWidth, h = document.documentElement.clientHeight;
    frame.setSize(w, h);
    frame.refresh.tick(now);
    // the table's still leaves room around the frame for its shadow (TABLE_PAD)
    const [l, t, r, b] = table ? TABLE_PAD : [0, 0, 0, 0];
    const fw = w / (1 + l + r), fh = h / (1 + t + b);
    frame.draw({ x: l * fw, y: t * fh, w: fw, h: fh }, pose);
    // a few frames on from the screen's picture reaching the glass
    if (frame.refresh.showing() !== src || ++settled < 5) request();
    else document.documentElement.dataset.wall = 'ready';
  }
  request();
}
