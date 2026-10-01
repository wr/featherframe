# AI spend guards, part 2: the front door holds the count (W-938) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On Featherframe Cloud, each household's front door (its Durable Object) keeps the spend records and the pause, runs the same rule atomically on every reservation, refuses past a platform backstop of its own, shows each household's AI spend on the admin page, and emails the admin when a household pauses, passes $3 in a day, or hits the backstop.

**Architecture:** A new `hosted/src/spend.ts` ports `spend.decide()` and adds a `SpendBook` class over the DO's SQLite; `Household.internal` routes `spend/*` to it. The Python server gets `spend.FrontDoorStore`, which speaks those routes, and uses it whenever hosted mode is active. Both ports of `decide()` are held to one file of cases.

**Tech Stack:** TypeScript on Cloudflare Workers + Durable Objects (SQLite storage), vitest on node (with `node:sqlite` for storage), Python 3.9+ with `requests`.

**Spec:** `docs/superpowers/specs/2026-10-01-ai-spend-guards-design.md`

**Depends on:** part 1 (`docs/superpowers/plans/2026-10-01-ai-spend-guards-1-gate.md`) merged: `spend.py`, `spend.decide`, `spend.Record`/`Rule`/`Snapshot`, the store interface (`reserve`, `settle`, `snapshot`, `resume`) and `server/tests/fixtures/spend-cases.json`.

## Global Constraints

- The rules stay in Python (AGENTS.md: no TS copy of any rule). The front door's `decide()` is a port held to `server/tests/fixtures/spend-cases.json`; its only rule of its own is the backstop.
- `BACKSTOP_USD_PER_DAY = 10` (per household, per UTC day, counting open records at their estimate). `ALERT_USD_PER_DAY = 3`. At most one email per household per reason per UTC day.
- A front door that cannot be reached refuses: the Python side raises, and the gate turns it into `Refused("unreachable")`.
- Email copy, exactly (the admin reads it; docs/STYLE.md applies):
  - Subjects: `Featherframe Cloud: {hid} AI paused`, `Featherframe Cloud: {hid} AI passed $3 today`, `Featherframe Cloud: {hid} AI hit the $10 backstop`
  - First line of the body, by reason:
    - paused: `{hid} bought more AI images in an hour than the pause allows. AI generation is paused until its owner resumes it.`
    - day: `{hid} has spent more than $3 on AI today (UTC).`
    - backstop: `{hid} asked for more than $10 of AI in one UTC day and the front door refused. The server's own checks did not stop it.`
  - Then: `Last hour: {n} images. Today (UTC): ${today:.2f}. This month: ${month:.2f} of ${limit:.2f}.` and a line with `https://{APP_HOST}/admin`.
- Admin table column header: `AI this month`; cell: `$1.20 of $10.00`, `Paused · $1.20 of $10.00`, or `—` when the household has no records this month.
- Worker tests: `cd hosted && npm test`. Python tests: `cd server && ./.venv/bin/python -m pytest tests/<file> -q`.
- Commit titles follow docs/STYLE.md, with `(W-938)` and `Refs: W-938`.

## File structure

- Create `hosted/src/spend.ts` — types, `decide()`, `SpendBook`, `alertMail()`.
- Create `hosted/test/sql.ts` — a `node:sqlite` stand-in for the DO's `ctx.storage.sql`.
- Create `hosted/test/spend.test.ts`.
- Modify `hosted/src/household.ts` — a `SpendBook`, the `spend/*` routes, `summary().ai`, alert emails.
- Modify `hosted/src/admin.ts` and `hosted/src/pages.ts` — the column.
- Modify `server/featherframe/spend.py` — `FrontDoorStore`.
- Modify `server/featherframe/service.py` — use it when hosted.
- Create `server/tests/test_spend_front_door.py`.
- Modify `AGENTS.md`.

---

### Task 1: The rule, ported, and the book it runs on

**Files:**
- Create: `hosted/src/spend.ts`
- Create: `hosted/test/sql.ts`
- Test: `hosted/test/spend.test.ts`

**Interfaces:**
- Produces:
  - `type SpendRow = { id: string; at: number; month: string; day: string; kind: string; subject: string; auto: boolean; model: string; quality: string | null; est_usd: number; cost_usd: number | null; state: "open" | "settled" | "released" }`
  - `type Rule = { limit_usd: number; runaway_per_hour: number | null; window_s: number | null }`
  - `decide(rows: SpendRow[], paused: boolean, resumedAt: number, rec: SpendRow, rule: Rule, now: number): string | null`
  - `class SpendBook(sql: SqlLike)` with `reserve(rec, rule): { ok: boolean; reason: string | null; alerts: Alert[] }`, `settle(id, state, cost_usd)`, `snapshot(since): { rows, pause, resumed_at }`, `resume(now)`, `importRows(rows): number`, `monthSummary(month): { usd: number; limit: number | null; paused: boolean; count: number }`
  - `type Alert = { reason: "paused" | "day" | "backstop"; lastHour: number; today: number; month: number; limit: number }`
  - `alertMail(hid: string, host: string, a: Alert): { subject: string; text: string; html: string }`
  - `BACKSTOP_USD_PER_DAY = 10`, `ALERT_USD_PER_DAY = 3`

