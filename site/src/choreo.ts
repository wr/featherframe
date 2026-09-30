// The frame's journey down the page (desktop, motion welcome, WebGL there):
// one WebGL frame on one fixed canvas, placed on each scroll by the stops it
// travels between.
//
//   hero      the cover's frame, three-quarter on its kickstand on the cover's table
//   pair      dead-on in the e-paper section, beside the other size, which slides in
//             from the right as it lands: both pinned while the words scroll past and
//             a studio light's bar sweeps up their glass, then both refresh together
//             at the panel's own pace, a timer under each (pair-ui.ts)
//   art       dead-on in the art spread's left half, showing the Wild Turkey, pinned
//             there while the spread's text scrolls past
//   wall 1    the gallery wall's empty first place: the still takes over there
//   wall 12   the wall's last frame tears off as its top meets the running head…
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
// the running head.
//
// The frame that travels is the page's tone's: the 10-inch in sixteen grays
// for B&W, the 13-inch for Color. The other size is loaded too, soon after the
// cover (or at once when the tone is switched to it): it is the pair's second
// frame, and when the tone changes it takes over the journey out of sight, its
// glass readied first, so it lands in and tears off the wall as the wall's own frames.
import { FLAT, HERO, TABLE, lerpPose, loadFrame, sheenAt, type Frame3D, type Pose, type Rect } from './viewer';
import { pairPictures, pairUI, type Model } from './pair-ui';
import type { SiteData, Size } from './card';

const SWAY = 0.06;        // the hero's idle sway, radians
const SWAY_PERIOD = 14;   // seconds
const SWAY_IDLE = 20;     // seconds without a scroll before the sway settles
const ART_LINGER = 0.1;   // viewport heights the art stop holds past its pin's release
const LAND_AT = 0.92;     // the wall's first place is landed in with its bottom this far down the window
const CONTACT = 8;        // px: the table's shadow comes in over the frame's last this many of descent
const AWAY = 12;          // px of scroll over which the cover's floor shadow goes
const CLEAR = 32;         // px: a flight that keeps clear of a spread's words stays this far left of them…
const LEAD = 0.6;         // …and makes its way across in this much of the flight, before it has settled down
const TEAR_GAP = 12;      // px: the wall's last frame tears off with its top this far under the running head
const PAIR_BAR = 0.5;     // viewport heights of scroll over which the light bar sweeps the pair (twice), from its landing
const PAIR_IN = 0.45;     // the other size slides in over the traveller's flight to the pair from this far through it
const PAIR_BEAT = 700;    // ms the pair holds still, landed, before it refreshes by itself
const OTHER_AFTER = 2500; // ms after the frame is live that the other size loads, if no scroll has asked for it first

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
type Screen = 'cycle' | 'pair' | 'art' | 'last' | 'table';

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
  /** A light bar sweeps its glass as the page scrolls through its hold… */
  bar?: boolean;
  /** …over this much scroll from s0 (the whole hold if not given). */
  barSpan?: number;
  /** The section a link to it should land in this stop's hold (main.ts). */
  section?: string;
  /** Arriving, the frame turns a full circle about its upright. */
  spin?: boolean;
  /** The flight in heads for where the stop will hold the frame (its box at s0), not for its slot on the way up
   *  with the page: it never dips below the window to meet a slot still coming up from under it. */
  settled?: boolean;
  /** The words the flight in comes up beside, at scroll `s` (the frame lands left of them): it crosses over before
   *  it settles down, and never meets them. */
  clear?: (s: number) => Rect;
}

