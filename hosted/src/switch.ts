// The front door's choice (W-1025): what a batch of pushed detections would
// switch the screens to, when the server has said ahead what each species
// would show. A port of featherframe/switching.py; both run
// server/tests/fixtures/switch-cases.json.

export const MARKER = "marker";
const NAME_MAX = 200;                       // a species name; anything longer is not one
const GO_TEST_SPECIES = "testus birdicus";  // BirdNET-Go's Test button (W-865)
// What Python's float() reads as a decimal (W-1025): Number() also takes 0x1 and 0b1.
const DECIMAL = /^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$/;

export type Pushed = { common: string; scientific: string; confidence: number;
                       note?: string | null; first_days?: number | null };
export type Queued = { push: Pushed | null } | { marker: true };
export type SwitchEntry = {
  common: string; first_today: boolean; etag: string;
  frames: Record<string, { etag: string; file: string; push: Record<string, unknown> }>;
  viewers: Record<string, { name: string; file: string }>;
};
export type SwitchTable = {
  valid_until: number; shown: string | null; shown_clean: boolean;
  blocked: string[]; floor: number; notes: string[];
  species: Record<string, SwitchEntry>;
};

// The first candidate that is a non-blank string, trimmed and bounded. A list
// or object under a name key is not a bird: skipped, never coerced.
const name = (...vs: unknown[]): string => {
  for (const v of vs) if (typeof v === "string" && v.trim()) return v.trim().slice(0, NAME_MAX);
  return "";
};

// A number or a decimal string in [0, 1]; anything else (true, null, a list,
// 7) is 0, as the server reads it (W-1025).
const confidence = (v: unknown): number => {
  const c = typeof v === "number" ? v : typeof v === "string" && DECIMAL.test(v) ? Number(v) : NaN;
  return Number.isFinite(c) && c >= 0 && c <= 1 ? c : 0;
};

export function kindOf(path: string): "apprise" | "birdnet_go" | null {
  const m = /^\/api\/ingest\/(apprise|birdnet-go)(\/|$)/.exec(path);
  return m ? (m[1] === "birdnet-go" ? "birdnet_go" : "apprise") : null;
}

/** Who a pushed body says was heard, or null for a body that is not a
 *  detection, the test species, or one that names neither name. */
export function parse(kind: "apprise" | "birdnet_go", body: string): Pushed | null {
  let o: any;
  try { o = JSON.parse(body); } catch { return null; }
  if (o === null || typeof o !== "object" || Array.isArray(o)) return null;
  let out: Pushed;
  if (kind === "birdnet_go") {
    const md = o.metadata;
    if (o.type !== "detection" || md === null || typeof md !== "object") return null;
    const note = md.note_id;
    const days = md.days_since_first_seen;
    out = { common: name(md.species), scientific: name(md.scientific_name), confidence: confidence(md.confidence),
            note: (typeof note === "number" || typeof note === "string") ? (String(note).trim().slice(0, 40) || null) : null,
            first_days: Number.isInteger(days) && days >= 0 && days < 100000 ? days : null };
  } else {
    // BirdNET-Pi's Apprise carries our JSON inside `message`, perhaps with
    // text after it: the {...} span, else the top-level fields.
    let f: any = o;
    if (typeof o.message === "string" && o.message.includes("{") && o.message.includes("}")) {
      try {
        const inner = JSON.parse(o.message.slice(o.message.indexOf("{"), o.message.lastIndexOf("}") + 1));
        if (inner !== null && typeof inner === "object" && !Array.isArray(inner)) f = inner;
      } catch { /* the fields at the top */ }
    }
    out = { common: name(f.comname, f.common, f.commonName), scientific: name(f.sciname, f.scientific, f.scientificName),
            confidence: confidence(f.confidence), note: null, first_days: null };
  }
  if (!(out.common || out.scientific) || out.scientific.toLowerCase() === GO_TEST_SPECIES) return null;
  return out;
}

// The table's key for a push: its scientific name, as the server keys a
// species. A push with none has no entry (W-1025): the server would draw its
// sheet with no Latin name, another sheet than any entry names, so the front
// door leaves it to the server.
function entry(t: SwitchTable, p: Pushed): string | null {
  const sci = p.scientific.trim().toLowerCase();
  return sci && Object.hasOwn(t.species, sci) ? sci : null;   // W-1025: not `in`, or "constructor" is a species
}

/** "wake", "same", or the species key every screen is switched to. `queued` is
 *  what the server has not yet taken, in order. Counts run over all of it, the
 *  choice over the pushes after the last marker. */
export function choose(t: SwitchTable, queued: Queued[]): string {
  const blocked = new Set(t.blocked);
  const notes = new Set(t.notes);
  const kept: [number, string][] = [];     // (index, key) of every push that counts
  for (let i = 0; i < queued.length; i++) {
    const q = queued[i];
    if ("marker" in q || !q.push) continue;
    const p = q.push;
    if (p.confidence < t.floor) continue;
    if (p.note) {
      if (notes.has(p.note)) continue;
      notes.add(p.note);
    }
    if ([p.common, p.scientific].some((n) => n && blocked.has(n.toLowerCase()))) continue;
    const key = entry(t, p);
    if (key === null || p.first_days === 0) return "wake";
    kept.push([i, key]);
  }
  let lastMarker = -1;
  queued.forEach((q, i) => { if ("marker" in q) lastMarker = i; });
  const counts = new Map<string, number>();
  for (const [, k] of kept) counts.set(k, (counts.get(k) || 0) + 1);
  // Of the pushes after the last marker, newest first: the highest rank, the
  // newest within it. A species heard once today, and only once, ranks above
  // the rest. (Python: switching.best.)
  let pick: string | null = null;
  let top = -1;
  for (let j = kept.length - 1; j >= 0; j--) {
    const [i, k] = kept[j];
    if (i <= lastMarker) continue;
    const rank = t.species[k].first_today && counts.get(k) === 1 ? 1 : 0;
    if (rank > top) { pick = k; top = rank; }
  }
  if (pick === null || (pick === t.shown && t.shown_clean)) return "same";
  return pick;
}