- [ ] **Step 1: The storage stand-in for tests**

Create `hosted/test/sql.ts`:

```ts
// @ts-nocheck: node:sqlite has no types under the Worker's tsconfig.
// The Durable Object's ctx.storage.sql, on node's own SQLite, for tests.
import { DatabaseSync } from "node:sqlite";

export function nodeSql(db = new DatabaseSync(":memory:")) {
  const empty = (columnNames: string[] = []) => ({ toArray: () => [], one: () => undefined, columnNames });
  return {
    db,
    exec(q: string, ...a: unknown[]) {
      const probe = /^SELECT \* FROM (\w+) LIMIT 0$/.exec(q.trim());
      if (probe) return empty(db.prepare(`PRAGMA table_info(${probe[1]})`).all().map((c) => c.name));
      if (!a.length && q.split(";").filter((s) => s.trim()).length > 1) { db.exec(q); return empty(); }
      const st = db.prepare(q);
      const rows = /^\s*(SELECT|PRAGMA|WITH)/i.test(q) ? st.all(...a) : (st.run(...a), []);
      return { toArray: () => rows, one: () => rows[0], columnNames: rows[0] ? Object.keys(rows[0]) : [] };
    },
  };
}
```

- [ ] **Step 2: Write the failing tests**

Create `hosted/test/spend.test.ts`:

```ts
// @ts-nocheck: node:fs has no types under the Worker's tsconfig.
// The front door's side of the spend guards (W-938): the same rule as the
// server's, held to the same cases, plus the backstop and the alerts.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { alertMail, BACKSTOP_USD_PER_DAY, decide, SpendBook } from "../src/spend";
import { nodeSql } from "./sql";

const CASES = JSON.parse(readFileSync(new URL("../../server/tests/fixtures/spend-cases.json", import.meta.url), "utf8"));

describe("decide", () => {
  for (const c of CASES) {
    it(c.name, () => {
      expect(decide(c.rows, c.paused, c.resumed_at, c.rec, c.rule, c.now)).toBe(c.expect);
    });
  }
});

const T = 1790000000;                       // 2026-09-21 (UTC)
let n = 0;
function rec(over: Record<string, unknown> = {}) {
  n++;
  return { id: `r${n}`, at: T, month: "2026-09", day: "2026-09-21", kind: "plate", subject: `s${n}`,
           auto: true, model: "gpt-image-2.5-sunburst", quality: "max", est_usd: 0.194,
           cost_usd: null, state: "open", ...over };
}
const RULE = { limit_usd: 1000, runaway_per_hour: 6, window_s: null };

describe("SpendBook", () => {
  it("inserts what the rule allows and settles it", () => {
    const book = new SpendBook(nodeSql());
    const r = rec();
    expect(book.reserve(r, RULE).ok).toBe(true);
    book.settle(r.id, "settled", 0.2);
    const snap = book.snapshot(0);
    expect(snap.rows.map((x) => [x.state, x.cost_usd, x.auto])).toEqual([["settled", 0.2, true]]);
  });

  it("trips the pause at the seventh image in an hour and keeps it until resumed", () => {
    const book = new SpendBook(nodeSql());
    const out = [];
    for (let i = 0; i < 8; i++) out.push(book.reserve(rec({ at: T + i * 300 }), RULE));
    expect(out.map((o) => o.reason)).toEqual([null, null, null, null, null, null, "runaway", "paused"]);
    expect(out[6].alerts.map((a) => a.reason)).toEqual(["paused"]);
    expect(book.snapshot(0).pause).toEqual({ at: T + 1800, count: 6 });
    book.resume(T + 2400);
    expect(book.reserve(rec({ at: T + 2500 }), RULE).ok).toBe(true);
  });

  it("refuses past the backstop whatever the request's limit", () => {
    const book = new SpendBook(nodeSql());
    const big = { limit_usd: 1e6, runaway_per_hour: null, window_s: null };
    let spent = 0;
    for (let i = 0; spent + 0.194 <= BACKSTOP_USD_PER_DAY; i++) {
      expect(book.reserve(rec({ at: T + i, auto: false }), big).ok).toBe(true);
      spent += 0.194;
    }
    const last = book.reserve(rec({ at: T + 999, auto: false }), big);
    expect(last.reason).toBe("backstop");
    expect(last.alerts.map((a) => a.reason)).toContain("backstop");
  });

  it("says once a day that a household passed $3", () => {
    const book = new SpendBook(nodeSql());
    const free = { limit_usd: 1e6, runaway_per_hour: null, window_s: null };
    const alerts = [];
    for (let i = 0; i < 20; i++) alerts.push(...book.reserve(rec({ at: T + i, auto: false }), free).alerts);
    expect(alerts.filter((a) => a.reason === "day")).toHaveLength(1);
  });

  it("imports the server's own records once", () => {
    const book = new SpendBook(nodeSql());
    const rows = [rec({ state: "settled", cost_usd: 0.2 }), rec({ state: "settled", cost_usd: 0.1 })];
    expect(book.importRows(rows)).toBe(2);
    expect(book.importRows(rows)).toBe(0);
    expect(book.monthSummary("2026-09").usd).toBeCloseTo(0.3);
  });

  it("sums the month for the admin page", () => {
    const book = new SpendBook(nodeSql());
    book.reserve(rec({ est_usd: 0.5 }), { ...RULE, limit_usd: 10 });
    const s = book.monthSummary("2026-09");
    expect(s).toEqual({ usd: 0.5, limit: 10, paused: false, count: 1 });
  });
});

describe("alertMail", () => {
  it("names the household and the numbers", () => {
    const m = alertMail("h1", "cloud.featherframe.app",
      { reason: "paused", lastHour: 6, today: 1.2, month: 4.5, limit: 10 });
    expect(m.subject).toBe("Featherframe Cloud: h1 AI paused");
    expect(m.text).toContain("h1 bought more AI images in an hour than the pause allows.");
    expect(m.text).toContain("Last hour: 6 images. Today (UTC): $1.20. This month: $4.50 of $10.00.");
    expect(m.text).toContain("https://cloud.featherframe.app/admin");
  });
});
```