interface Layout {
  stops: Stop[];
  /** Stop index after which the frame is the wall's still, not the canvas… */
  landAt: number;
  /** …until this stop, where it tears off again. */
  tearAt: number;
  /** The e-paper pair: its stop, the other size's slot beside it at scroll `s`, and the scroll range over which
   *  the other size slides in. */
  pair: { at: number; partner: (s: number) => Rect; in0: number; in1: number } | null;
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
  // The cover and the table draw the frames to scale: the 10.3-inch stands smaller than the 13-inch
  // (295 mm tall to 371 mm), on the same spot.
  const toScale = (r: Rect): Rect => {
    if (tone() !== '10') return r;
    const k = 295 / 371;
    return { x: r.x + (r.w * (1 - k)) / 2, y: r.y + r.h * (1 - k), w: r.w * k, h: r.h * k };
  };
  const hero = toScale(heroRect(els.stage.getBoundingClientRect(), scrollY));
  stops.push({ rect: scrolled(hero), pose: HERO, s0: -Infinity, s1: 0, hold: true });
  let landAt = Infinity, tearAt = Infinity;
  let pair: Layout['pair'] = null;
  const after = (s: number, min: number) => Math.max(s, stops[stops.length - 1].s1 + min * vh);

  if (els.pair && els.pairSlots) {
    // the tone's frame in its slot, the other size in the one beside it: both pinned together
    const t = tone();
    const p = pinned(els.pairSlots[t], els.pair);
    const q = pinned(els.pairSlots[t === '10' ? '13' : '10'], els.pair);
    // it lands as the pair pins, and it stays a moment after the pin lets go, going up with the page
    const s0 = after(p.s0, 0.3);
    const s1 = Math.max(s0, p.s1 + ART_LINGER * vh);
    const from = stops[stops.length - 1].s1;
    stops.push({ rect: p.rect, pose: FLAT, s0, s1, bar: true, barSpan: PAIR_BAR * vh, section: 'epaper', settled: true });
    pair = { at: stops.length - 1, partner: q.rect, in0: from + PAIR_IN * (s0 - from), in1: s0 };
  }
  if (els.art) {
    const p = pinned(els.art, els.art);
    const s0 = after(p.s0, 0.3);
    // it stays a moment after the spread lets go of it, going up with the page, before it sets off
    stops.push({ rect: p.rect, pose: FLAT, s0, s1: Math.max(s0, p.s1 + ART_LINGER * vh), section: 'art',
      clear: els.artText ? scrolled(pageBox(els.artText)) : undefined });
  }
  if (els.first && els.last) {
    // the wall's frames as drawn: in B&W, each still is scaled to the 10-inch's true size
    const b1 = pageBox(els.first);
    // it lands as the wall's first row comes up to the bottom of the window
    const s = after(b1.y + b1.h - LAND_AT * vh, 0.35);
    stops.push({ rect: scrolled(b1), pose: FLAT, s0: s, s1: s, path: 'drop', over: true });
    landAt = stops.length - 1;
    const b12 = pageBox(els.last);
    // it tears off as its top comes up to just under the running head, and leaves from there
    const t = after(b12.y - (nav + TEAR_GAP), 0.2);
    stops.push({ rect: scrolled(b12), pose: FLAT, s0: t, s1: t, hold: true });
    tearAt = stops.length - 1;
    if (els.table && els.tablePin) {
      const p = pinned(els.table, els.tablePin);
      // it lands as the table comes up level with the video, a little before the table pins
      stops.push({ rect: (s) => toScale(p.rect(s)), pose: TABLE, s0: after(p.s0 - 0.3 * vh, 0.3), s1: Infinity, over: true, spin: true });
    }
  }
  const last = stops[stops.length - 1];
  if (last.s1 < Infinity) last.s1 = Infinity;
  return { stops, landAt, tearAt, pair, vw, vh };
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
    const a = prev.rect(prev.hold ? prev.s1 : s), b = stop.rect(stop.settled ? Math.max(s, stop.s0) : s);
    target = b;
    if (stop.path === 'drop') {
      // the size arrives first; the place follows, from above
      const k = smooth(clamp01(u / 0.55));
      const w = lerp(a.w, b.w, k), h = lerp(a.h, b.h, k);
      rect = { x: lerp(a.x, b.x, t), y: lerp(a.y + a.h, b.y + b.h, t) - h, w, h };
    } else rect = lerpRect(a, b, t);
    if (stop.clear) {
      // across first: its right edge arrives over the first LEAD of the flight, its size and height as before…
      const w = rect.w;
      rect.x = lerp(a.x + a.w, b.x + b.w, smooth(clamp01(u / LEAD))) - w;
      // …and the words keep it out: level with them its right edge stays CLEAR px left of theirs, and above them it
      // may reach as much further right as it is above them, so the frame is eased aside, never jumped
      const c = stop.clear(s);
      const gap = Math.min(CLEAR, c.x - (b.x + b.w));
      if (rect.y < c.y + c.h) rect.x = Math.min(rect.x, c.x - gap + Math.max(0, c.y - (rect.y + rect.h)) - w);
    }
    pose = lerpPose(prev.pose, stop.pose, t);
    // on its way down to the table the frame turns once about its upright
    if (stop.spin) pose = { ...pose, yaw: pose.yaw + 2 * Math.PI * t };
  }
  // (held at the table too: its pinned slot is a stacking context of its own, backdrop and all)
  over = !!stop.over;
  const fromHero = i === 0 ? 0 : i === 1 && s < stop.s0 ? t : 1;
  if (landed && !torn) rect = null;
  // The glass: the species cycle at the hero; at the pair, what it arrived with, until the pair refreshes (the
  // pair's own, below); the Wild Turkey at the art spread and on to the wall, the wall's last print when it tears
  // off, the latest detection on the table. A refresh is never drawn in flight: each stop's picture arrives once
  // the frame is (all but) still there.
  let screen: Screen | null;
  const pairAt = l.pair ? l.pair.at : -1;
  if (i === 0 || (i === 1 && s < stop.s0)) screen = fromHero >= 0.97 ? (pairAt === 1 ? 'pair' : 'art') : fromHero <= 0.3 ? 'cycle' : null;
  else if (i === pairAt) screen = 'pair';
  else if (i === pairAt + 1 && s < stop.s0) screen = null;
  else if (!torn) screen = landed ? null : 'art';
  else if (i > tearAt + 1 || (i === tearAt + 1 && s >= stop.s0)) screen = 'table';
  else if (i === tearAt) screen = 'last';
  else screen = 'last'; // (on its way to the table: the table's picture waits for the landing)
  const land = i > tearAt + 1 ? 1 : i === tearAt + 1 ? (s >= stop.s0 ? 1 : t) : 0;
  // The shadow is the table's: none while the frame is in the air, and it comes
  // in only as the frame's foot (the bottom middle of its box) meets the
  // table's, over the last few pixels of the descent.
  let ground = land >= 1 ? 1 : 0;
  if (land > 0 && land < 1 && rect && target) {
    const d = Math.hypot(rect.x + rect.w / 2 - (target.x + target.w / 2), rect.y + rect.h - (target.y + target.h));
    ground = smooth(clamp01(1 - d / CONTACT));
  }
  const span = Math.min(stop.barSpan ?? Infinity, stop.s1 - stop.s0);
  const bar = stop.bar && span > 0 && s >= stop.s0 && s <= stop.s0 + span ? (s - stop.s0) / span : null;
  return { rect, pose: { ...pose, ground }, sway: 1 - fromHero, bar, landed, torn, over, screen, lift: fromHero, land, ground };
}

