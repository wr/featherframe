# The Cloud webapp from a cache (W-946) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The front door answers the webapp's reads from a stale-while-revalidate cache in R2, so opening or leaving open the webapp never starts the household's server just to look.

**Architecture:**
- **The cache.** A new `hosted/src/pagecache.ts` holds it: which reads are cached and under what key, the copies (an index in the front door's SQLite, bodies in R2), how a read is answered, and how the copies are refreshed in one generation.
- **When to wake.** The alarm's rule moves into a pure `hosted/src/wakes.ts`, which gains a `look` flag. `household.ts` wires both in.
- **Server and page.** The server reports its page build and gives the page epoch times. The page renders relative times itself, applies the status on load, and asks `live=1` where it must be live. It also warms the server when an edit starts.

**Tech Stack:** Cloudflare Worker and Durable Object (TypeScript), R2, the Python server (FastAPI, Jinja), vitest (`hosted/test`), pytest (`server/tests`).

**Spec:** `docs/superpowers/specs/2026-10-02-cloud-webapp-cache-design.md`, part 2.

## Global Constraints

- **Freshness.** A copy is fresh for 15 minutes (`FRESH_MS = 15 * 60_000`). Refreshes take rows read in the last 2 hours (`READ_WINDOW_MS`). Rows unread for 30 days are dropped.
- **What is stored.** Only a 200 GET, under 5 MB (`MAX_BODY`), on the paths in the spec's table, with the query keys it names. Every other query key is left out of the key; `live=1` skips the cache.
- **The key** carries the server's `page_build`, and for `/` the three account headers (`x-ff-account-email`, `x-ff-account-email-pending`, `x-ff-account-unverified`).
- **Asking for a wake** is always the alarm's way (`look`), never inside a request. It waits out `MIN_GAP_MS` (5 min). Never for a suspended household. In quiet hours (`poll = 0`), only when check-ins or detections are queued.
- **Refresh requests** go straight to the Container's stub, never through `proxy()`, so they never count as page activity (`page_ms`).
- **Page script** is ES5 (the box serves the same page). Every inline script must parse (`tests/test_page_scripts.py`).
- **Copy.** Read `docs/STYLE.md` before writing any string; the loading page has no text. "Saving…", "Saved" and "Not saved" already exist on the page.
- **Tests.** `cd hosted && npx vitest run && npx tsc --noEmit -p .` and `make test` (from the repo root, with `FEATHERFRAME_PLATES_DIR=/Users/wells/Projects/featherframe/server/plates`). CI does not run the hosted tests.

## Review Focus

1. **A page open across a save in another tab:** that tab's next status poll after the save is a miss. The server is up, so it's asked and stored. Test that a POST clears changing copies and keeps fixed ones.
2. **Two tabs opening `/` while the server is asleep:** both get the loading page, both set `look`, and there is one wake. Test that `look` twice gives one alarm time.
3. **The account changes** (email confirmed in another tab): the next `/` is a new key, not a stale page with the old banner. Test that the key differs when the account headers differ.
4. **A copy in R2 that has gone missing** (deleted by hand, or a half-finished clear): treated as a miss, never a 500. Test with an index row whose object is absent.
5. **A refresh while the server dies part way:** the old generation stays served. Nothing is swapped unless every ask answered 200. Test that a refresh with one failing ask leaves the rows as they were.

---

### Task 1: The wake rule, with `look`

**Files:**
- Create: `hosted/src/wakes.ts`
- Create: `hosted/test/wakes.test.ts`
- Modify: `hosted/src/household.ts` (constants and `alarm()`/`schedule()` call `due`/`nextAlarm`; `wake()` clears `look`; new `look()` method)

**Interfaces:**
- Produces: `POLL_MS`, `MIN_GAP_MS`, `SAFETY_MS`, `interface WakeState { now; lastWake; named; news; look; birdweather }`, `due(s: WakeState): boolean`, `nextAlarm(s: WakeState): number`; `Household.look(): Promise<void>`.

- [ ] **Step 1: Write the failing tests**

`hosted/test/wakes.test.ts`:

```ts
// When the front door wakes the household's server (W-847), now with a look
// at a stale page (W-946): never inside a request, never before MIN_GAP_MS.
import { describe, expect, it } from "vitest";
import { due, MIN_GAP_MS, nextAlarm, POLL_MS, SAFETY_MS, type WakeState } from "../src/wakes";

const T = 1_790_000_000_000;
const s = (o: Partial<WakeState> = {}): WakeState =>
  ({ now: T, lastWake: T - 60_000, named: 0, news: false, look: false, birdweather: false, ...o });

describe("due", () => {
  it("wakes for a named time that has come", () => {
    expect(due(s({ named: T - 1 }))).toBe(true);
    expect(due(s({ named: T + 1 }))).toBe(false);
  });
  it("wakes for news or a look only once MIN_GAP_MS has passed", () => {
    expect(due(s({ news: true }))).toBe(false);
    expect(due(s({ look: true }))).toBe(false);
    expect(due(s({ look: true, lastWake: T - MIN_GAP_MS }))).toBe(true);
    expect(due(s({ news: true, lastWake: T - MIN_GAP_MS }))).toBe(true);
  });
  it("wakes once a day regardless", () => {
    expect(due(s({ lastWake: T - SAFETY_MS }))).toBe(true);
  });
});

describe("nextAlarm", () => {
  it("sets a look's alarm to when the gap ends, never sooner than a second", () => {
    expect(nextAlarm(s({ look: true }))).toBe(T - 60_000 + MIN_GAP_MS);
    expect(nextAlarm(s({ look: true, lastWake: T - MIN_GAP_MS - 1 }))).toBe(T + 1000);
  });
  it("gives two looks the same alarm", () => {
    expect(nextAlarm(s({ look: true }))).toBe(nextAlarm(s({ look: true })));
  });
  it("keeps the BirdWeather poll and the daily wake as before", () => {
    expect(nextAlarm(s({ birdweather: true }))).toBe(T + POLL_MS);
    expect(nextAlarm(s())).toBe(T - 60_000 + SAFETY_MS);
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd hosted && npx vitest run test/wakes.test.ts`
Expected: FAIL, `Failed to load url ../src/wakes`.

- [ ] **Step 3: Write `wakes.ts`**

```ts
// When the front door wakes the household's server (W-847): for news, at
// most once per MIN_GAP_MS; at a time the server named; once a day
// regardless. A look (W-946) is a stale page someone opened: it waits out
// the same gap as news, and is never a reason to wake sooner.

export const POLL_MS = 2 * 60 * 1000;
export const MIN_GAP_MS = 5 * 60 * 1000;
export const SAFETY_MS = 24 * 60 * 60 * 1000;

export interface WakeState {
  now: number;
  lastWake: number;     // ms; 0 when never
  named: number;        // ms; 0 when none
  news: boolean;
  look: boolean;
  birdweather: boolean; // a BirdWeather station is polled (and not in quiet hours)
}

export function due(s: WakeState): boolean {
  return (s.named > 0 && s.named <= s.now)
    || ((s.news || s.look) && s.now - s.lastWake >= MIN_GAP_MS)
    || s.now - s.lastWake >= SAFETY_MS;
}

export function nextAlarm(s: WakeState): number {
  const times = [s.lastWake + SAFETY_MS];
  if (s.named > s.now) times.push(s.named);
  if (s.news || s.look) times.push(Math.max(s.now, s.lastWake + MIN_GAP_MS));
  if (s.birdweather) times.push(s.now + POLL_MS);
  return Math.max(s.now + 1000, Math.min(...times));
}
```

- [ ] **Step 4: Run them to see them pass**

Run: `cd hosted && npx vitest run test/wakes.test.ts`
Expected: PASS (7 tests).

- [ ] **Step 5: Use it in `household.ts`**

Delete the `POLL_MS`, `MIN_GAP_MS` and `SAFETY_MS` constants (household.ts:17-19) and import them, with the rule, from `./wakes`:

```ts
import { due, MIN_GAP_MS, nextAlarm, type WakeState } from "./wakes";
```

Add to the `Household` class, before `wake()`:

```ts
  /** The wake rule's inputs, from meta. */
  wakeState(now = Date.now()): WakeState {
    return {
      now, lastWake: Number(this.meta("wake_ms") || 0),
      named: Number(this.meta("next_wake_epoch") || 0) * 1000,
      news: this.meta("news") === "1", look: this.meta("look") === "1",
      birdweather: this.meta("source_kind") === "birdweather" && this.meta("poll") !== "0",
    };
  }

  /** Someone opened a stale page (W-946): one wake by the alarm, never in
   * the request. In quiet hours, only when something is waiting for it. */
  async look(): Promise<void> {
    if (this.meta("suspended")) return;
    if (this.meta("poll") === "0") {
      const waiting = this.sql.exec<{ n: number }>(
        "SELECT (SELECT count(*) FROM checkins) + (SELECT count(*) FROM ingest) AS n").one().n;
      if (!waiting) return;
    }
    this.setMeta("look", "1");
    await this.schedule();
  }
```

Replace the bodies of `alarm()` and `schedule()` (household.ts:350-373) with:

```ts
  async alarm(): Promise<void> {
    if (this.meta("suspended")) return;
    await this.lookForNews();
    if (due(this.wakeState())) await this.wake();
    else await this.schedule();
  }

  async schedule(): Promise<void> {
    if (this.meta("suspended")) return;
    await this.ctx.storage.setAlarm(nextAlarm(this.wakeState()));
  }
```

In `wake()`, after `this.setMeta("news", "0");`, add `this.setMeta("look", null);`.

- [ ] **Step 6: Run everything and typecheck**

Run: `cd hosted && npx vitest run && npx tsc --noEmit -p .`
Expected: all pass, tsc exits 0.

- [ ] **Step 7: Commit**

```bash
git add hosted/src/wakes.ts hosted/test/wakes.test.ts hosted/src/household.ts
git commit -m "The front door's wake rule in one place, with a look at a stale page (W-946)"
```

---

### Task 2: The cache itself

**Files:**
- Create: `hosted/src/pagecache.ts`
- Create: `hosted/test/pagecache.test.ts`

**Interfaces:**
Companions (the spec's "`/` brings `/api/status`, `/api/tasks`, `/api/history` and the previews"): the page asks for all of them as it loads, so their rows are read whenever `/` is. The 2-hour rule refreshes them together, in one generation, with no list of their own.

- Produces: `FRESH_MS`, `READ_WINDOW_MS`, `MAX_BODY`, `type Kind = "changing" | "fixed"`, `interface Route { kind: Kind; keys: string[] }`, `routeOf(path: string, today: string): Route | null`, `interface Ask { path: string; query: [string, string][]; account: Record<string, string> }`, `ACCOUNT_HEADERS`, `cacheKey(url: URL, route: Route, account: Record<string, string>, build: string): { key: string; ask: Ask }`, `interface Bucket`, `class PageCache` with `find`, `touch`, `fresh`, `body`, `note`, `store`, `clear`, `stale`, `readSince`, `refresh`, `respond`.

- [ ] **Step 1: Write the failing tests**

`hosted/test/pagecache.test.ts`:

```ts
// The front door's copy of the webapp's reads (W-946, src/pagecache.ts).
import { describe, expect, it } from "vitest";
import { cacheKey, FRESH_MS, MAX_BODY, PageCache, READ_WINDOW_MS, routeOf } from "../src/pagecache";
import { nodeSql } from "./sql";

function bucket() {
  const m = new Map<string, Uint8Array>();
  return {
    m,
    async get(k: string) {
      const b = m.get(k);
      return b ? { arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) } : null;
    },
    async put(k: string, v: ArrayBuffer) { m.set(k, new Uint8Array(v)); },
    async delete(k: string | string[]) { for (const x of [k].flat()) m.delete(x); },
  };
}

const T = 1_790_000_000_000;
const U = (p: string) => new URL(`https://cloud.featherframe.app${p}`);
const page = (body: string, type = "text/html; charset=utf-8", status = 200) =>
  new Response(body, { status, headers: { "Content-Type": type, "Cache-Control": "no-cache", "Set-Cookie": "x=1" } });

describe("routeOf", () => {
  it("caches the webapp's reads and nothing else", () => {
    expect(routeOf("/", "2026-10-02")).toEqual({ kind: "changing", keys: [] });
    expect(routeOf("/api/status", "2026-10-02")?.kind).toBe("changing");
    expect(routeOf("/api/battery", "2026-10-02")?.keys).toEqual(["frame", "hours"]);
    expect(routeOf("/api/frames/AA%3ABB/preview.png", "2026-10-02")?.kind).toBe("changing");
    expect(routeOf("/api/collages/2026-10-02.png", "2026-10-02")?.kind).toBe("changing");
    expect(routeOf("/api/collages/2026-10-01.png", "2026-10-02")?.kind).toBe("fixed");
    expect(routeOf("/api/generated/blue-jay.png", "2026-10-02")?.keys).toEqual(["thumb", "v"]);
    expect(routeOf("/api/history/0123456789abcdef.jpg", "2026-10-02")?.kind).toBe("fixed");
    expect(routeOf("/static/examples/a.png", "2026-10-02")?.kind).toBe("fixed");
    for (const p of ["/api/frame", "/api/imagegen/models", "/api/generated/export", "/settings", "/api/flash/ee02/manifest.json"]) {
      expect(routeOf(p, "2026-10-02")).toBeNull();
    }
  });
});

describe("cacheKey", () => {
  it("keeps the query keys that name a read and drops the cache-busters", () => {
    const r = routeOf("/api/generated/x.png", "d")!;
    const a = cacheKey(U("/api/generated/x.png?thumb=1&v=7&t=99"), r, {}, "b1");
    const b = cacheKey(U("/api/generated/x.png?t=12&v=7&thumb=1"), r, {}, "b1");
    expect(a.key).toBe(b.key);
    expect(a.ask).toEqual({ path: "/api/generated/x.png", query: [["thumb", "1"], ["v", "7"]], account: {} });
    expect(cacheKey(U("/api/generated/x.png?thumb=1&v=8"), r, {}, "b1").key).not.toBe(a.key);
  });
  it("drops the page's own flash query, and carries the account and the build", () => {
    const r = routeOf("/", "d")!;
    const acct = { "x-ff-account-email": "w@example.com" };
    expect(cacheKey(U("/?saved=1&open=general"), r, acct, "b1").key).toBe(cacheKey(U("/"), r, acct, "b1").key);
    expect(cacheKey(U("/"), r, { "x-ff-account-email": "x@example.com" }, "b1").key)
      .not.toBe(cacheKey(U("/"), r, acct, "b1").key);
    expect(cacheKey(U("/"), r, acct, "b2").key).not.toBe(cacheKey(U("/"), r, acct, "b1").key);
  });
});

describe("PageCache", () => {
  const make = () => { const b = bucket(); return { b, c: new PageCache(nodeSql(), b, "households/h/cache/") }; };
  const route = routeOf("/", "d")!;

  it("stores a 200 and answers it, with the server's headers and none of its cookies", async () => {
    const { c } = make();
    const { key, ask } = cacheKey(U("/"), route, {}, "b");
    const res = await c.store(key, route.kind, ask, page("<p>hi</p>"), T);
    expect(await res.text()).toBe("<p>hi</p>");
    const e = c.find(key)!;
    expect(c.fresh(e, T + FRESH_MS - 1)).toBe(true);
    expect(c.fresh(e, T + FRESH_MS)).toBe(false);
    const again = await c.respond(e, "GET");
    expect(await again!.text()).toBe("<p>hi</p>");
    expect(again!.headers.get("Content-Type")).toBe("text/html; charset=utf-8");
    expect(again!.headers.get("Set-Cookie")).toBeNull();
  });

  it("stores nothing but a 200 under 5 MB", async () => {
    const { c } = make();
    const { key, ask } = cacheKey(U("/"), route, {}, "b");
    await c.store(key, route.kind, ask, page("gone", "text/plain", 404), T);
    expect(c.find(key)).toBeNull();
    await c.store(key, route.kind, ask, new Response(new Uint8Array(MAX_BODY + 1)), T);
    expect(c.find(key)).toBeNull();
  });

  it("treats a copy missing from R2 as a miss", async () => {
    const { b, c } = make();
    const { key, ask } = cacheKey(U("/"), route, {}, "b");
    await c.store(key, route.kind, ask, page("x"), T);
    b.m.clear();
    expect(await c.respond(c.find(key)!, "GET")).toBeNull();
  });

  it("a POST's clear takes the changing copies and keeps the fixed ones", async () => {
    const { b, c } = make();
    const fixed = routeOf("/api/history/0123456789abcdef.png", "d")!;
    const k1 = cacheKey(U("/"), route, {}, "b"), k2 = cacheKey(U("/api/history/0123456789abcdef.png"), fixed, {}, "b");
    await c.store(k1.key, route.kind, k1.ask, page("p"), T);
    await c.store(k2.key, fixed.kind, k2.ask, page("h", "image/png"), T);
    await c.clear("changing");
    expect(c.find(k1.key)).toBeNull();
    expect(c.find(k2.key)).not.toBeNull();
    expect(b.m.size).toBe(1);
    await c.clear("all");
    expect(b.m.size).toBe(0);
  });

  it("refreshes what was read in the last 2 hours and empty rows, all in one generation", async () => {
    const { c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    const ks = cacheKey(U("/api/status"), routeOf("/api/status", "d")!, {}, "b");
    const kb = cacheKey(U("/api/battery?frame=A&hours=24"), routeOf("/api/battery", "d")!, {}, "b");
    await c.store(kp.key, "changing", kp.ask, page("old page"), T);
    await c.store(kb.key, "changing", kb.ask, page("{}", "application/json"), T);
    c.touch(kp.key, T);
    c.touch(kb.key, T - READ_WINDOW_MS - 1);
    c.note(ks.key, "changing", ks.ask, T);            // a miss: a row with no copy yet
    const asked: string[] = [];
    const ok = await c.refresh(async (a) => { asked.push(a.path); return page(`new ${a.path}`); }, T + 1);
    expect(ok).toBe(true);
    expect(asked.sort()).toEqual(["/", "/api/status"]);
    expect(await (await c.respond(c.find(kp.key)!, "GET"))!.text()).toBe("new /");
    expect(await (await c.respond(c.find(ks.key)!, "GET"))!.text()).toBe("new /api/status");
    expect(c.find(kp.key)!.generation).toBe(c.find(ks.key)!.generation);
  });

  it("keeps every copy as it was when one ask fails", async () => {
    const { c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    const ks = cacheKey(U("/api/status"), routeOf("/api/status", "d")!, {}, "b");
    await c.store(kp.key, "changing", kp.ask, page("old page"), T);
    await c.store(ks.key, "changing", ks.ask, page("old status"), T);
    c.touch(kp.key, T); c.touch(ks.key, T);
    const ok = await c.refresh(async (a) => a.path === "/" ? page("new page") : page("down", "text/plain", 500), T + 1);
    expect(ok).toBe(false);
    expect(await (await c.respond(c.find(kp.key)!, "GET"))!.text()).toBe("old page");
  });

  it("knows when the copies someone reads are stale", async () => {
    const { c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    await c.store(kp.key, "changing", kp.ask, page("p"), T);
    c.touch(kp.key, T);
    expect(c.readSince(T - READ_WINDOW_MS)).toBe(true);
    expect(c.stale(T + FRESH_MS - 1)).toBe(false);
    expect(c.stale(T + FRESH_MS)).toBe(true);
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd hosted && npx vitest run test/pagecache.test.ts`
Expected: FAIL, `Failed to load url ../src/pagecache`.

- [ ] **Step 3: Write `pagecache.ts`**

```ts
// The front door's copy of the webapp's reads (W-946): a standard
// stale-while-revalidate cache. A read is answered from the server's last
// answer to it; a copy older than FRESH_MS is still answered while a wake is
// asked for. Bodies live in R2, the index in the front door's SQLite.

import type { SqlLike } from "./spend";
import { randomHex } from "./util";

export const FRESH_MS = 15 * 60_000;
export const READ_WINDOW_MS = 2 * 60 * 60_000;
export const UNREAD_DROP_MS = 30 * 24 * 60 * 60_000;
export const MAX_BODY = 5 * 1024 * 1024;
const KEEP_HEADERS = ["content-type", "cache-control", "content-disposition", "etag", "last-modified"];
export const ACCOUNT_HEADERS = ["x-ff-account-email", "x-ff-account-email-pending", "x-ff-account-unverified"];

export type Kind = "changing" | "fixed";
export interface Route { kind: Kind; keys: string[] }
/** What to ask the server again: the path, the query in the key, and for
 * `/` the account it was drawn for. */
export interface Ask { path: string; query: [string, string][]; account: Record<string, string> }

export interface Bucket {
  get(key: string): Promise<{ arrayBuffer(): Promise<ArrayBuffer> } | null>;
  put(key: string, value: ArrayBuffer): Promise<unknown>;
  delete(keys: string | string[]): Promise<void>;
}
export interface Entry {
  key: string; kind: Kind; ask: string; object: string | null; headers: string | null;
  generation: number; filled_at: number | null; read_at: number;
}

/** Which of the webapp's reads are kept, and the query keys that name them.
 * `today` is the household's date: today's collage is still being drawn. */
export function routeOf(path: string, today: string): Route | null {
  const changing = (keys: string[] = []): Route => ({ kind: "changing", keys });
  const fixed = (keys: string[] = []): Route => ({ kind: "fixed", keys });
  if (path === "/" || path === "/api/status" || path === "/api/history" || path === "/api/tasks") return changing();
  if (path === "/api/battery") return changing(["frame", "hours"]);
  if (path === "/api/preview.png" || /^\/api\/frames\/[^/]+\/preview\.png$/.test(path)) return changing();
  const day = path.match(/^\/api\/collages\/(\d{4}-\d{2}-\d{2})\.png$/);
  if (day) return day[1] === today ? changing(["thumb"]) : fixed(["thumb"]);
  if (/^\/api\/generated\/[^/]+\.png$/.test(path)) return fixed(["thumb", "v"]);
  if (/^\/api\/history\/[0-9a-f]{16}\.(png|jpg)$/.test(path)) return fixed();
  if (path.startsWith("/static/")) return fixed();
  return null;
}

export function cacheKey(url: URL, route: Route, account: Record<string, string>, build: string):
    { key: string; ask: Ask } {
  const query = route.keys.filter((k) => url.searchParams.has(k))
    .map((k) => [k, url.searchParams.get(k)!] as [string, string]).sort((a, b) => a[0].localeCompare(b[0]));
  const acct = url.pathname === "/" ? Object.fromEntries(ACCOUNT_HEADERS.map((h) => [h, account[h] || ""])) : {};
  const ask: Ask = { path: url.pathname, query, account: acct };
  return { key: JSON.stringify([build, ask.path, query, acct]), ask };
}

export class PageCache {
  constructor(private sql: SqlLike, private bucket: Bucket, private prefix: string) {
    sql.exec(`CREATE TABLE IF NOT EXISTS page_cache (key TEXT PRIMARY KEY, kind TEXT NOT NULL, ask TEXT NOT NULL,
      object TEXT, headers TEXT, generation INTEGER NOT NULL DEFAULT 0, filled_at INTEGER, read_at INTEGER NOT NULL)`);
  }

  find(key: string): Entry | null {
    return (this.sql.exec("SELECT * FROM page_cache WHERE key = ?", key).toArray()[0] as Entry | undefined) ?? null;
  }
  touch(key: string, now: number): void {
    this.sql.exec("UPDATE page_cache SET read_at = ? WHERE key = ?", now, key);
  }
  fresh(e: Entry, now: number): boolean {
    return e.filled_at !== null && now - e.filled_at < FRESH_MS;
  }
  /** A row with no copy yet: the next refresh fills it. */
  note(key: string, kind: Kind, ask: Ask, now: number): void {
    this.sql.exec(`INSERT INTO page_cache (key, kind, ask, read_at) VALUES (?, ?, ?, ?)
      ON CONFLICT (key) DO UPDATE SET read_at = excluded.read_at`, key, kind, JSON.stringify(ask), now);
  }

  /** The stored answer, or null when there is none (or its body is gone). */
  async respond(e: Entry, method: string): Promise<Response | null> {
    if (!e.object) return null;
    const obj = await this.bucket.get(this.prefix + e.object);
    if (!obj) return null;
    const headers = new Headers(JSON.parse(e.headers || "{}"));
    return new Response(method === "HEAD" ? null : await obj.arrayBuffer(), { headers });
  }

  /** Keep `res` if it is a 200 under MAX_BODY; answer the client either way. */
  async store(key: string, kind: Kind, ask: Ask, res: Response, now: number): Promise<Response> {
    const body = await res.arrayBuffer();
    const out = new Response(body, { status: res.status, headers: res.headers });
    if (res.status !== 200 || body.byteLength > MAX_BODY) return out;
    const object = randomHex(16);
    await this.bucket.put(this.prefix + object, body);
    const old = this.find(key)?.object;
    this.sql.exec(`INSERT INTO page_cache (key, kind, ask, object, headers, filled_at, read_at) VALUES (?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT (key) DO UPDATE SET object = excluded.object, headers = excluded.headers, filled_at = excluded.filled_at`,
      key, kind, JSON.stringify(ask), object, JSON.stringify(kept(res.headers)), now, now);
    if (old) await this.bucket.delete(this.prefix + old);
    return out;
  }

  async clear(which: "changing" | "all"): Promise<void> {
    const where = which === "all" ? "" : " WHERE kind = 'changing'";
    const objects = this.sql.exec(`SELECT object FROM page_cache${where}`).toArray()
      .map((r) => r.object as string | null).filter((o): o is string => !!o);
    this.sql.exec(`DELETE FROM page_cache${where}`);
    if (objects.length) await this.bucket.delete(objects.map((o) => this.prefix + o));
  }

  /** Someone read a changing copy since `since`. */
  readSince(since: number): boolean {
    return this.sql.exec(
      "SELECT count(*) AS n FROM page_cache WHERE kind = 'changing' AND read_at >= ?", since).toArray()[0].n > 0;
  }
  /** Some changing copy someone reads is older than FRESH_MS. */
  stale(now: number): boolean {
    return this.sql.exec(`SELECT count(*) AS n FROM page_cache WHERE kind = 'changing'
      AND read_at >= ? AND (filled_at IS NULL OR filled_at <= ?)`, now - READ_WINDOW_MS, now - FRESH_MS)
      .toArray()[0].n > 0;
  }

  /** Ask the server again for every changing copy read in the last 2 hours,
   * and every row with no copy. Only when every ask answers 200 do the rows
   * move to the new copies, together (no await between the updates, so no
   * other request sees half of them); else nothing changes. True when it
   * swapped. */
  async refresh(ask: (a: Ask) => Promise<Response>, now: number): Promise<boolean> {
    const rows = this.sql.exec(`SELECT * FROM page_cache WHERE kind = 'changing' AND (read_at >= ? OR object IS NULL)`,
      now - READ_WINDOW_MS).toArray() as Entry[];
    if (!rows.length) return true;
    const fresh: { e: Entry; object: string; headers: string }[] = [];
    for (const e of rows) {
      const res = await ask(JSON.parse(e.ask) as Ask);
      const body = await res.arrayBuffer();
      if (res.status !== 200 || body.byteLength > MAX_BODY) {
        if (fresh.length) await this.bucket.delete(fresh.map((f) => this.prefix + f.object));
        return false;
      }
      const object = randomHex(16);
      await this.bucket.put(this.prefix + object, body);
      fresh.push({ e, object, headers: JSON.stringify(kept(res.headers)) });
    }
    const generation = Number(this.sql.exec("SELECT coalesce(max(generation), 0) AS g FROM page_cache")
      .toArray()[0].g) + 1;
    for (const f of fresh) {
      this.sql.exec("UPDATE page_cache SET object = ?, headers = ?, generation = ?, filled_at = ? WHERE key = ?",
        f.object, f.headers, generation, now, f.e.key);
    }
    const old = fresh.map((f) => f.e.object).filter((o): o is string => !!o);
    if (old.length) await this.bucket.delete(old.map((o) => this.prefix + o));
    const unread = this.sql.exec(
      "SELECT object FROM page_cache WHERE read_at < ?", now - UNREAD_DROP_MS).toArray() as { object: string | null }[];
    this.sql.exec("DELETE FROM page_cache WHERE read_at < ?", now - UNREAD_DROP_MS);
    const gone = unread.map((r) => r.object).filter((o): o is string => !!o);
    if (gone.length) await this.bucket.delete(gone.map((o) => this.prefix + o));
    return true;
  }
}

function kept(h: Headers): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of KEEP_HEADERS) { const v = h.get(k); if (v) out[k] = v; }
  return out;
}
```

- [ ] **Step 4: Run them to see them pass**

Run: `cd hosted && npx vitest run test/pagecache.test.ts && npx tsc --noEmit -p .`
Expected: PASS (all); tsc exits 0.

- [ ] **Step 5: Commit**

```bash
git add hosted/src/pagecache.ts hosted/test/pagecache.test.ts
git commit -m "The front door's cache of the webapp's reads (W-946)"
```

---

### Task 3: How a read is answered

**Files:**
- Modify: `hosted/src/pagecache.ts` (add `answerRead`)
- Create: `hosted/src/loading.ts`
- Test: `hosted/test/pagecache.test.ts`

**Interfaces:**
- Consumes: Task 2's `routeOf`, `cacheKey`, `PageCache`, `ACCOUNT_HEADERS`.
- Produces: `interface ReadDeps { cache; now; build; today; running(): Promise<boolean>; ask(a: Ask): Promise<Response>; proxy(): Promise<Response>; look(): Promise<void>; loading(): Response }`, `answerRead(request: Request, url: URL, d: ReadDeps): Promise<Response | null>`; `loadingPage(): Response` in `loading.ts`.

- [ ] **Step 1: Write the failing tests**

Append to `hosted/test/pagecache.test.ts` (add `answerRead` to the import):

```ts
describe("answerRead", () => {
  function deps(over: Record<string, unknown> = {}) {
    const b = bucket();
    const calls = { ask: 0, proxy: 0, look: 0 };
    const d = {
      cache: new PageCache(nodeSql(), b, "p/"), now: T, build: "b", today: "2026-10-02",
      running: async () => false,
      ask: async () => { calls.ask++; return page("from server"); },
      proxy: async () => { calls.proxy++; return page("proxied"); },
      look: async () => { calls.look++; },
      loading: () => new Response("loading"),
      ...over,
    };
    return { d, calls };
  }
  const get = (p: string, headers: Record<string, string> = {}) => new Request(`https://cloud.featherframe.app${p}`, { headers });

  it("leaves to the server what it does not cache: writes, live reads, other paths", async () => {
    const { d } = deps();
    expect(await answerRead(new Request("https://cloud.featherframe.app/settings", { method: "POST" }), U("/settings"), d)).toBeNull();
    expect(await answerRead(get("/api/status?live=1"), U("/api/status?live=1"), d)).toBeNull();
    expect(await answerRead(get("/api/frame"), U("/api/frame"), d)).toBeNull();
  });

  it("answers a fresh copy without the server, and a stale one while asking for a look", async () => {
    const { d, calls } = deps({ running: async () => true });
    expect(await (await answerRead(get("/"), U("/"), d))!.text()).toBe("from server");   // a miss, server up
    expect(calls.ask).toBe(1);
    expect(await (await answerRead(get("/"), U("/"), { ...d, now: T + 1 }))!.text()).toBe("from server");
    expect(calls).toMatchObject({ ask: 1, look: 0 });
    await answerRead(get("/"), U("/"), { ...d, now: T + FRESH_MS });
    expect(calls).toMatchObject({ ask: 1, look: 1 });
  });

  it("with the server asleep, answers / with the loading page and asks for a look", async () => {
    const { d, calls } = deps();
    expect(await (await answerRead(get("/?welcome=1"), U("/?welcome=1"), d))!.text()).toBe("loading");
    expect(calls).toMatchObject({ ask: 0, proxy: 0, look: 1 });
    // …and leaves a row the next refresh fills.
    const key = cacheKey(U("/"), routeOf("/", "d")!, {}, "b").key;
    expect(d.cache.find(key)?.object).toBeNull();
  });

  it("with the server asleep, asks it for any other read and keeps the answer", async () => {
    const { d, calls } = deps();
    expect(await (await answerRead(get("/api/battery?frame=A&hours=24"), U("/api/battery?frame=A&hours=24"), d))!.text())
      .toBe("proxied");
    expect(calls.proxy).toBe(1);
    await answerRead(get("/api/battery?hours=24&frame=A"), U("/api/battery?hours=24&frame=A"), { ...d, now: T + 1 });
    expect(calls.proxy).toBe(1);
  });

  it("keys / by the account it was drawn for", async () => {
    const { d, calls } = deps({ running: async () => true });
    await answerRead(get("/", { "X-FF-Account-Email": "w@example.com" }), U("/"), d);
    await answerRead(get("/", { "X-FF-Account-Email": "w@example.com", "X-FF-Account-Unverified": "1" }), U("/"), d);
    expect(calls.ask).toBe(2);
  });

  it("answers a HEAD with headers and no body", async () => {
    const { d } = deps({ running: async () => true });
    await answerRead(get("/api/status"), U("/api/status"), d);
    const res = await answerRead(new Request("https://cloud.featherframe.app/api/status", { method: "HEAD" }), U("/api/status"), d);
    expect(res!.body).toBeNull();
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd hosted && npx vitest run test/pagecache.test.ts`
Expected: FAIL, `answerRead` is not exported.

- [ ] **Step 3: Write `answerRead`**

Append to `hosted/src/pagecache.ts`:

```ts
export interface ReadDeps {
  cache: PageCache;
  now: number;
  build: string;              // the server's page build (meta page_build), "" before the first report
  today: string;              // the household's date, YYYY-MM-DD
  running(): Promise<boolean>; // the server is up and not on its way out
  ask(a: Ask): Promise<Response>;   // straight to the server: not page activity
  proxy(): Promise<Response>;       // today's path, which starts the server
  look(): Promise<void>;            // a wake by the alarm
  loading(): Response;              // the bundled loading page
}

/** A webapp read answered from the cache, or null when the cache does not
 * keep it (a write, `live=1`, a path not in the table). */
export async function answerRead(request: Request, url: URL, d: ReadDeps): Promise<Response | null> {
  if (request.method !== "GET" && request.method !== "HEAD") return null;
  if (url.searchParams.get("live") === "1") return null;
  const route = routeOf(url.pathname, d.today);
  if (!route) return null;
  const account: Record<string, string> = {};
  for (const h of ACCOUNT_HEADERS) account[h] = request.headers.get(h) || "";
  const { key, ask } = cacheKey(url, route, account, d.build);
  const e = d.cache.find(key);
  if (e) {
    const hit = await d.cache.respond(e, request.method);
    if (hit) {
      d.cache.touch(key, d.now);
      if (route.kind === "changing" && !d.cache.fresh(e, d.now)) await d.look();
      return hit;
    }
  }
  d.cache.note(key, route.kind, ask, d.now);
  if (await d.running()) return d.cache.store(key, route.kind, ask, await d.ask(ask), d.now);
  if (url.pathname === "/") {
    await d.look();
    return d.loading();
  }
  return d.cache.store(key, route.kind, ask, await d.proxy(), d.now);
}
```

- [ ] **Step 4: Write the loading page**

`hosted/src/loading.ts`:

```ts
// The webapp before there is a copy of it (W-946): its header and its cards
// as gray blocks, no text. It asks the front door whether the page is ready
// and reloads with its own query and hash once it is.

const HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Featherframe</title>
<style>
  :root { --bg:#f6f4ef; --block:#e7e3da; }
  @media (prefers-color-scheme: dark) { :root { --bg:#171614; --block:#262420; } }
  body { margin:0; background:var(--bg); }
  header { height:64px; max-width:1180px; margin:0 auto; padding:0 16px; display:flex; align-items:center; }
  .mark { width:150px; height:30px; border-radius:6px; background:var(--block); }
  main { max-width:1180px; margin:0 auto; padding:8px 16px 32px; display:grid; gap:20px; grid-template-columns:minmax(0,380px) minmax(0,1fr); }
  @media (max-width:760px) { main { grid-template-columns:1fr; } }
  .b { background:var(--block); border-radius:12px; animation:p 1.6s ease-in-out infinite; }
  .col { display:grid; gap:20px; align-content:start; }
  @keyframes p { 50% { opacity:.55; } }
  @media (prefers-reduced-motion:reduce) { .b { animation:none; } }
</style></head><body>
<header><div class="mark b"></div></header>
<main><div class="col"><div class="b" style="aspect-ratio:3/4"></div><div class="b" style="height:120px"></div></div>
<div class="col"><div class="b" style="height:220px"></div><div class="b" style="height:320px"></div></div></main>
<script>(function(){function look(){fetch("/api/page/ready",{credentials:"same-origin"}).then(function(r){return r.json();})
.then(function(s){if(s.ready)location.reload();else setTimeout(look,2000);}).catch(function(){setTimeout(look,2000);});}
setTimeout(look,2000);})();</script></body></html>`;

export function loadingPage(): Response {
  return new Response(HTML, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}
```

(`location.reload()` keeps the query and the hash.)

- [ ] **Step 5: Run the tests and the typecheck**

Run: `cd hosted && npx vitest run && npx tsc --noEmit -p .`
Expected: all pass, tsc exits 0.

- [ ] **Step 6: Commit**

```bash
git add hosted/src/pagecache.ts hosted/src/loading.ts hosted/test/pagecache.test.ts
git commit -m "Answer the webapp's reads from the cache, and a loading page before there is one (W-946)"
```

---

### Task 4: Wire the cache into the front door

**Files:**
- Modify: `hosted/src/containers.ts` (`HouseholdServer.running()`)
- Modify: `hosted/src/household.ts` (the cache, reads, warm, page/ready, clears, refreshes, the build)
- Test: `hosted/test/containers.test.ts`

**Interfaces:**
- Consumes: `answerRead`, `PageCache`, `cacheKey`, `routeOf`, `ACCOUNT_HEADERS`, `type Ask` (Tasks 2–3); `loadingPage` (Task 3); `Household.look` (Task 1).
- Produces: `HouseholdServer.running(): Promise<boolean>`; front-door routes `POST /api/warm` → 204 and `GET /api/page/ready` → `{ready: boolean}`.

- [ ] **Step 1: Write the failing test for `running()`**

Append to the `HouseholdServer` describe in `hosted/test/containers.test.ts`:

```ts
  it("says it is running only while up and not on its way out", async () => {
    const c = fakeContainer();
    const h = household(c);
    expect(await h.running()).toBe(true);
    const stopping = h.stop();
    expect(await h.running()).toBe(false);
    c.exit();
    await stopping;
    expect(await h.running()).toBe(false);
  });
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd hosted && npx vitest run test/containers.test.ts`
Expected: FAIL, `h.running is not a function`.

- [ ] **Step 3: Add `running()` to `HouseholdServer`**

In `hosted/src/containers.ts`, inside `class HouseholdServer`, after `sleepWhenIdle()`:

```ts
  /** Up and not on its way out (W-946): a stopping server counts as asleep,
   * so the cache answers rather than waiting out the stop and a new start. */
  async running(): Promise<boolean> {
    return !!this.ctx.container?.running && !this.stopping;
  }
```

Run: `cd hosted && npx vitest run test/containers.test.ts`
Expected: PASS.

- [ ] **Step 4: Wire the front door**

In `hosted/src/household.ts`, add the imports:

```ts
import { answerRead, type Ask, PageCache } from "./pagecache";
import { loadingPage } from "./loading";
```

Add the cache as a getter (the household's id is not known in the constructor of a new front door), and a single-flight refresh:

```ts
  private _cache?: PageCache;
  /** The webapp's reads, kept (W-946); bodies under the household's own prefix. */
  get cache(): PageCache {
    return this._cache ??= new PageCache(this.sql, this.env.DATA, `households/${this.meta("hid")}/cache/`);
  }
  refreshing: Promise<void> | null = null;
```

In `fetch()`, after the `X-FF-Viewer` branch and the ingest branch, before `return this.proxy(request);`:

```ts
    if (url.pathname === "/api/warm" && request.method === "POST") return this.warm();
    if (url.pathname === "/api/page/ready" && request.method === "GET") return this.pageReady(request, url);
    const cached = await answerRead(request, url, this.readDeps(request));
    if (cached) return cached;
```

Add these methods to the class:

```ts
  // -- the webapp's reads, from the cache (W-946) ------------------------------
  readDeps(request: Request) {
    return {
      cache: this.cache, now: Date.now(), build: this.meta("page_build") || "",
      today: localIso(this.meta("tz") || "UTC").slice(0, 10),
      // Asked without configure(): a read must not cost the server DO a write.
      running: async () => this.env.SERVER.getByName(this.meta("hid")!).running(),
      ask: (a: Ask) => this.askServer(a),
      proxy: () => this.proxy(request),
      look: () => this.look(),
      loading: () => loadingPage(),
    };
  }

  /** Straight to the server, not through proxy(): a refresh or a cache fill
   * is not someone using the page, and must not hold the server up. */
  async askServer(a: Ask): Promise<Response> {
    const stub = await this.server();
    const q = new URLSearchParams(a.query).toString();
    const headers = new Headers({ "X-FF-Hosted": "1", ...a.account });
    return stub.fetch(new Request(`http://server${a.path}${q ? `?${q}` : ""}`, { headers }));
  }

  /** The loading page's question: is / kept for this account yet? When it is
   * not but the server is up, keep it now. */
  async pageReady(request: Request, url: URL): Promise<Response> {
    const res = await answerRead(new Request(new URL("/", url), { headers: request.headers }), new URL("/", url),
      { ...this.readDeps(request), loading: () => new Response(null, { status: 204 }), look: async () => {} });
    return Response.json({ ready: !!res && res.status === 200 }, { headers: { "Cache-Control": "no-store" } });
  }

  /** The owner started an edit (W-946): start the server now, so the save
   * meets it up. Counted as page activity; never for a suspended household. */
  async warm(): Promise<Response> {
    if (!this.meta("suspended")) {
      this.setMeta("page_ms", String(Date.now()));
      this.ctx.waitUntil((async () => {
        try { await (await this.server()).fetch("http://server/api/hosted/busy"); } catch { /* the save will start it */ }
      })());
    }
    return new Response(null, { status: 204 });
  }

  /** Ask the server again for the copies someone reads, one refresh at a time. */
  async refreshCache(): Promise<void> {
    if (this.refreshing) return this.refreshing;
    this.refreshing = (async () => {
      try { await this.cache.refresh((a) => this.askServer(a), Date.now()); }
      catch (err) { console.error("cache refresh", err); }
    })().finally(() => { this.refreshing = null; });
    return this.refreshing;
  }
```

`answerRead` is given a fresh `Request` for `/` in `pageReady`, so its key and account match what `/` itself would use. `/api/warm` and `/api/page/ready` are answered here, never proxied.

In `proxy()`, mark that a POST is under way and clear the changing copies when it lands. Replace `const res = await stub.fetch(again ? forward.clone() : forward);` and the lines after it, through the final `return res;`, with:

```ts
    if (request.method === "POST") this.setMeta("post_pending", "1");
    const res = await stub.fetch(again ? forward.clone() : forward);
    if (removing && res.ok) await this.unpair(removing);
    // A change the owner made: the next reads ask the server (W-946).
    if (request.method === "POST" && res.status < 400) await this.cache.clear("changing");
    if (again && res.status === 500 && (await res.clone().text()).startsWith("Container suddenly disconnected")) {
      console.warn("container link dropped; asking again", new URL(request.url).pathname);
      return stub.fetch(forward);
    }
    return res;
```

In `adopt()`, after `this.setMeta("wake_ms", "0");`, add:

```ts
    await this.cache.clear("changing");     // the reload after pairing shows the new frame
```

At the start of `takeState()`, keep the frames, viewers and build from before the report:

```ts
    const shown = (): string => JSON.stringify([
      this.sql.exec("SELECT id, status, etag, headers FROM frames ORDER BY id").toArray(),
      this.sql.exec("SELECT id, status, name FROM viewers ORDER BY id").toArray()]);
    const shownBefore = shown();
    const buildBefore = this.meta("page_build");
```

Add `page_build?: string;` to `takeState`'s parameter type. At the end of `takeState()`, before `await this.schedule();`, add:

```ts
    // A new server image draws a new page: nothing kept from the old one.
    if ((state.page_build || null) !== buildBefore) {
      this.setMeta("page_build", state.page_build || null);
      await this.cache.clear("all");
    }
    // While the server is up, refresh what someone reads when the page would
    // show something new, or after the owner changed something.
    const afterPost = this.meta("post_pending") === "1";
    this.setMeta("post_pending", null);
    if (afterPost || shown() !== shownBefore) this.ctx.waitUntil(this.refreshCache());
```

In `wake()`, after `await stub.fetch("http://server/api/hosted/run", { method: "POST" });` and before the `sleepWhenIdle` line, add:

```ts
      // Copies someone reads that this wake did not refresh, while it is up.
      if (this.cache.stale(Date.now())) await this.refreshCache();
```

- [ ] **Step 5: Run the tests and the typecheck**

Run: `cd hosted && npx vitest run && npx tsc --noEmit -p .`
Expected: all pass, tsc exits 0. `stub.running()` type-checks because `HouseholdServer` declares it. If tsc says `this.env.DATA` is unknown in the constructor, the field is already on `Env` (index.ts:36); fix the import, not the type.

- [ ] **Step 6: Commit**

```bash
git add hosted/src/containers.ts hosted/src/household.ts hosted/test/containers.test.ts
git commit -m "The front door answers the webapp from its cache and refreshes it while the server is up (W-946)"
```

---

### Task 5: The server's page build, and epoch times for the page

**Files:**
- Modify: `server/featherframe/service.py` (`hosted_state()["page_build"]`; `frame_card`/`frame_health` `last_checkin_ts`; `status()["last_detection"]["at_ts"]`; `frame_view` `queued_until` and `preview_etag`)
- Create: `server/featherframe/page_build.py`
- Test: `server/tests/test_page_cache_fields.py`

**Interfaces:**
- Produces: `page_build.build() -> str` (12 hex); `hosted_state()["page_build"]`; per frame `card["last_checkin_ts"]: Optional[int]`, `frame_view["queued_until"]: Optional[int]`, `frame_view["preview_etag"]: Optional[str]`; `status()["last_detection"]["at_ts"]: Optional[int]`.

- [ ] **Step 1: Write the failing tests**

`server/tests/test_page_cache_fields.py`:

```python
"""What the cached webapp needs from the server (W-946): its page build, and
epoch times the page turns into "4 min ago" itself, so a copy served later
still says the right age."""
from __future__ import annotations

from datetime import datetime, timedelta

from tests._frames import add_kit

from featherframe import page_build
from featherframe.service import FeatherframeService


def _svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    return FeatherframeService()


def test_page_build_is_12_hex_and_follows_the_template(tmp_path, monkeypatch):
    b = page_build.build()
    assert len(b) == 12 and int(b, 16) >= 0
    tpl = tmp_path / "templates"
    tpl.mkdir()
    (tpl / "index.html").write_text("a")
    one = page_build.build(tpl, tmp_path / "nostatic")
    (tpl / "index.html").write_text("b")
    assert page_build.build(tpl, tmp_path / "nostatic") != one


def test_hosted_state_carries_the_page_build(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    assert svc.hosted_state()["page_build"] == page_build.build()


def test_a_frame_card_carries_its_last_check_in_as_epoch_seconds(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    then = datetime.now().replace(microsecond=0) - timedelta(minutes=7)
    add_kit(svc, reported={"last_checkin": then.isoformat(timespec="seconds")})
    card = svc.frames_list()[0]["card"]
    assert card["last_checkin_ts"] == int(then.timestamp())


def test_a_frame_never_heard_from_has_no_epoch(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    add_kit(svc)
    assert svc.frames_list()[0]["card"]["last_checkin_ts"] is None


def test_a_frame_view_carries_its_preview_etag(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    add_kit(svc)
    view = svc.frames_list()[0]
    assert "preview_etag" in view and "queued_until" in view
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd server && FEATHERFRAME_PLATES_DIR=/Users/wells/Projects/featherframe/server/plates ./.venv/bin/python -m pytest tests/test_page_cache_fields.py -q`
Expected: FAIL, `ImportError: cannot import name 'page_build'`.

- [ ] **Step 3: Write `page_build.py`**

```python
"""The webapp's own build (W-946): a hash of the page's template and static
files. Featherframe Cloud's front door keys its cached copies of the page by
it, so a new server image never serves a page drawn by an older one."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import paths

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def build(templates: Optional[Path] = None, static: Optional[Path] = None) -> str:
    """The first 12 hex of the sha256 of every file under templates/ and
    static/, by path and content. Computed once per process for the real ones."""
    if templates is None and static is None:
        return _real()
    return _hash(templates or _TEMPLATES, static or paths.static_dir())


@lru_cache(maxsize=1)
def _real() -> str:
    return _hash(_TEMPLATES, paths.static_dir())


def _hash(*roots: Path) -> str:
    h = hashlib.sha256()
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(x for x in root.rglob("*") if x.is_file()):
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]
```

(Check `paths.static_dir()` exists: it is used in app.py:225. If `_TEMPLATES` does not resolve to `server/templates` in the hosted image (`/app/templates`), use the same lookup `app.py` uses for its `Jinja2Templates` directory.)

- [ ] **Step 4: Add the fields in `service.py`**

In `hosted_state()`'s returned dict (service.py, the `return {"frames": out, ...` block), add:

```python
                # The webapp's own build (W-946): the front door's cache key.
                "page_build": page_build.build(),
```

with `from . import page_build` beside the module's other imports.

In `frame_card` (service.py:499), where `card["last_checkin_iso"] = then.isoformat(...)` is set, add:

```python
    card["last_checkin_ts"] = int(then.timestamp())
```

and add `"last_checkin_ts": None,` to the initial `card = {...}` dict.

In `frame_health`, in the branch where a frame on a push socket is "just now" (`card["last_seen"] = "just now"`), add:

```python
                card["last_checkin_ts"] = int(self._clock().timestamp())
```

In `status()`'s `"last_detection"` dict, beside `"when_text"`, add:

```python
                "at_ts": (int(datetime.fromisoformat(heard["ts"]).timestamp())
                          if heard.get("ts") else None),
```

In `frame_view`, just before its `return {`, add:

```python
        queued = self._queued_seconds(row, now) if kit and on else None
```

and in the returned dict replace `"queued_s": self._queued_seconds(row, now) if kit and on else None,` with:

```python
            "queued_s": queued,
            # When a held change paints, for a countdown that a copy served
            # later still gets right (W-946).
            "queued_until": int(now.timestamp()) + queued if queued else None,
            # What the page's preview of this frame shows: it reloads only when this moves.
            "preview_etag": ((self._output_etag(fid) if kit else self.pictures[self._kind_for(shows, now)].etag)
                             if on else None),
```

- [ ] **Step 5: Run the tests**

Run: `cd server && FEATHERFRAME_PLATES_DIR=/Users/wells/Projects/featherframe/server/plates ./.venv/bin/python -m pytest tests/test_page_cache_fields.py -q`
Expected: PASS (5).

Run: `cd .. && FEATHERFRAME_PLATES_DIR=/Users/wells/Projects/featherframe/server/plates make test`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/featherframe/page_build.py server/featherframe/service.py server/tests/test_page_cache_fields.py
git commit -m "The server reports its page build, and gives the page epoch times (W-946)"
```

---

### Task 6: The page says ages itself, applies the status on load, and reloads its preview only on a change

**Files:**
- Create: `server/templates/page-time.js`
- Create: `server/tests/fixtures/page-time-cases.json`
- Create: `server/tests/test_page_time.py`
- Create: `hosted/test/pagetime.test.ts`
- Modify: `server/templates/index.html`

Scope (decided while planning, against the spec's "every relative time"): ages ("4 min ago") and the countdown move to the browser, because they go wrong within 15 minutes. The quiet and outage durations are hours long and computed from active time only the server knows. The history captions and "since" times are clock times that change only at midnight. Those three stay server-rendered.

**Interfaces:**
- Consumes: Task 5's `card.last_checkin_ts`, `last_detection.at_ts`, `queued_until`, `preview_etag`.
- Produces: page global `ffAgo(ts, now)` (seconds → text, the same as `_ago`).

- [ ] **Step 1: Write the shared fixture and the failing tests**

`server/tests/fixtures/page-time-cases.json`:

```json
[
  {"secs": 0, "text": "just now"},
  {"secs": 59, "text": "just now"},
  {"secs": 60, "text": "1 min ago"},
  {"secs": 3599, "text": "59 min ago"},
  {"secs": 3600, "text": "1 hour ago"},
  {"secs": 7200, "text": "2 hours ago"},
  {"secs": 86399, "text": "23 hours ago"},
  {"secs": 86400, "text": "yesterday"},
  {"secs": 172800, "text": "2 days ago"},
  {"secs": -30, "text": "just now"}
]
```

`server/tests/test_page_time.py`:

```python
"""The page's "4 min ago" is the server's _ago, held to the same cases
(W-946): hosted/test/pagetime.test.ts runs the page's own function on them."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from featherframe.service import _ago

CASES = json.loads((Path(__file__).parent / "fixtures" / "page-time-cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[str(c["secs"]) for c in CASES])
def test_ago(case):
    now = datetime(2026, 10, 2, 12, 0, 0)
    assert _ago(now - timedelta(seconds=case["secs"]), now) == case["text"]
```

`hosted/test/pagetime.test.ts`:

```ts
// @ts-nocheck: node:fs has no types under the Worker's tsconfig.
// The page's ages (server/templates/page-time.js) are the server's _ago,
// held to the same cases (W-946).
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const SRC = readFileSync(new URL("../../server/templates/page-time.js", import.meta.url), "utf8");
const ffAgo = new Function(`${SRC}; return ffAgo;`)();
const CASES = JSON.parse(readFileSync(new URL("../../server/tests/fixtures/page-time-cases.json", import.meta.url), "utf8"));

describe("ffAgo", () => {
  for (const c of CASES) it(`${c.secs} s`, () => expect(ffAgo(1_790_000_000 - c.secs, 1_790_000_000)).toBe(c.text));
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd hosted && npx vitest run test/pagetime.test.ts`
Expected: FAIL, `ENOENT … page-time.js`.
Run: `cd server && ./.venv/bin/python -m pytest tests/test_page_time.py -q`
Expected: PASS for every case except `-30`, if `_ago` already clamps negatives; it does (`max(0.0, …)`), so all pass. This test pins `_ago`, which exists.

- [ ] **Step 3: Write `page-time.js`**

`server/templates/page-time.js`:

```js
// Ages on the page, as the server's _ago writes them (W-946): a copy of the
// page served later from Featherframe Cloud's cache still says the right
// age. Held to server/tests/fixtures/page-time-cases.json with _ago.
function ffAgo(ts, now) {
  var secs = Math.max(0, now - ts);
  if (secs < 60) return 'just now';
  var mins = Math.floor(secs / 60);
  if (mins < 60) return mins + ' min ago';
  var hours = Math.floor(mins / 60);
  if (hours < 24) return hours === 1 ? '1 hour ago' : hours + ' hours ago';
  var days = Math.floor(hours / 24);
  return days === 1 ? 'yesterday' : days + ' days ago';
}
```

Run: `cd hosted && npx vitest run test/pagetime.test.ts`
Expected: PASS (10).

- [ ] **Step 4: Change the page**

All edits in `server/templates/index.html`. Find each by the quoted text.

1. Inline the function as the first line inside the page's main script (the IIFE that begins `var $ = function (id)`), immediately before `var $ = function (id) {`:

```html
{% include "page-time.js" %}
```

(The file is included raw inside the existing `<script>`; it declares a function and nothing else, so it parses inside the IIFE.)

2. The set of frames the page drew: on the `<main class="grid">` element, add a `data-frames` attribute:

```html
  {% set _ids = [] %}{% for f in status.frames.list %}{% set _ = _ids.append(f.id ~ ':' ~ f.status) %}{% endfor %}
  <main class="grid" data-frames="{{ _ids | join(',') }}">
```

and replace `var knownFrames = null;` with:

```js
  // The frames this page drew, not the first status's (W-946): a status
  // newer than the page reloads it, once, onto the same refresh.
  var knownFrames = (document.querySelector('main.grid') || { getAttribute: function () { return null; } }).getAttribute('data-frames');
```

In `applyStatus`, replace

```js
    if (knownFrames === null) knownFrames = ids;
    else if (ids !== knownFrames && !dirty && !framesDirty()) { knownFrames = ids; location.reload(); return; }
```

with

```js
    if (knownFrames === null) knownFrames = ids;
    else if (ids !== knownFrames && !dirty && !framesDirty()) { knownFrames = ids; location.reload(); return; }
```

(unchanged lines: only the initial value moved), and in the frame row save handler replace `knownFrames = null;` with `knownFrames = (document.querySelector('main.grid') || { getAttribute: function () { return null; } }).getAttribute('data-frames');`. Without this, a save would re-seed it from the next status and miss a change.

3. A frame row's age: replace

```html
<span data-h="seen-text">{{ card.last_seen if card.seen else 'never' }}</span>
```

with

```html
<span data-h="seen-text" data-ts="{{ card.last_checkin_ts or '' }}">{{ card.last_seen if card.seen else 'never' }}</span>
```

and in `applyHealth` replace

```js
    subText(row, 'data-h', 'seen-text', card.seen ? (card.last_seen || '') : 'never');
```

with

```js
    var seenEl = sub(row, 'data-h', 'seen-text');
    if (seenEl) seenEl.setAttribute('data-ts', card.last_checkin_ts || '');
    subText(row, 'data-h', 'seen-text', card.last_checkin_ts ? ffAgo(card.last_checkin_ts, Date.now() / 1000)
                                                              : (card.seen ? (card.last_seen || '') : 'never'));
```

4. The last detection's age: in `applyStatus`, replace

```js
      setWhoWhen(heard, ld && ld.common, ld && ld.when_text, 'None yet');
```

with

```js
      setWhoWhen(heard, ld && ld.common, ld && (ld.at_ts ? ffAgo(ld.at_ts, Date.now() / 1000) : ld.when_text), 'None yet');
```

5. Ages tick on their own: after the `setInterval(function () { document.querySelectorAll('[data-h="queued"]')…` line, add:

```js
  // Ages move on between polls, and a copy served later says the right one.
  function tickAges() {
    var now = Date.now() / 1000;
    document.querySelectorAll('[data-h="seen-text"][data-ts]').forEach(function (el) {
      var ts = Number(el.getAttribute('data-ts'));
      if (ts) el.textContent = ffAgo(ts, now);
    });
  }
  tickAges();
  setInterval(tickAges, 30000);
```

6. The countdown from an absolute time: replace the `data-s="{{ fr.queued_s or '' }}"` attribute with `data-until="{{ fr.queued_until or '' }}"`. Replace `applyQueued` and its initial loop:

```js
  function applyQueued(row, until) {
    var el = sub(row, 'data-h', 'queued');
    if (!el) return;
    el.dataset.until = until ? String(until * 1000) : '';
    tickQueued(el);
  }
```

```js
  document.querySelectorAll('[data-h="queued"]').forEach(function (el) {
    var row = el.closest('.fr');
    if (row) applyQueued(row, Number(el.getAttribute('data-until')) || 0);
  });
```

and in `applyStatus` replace `applyQueued(row, fr.queued_s || 0);` with `applyQueued(row, fr.queued_until || 0);`. (The flash after a save still reads `mine.queued_s` from the live answer; leave it.)

7. The status on load: after `onReturn.push(pollStatus);`, add:

```js
  // On load, at once: a page from Featherframe Cloud's cache takes the newest
  // status the front door holds before anyone looks.
  pollStatus();
```

8. The preview reloads on a change, not a timer: delete these three lines:

```js
    setInterval(function () { if (!idle()) refreshPreview(); }, PREVIEW_MS);
    onReturn.push(refreshPreview);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) refreshPreview(); });
```

and the `var PREVIEW_MS = 15000;      // live preview image` line. In `applyStatus`, after the `frames.forEach(function (fr) {` loop's `if (!row) return;` line, add:

```js
      if (row.getAttribute('data-pe') !== null && row.getAttribute('data-pe') !== String(fr.preview_etag || '')
          && previewSrc && previewSrc.indexOf('/api/frames/' + encodeURIComponent(fr.id) + '/') === 0) refreshPreview();
      row.setAttribute('data-pe', String(fr.preview_etag || ''));
```

and on the frame row's `<details class="disc fr…" data-frame="{{ fr.id }}">` add `data-pe="{{ fr.preview_etag or '' }}"`.

With no frames, the preview is the picture itself (`/api/preview.png`): in `applyStatus`, beside `refreshHistory(cur.etag || '');`, add

```js
    if (previewSrc === '/api/preview.png' && lastCurEtag !== null && lastCurEtag !== (cur.etag || '')) refreshPreview();
    lastCurEtag = cur.etag || '';
```

with `var lastCurEtag = null;` declared beside `var knownFrames`.

- [ ] **Step 5: Check the page**

Run: `cd server && FEATHERFRAME_PLATES_DIR=/Users/wells/Projects/featherframe/server/plates ./.venv/bin/python -m pytest tests/test_page_scripts.py tests/test_page_time.py tests/test_status_page.py tests/test_frames_page.py -q`
Expected: all pass. Every inline script parses (the include sits inside the IIFE).

Add to `server/tests/test_page_scripts.py`:

```python
def test_the_page_says_ages_itself_and_applies_the_status_on_load(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    monkeypatch.setattr(app.state, "service", FeatherframeService(), raising=False)
    html = TestClient(app).get("/").text
    assert "function ffAgo(ts, now)" in html
    assert 'data-frames="' in html
    assert "pollStatus();\n" in html
    assert "PREVIEW_MS" not in html
```

Run it: PASS.

- [ ] **Step 6: Run everything**

Run: `cd hosted && npx vitest run && npx tsc --noEmit -p .` and `cd .. && FEATHERFRAME_PLATES_DIR=/Users/wells/Projects/featherframe/server/plates make test`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add server/templates/page-time.js server/templates/index.html server/tests/fixtures/page-time-cases.json server/tests/test_page_time.py server/tests/test_page_scripts.py hosted/test/pagetime.test.ts
git commit -m "The page says ages itself, applies the status on load, and reloads its preview only on a change (W-946)"
```

---

### Task 7: Live polls, warming on an edit, and saves that say they are saving

**Files:**
- Modify: `server/templates/index.html`
- Test: `server/tests/test_page_scripts.py`

**Interfaces:**
- Consumes: the front door's `POST /api/warm` (Task 4); the template variable `hosted`.

- [ ] **Step 1: Write the failing test**

Add to `server/tests/test_page_scripts.py`:

```python
@pytest.mark.parametrize("hosted", [False, True])
def test_live_polls_and_warming(tmp_path, monkeypatch, hosted):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    monkeypatch.setattr(app.state, "service", FeatherframeService(), raising=False)
    monkeypatch.setattr(app.state, "hosted", object() if hosted else None, raising=False)
    html = TestClient(app).get("/").text
    assert "jsonFetch('/api/tasks?live=1')" in html
    assert "jsonFetch('/api/generated?live=1')" in html
    assert "keepalive: true" in html
    assert ("fetch('/api/warm'" in html) is hosted
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd server && FEATHERFRAME_PLATES_DIR=/Users/wells/Projects/featherframe/server/plates ./.venv/bin/python -m pytest tests/test_page_scripts.py -k live_polls -q`
Expected: FAIL on the first assertion.

- [ ] **Step 3: Change the page**

1. **Live polls.**
   - In the task poller, replace `function poll() { if (document.hidden) return; jsonFetch('/api/tasks').then(apply).catch(function () {}); }` with the same line asking `jsonFetch('/api/tasks?live=1')`.
   - In the regenerate poller, replace `jsonFetch('/api/generated')` with `jsonFetch('/api/generated?live=1')`.
   - In `applyStatus`, the firmware quick poll calls `pollStatus()`. Make `pollStatus` take a flag: change `function pollStatus() {` to `function pollStatus(live) {` and its `jsonFetch('/api/status')` to `jsonFetch(live ? '/api/status?live=1' : '/api/status')`.
   - Change the quick poll's call to `quickPoll = setTimeout(function () { quickPoll = null; pollStatus(true); }, 2000);`.
   - Leave the on-load `/api/tasks` ask (the one inside `if (Object.keys(forms).length)`) as it is: a cached read.

2. **Switches send with `keepalive`.** In the switch handler, change `fetch('/settings', { method: 'POST', body: body, credentials: 'same-origin' })` to:

```js
      fetch('/settings', { method: 'POST', body: body, credentials: 'same-origin', keepalive: true })
```

   and, before that `fetch`, show the tick saving:

```js
      if (tick) { tick.textContent = 'Saving…'; tick.classList.remove('bad'); tick.classList.add('on'); clearTimeout(tick._h); }
```

3. **Save buttons say they are saving.** In the section form's submit path (`submitForm`, and the `submit` listener that sets `saving = true`), set the clicked Save button's text to "Saving…" and disable it before the navigation:

```js
  document.querySelectorAll('form.set-form').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      var b = e.submitter || f.querySelector('button[type="submit"]');
      if (b && !e.defaultPrevented) { b.textContent = 'Saving…'; b.disabled = true; }
    });
  });
```

   (Add it beside the existing `beforeunload` listener. The frame row's Save already shows "Saving…" through `busy(row, true)`; check that it does, and if it does not, set `save.textContent = 'Saving…'` where `busy(row, true)` is called.)

4. **Warm on an edit, on Featherframe Cloud only.** At the end of the main script, add:

```js
  {% if hosted %}
  // The owner started an edit (W-946): Featherframe Cloud starts the
  // household's server now, so the save meets it up. Again at most every 20 s.
  (function () {
    var last = 0;
    function warm() {
      if (Date.now() - last < 20000) return;
      last = Date.now();
      fetch('/api/warm', { method: 'POST', credentials: 'same-origin', keepalive: true }).catch(function () {});
    }
    ['input', 'change', 'focusin'].forEach(function (t) {
      document.addEventListener(t, function (e) {
        var el = e.target;
        if (el && el.closest && el.closest('form.set-form, .fr-body') && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) warm();
      }, true);
    });
    document.querySelectorAll('dialog').forEach(function (d) {
      new MutationObserver(function () { if (d.open) warm(); }).observe(d, { attributes: true, attributeFilter: ['open'] });
    });
  })();
  {% endif %}
```

   (Check the selectors against the page: the settings sections are `form.set-form`; a frame row's settings are inside `.fr-body`. Adjust to the real class names if they differ, and ledger the change.)

- [ ] **Step 4: Run the tests**

Run: `cd server && FEATHERFRAME_PLATES_DIR=/Users/wells/Projects/featherframe/server/plates ./.venv/bin/python -m pytest tests/test_page_scripts.py -q`
Expected: PASS (every script parses; the new test passes for both modes).

Run: `cd .. && FEATHERFRAME_PLATES_DIR=/Users/wells/Projects/featherframe/server/plates make test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add server/templates/index.html server/tests/test_page_scripts.py
git commit -m "Live polls ask past the cache, an edit warms the server, and saves say they are saving (W-946)"
```

---

### Task 8: AGENTS.md, ship, and measure

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Say it in AGENTS.md**

In the Hosted section, after the sentence ending "wakes the server only for news (…)", add a paragraph:

```
The webapp's reads come from a cache at the front door (W-946,
`hosted/src/pagecache.ts`): the server's last answer to `/`, `/api/status`,
`/api/history`, `/api/tasks`, the battery, the previews, the history and
generated images, kept in R2 and keyed by the server's `page_build` (and,
for `/`, the account). Fresh for 15 min; a stale copy is still served while
a `look` asks the alarm for a wake (`wakes.ts`, never before `MIN_GAP_MS`,
in quiet hours only when something is queued). The server's reports
(`takeState`) refresh the copies read in the last 2 h when the page would
change or after a POST; a POST clears the changing ones; `/` with nothing
kept and the server asleep is the bundled loading page. Reads that must be
live ask `live=1`. An edit warms the server (`POST /api/warm`).
```

- [ ] **Step 2: Run everything**

Run: `cd hosted && npx vitest run && npx tsc --noEmit -p .` and `cd .. && FEATHERFRAME_PLATES_DIR=/Users/wells/Projects/featherframe/server/plates make test`
Expected: all pass.

- [ ] **Step 3: Measure before**

Using W-915's diagnostics (memory: `hosted-cost-diagnostics`), record the `wells` household's server starts and container cost for the last 24 hours. Time `/` to first byte with the server asleep:

```bash
curl -s -o /dev/null -w "%{time_starttransfer}\n" -H "Cookie: ff_session=<a test household's session>" https://cloud.featherframe.app/
```

Make the test household with the admin API's `link` (keychain `featherframe-hosted-admin-token`), never Wells's own login.

- [ ] **Step 4: PR, merge, deploy**

Branch `wells/w-946-featherframe-cloud-the-webapp-opens-at-once-from-a-cache`; PR titled "Featherframe Cloud: the webapp opens at once from a cache, without starting the server (W-946)", body with what changed, how it was verified, and what is left, ending `Refs W-946`. Merge when CI is green. From `origin/main`:

```bash
cd hosted
npx wrangler deploy
```

- [ ] **Step 5: Measure after, and check the loading page**

- Right after the deploy, the test household's `/` shows the loading page, then the webapp.
- Measure the time to first byte for `/` again, with the server asleep and a fresh copy kept. Target: under 300 ms.
- Leave a tab open on the test household for an hour; it should cause no starts.
- Record before and after in a Linear comment on `W-946`.
- Ask Wells to open the webapp on his phone after a while away, and to save a setting.