- [ ] **Step 3: Run them to see them fail**

Run: `cd hosted && npm test -- spend`
Expected: FAIL, `Cannot find module '../src/spend'`.

- [ ] **Step 4: Write `spend.ts`**

Create `hosted/src/spend.ts`:

```ts
// The front door's side of the AI spend guards (W-938). The household's
// server reserves every paid call here before making it, so the record
// outlives a Container that is stopped mid-call. The rule is the server's
// (server/featherframe/spend.py `decide`), ported and held to the same cases;
// the one rule of the front door's own is the backstop.

export type SpendRow = {
  id: string; at: number; month: string; day: string; kind: string; subject: string;
  auto: boolean; model: string; quality: string | null; est_usd: number;
  cost_usd: number | null; state: "open" | "settled" | "released";
};
export type Rule = { limit_usd: number; runaway_per_hour: number | null; window_s: number | null };
export type Alert = { reason: "paused" | "day" | "backstop"; lastHour: number; today: number;
                      month: number; limit: number };
export interface SqlLike {
  exec<T = Record<string, unknown>>(q: string, ...a: unknown[]): { toArray(): T[]; one(): T };
}

export const OPEN_HOLD_S = 86400;
export const RUNAWAY_KINDS = ["plate", "collage"];
/** At most this much a UTC day per household, whatever the server asks. */
export const BACKSTOP_USD_PER_DAY = 10;
/** The admin hears of a household past this in a UTC day. */
export const ALERT_USD_PER_DAY = 3;
const LOOKBACK_S = 36 * 3600;

const spent = (r: SpendRow) =>
  r.state === "released" ? 0 : r.state === "settled" && r.cost_usd !== null ? r.cost_usd : r.est_usd;
const utcDay = (at: number) => new Date(at * 1000).toISOString().slice(0, 10);

/** The server's `decide`: null, or why not. */
export function decide(rows: SpendRow[], paused: boolean, resumedAt: number, rec: SpendRow,
                       rule: Rule, now: number): string | null {
  if (paused) return "paused";
  const month = rows.filter((r) => r.month === rec.month).reduce((a, r) => a + spent(r), 0);
  if (rec.est_usd > 0 && month + rec.est_usd > rule.limit_usd + 1e-9) return "limit";
  if (!rec.auto) return null;
  const same = rows.filter((r) => r.kind === rec.kind && r.subject === rec.subject && r.state !== "released");
  if (same.some((r) => r.state === "open" && now - r.at < OPEN_HOLD_S)) return "subject";
  if (rule.window_s && same.some((r) => now - r.at < rule.window_s!)) return "subject";
  if (rule.runaway_per_hour && RUNAWAY_KINDS.includes(rec.kind)) {
    const since = Math.max(now - 3600, resumedAt);
    const recent = rows.filter((r) => r.auto && RUNAWAY_KINDS.includes(r.kind)
      && r.state !== "released" && r.at > since);
    if (recent.length >= rule.runaway_per_hour) return "runaway";
  }
  return null;
}

type Raw = Omit<SpendRow, "auto"> & { auto: number };

export class SpendBook {
  constructor(private sql: SqlLike) {
    sql.exec(`
      CREATE TABLE IF NOT EXISTS spend (id TEXT PRIMARY KEY, at REAL NOT NULL, month TEXT NOT NULL,
        day TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL, auto INTEGER NOT NULL,
        model TEXT, quality TEXT, est_usd REAL NOT NULL, cost_usd REAL, state TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS spend_at ON spend(at);
      CREATE TABLE IF NOT EXISTS spend_meta (k TEXT PRIMARY KEY, v TEXT);
    `);
  }

  private get(k: string): string | null {
    const r = this.sql.exec<{ v: string }>("SELECT v FROM spend_meta WHERE k = ?", k).toArray();
    return r.length ? r[0].v : null;
  }
  private set(k: string, v: string | null): void {
    if (v === null) this.sql.exec("DELETE FROM spend_meta WHERE k = ?", k);
    else this.sql.exec("INSERT OR REPLACE INTO spend_meta (k, v) VALUES (?, ?)", k, v);
  }
  private rows(since: number, month = ""): SpendRow[] {
    return this.sql.exec<Raw>("SELECT * FROM spend WHERE at >= ? OR month = ? ORDER BY at", since, month)
      .toArray().map((r) => ({ ...r, auto: !!r.auto }));
  }
  private insert(r: SpendRow): void {
    this.sql.exec(`INSERT OR IGNORE INTO spend (id, at, month, day, kind, subject, auto, model, quality,
      est_usd, cost_usd, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      r.id, r.at, r.month, r.day, r.kind, r.subject, r.auto ? 1 : 0, r.model, r.quality,
      r.est_usd, r.cost_usd, r.state);
  }

  /** Check and insert as one step: a Durable Object runs one request at a
   * time, and nothing here awaits. */
  reserve(rec: SpendRow, rule: Rule): { ok: boolean; reason: string | null; alerts: Alert[] } {
    this.set("limit", String(rule.limit_usd));
    const rows = this.rows(rec.at - LOOKBACK_S, rec.month);
    const today = utcDay(rec.at);
    const dayTotal = rows.filter((r) => utcDay(r.at) === today).reduce((a, r) => a + spent(r), 0);
    const alerts: Alert[] = [];
    let reason: string | null = null;
    if (rec.est_usd > 0 && dayTotal + rec.est_usd > BACKSTOP_USD_PER_DAY + 1e-9) {
      reason = "backstop";
    } else {
      reason = decide(rows, this.get("pause") !== null, Number(this.get("resumed_at") || 0), rec, rule, rec.at);
      if (reason === "runaway") this.set("pause", JSON.stringify({ at: rec.at, count: rule.runaway_per_hour }));
    }
    if (reason === null) this.insert({ ...rec, state: "open", cost_usd: null });
    const after = dayTotal + (reason === null ? rec.est_usd : 0);
    const want: Alert["reason"][] = [];
    if (reason === "runaway") want.push("paused");
    if (reason === "backstop") want.push("backstop");
    if (reason === null && after > ALERT_USD_PER_DAY) want.push("day");
    for (const why of want) {
      if (this.get(`alerted:${why}`) === today) continue;
      this.set(`alerted:${why}`, today);
      const month = rows.filter((r) => r.month === rec.month).reduce((a, r) => a + spent(r), 0)
        + (reason === null ? rec.est_usd : 0);
      const lastHour = rows.filter((r) => r.auto && RUNAWAY_KINDS.includes(r.kind)
        && r.state !== "released" && r.at > rec.at - 3600).length;
      alerts.push({ reason: why, lastHour, today: after, month, limit: rule.limit_usd });
    }
    return { ok: reason === null, reason, alerts };
  }

  settle(id: string, state: "settled" | "released", cost_usd: number | null): void {
    this.sql.exec("UPDATE spend SET state = ?, cost_usd = ? WHERE id = ?", state, cost_usd, id);
  }

  snapshot(since: number): { rows: SpendRow[]; pause: { at: number; count: number } | null; resumed_at: number } {
    const p = this.get("pause");
    return { rows: this.rows(since), pause: p ? JSON.parse(p) : null, resumed_at: Number(this.get("resumed_at") || 0) };
  }

  resume(now: number): void {
    this.set("pause", null);
    this.set("resumed_at", String(now));
  }

  /** The server's own records from before the front door kept them. */
  importRows(rows: SpendRow[]): number {
    let added = 0;
    for (const r of rows) {
      const had = this.sql.exec("SELECT 1 FROM spend WHERE id = ?", r.id).toArray().length;
      if (!had) { this.insert(r); added++; }
    }
    return added;
  }

  monthSummary(month: string): { usd: number; limit: number | null; paused: boolean; count: number } {
    const rows = this.sql.exec<Raw>("SELECT * FROM spend WHERE month = ?", month).toArray()
      .map((r) => ({ ...r, auto: !!r.auto }));
    const limit = this.get("limit");
    return { usd: Math.round(rows.reduce((a, r) => a + spent(r), 0) * 1e4) / 1e4,
             limit: limit === null ? null : Number(limit), paused: this.get("pause") !== null,
             count: rows.filter((r) => r.state !== "released").length };
  }
}