/** The pair's other size at scroll `s`: its box, sliding in level from past the window's right edge to where the
 *  pair holds it (null before it sets off, and once it is off the window), and how far in it has slid, 0–1. */
function partnerAt(l: Layout, s: number): { rect: Rect | null; in: number } {
  const p = l.pair;
  if (!p || s <= p.in0) return { rect: null, in: 0 };
  const u = smooth(clamp01((s - p.in0) / Math.max(1, p.in1 - p.in0)));
  const b = p.partner(Math.max(s, p.in1));
  if (b.y + b.h <= 0 || b.y >= l.vh) return { rect: null, in: u };
  return { rect: { ...b, x: lerp(l.vw + 8, b.x, u) }, in: u };
}

interface Els {
  head: HTMLElement | null;
  stage: HTMLElement;
  /** The e-paper pair's pin, and its two frames' slots. */
  pair: HTMLElement | null;
  pairSlots: Record<Model, HTMLElement> | null;
  art: HTMLElement | null;
  /** The art spread's words, which the frame comes up beside. */
  artText: HTMLElement | null;
  wall: HTMLElement | null;
  first: HTMLElement | null;
  last: HTMLElement | null;
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
  holdMs?: number; speed?: number; onShown: (i: number) => void; poster?: boolean;
}): Promise<{ dispose(): void; landing(section: string): [number, number] | null }> {
  const root = document.documentElement;
  const images = [...document.querySelectorAll<HTMLElement>('.wall .cat .im img')];
  const table = document.getElementById('table-slot');
  const els: Els = {
    head: document.querySelector('.head'),
    stage: document.getElementById('stage')!,
    pair: document.getElementById('pair'),
    pairSlots: null,
    art: document.getElementById('art-slot'),
    artText: document.querySelector('#art .text'),
    wall: document.querySelector('.wall'),
    first: images[0] ?? null,
    last: images[images.length - 1] ?? null,
    table,
    tablePin: table?.closest<HTMLElement>('.pin') ?? null,
  };
  {
    const [p10, p13] = [document.getElementById('pair-10'), document.getElementById('pair-13')];
    if (els.pair && p10 && p13) els.pairSlots = { '10': p10, '13': p13 };
    else els.pair = null;
  }
  const ui = els.pair ? pairUI() : null;
  /** The detection on the table (main.ts): the species' own screen, as the hero cycle draws it. */
  const detected = () => root.dataset.detected || 'cardinal';
  const screensOf = (size: Size): Record<'art' | 'last' | 'table', string | undefined> => ({
    art: size.wall?.[0],
    last: size.wall?.[size.wall.length - 1],
    table: size.screens.find((f) => f.includes(`-${detected()}.`)),
  });
  const detections = (size: Size) => size.screens.filter((f) => /-(cardinal|eastern-bluebird|tufted-titmouse|black-capped-chickadee)\./.test(f));

  let layout = measure(els);
  // scripts and debugging: where the journey is, and its stops; the pair's other size; what each glass shows
  (window as any).__ff = () => ({
    st: at(layout, scrollY), floor: Object.values(frames).some((f) => f!.floor), stops: layout.stops.map((x) => [x.s0, x.s1, x.rect(scrollY)]),
    pair: layout.pair && { at: layout.pair.at, partner: partnerAt(layout, scrollY), active },
    glass: Object.fromEntries(Object.entries(frames).map(([m, f]) => [m, f!.refresh.onGlass()])),
  });
  /** The frames: the cover's, and the other size, the pair's second frame, which takes over the journey when the tone is switched to it. */
  const frames: Partial<Record<Model, Frame3D>> = {};
  const shown: Partial<Record<Model, string>> = {};
  /** The other size's frame: loaded soon after the cover's, or at once when the visitor asks for its tone. */
  const other: Model = hero === '13' ? '10' : '13';
  const partnerOf = (m: Model): Model => (m === '13' ? '10' : '13');
  /** A picture on one size's glass, in the other size's drawing. */
  const match = (src: string | null, from: Model, to: Model) => {
    const a = data.sizes[from], b = data.sizes[to];
    for (const k of ['screens', 'wall'] as const) {
      const i = src ? a[k].indexOf(src) : -1;
      if (i >= 0) return b[k][i];
    }
    return b.screens[0];
  };
  let loadingOther: Promise<void> | null = null;
  /** What the 10-inch's glass was last told to show while out of sight. */
  let queued: string | null = null;
  let active: Model = hero;
  let raf = 0, reveal = 0, dirty = true, lastKey = '', lastAway = '', disposed = false, wasSeen = true;
  /** The pair: its other size's last drawn box; its glass matched to the traveller's since it last came on the
   *  window; whether it has refreshed by itself yet, the beat before it does, and whether it could now. */
  let lastPartnerKey = '', partnerSynced = false, pairRan = false, pairBeat = 0, pairCan = false, otherTimer = 0;
  let lastScroll = performance.now();
  /** A lost WebGL context (a laptop asleep, a tab in the background too long) leaves nothing to draw with: the
   *  journey steps aside as if the model had failed, and the page's own stills come back. */
  const watch = (c: HTMLCanvasElement) => c.addEventListener('webglcontextlost', (e) => {
    e.preventDefault();
    if (disposed) return;
    undo();
    root.classList.remove('choreo');
  });
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
    // at the pair: whatever it arrived with, held there (a refresh under way finishes first); the pair refreshes it
    if (screen === 'pair') { frame.refresh.show(frame.refresh.onGlass(), 'panel'); return; }
    const want2 = screen;
    // the 10-inch only ever travels: it has no cycle of its own, so it shows the art stop's
    // each frame has its own cycle (the same species, in its own panel's drawing)
    const src = want2 === 'cycle' ? null : screensOf(data.sizes[m])[want2];
    // A new detection arrives at the panel's own pace, as it would at home; a change a scroll asks for, quickly
    if (src !== undefined) frame.refresh.show(src, instant ? 'instant' : want2 === 'table' ? 'panel' : 'quick');
  };
  const loadOther = () => {
    if (loadingOther) return;
    loadingOther = loadFrame(data.sizes[other], { holdMs: opts.holdMs, speed: opts.speed, onShown: opts.onShown, wake: request, keep: opts.poster }).then((f) => {
      if (disposed) { f.dispose(); return; }
      for (const src of [...Object.values(screensOf(data.sizes[other])), ...detections(data.sizes[other])]) if (src) f.refresh.prepare(src);
      f.canvas.className = 'ff3d live empty';
      f.setSize(layout.vw, layout.vh);
      // in the pair the 10.3-inch stands in front of the 13.3-inch: its canvas comes after
      if (other === '10') frames['13']!.canvas.after(f.canvas);
      else document.body.prepend(f.canvas);
      frames[other] = f;
      watch(f.canvas);
      dirty = true;
      request();
    }, (e) => {
      console.warn(`featherframe: the ${other}-inch frame is unavailable`, e);
      // the pair keeps its still of it
      els.pairSlots?.[other].querySelector<HTMLElement>('.still')?.style.setProperty('visibility', 'visible');
    });
  };
  /** Both frames of the pair ready to refresh: here, still, and neither refreshing already. */
  const pairIdle = () => {
    const t = frames[active], p = frames[partnerOf(active)];
    return !!t && !!p && !!ui && !t.refresh.progress() && !p.refresh.progress() && !ui.watch['10'].running && !ui.watch['13'].running;
  };
  /** Both to whichever of the two pictures the traveller is not showing, at the panel's own pace, together. */
  const runPair = () => {
    if (!ui || !pairCan || !pairIdle()) return;
    const pm = partnerOf(active);
    const k = frames[active]!.refresh.onGlass() === pairPictures(data.sizes[active])[0] ? 1 : 0;
    for (const m of [active, pm]) {
      const f = frames[m]!, src = pairPictures(data.sizes[m])[k];
      if (f.refresh.onGlass() !== src) ui.watch[m].start();
      f.refresh.show(src, 'panel');
    }
    pairCan = false;
    ui.ready(false);
    request();
  };
  const offRefresh = ui?.onRefresh(runPair);

  function tick(now: number) {
    raf = 0;
    const st = at(layout, scrollY);
    applyClasses(st);
    // The cover's floor shadow is the room's, and scrolls with it while the
    // frame holds still: gone within the first few pixels, so it never falls across the frame.
    const away = Math.min(1, Math.max(0, scrollY) / AWAY).toFixed(3);
    if (away !== lastAway) { lastAway = away; root.style.setProperty('--away', away); }
    const main = frames[hero];
    if (!main) return;
    // A hidden page runs no rAFs; one that says it is hidden but runs them
    // (a throttled tab) keeps checking back without drawing.
    if (document.hidden) { request(); return; }
    // Which frame travels: the tone's (Color: the 13-inch; B&W: the 10-inch), wherever the page is.
    const want: Model = tone();
    if (want !== hero || (els.pair && scrollY > 0)) loadOther();
    // …once its glass already shows what the frame should (put there out of sight):
    // never a caption over the wrong species
    let ready = want === active;
    if (!ready && frames[want]) {
      const sc = screensOf(data.sizes[want]);
      const screen = st.screen ?? (st.lift < 0.5 ? 'cycle' : 'art');
      if (screen === 'cycle') {
        // back to its own cycle, as it is
        frames[want]!.refresh.show(null, 'instant');
        ready = true;
      } else if (screen === 'pair') {
        // both are there already: they change places
        ready = true;
      } else {
        const src = screen === 'table' ? sc.table : screen === 'last' ? sc.last : sc.art;
        if (frames[want]!.refresh.showing() === src) ready = true;
        else if (src && queued !== src) { frames[want]!.refresh.show(src, 'instant'); queued = src; }
      }
    }
    const next: Model = ready ? want : active;
    if (next !== active) {
      frames[active]!.draw(null, st.pose);
      frames[active]!.canvas.classList.add('empty');
      shown[active] = 'hidden';
      queued = null;
      shown[next] = st.screen === 'table' ? `table:${detected()}` : st.screen ?? (st.lift < 0.5 ? 'cycle' : 'art');
      active = next;
      partnerSynced = false;
      lastPartnerKey = '';
      dirty = true;
    }
    const frame = frames[active]!;
    applyScreen(active, st);
    // Leaving the pair for the art spread, the frame flies across to it: a refresh still under way there (the
    // colour one, or a jump from the running head) finishes before it goes, not in flight.
    const pairStop = layout.pair ? layout.stops[layout.pair.at] : undefined;
    const artStop = layout.pair ? layout.stops[layout.pair.at + 1] : undefined;
    if (pairStop && artStop && scrollY > pairStop.s1 && scrollY < artStop.s0) frame.refresh.hurry(250);
    // The pair's other size: on its way in, or beside the traveller. Its glass is matched to the traveller's
    // (out of sight, as it comes on the window) before it is drawn.
    const pm = partnerOf(active), pf = frames[pm];
    const pst = partnerAt(layout, scrollY);
    if (!pst.rect) partnerSynced = false;
    else if (pf && !partnerSynced) {
      pf.refresh.show(match(frame.refresh.onGlass(), active, pm), 'instant');
      partnerSynced = true;
    }
    let changed = false, partnerChanged = false, busy = false;
    for (const [m, f] of Object.entries(frames) as [Model, Frame3D][]) {
      // the other size's frame, unseen, is left as it is (it is readied by an instant show() when it is wanted)
      if (m !== active && m !== want && !(m === pm && (pst.rect || f.refresh.progress()))) continue;
      const r = f.refresh.tick(now);
      if (f === frame) changed = r.changed;
      if (m === pm) partnerChanged = r.changed;
      busy ||= r.busy;
    }
    // The pair's timers, and its Refresh: pressable once both frames are there, still and idle; the first time,
    // they refresh by themselves after a beat.
    if (ui && pairStop) {
      for (const m of ['10', '13'] as Model[]) {
        ui.watch[m].update(frames[m]?.refresh.progress() ?? null, now);
        busy ||= ui.watch[m].running;
      }
      // once the traveller has left for the art spread, the captions and the button go too: the pair is over
      const away = scrollY > pairStop.s1;
      if (ui.el.classList.contains('away') !== away) ui.el.classList.toggle('away', away);
      pairCan = st.screen === 'pair' && scrollY >= pairStop.s0 && scrollY <= pairStop.s1 && pst.in >= 1 && want === active && pairIdle();
      ui.ready(pairCan);
      if (!pairCan) { clearTimeout(pairBeat); pairBeat = 0; }
      else if (!pairRan && !pairBeat && !opts.poster) {
        pairBeat = window.setTimeout(() => {
          pairBeat = 0;
          if (pairCan && !pairRan) { pairRan = true; runPair(); }
        }, PAIR_BEAT);
      }
    }
    // test hook: the species the frame on the table is showing, once it is on the glass
    if (els.table) {
      const on = st.screen === 'table' && frame.refresh.showing() === screensOf(data.sizes[active]).table ? detected() : '';
      if ((els.table.dataset.shown ?? '') !== on) els.table.dataset.shown = on;
      // main.ts holds the next detection until the frame on the table has finished its refresh
      const refreshing = st.screen === 'table' && !on ? '1' : '';
      if ((els.table.dataset.refreshing ?? '') !== refreshing) els.table.dataset.refreshing = refreshing;
    }
    // The cover's idle sway: drawn at 30 fps, not 60, and settling to still after a while without a scroll (the
    // loop then parks), so a page left open on the cover isn't a laptop's fan.
    const idle = Math.max(0, (now - lastScroll) / 1000 - SWAY_IDLE);
    const swayK = st.sway * Math.max(0, 1 - idle / 2);
    const swayT = Math.floor((now - t0) / 33) * 33;
    const sway = opts.poster || !swayK ? 0 : Math.sin((swayT / 1000) * (2 * Math.PI / SWAY_PERIOD)) * SWAY * swayK;
    const sheen = opts.poster || !st.rect ? null : sheenAt(st.rect.y + st.rect.h / 2, layout.vh);
    const bar = opts.poster ? null : st.bar;
    const key = st.rect ? `${active},${st.rect.x},${st.rect.y},${st.rect.w},${st.rect.h},${st.pose.yaw},${st.pose.lean},${st.pose.pitch},${st.pose.ground},${sway},${st.over},${bar}` : '';
    // wholly off the window (below the table, the pinned slot still has a rect): no redraw on every scroll
    const seen = !!st.rect && st.rect.y < layout.vh && st.rect.y + st.rect.h > 0;
    if ((changed || dirty || key !== lastKey) && (seen || wasSeen || dirty)) {
      wasSeen = seen;
      frame.draw(st.rect, st.pose, sway, sheen, bar);
      frame.canvas.classList.toggle('empty', !st.rect);
      frame.canvas.classList.toggle('over', st.over);
      dirty = false;
      lastKey = key;
      root.style.setProperty('--land', st.ground.toFixed(3));
      if (frame.drawn && !reveal) {
        // Live (poster out, canvas in) on the rAF after the first real draw, once it is on screen.
        reveal = requestAnimationFrame(() => {
          els.stage.classList.add('live');
          main.canvas.classList.add('live');
          ui?.live(true);
          // the pair's second frame, before anyone scrolls down to it
          if (els.pair) otherTimer = window.setTimeout(() => { if (!disposed) loadOther(); }, OTHER_AFTER);
        });
      }
    }
    // …and the 10.3-inch, in front, casts its shadow on the other while both are there
    const front = frames['10']?.canvas;
    if (front && front.classList.contains('front') !== !!pst.rect) front.classList.toggle('front', !!pst.rect);
    if (pf) {
      const r = pst.rect;
      const pkey = r ? `${pm},${r.x},${r.y},${r.w},${r.h},${bar}` : '';
      if (partnerChanged || dirty || pkey !== lastPartnerKey) {
        pf.draw(r, FLAT, 0, opts.poster || !r ? null : sheenAt(r.y + r.h / 2, layout.vh), r ? bar : null);
        pf.canvas.classList.toggle('empty', !r);
        pf.canvas.classList.remove('over');
        lastPartnerKey = pkey;
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
  const onScroll = () => { lastScroll = performance.now(); request(); };
  addEventListener('scroll', onScroll, { passive: true });
  const onVisible = () => { if (!document.hidden) request(); };
  document.addEventListener('visibilitychange', onVisible);
  // B&W resizes the wall's frames (a transition): measure again once they have settled
  const onTone = () => { if (tone() !== hero) loadOther(); relayout(); };
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
    removeEventListener('scroll', onScroll);
    document.removeEventListener('visibilitychange', onVisible);
    document.removeEventListener('ff-tone', onTone);
    document.removeEventListener('ff-detect', request);
    els.wall?.removeEventListener('transitionend', relayout);
    els.wall?.classList.remove('landed', 'torn');
    els.stage.classList.remove('live');
    root.style.removeProperty('--away');
    root.style.removeProperty('--land');
    clearTimeout(pairBeat);
    clearTimeout(otherTimer);
    offRefresh?.();
    ui?.live(false);
    ui?.ready(false);
    frames['10']?.canvas.classList.remove('front');
    ui?.el.classList.remove('away');
    if (els.pairSlots) for (const slot of Object.values(els.pairSlots)) slot.querySelector<HTMLElement>('.still')?.style.removeProperty('visibility');
    for (const f of Object.values(frames)) f!.dispose();
  };

  let frame: Frame3D;
  try {
    frame = await loadFrame(data.sizes[hero], { holdMs: opts.holdMs, speed: opts.speed, onShown: opts.onShown, wake: request, keep: opts.poster });
  } catch (e) {
    undo();
    throw e;
  }
  frames[hero] = frame;
  watch(frame.canvas);
  for (const src of [...Object.values(screensOf(data.sizes[hero])), ...detections(data.sizes[hero])]) if (src) frame.refresh.prepare(src);
  frame.canvas.className = 'ff3d';
  frame.setSize(layout.vw, layout.vh);
  document.body.prepend(frame.canvas);
  if (tone() !== hero) loadOther();
  dirty = true;
  request();
  /** The scroll range over which `section`'s stop holds the frame, if the journey has one. */
  const landing = (section: string): [number, number] | null => {
    const st = layout.stops.find((x) => x.section === section);
    return st ? [st.s0, st.s1] : null;
  };
  return { dispose: undo, landing };
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
  // ?wall=hole: the frame dead-on with its screen a transparent hole, and where the screen lies in it as shares of
  // the picture (<html data-screen>): the e-paper section's flat pair lays its live glass behind it (scripts/wall.mjs hole)
  const hole = which === 'hole';
  // ?wall=screen&src=<a screen texture>: any picture on the frame, dead-on (scripts/wall.mjs seasons)
  const src = table ? size.screens.find((f) => f.includes('-cardinal.'))!
    : which === 'screen' ? new URLSearchParams(location.search).get('src')!
    : hole ? size.screens[0]
    : size.wall[Number(which)];
  let raf = 0;
  const request = () => { if (!raf) raf = requestAnimationFrame(tick); };
  const frame = await loadFrame(size, { wake: request, keep: true, holdMs: 1e9, floor: table, hole });
  const pose = table ? TABLE : FLAT;
  const [l, t, r, b] = table ? TABLE_PAD : [0, 0, 0, 0];
  document.documentElement.dataset.aspect = (frame.aspect(pose) * (1 + l + r) / (1 + t + b)).toFixed(5);
  frame.canvas.className = 'ff3d live';
  document.body.prepend(frame.canvas);
  frame.refresh.show(src, 'instant');
  let settled = 0;
  function tick(now: number) {
    raf = 0;
    const w = document.documentElement.clientWidth, h = document.documentElement.clientHeight;
    frame.setSize(w, h);
    frame.refresh.tick(now);
    // the table's still leaves room around the frame for its shadow (TABLE_PAD)
    const [l, t, r, b] = table ? TABLE_PAD : [0, 0, 0, 0];
    const fw = w / (1 + l + r), fh = h / (1 + t + b);
    const rect = { x: l * fw, y: t * fh, w: fw, h: fh };
    frame.draw(rect, pose);
    // a few frames on from the screen's picture reaching the glass
    if (frame.refresh.showing() !== src || ++settled < 5) request();
    else {
      if (hole) {
        const s = frame.screenIn(rect, pose);
        document.documentElement.dataset.screen = JSON.stringify([s.x / w, s.y / h, s.w / w, s.h / h].map((v) => +v.toFixed(5)));
      }
      document.documentElement.dataset.wall = 'ready';
    }
  }
  request();
}
