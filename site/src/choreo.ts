// The frame's journey down the page (desktop, motion welcome, WebGL there):
// one WebGL frame on one fixed canvas, placed on each scroll by the stops it
// travels between.
//
//   hero      the cover's frame, three-quarter on its kickstand on the cover's table
//   centre    dead-on, about 90% of the viewport tall, frozen dead centre for its
//             dwell while a studio light's bar sweeps up its glass
//   art       dead-on in the art spread's left half, showing the Carolina Parakeet, pinned
//             there while the spread's text scrolls past
//   wall 1    the gallery wall's empty first place: the still takes over there
//   wall 12   the wall's last frame tears off as its top meets the sticky folio…
//   table     …and leans back on its kickstand on III's table, pinned while the
//             song and the photograph scroll past
//
// Every stop is an invisible slot on the page with the frame's own aspect, so
// the layout decides where the frame goes and the frame only follows it; a
// pinned stop is a sticky slot, and the frame follows its sticking. A stop
// holds over a range of scroll; between two stops the frame's box and pose
// ease (smoothstep) from one to the other, each end still moving with the page
// as it does at rest — except out of a `hold` stop, whose flight starts from
// where the frame was when it set off (the hero, the torn-off frame), so the
// frame never rides up with the page before it turns for its next stop. Layout is read only on resize and font load; each frame
// reads scrollY alone. Nothing is drawn while nothing changes.
//
// The canvas sits behind the text, except on the two flights that cross it —
// down into the wall's first place, and from the wall's last to the table —
// where the frame passes over the captions it flies across, but still under
// the running head and the wall's sticky folio.
//
// With the wall on B&W, the frame that leaves the cover is the 10-inch in
// sixteen grays (loaded when B&W is first chosen): it takes over from the
// 13-inch as the glass repaints on the way up, so it lands in and tears off
// the wall as the wall's own gray frames.
import { FLAT, HERO, TABLE, lerpPose, loadFrame, sheenAt, type Frame3D, type Pose, type Rect } from './viewer';
import type { SiteData, Size } from './card';

const SWAY = 0.06;        // the hero's idle sway, radians
const SWAY_PERIOD = 14;   // seconds
const ART_LINGER = 0.1;   // viewport heights the art stop holds past its pin's release
const LAND_AT = 0.92;     // the wall's first place is landed in with its bottom this far down the window
const CONTACT = 8;        // px: the table's shadow comes in over the frame's last this many of descent
const TEAR_GAP = 12;      // px: the wall's last frame tears off with its top this far under the sticky folio

// The poster's canvas is 1200 × 1400 with the frame at 111,108 → 1086,1352.
export const heroRect = (stage: DOMRect | Rect, offsetY = 0): Rect => {
  const s = 'width' in stage ? { x: stage.x, y: stage.y, w: stage.width, h: stage.height } : stage;
  return { x: s.x + (s.w * 111) / 1200, y: s.y + offsetY + (s.h * 108) / 1400, w: (s.w * 975) / 1200, h: (s.h * 1244) / 1400 };
};

const smooth = (t: number) => t * t * (3 - 2 * t);
const clamp01 = (t: number) => Math.max(0, Math.min(1, t));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const lerpRect = (a: Rect, b: Rect, t: number): Rect => ({
  x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t), w: lerp(a.w, b.w, t), h: lerp(a.h, b.h, t),
});

/** What the frame shows: on the table, the latest detection (main.ts, <html data-detected>). */
type Screen = 'cycle' | 'art' | 'oriole' | 'table';
type Model = '13' | '10';

interface Stop {
  /** The frame's box on screen at scroll `s`. */
  rect(s: number): Rect;
  pose: Pose;
  /** The scroll range it holds over. */
  s0: number;
  s1: number;
  /** How the frame flies in: `drop` shrinks to the stop's size first and
   *  then settles into it, so it never sweeps across the stop's neighbours. */
  path?: 'drop';
  /** The flight in crosses the page's text: draw the frame over it. */
  over?: boolean;
  /** The flight out starts from where the frame was at s1, not moving with the page. */
  hold?: boolean;
  /** A light bar sweeps its glass as the page scrolls through its hold. */
  bar?: boolean;
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

/** A box in page coordinates. */
const pageBox = (el: Element): Rect => {
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y + scrollY, w: r.width, h: r.height };
};
const scrolled = (b: Rect) => (s: number): Rect => ({ x: b.x, y: b.y - s, w: b.w, h: b.h });