const FIRST: Record<Alert["reason"], (hid: string) => string> = {
  paused: (hid) => `${hid} bought more AI images in an hour than the pause allows. AI generation is paused until its owner resumes it.`,
  day: (hid) => `${hid} has spent more than $${ALERT_USD_PER_DAY} on AI today (UTC).`,
  backstop: (hid) => `${hid} asked for more than $${BACKSTOP_USD_PER_DAY} of AI in one UTC day and the front door refused. The server's own checks did not stop it.`,
};
const SUBJECT: Record<Alert["reason"], string> = {
  paused: "AI paused", day: `AI passed $${ALERT_USD_PER_DAY} today`, backstop: `AI hit the $${BACKSTOP_USD_PER_DAY} backstop`,
};

export function alertMail(hid: string, host: string, a: Alert): { subject: string; text: string; html: string } {
  const numbers = `Last hour: ${a.lastHour} images. Today (UTC): $${a.today.toFixed(2)}. ` +
    `This month: $${a.month.toFixed(2)} of $${a.limit.toFixed(2)}.`;
  const link = `https://${host}/admin`;
  const text = `${FIRST[a.reason](hid)}\n\n${numbers}\n\n${link}\n`;
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const html = `<p>${esc(FIRST[a.reason](hid))}</p><p>${esc(numbers)}</p><p><a href="${link}">${link}</a></p>`;
  return { subject: `Featherframe Cloud: ${hid} ${SUBJECT[a.reason]}`, text, html };
}
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `cd hosted && npm test -- spend`
Expected: PASS. If a `decide` case fails, the port differs from `spend.py`: fix the port, never the case.