/**
 * A slot inside a sticky element: its box on screen at any scroll, and the
 * scroll range over which it is stuck. Measured unstuck (html.measuring), so
 * the natural place is read whatever the scroll.
 */
function pinned(slot: Element, pin: HTMLElement) {
  const b = pageBox(slot);
  const p = pageBox(pin);
  const parent = pin.parentElement!;
  const cs = getComputedStyle(parent);
  const pb = pageBox(parent);
  const bottom = pb.y + pb.h - parseFloat(cs.paddingBottom) - parseFloat(cs.borderBottomWidth);
  const top = pinTops.get(pin) ?? 0;
  const dy = b.y - p.y;
  const end = bottom - p.h; // the pin's lowest page y
  return {
    box: b,
    /** stuck from s0 to s1 */
    s0: p.y - top,
    s1: end - top,
    rect: (s: number): Rect => ({ x: b.x, y: Math.min(Math.max(p.y - s, top), end - s) + dy, w: b.w, h: b.h }),
  };
}

/** Each pin's sticky top, read before html.measuring unpins it. */
const pinTops = new WeakMap<Element, number>();

function measure(els: Els): Layout {
  const root = document.documentElement;
  for (const pin of document.querySelectorAll('.pin')) pinTops.set(pin, parseFloat(getComputedStyle(pin).top) || 0);
  root.classList.add('measuring');
  try {
    return measureNow(els);
  } finally {
    root.classList.remove('measuring');
  }
}