- [ ] **Step 6: Commit**

```bash
git add hosted/src/spend.ts hosted/test/spend.test.ts hosted/test/sql.ts
git commit -m "The front door can keep a household's AI spend, with a backstop of its own (W-938)" -m "Refs: W-938"
```

---

### Task 2: The front door's routes, its summary and the alert emails

**Files:**
- Modify: `hosted/src/household.ts`
- Test: `hosted/test/spend.test.ts` (add)

**Interfaces:**
- Consumes: `SpendBook`, `alertMail` (Task 1); `sendMail(env, to, mail)` from `./accounts`.
- Produces, under `/_internal/<hid>/` (Bearer: the household's key, as every internal route):
  - `POST spend/reserve` body `{record: SpendRow, rule: Rule}` → `{ok: true}` or `{ok: false, reason}`
  - `POST spend/settle` body `{id, state, cost_usd}` → `{ok: true}`
  - `GET spend/snapshot?since=<epoch>` → `{rows, pause, resumed_at}`
  - `POST spend/resume` body `{now}` → `{ok: true}`
  - `POST spend/import` body `{rows}` → `{added}`
  - `Household.summary()` gains `ai: { usd, limit, paused, count }` for the current UTC month.

- [ ] **Step 1: Write the failing test**

Append to `hosted/test/spend.test.ts`:

```ts
import { vi } from "vitest";

vi.mock("cloudflare:workers", () => ({
  DurableObject: class { ctx: unknown; env: unknown; constructor(ctx: unknown, env: unknown) { this.ctx = ctx; this.env = env; } },
}));
vi.mock("../src/viewers", () => ({ display: vi.fn(), lobbyPng: vi.fn(), shortOf: vi.fn(), trmnlHeaders: vi.fn() }));

describe("the front door's spend routes", () => {
  async function door() {
    const { Household } = await import("../src/household");
    const sql = nodeSql();
    const mail: string[] = [];
    const h = new Household({ storage: { sql }, getWebSockets: () => [] } as never,
      { ADMIN_EMAILS: "a@x.test", APP_HOST: "cloud.featherframe.app", RESEND_API_KEY: "" } as never);
    h.setMeta("hid", "h1");
    h.setMeta("key", "k");
    (h as never as { mailer: (to: string, m: { subject: string }) => Promise<boolean> }).mailer =
      async (to, m) => { mail.push(`${to}: ${m.subject}`); return true; };
    return { h, mail };
  }
  const call = (h, path, body?) => h.internal(new Request(`https://x/_internal/h1/${path}`,
    body === undefined ? {} : { method: "POST", body: JSON.stringify(body) }), path);

  it("reserves, settles, and tells the admin when a household pauses", async () => {
    const { h, mail } = await door();
    for (let i = 0; i < 6; i++) {
      const r = await call(h, "spend/reserve", { record: rec({ at: T + i * 300 }), rule: RULE });
      expect(await r.json()).toEqual({ ok: true });
    }
    const seventh = await call(h, "spend/reserve", { record: rec({ at: T + 1800 }), rule: RULE });
    expect(await seventh.json()).toEqual({ ok: false, reason: "runaway" });
    expect(mail).toEqual(["a@x.test: Featherframe Cloud: h1 AI paused"]);
    const snap = await (await h.internal(new Request("https://x/_internal/h1/spend/snapshot?since=0"),
      "spend/snapshot")).json();
    expect(snap.pause).toEqual({ at: T + 1800, count: 6 });
    await call(h, "spend/resume", { now: T + 2000 });
    expect(h.summary().ai.paused).toBe(false);
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd hosted && npm test -- spend`
Expected: the new test FAILS (`spend/reserve` answers 404).

- [ ] **Step 3: Wire the book into `Household`**

In `hosted/src/household.ts`, add imports:

```ts
import { sendMail } from "./accounts";
import { alertMail, type Alert, type Rule, SpendBook, type SpendRow } from "./spend";
```

In the class, add fields and set them at the end of the constructor:

```ts
  spend: SpendBook;
  /** Sends an alert; a field so a test can stand in for Resend. */
  mailer = (to: string, mail: { subject: string; text: string; html: string }) => sendMail(this.env, to, mail);
```

```ts
    // The household's AI spend (W-938): the server reserves each paid call here first.
    this.spend = new SpendBook(this.sql);
```

In `internal()`, before the final `return new Response("not found", …)`, add:

```ts
    if (path.startsWith("spend/")) return this.spendRoute(request, path.slice("spend/".length));
```

Add the method:

```ts
  // -- AI spend (W-938) ----------------------------------------------------------
  async spendRoute(request: Request, op: string): Promise<Response> {
    if (op === "reserve" && request.method === "POST") {
      const { record, rule } = await request.json<{ record: SpendRow; rule: Rule }>();
      const out = this.spend.reserve(record, rule);
      for (const a of out.alerts) await this.alert(a);
      return Response.json(out.ok ? { ok: true } : { ok: false, reason: out.reason });
    }
    if (op === "settle" && request.method === "POST") {
      const { id, state, cost_usd } = await request.json<{ id: string; state: "settled" | "released"; cost_usd: number | null }>();
      this.spend.settle(id, state, cost_usd);
      return Response.json({ ok: true });
    }
    if (op.startsWith("snapshot") && request.method === "GET") {
      const since = Number(new URL(request.url).searchParams.get("since") || 0);
      return Response.json(this.spend.snapshot(since));
    }
    if (op === "resume" && request.method === "POST") {
      const { now } = await request.json<{ now: number }>();
      this.spend.resume(now);
      return Response.json({ ok: true });
    }
    if (op === "import" && request.method === "POST") {
      const { rows } = await request.json<{ rows: SpendRow[] }>();
      return Response.json({ added: this.spend.importRows(rows) });
    }
    return new Response("not found", { status: 404 });
  }

  /** One email per admin; SpendBook already keeps it to once a reason a day. */
  async alert(a: Alert): Promise<void> {
    const hid = this.meta("hid") || "?";
    const mail = alertMail(hid, this.env.APP_HOST, a);
    for (const to of (this.env.ADMIN_EMAILS || "").split(",").map((e) => e.trim()).filter(Boolean)) {
      try { await this.mailer(to, mail); } catch (e) { console.error("spend alert", e); }
    }
  }
```

In `summary()`, add `ai` to the returned object and its type:

```ts
             ai: this.spend.monthSummary(new Date().toISOString().slice(0, 7)),
```

and add `ai: { usd: number; limit: number | null; paused: boolean; count: number }` to the return type. In `admin.ts`'s `gather()`, add `ai: { usd: 0, limit: null, paused: false, count: 0 }` to the fallback object in its `catch`.

- [ ] **Step 4: Run the Worker tests and the type check**

Run: `cd hosted && npm test && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add hosted/src/household.ts hosted/src/admin.ts hosted/test/spend.test.ts
git commit -m "The front door answers the server's spend reservations and emails the admin when one pauses (W-938)" -m "Refs: W-938"
```

---

### Task 3: The admin page shows each household's AI spend

**Files:**
- Modify: `hosted/src/pages.ts`
- Test: `hosted/test/spend.test.ts` (add)

**Interfaces:**
- Consumes: `summary().ai` (Task 2), carried into `AdminData.households[n].ai` by `gather()`.
- Produces: `aiCell(ai: { usd: number; limit: number | null; paused: boolean; count: number }): string`, exported from `pages.ts`.

- [ ] **Step 1: Write the failing test**

Append to `hosted/test/spend.test.ts`:

```ts
import { aiCell } from "../src/pages";

describe("the admin's AI column", () => {
  it("reads as the webapp's summary does", () => {
    expect(aiCell({ usd: 1.2, limit: 10, paused: false, count: 3 })).toBe("$1.20 of $10.00");
    expect(aiCell({ usd: 1.2, limit: 10, paused: true, count: 3 })).toBe("Paused · $1.20 of $10.00");
    expect(aiCell({ usd: 0, limit: null, paused: false, count: 0 })).toBe("—");
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd hosted && npm test -- spend`
Expected: FAIL, `aiCell` is not exported.

- [ ] **Step 3: The cell and the column**

In `hosted/src/pages.ts`, add:

```ts
/** A household's AI spend this month, as the webapp's AI row reads (W-938). */
export function aiCell(ai: { usd: number; limit: number | null; paused: boolean; count: number }): string {
  if (!ai.count && !ai.paused) return "—";
  const money = `$${ai.usd.toFixed(2)}${ai.limit === null ? "" : ` of $${ai.limit.toFixed(2)}`}`;
  return ai.paused ? `Paused · ${money}` : money;
}
```

Add `ai: { usd: number; limit: number | null; paused: boolean; count: number }` to the household row type in `AdminData`. In the households table, add the header `<th>AI this month</th>` after `<th>Server, 7 days</th>`, and in each row the matching cell after the server-time cell:

```ts
<td>${e(aiCell(h.ai))}</td>
```

- [ ] **Step 4: Run the tests and look at it**

Run: `cd hosted && npm test && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hosted/src/pages.ts hosted/test/spend.test.ts
git commit -m "Admin: each household's AI spend this month, beside its server time (W-938)" -m "Refs: W-938"
```

---

### Task 4: The server reserves at the front door when it is on Cloud

**Files:**
- Modify: `server/featherframe/spend.py` (add `FrontDoorStore`)
- Modify: `server/featherframe/hosted.py` (add `link()`)
- Modify: `server/featherframe/service.py` (pick the store)
- Test: `server/tests/test_spend_front_door.py`

**Interfaces:**
- Consumes: the routes of Task 2; `hosted.HostedLink` (`.http`, `._url(rel)`); the part 1 store interface.
- Produces:
  - `hosted.link() -> Optional[HostedLink]` (the active link, None off hosted)
  - `spend.FrontDoorStore(link, local_db=None)`: `reserve`, `settle`, `snapshot`, `resume` over HTTP; on first use it sends the local `spend` table's rows to `spend/import` once (kv `spend_rows_at_door`).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_spend_front_door.py`:

```python
"""On Cloud the front door keeps the spend records (W-938): the server
reserves there, and a front door it cannot reach buys nothing."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from featherframe import hosted, spend
from featherframe.db import Database

T0 = datetime(2026, 9, 27, 18, 40)


def fake_door():
    """The routes of hosted/src/household.ts spendRoute, on a MemoryStore."""
    door = FastAPI()
    book = spend.MemoryStore()
    door.state.book, door.state.imported = book, []

    @door.post("/h/spend/reserve")
    async def reserve(request: Request):
        body = await request.json()
        reason = book.reserve(spend.Record(**body["record"]), spend.Rule(**body["rule"]))
        return {"ok": True} if reason is None else {"ok": False, "reason": reason}

    @door.post("/h/spend/settle")
    async def settle(request: Request):
        b = await request.json()
        book.settle(b["id"], b["state"], b["cost_usd"], None)
        return {"ok": True}

    @door.get("/h/spend/snapshot")
    async def snapshot(since: float = 0):
        s = book.snapshot(since)
        return {"rows": [asdict(r) for r in s.rows], "pause": s.pause, "resumed_at": s.resumed_at}

    @door.post("/h/spend/resume")
    async def resume(request: Request):
        book.resume((await request.json())["now"])
        return {"ok": True}

    @door.post("/h/spend/import")
    async def imp(request: Request):
        rows = (await request.json())["rows"]
        door.state.imported.extend(rows)
        return {"added": len(rows)}

    return door


@pytest.fixture
def link(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    door = fake_door()
    return door, hosted.HostedLink("http://door/h", "k", tmp_path / "data", session=TestClient(door))


def test_a_purchase_is_reserved_and_settled_at_the_door(link):
    door, ln = link
    gate = spend.Gate(spend.FrontDoorStore(ln), now=lambda: T0)
    with gate.purchase("collage", "2026-09-27", model="gpt-image-2.5-sunburst", quality="max") as p:
        assert [r.state for r in door.state.book.snapshot(0).rows] == ["open"]
        p.settle(None, 0.2)
    assert [r.state for r in door.state.book.snapshot(0).rows] == ["settled"]
    assert gate.summary()["usd"] == pytest.approx(0.2)


def test_the_door_refuses_and_the_gate_says_why(link):
    door, ln = link
    gate = spend.Gate(spend.FrontDoorStore(ln), now=lambda: T0)
    with pytest.raises(RuntimeError):
        with gate.purchase("collage", "2026-09-27", model="m"):
            raise RuntimeError("stopped mid-call")
    with pytest.raises(spend.Refused) as e:
        with gate.purchase("collage", "2026-09-27", model="m"):
            pass
    assert e.value.reason == "subject"


def test_an_unreachable_door_buys_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    ln = hosted.HostedLink("http://127.0.0.1:9/h", "k", tmp_path / "data")
    gate = spend.Gate(spend.FrontDoorStore(ln), now=lambda: T0)
    with pytest.raises(spend.Refused) as e:
        with gate.purchase("plate", "tyto-alba", model="m"):
            pass
    assert e.value.reason == "unreachable"


def test_the_servers_own_records_go_to_the_door_once(link, tmp_path):
    door, ln = link
    db = Database(tmp_path / "ff.db")
    local = spend.Gate(spend.LocalStore(db), now=lambda: T0)
    with local.purchase("plate", "tyto-alba", model="m") as p:
        p.settle(None, 0.05)
    spend.FrontDoorStore(ln, local_db=db)
    spend.FrontDoorStore(ln, local_db=db)
    assert [r["subject"] for r in door.state.imported] == ["tyto-alba"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_front_door.py -q`
Expected: FAIL, `AttributeError: … 'FrontDoorStore'`.

- [ ] **Step 3: `hosted.link()`**

In `server/featherframe/hosted.py`, after `activate()`:

```python
def link() -> Optional[HostedLink]:
    """The link this process pulled with, or None off hosted."""
    return _active
```

- [ ] **Step 4: `FrontDoorStore`**

Append to `server/featherframe/spend.py`:

```python
#: A reservation waits this long for the front door, then the gate refuses.
FRONT_DOOR_TIMEOUT_S = 10
_AT_DOOR_KEY = "spend_rows_at_door"


class FrontDoorStore:
    """The records at the household's front door (W-938, part 2): they
    outlive a Container stopped mid-call. The front door runs `decide()`'s
    port with the insert and adds a backstop of its own."""

    def __init__(self, link, local_db=None) -> None:
        self._link = link
        if local_db is not None and not local_db.get(_AT_DOOR_KEY):
            rows = local_db.spend_rows(0.0)
            if rows:
                r = link.http.post(link._url("spend/import"), json={"rows": rows},
                                   timeout=FRONT_DOOR_TIMEOUT_S)
                r.raise_for_status()
            local_db.set(_AT_DOOR_KEY, True)

    def _post(self, op: str, body: dict) -> dict:
        r = self._link.http.post(self._link._url(f"spend/{op}"), json=body,
                                 timeout=FRONT_DOOR_TIMEOUT_S)
        r.raise_for_status()
        return r.json()

    def reserve(self, rec: Record, rule: Rule) -> Optional[str]:
        body = self._post("reserve", {"record": asdict(rec), "rule": asdict(rule)})
        return None if body.get("ok") else str(body.get("reason") or "unreachable")

    def settle(self, rec_id: str, state: str, cost_usd: Optional[float],
               usage: Optional[dict]) -> None:
        self._post("settle", {"id": rec_id, "state": state, "cost_usd": cost_usd})

    def snapshot(self, since: float) -> Snapshot:
        r = self._link.http.get(self._link._url("spend/snapshot"), params={"since": since},
                                timeout=FRONT_DOOR_TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
        return Snapshot(rows=[Record(**x) for x in body.get("rows") or []],
                        pause=body.get("pause"), resumed_at=float(body.get("resumed_at") or 0.0))

    def resume(self, now: float) -> None:
        self._post("resume", {"now": now})
```

The import of local rows runs in `__init__`; if it fails, the constructor raises. The service (Step 5) catches that and keeps `LocalStore` for this start, so a front door that is briefly away never stops the server starting.

- [ ] **Step 5: The service picks the store**

In `service.py`, replace `spend.LocalStore(self.db)` in the gate's construction with `self._spend_store()`, and add:

```python
    def _spend_store(self):
        """On Cloud the front door keeps the records (W-938); else our DB."""
        link = hosted.link()
        if link is not None:
            try:
                return spend.FrontDoorStore(link, local_db=self.db)
            except Exception:
                log.warning("front door spend records unavailable; using the local ones",
                            exc_info=True)
        return spend.LocalStore(self.db)
```

(`hosted` is already imported in `service.py`; check with `grep -n "^from . import\|import hosted" server/featherframe/service.py`.)

`Gate.summary()` is now an HTTP call on Cloud; it is read by `status()`, which the page polls every 30 s. Add a 15-second cache to `Gate.summary` so a page left open does not ask the front door twice a poll:

```python
    _SUMMARY_TTL_S = 15.0

    def summary(self) -> dict:
        now = time.monotonic()
        cached = getattr(self, "_summary_cache", None)
        if cached and now - cached[0] < self._SUMMARY_TTL_S:
            return cached[1]
        out = self._summary()
        self._summary_cache = (now, out)
        return out
```

Rename the existing `summary` body to `_summary`, and clear the cache (`self._summary_cache = None`) at the end of `purchase()` (in its `finally`) and in `resume()`. Run part 1's tests again after this change: `./.venv/bin/python -m pytest tests/test_spend*.py -q`.

- [ ] **Step 6: Run the tests**

Run: `cd server && ./.venv/bin/python -m pytest tests/test_spend_front_door.py tests/test_spend.py tests/test_spend_service.py tests/test_hosted.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add server/featherframe/spend.py server/featherframe/hosted.py server/featherframe/service.py server/tests/test_spend_front_door.py
git commit -m "On Featherframe Cloud every AI purchase is reserved at the household's front door first (W-938)" -m "Refs: W-938"
```

---

### Task 5: Docs, the PR, and the deploy

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: AGENTS.md**

In the **Hosted** section, after the sentence about genart's per-key failure cooldown being kept in the DB, add:

```markdown
The household's AI spend is kept at its front door (W-938, `hosted/src/spend.ts`):
`spend.FrontDoorStore` reserves every paid call at `/_internal/<hid>/spend/reserve`
before the server makes it, and settles it after; a front door it cannot reach
buys nothing. The front door runs `spend.decide`'s port (held to
`server/tests/fixtures/spend-cases.json`) with the insert, plus one rule of its
own, a $10-a-UTC-day backstop. It emails `ADMIN_EMAILS` once a reason a day
when a household pauses, passes $3 in a UTC day, or hits the backstop, and the
admin households table shows each one's AI spend this month.
```

- [ ] **Step 2: Everything**

Run from the repo root: `make test`, then `cd hosted && npm test && npx tsc --noEmit`.
Expected: PASS.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin wells/w-938-ai-spend-front-door
gh pr create --title "Featherframe Cloud keeps each household's AI spend at its front door (W-938)" --body-file /tmp/w938-2-pr.md
```

Branch from `main` after part 1 merged: `git checkout -b wells/w-938-ai-spend-front-door origin/main` before Task 1. Write `/tmp/w938-2-pr.md` first: what changed and why, how it was verified, and that the old `spend.jsonl` and the server's local rows are carried to the front door once. End with `Refs: W-938`. Attach the PR to the Linear issue.

- [ ] **Step 4: Deploy after merge**

From an up-to-date `main`, with Docker running:

```bash
cd hosted && npx wrangler deploy
```

Then:
1. Open `https://cloud.featherframe.app/admin`: the households table has **AI this month**, and the admin's own household shows this month's spend.
2. Open the household's webapp: the AI image generation summary shows the same figure.
3. Read the front door's records for the household through the internal route only if something disagrees; never print the household's `config` kv row (it holds the owner's API key).