function measureNow(els: Els): Layout {
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const nav = els.head ? els.head.getBoundingClientRect().height : 0;
  const stops: Stop[] = [];
  const hero = heroRect(els.stage.getBoundingClientRect(), scrollY);
  stops.push({ rect: scrolled(hero), pose: HERO, s0: -Infinity, s1: 0, hold: true });
  let landAt = Infinity, tearAt = Infinity;
  const after = (s: number, min: number) => Math.max(s, stops[stops.length - 1].s1 + min * vh);

  if (els.centre) {
    // Its size and x from the slot; on screen, dead centre in the viewport
    // below the running head, and frozen there: it does not move with the page.
    const b = pageBox(els.centre);
    const spacer = pageBox(els.centre.parentElement!);
    const s0 = after(spacer.y - 0.1 * vh, 0.3);
    const s1 = after(spacer.y + 0.4 * vh, 0);
    const box = { x: b.x, y: nav + (vh - nav - b.h) / 2, w: b.w, h: b.h };
    stops.push({ rect: () => box, pose: FLAT, s0, s1, bar: true });
  }
  if (els.art) {
    const p = pinned(els.art, els.art);
    const s0 = after(p.s0, 0.3);
    // it stays a moment after the spread lets go of it, going up with the page, before it sets off
    stops.push({ rect: p.rect, pose: FLAT, s0, s1: Math.max(s0, p.s1 + ART_LINGER * vh) });
  }
  if (els.first && els.last) {
    // the wall's frames as drawn: in B&W, each still is scaled to the 10-inch's true size
    const b1 = pageBox(els.first);
    // it lands as the wall's first row comes up to the bottom of the window
    const s = after(b1.y + b1.h - LAND_AT * vh, 0.35);
    stops.push({ rect: scrolled(b1), pose: FLAT, s0: s, s1: s, path: 'drop', over: true });
    landAt = stops.length - 1;
    const b12 = pageBox(els.last);
    // it tears off as its top comes up to just under the wall's sticky folio,
    // and leaves from there: it never goes under (or over) the folio
    const folio = els.folio ? els.folio.getBoundingClientRect().height : 0;
    const t = after(b12.y - (nav + folio + TEAR_GAP), 0.2);
    stops.push({ rect: scrolled(b12), pose: FLAT, s0: t, s1: t, hold: true });
    tearAt = stops.length - 1;
    if (els.table && els.tablePin) {
      const p = pinned(els.table, els.tablePin);
      stops.push({ rect: p.rect, pose: TABLE, s0: after(p.s0, 0.3), s1: Infinity, over: true });
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
  /** The light bar on the glass, 0 (below it) … 1 (above it), or null for none. */
  bar: number | null;
  landed: boolean;
  torn: boolean;
  /** Drawn over the page's text. */
  over: boolean;
  /** What the glass should show, or null to leave it as it is (hysteresis). */
  screen: Screen | null;
  /** 0 at the hero, 1 once the frame has left it. */
  lift: number;
  /** 0 until the frame sets off for the table, 1 once it is on it. */
  land: number;
  /** The table's shadow, 0–1: only as the frame's foot meets the table, over its last CONTACT px. */
  ground: number;
}

function at(l: Layout, s: number): State {
  const { stops, landAt, tearAt } = l;
  let i = 0;
  while (i < stops.length - 1 && s > stops[i].s1) i++;
  // now s <= stops[i].s1: holding at i, or travelling into it from i - 1
  const stop = stops[i];
  const landed = i > landAt || (i === landAt && s >= stop.s0);
  const torn = i > tearAt || (i === tearAt && s >= stop.s0);
  let rect: Rect | null, pose: Pose, t = 1, over: boolean, target: Rect | null = null;
  if (s >= stop.s0 || i === 0) {
    rect = stop.rect(s);
    pose = stop.pose;
  } else {
    const prev = stops[i - 1];
    const u = clamp01((s - prev.s1) / (stop.s0 - prev.s1));
    t = smooth(u);
    const a = prev.rect(prev.hold ? prev.s1 : s), b = stop.rect(s);
    target = b;
    if (stop.path === 'drop') {
      // the size arrives first; the place follows, from above
      const k = smooth(clamp01(u / 0.55));
      const w = lerp(a.w, b.w, k), h = lerp(a.h, b.h, k);
      rect = { x: lerp(a.x, b.x, t), y: lerp(a.y + a.h, b.y + b.h, t) - h, w, h };
    } else rect = lerpRect(a, b, t);
    pose = lerpPose(prev.pose, stop.pose, t);
  }
  // (held at the table too: its pinned slot is a stacking context of its own, backdrop and all)
  over = !!stop.over;
  const fromHero = i === 0 ? 0 : i === 1 && s < stop.s0 ? t : 1;
  if (landed && !torn) rect = null;
  // The glass: the species cycle at the hero, the Carolina Parakeet from the centre
  // to the wall, the wall's last print when it tears off, the latest detection on the table.
  let screen: Screen | null;
  if (i === 0 || (i === 1 && s < stop.s0)) screen = fromHero >= 0.5 ? 'art' : fromHero <= 0.3 ? 'cycle' : null;
  else if (!torn) screen = landed ? null : 'art';
  else if (i > tearAt + 1 || (i === tearAt + 1 && s >= stop.s0)) screen = 'table';
  else if (i === tearAt) screen = 'oriole';
  else screen = t >= 0.75 ? 'table' : t <= 0.5 ? 'oriole' : null;
  const land = i > tearAt + 1 ? 1 : i === tearAt + 1 ? (s >= stop.s0 ? 1 : t) : 0;
  // The shadow is the table's: none while the frame is in the air, and it comes
  // in only as the frame's foot (the bottom middle of its box) meets the
  // table's, over the last few pixels of the descent.
  let ground = land >= 1 ? 1 : 0;
  if (land > 0 && land < 1 && rect && target) {
    const d = Math.hypot(rect.x + rect.w / 2 - (target.x + target.w / 2), rect.y + rect.h - (target.y + target.h));
    ground = smooth(clamp01(1 - d / CONTACT));
  }
  const bar = stop.bar && s >= stop.s0 && s <= stop.s1 && stop.s1 > stop.s0 ? (s - stop.s0) / (stop.s1 - stop.s0) : null;
  return { rect, pose: { ...pose, ground }, sway: 1 - fromHero, bar, landed, torn, over, screen, lift: fromHero, land, ground };
}

interface Els {
  head: HTMLElement | null;
  stage: HTMLElement;
  centre: HTMLElement | null;
  art: HTMLElement | null;
  wall: HTMLElement | null;
  first: HTMLElement | null;
  last: HTMLElement | null;
  folio: HTMLElement | null;
  table: HTMLElement | null;
  tablePin: HTMLElement | null;
}

/** The wall's tone, as main.ts keeps it on <html data-tone>. */
const tone = (): Model => (document.documentElement.dataset.tone === '10' ? '10' : '13');

/**
 * Run the page's choreography. The layout and the wall's classes start at
 * once; the frame joins when its model has loaded. Rejects (and undoes
 * itself) if the frame cannot be drawn.
 */
export async function startPage(data: SiteData, hero: Model, opts: {
  holdMs?: number; onShown: (i: number) => void; poster?: boolean;
}): Promise<{ dispose(): void }> {
  const root = document.documentElement;
  const images = [...document.querySelectorAll<HTMLElement>('.wall .cat .im img')];
  const table = document.getElementById('table-slot');
  const els: Els = {
    head: document.querySelector('.head'),
    stage: document.getElementById('stage')!,
    centre: document.getElementById('centre-slot'),
    art: document.getElementById('art-slot'),
    wall: document.querySelector('.wall'),
    first: images[0] ?? null,
    last: images[images.length - 1] ?? null,
    folio: document.querySelector('.wall .folio'),
    table,
    tablePin: table?.closest<HTMLElement>('.pin') ?? null,
  };
  /** The detection on the table (main.ts): the species' own screen, as the hero cycle draws it. */
  const detected = () => root.dataset.detected || 'cardinal';
  const screensOf = (size: Size): Record<Exclude<Screen, 'cycle'>, string | undefined> => ({
    art: size.wall?.[0],
    oriole: size.wall?.[size.wall.length - 1],
    table: size.screens.find((f) => f.includes(`-${detected()}.`)),
  });
  const detections = (size: Size) => size.screens.filter((f) => /-(cardinal|blue-jay|goldfinch)\./.test(f));

  let layout = measure(els);
  // scripts and debugging: where the journey is, and its stops
  (window as any).__ff = () => ({ st: at(layout, scrollY), stops: layout.stops.map((x) => [x.s0, x.s1, x.rect(scrollY)]) });
  /** The frames: the cover's, and (B&W) the 10-inch that takes over from it. */
  const frames: Partial<Record<Model, Frame3D>> = {};
  const shown: Partial<Record<Model, string>> = {};
  let loading10: Promise<void> | null = null;
  let active: Model = hero;
  let raf = 0, reveal = 0, dirty = true, lastKey = '', disposed = false;
  const t0 = performance.now();
  const request = () => { if (!raf) raf = requestAnimationFrame(tick); };

  const applyClasses = (st: State) => {
    els.wall?.classList.toggle('landed', st.landed);
    els.wall?.classList.toggle('torn', st.torn);
  };
  const applyScreen = (m: Model, st: State) => {
    const frame = frames[m];
    if (!frame) return;
    const screen: Screen | 'hidden' | null = st.rect ? st.screen : 'hidden';
    if (screen === null) return;
    const want = screen === 'table' ? `table:${detected()}` : screen;
    if (want === shown[m]) return;
    // Out of sight (or not yet drawn), the glass changes without a refresh.
    const instant = shown[m] === undefined || shown[m] === 'hidden';
    shown[m] = want;
    if (screen === 'hidden') return;
    const want2 = screen;
    // the 10-inch only ever travels: it has no cycle of its own, so it shows the art stop's
    const src = want2 === 'cycle' ? (m === hero ? null : screensOf(data.sizes[m]).art!) : screensOf(data.sizes[m])[want2];
    if (src !== undefined) frame.refresh.show(src, instant);
  };
  const load10 = () => {
    if (loading10 || hero === '10') return;
    loading10 = loadFrame(data.sizes['10'], { wake: request, keep: opts.poster, holdMs: 1e9 }).then((f) => {
      if (disposed) { f.dispose(); return; }
      for (const src of [...Object.values(screensOf(data.sizes['10'])), ...detections(data.sizes['10'])]) if (src) f.refresh.prepare(src);
      f.canvas.className = 'ff3d live empty';
      f.setSize(layout.vw, layout.vh);
      document.body.prepend(f.canvas);
      frames['10'] = f;
      dirty = true;
      request();
    }, (e) => console.warn('featherframe: the 10-inch frame is unavailable', e));
  };

  function tick(now: number) {
    raf = 0;
    const st = at(layout, scrollY);
    applyClasses(st);
    const main = frames[hero];
    if (!main) return;
    // A hidden page runs no rAFs; one that says it is hidden but runs them
    // (a throttled tab) keeps checking back without drawing.
    if (document.hidden) { request(); return; }
    // Which frame travels: the cover's, or — the wall on B&W — the 10-inch once it has left the cover.
    const want: Model = hero === '13' && tone() === '10' && st.lift >= 0.5 ? '10' : hero;
    if (want === '10') load10();
    const next: Model = frames[want] ? want : hero;
    if (next !== active) {
      frames[active]!.draw(null, st.pose);
      frames[active]!.canvas.classList.add('empty');
      if (active !== hero) shown[active] = 'hidden';
      active = next;
      dirty = true;
    }
    const frame = frames[active]!;
    applyScreen(active, st);
    let changed = false, busy = false;
    for (const f of Object.values(frames)) {
      const r = f!.refresh.tick(now);
      if (f === frame) changed = r.changed;
      busy ||= r.busy;
    }
    // test hook: the species the frame on the table is showing, once it is on the glass
    if (els.table) {
      const on = st.screen === 'table' && frame.refresh.showing() === screensOf(data.sizes[active]).table ? detected() : '';
      if ((els.table.dataset.shown ?? '') !== on) els.table.dataset.shown = on;
    }
    const sway = opts.poster || !st.sway ? 0 : Math.sin(((now - t0) / 1000) * (2 * Math.PI / SWAY_PERIOD)) * SWAY * st.sway;
    const sheen = opts.poster || !st.rect ? null : sheenAt(st.rect.y + st.rect.h / 2, layout.vh);
    const bar = opts.poster ? null : st.bar;
    const key = st.rect ? `${active},${st.rect.x},${st.rect.y},${st.rect.w},${st.rect.h},${st.pose.yaw},${st.pose.lean},${st.pose.pitch},${st.pose.ground},${sway},${st.over},${bar}` : '';
    if (changed || dirty || key !== lastKey) {
      frame.draw(st.rect, st.pose, sway, sheen, bar);
      frame.canvas.classList.toggle('empty', !st.rect);
      frame.canvas.classList.toggle('over', st.over);
      dirty = false;
      lastKey = key;
      root.style.setProperty('--lift', st.lift.toFixed(3));
      root.style.setProperty('--land', st.ground.toFixed(3));
      if (frame.drawn && !reveal) {
        // Live (poster out, canvas in) on the rAF after the first real draw, once it is on screen.
        reveal = requestAnimationFrame(() => {
          els.stage.classList.add('live');
          main.canvas.classList.add('live');
        });
      }
    }
    if (busy || sway !== 0) request();
  }

  const relayout = () => {
    layout = measure(els);
    for (const f of Object.values(frames)) f!.setSize(layout.vw, layout.vh);
    dirty = true;
    request();
  };
  const ro = new ResizeObserver(relayout);
  ro.observe(document.body);
  addEventListener('resize', relayout);
  addEventListener('scroll', request, { passive: true });
  const onVisible = () => { if (!document.hidden) request(); };
  document.addEventListener('visibilitychange', onVisible);
  // B&W resizes the wall's frames (a transition): measure again once they have settled
  const onTone = () => { if (tone() === '10') load10(); relayout(); };
  document.addEventListener('ff-tone', onTone);
  document.addEventListener('ff-detect', request);
  els.wall?.addEventListener('transitionend', relayout);
  void document.fonts?.ready.then(relayout);
  request();

  const undo = () => {
    disposed = true;
    cancelAnimationFrame(raf);
    cancelAnimationFrame(reveal);
    ro.disconnect();
    removeEventListener('resize', relayout);
    removeEventListener('scroll', request);
    document.removeEventListener('visibilitychange', onVisible);
    document.removeEventListener('ff-tone', onTone);
    document.removeEventListener('ff-detect', request);
    els.wall?.removeEventListener('transitionend', relayout);
    els.wall?.classList.remove('landed', 'torn');
    els.stage.classList.remove('live');
    root.style.removeProperty('--lift');
    root.style.removeProperty('--land');
    for (const f of Object.values(frames)) f!.dispose();
  };

  let frame: Frame3D;
  try {
    frame = await loadFrame(data.sizes[hero], { holdMs: opts.holdMs, onShown: opts.onShown, wake: request, keep: opts.poster });
  } catch (e) {
    undo();
    throw e;
  }
  frames[hero] = frame;
  for (const src of [...Object.values(screensOf(data.sizes[hero])), ...detections(data.sizes[hero])]) if (src) frame.refresh.prepare(src);
  frame.canvas.className = 'ff3d';
  frame.setSize(layout.vw, layout.vh);
  document.body.prepend(frame.canvas);
  if (tone() === '10') load10();
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
  // ?wall=screen&src=<a screen texture>: any picture on the frame, dead-on (scripts/wall.mjs seasons)
  const src = table ? size.wall.find((f) => f.includes('cardinal'))!
    : which === 'screen' ? new URLSearchParams(location.search).get('src')!
    : size.wall[Number(which)];
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
