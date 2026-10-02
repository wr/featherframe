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
/** What of the Durable Object's `ctx.storage.sql` this needs (and of the test stand-in). */
export interface SqlLike {
  exec(q: string, ...a: any[]): { toArray(): Record<string, any>[]; one(): Record<string, any> | undefined;
                                  readonly rowsWritten: number };
}

export const OPEN_HOLD_S = 86400;
/** What the alert's "last hour" counts. The pause itself counts less: `countsTowardRunaway`. */
export const RUNAWAY_KINDS = ["plate", "collage"];
/** At most this much a UTC day per household, whatever the server asks. */
export const BACKSTOP_USD_PER_DAY = 10;
/** The admin hears of a household past this in a UTC day. */
export const ALERT_USD_PER_DAY = 3;
const LOOKBACK_S = 36 * 3600;

const spent = (r: SpendRow) =>
  r.state === "released" ? 0 : r.state === "settled" && r.cost_usd != null ? r.cost_usd : r.est_usd;
const utcDay = (at: number) => new Date(at * 1000).toISOString().slice(0, 10);

/** Collages always; an illustration only when it buys a species again
 * within a day of buying it (W-938). */
export function countsTowardRunaway(rows: SpendRow[], r: SpendRow): boolean {
  if (r.kind === "collage") return true;
  if (r.kind !== "plate") return false;
  return rows.some((o) => o.kind === "plate" && o.subject === r.subject && o.state !== "released"
    && o.id !== r.id && r.at - OPEN_HOLD_S <= o.at && o.at < r.at);
}

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
  if (rule.runaway_per_hour && countsTowardRunaway(rows, rec)) {
    const since = Math.max(now - 3600, resumedAt);
    const recent = rows.filter((r) => r.auto && r.state !== "released" && r.at > since
      && countsTowardRunaway(rows, r));
    if (recent.length >= rule.runaway_per_hour) return "runaway";
  }
  return null;
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;

/** A reservation as the server sends it (`spend/reserve`), or null. */
export function reserveBody(b: unknown): { record: SpendRow; rule: Rule } | null {
  if (!isObj(b) || !isObj(b.record) || !isObj(b.rule)) return null;
  const r = b.record, u = b.rule;
  const ok = ["id", "month", "day", "kind", "subject", "model"].every((k) => isStr(r[k]))
    && isNum(r.at) && isNum(r.est_usd) && r.est_usd >= 0 && typeof r.auto === "boolean"
    && (r.quality === null || isStr(r.quality))
    && isNum(u.limit_usd) && (u.runaway_per_hour === null || isNum(u.runaway_per_hour))
    && (u.window_s === null || isNum(u.window_s));
  return ok ? { record: r as SpendRow, rule: u as Rule } : null;
}

/** A settle as the server sends it (`spend/settle`), or null. */
export function settleBody(b: unknown): { id: string; state: "settled" | "released"; cost_usd: number | null } | null {
  if (!isObj(b) || !isStr(b.id) || (b.state !== "settled" && b.state !== "released")) return null;
  if (!(b.cost_usd === null || isNum(b.cost_usd))) return null;
  return { id: b.id, state: b.state, cost_usd: b.cost_usd };
}

type Raw = Omit<SpendRow, "auto"> & { auto: number };

export class SpendBook {
  constructor(private sql: SqlLike) {
    sql.exec(`
      CREATE TABLE IF NOT EXISTS spend (id TEXT PRIMARY KEY, at REAL NOT NULL, month TEXT NOT NULL,
        day TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL, auto INTEGER NOT NULL,
        model TEXT, quality TEXT, est_usd REAL NOT NULL, cost_usd REAL, state TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS spend_at ON spend(at);
      CREATE INDEX IF NOT EXISTS spend_month ON spend(month);
      CREATE TABLE IF NOT EXISTS spend_meta (k TEXT PRIMARY KEY, v TEXT);
    `);
  }

  private get(k: string): string | null {
    const r = this.sql.exec("SELECT v FROM spend_meta WHERE k = ?", k).toArray();
    return r.length ? r[0].v : null;
  }
  private set(k: string, v: string | null): void {
    if (v === null) this.sql.exec("DELETE FROM spend_meta WHERE k = ?", k);
    else this.sql.exec("INSERT OR REPLACE INTO spend_meta (k, v) VALUES (?, ?)", k, v);
  }
  private rows(since: number, month = ""): SpendRow[] {
    return (this.sql.exec("SELECT * FROM spend WHERE at >= ? OR month = ? ORDER BY at", since, month)
      .toArray() as Raw[]).map((r) => ({ ...r, auto: !!r.auto }));
  }
  /** Whether the row was written: not when its id is already here. */
  private insert(r: SpendRow): boolean {
    const c = this.sql.exec(`INSERT OR IGNORE INTO spend (id, at, month, day, kind, subject, auto, model, quality,
      est_usd, cost_usd, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      r.id, r.at, r.month, r.day, r.kind, r.subject, r.auto ? 1 : 0, r.model, r.quality,
      r.est_usd, r.cost_usd, r.state);
    c.toArray();                             // run it to the end before counting
    return c.rowsWritten > 0;
  }

  /** Check and insert as one step: a Durable Object runs one request at a
   * time, and nothing here awaits. The backstop and the alerts count the UTC
   * day on `now`, the front door's own clock: the backstop is for when the
   * server is wrong, its clock included. The rule itself is judged on the
   * record's `at`, as the server judges it. */
  reserve(rec: SpendRow, rule: Rule, now = Date.now() / 1000): { ok: boolean; reason: string | null; alerts: Alert[] } {
    this.set("limit", String(rule.limit_usd));
    const rows = this.rows(Math.min(rec.at, now) - LOOKBACK_S, rec.month);
    const today = utcDay(now);
    const dayTotal = rows.filter((r) => utcDay(r.at) === today).reduce((a, r) => a + spent(r), 0);
    const alerts: Alert[] = [];
    const paused = this.get("pause") !== null;
    // The server's rule first: a backstop refusal says its own checks did not
    // stop it, so a pause or the owner's limit is never reported as one.
    let reason = decide(rows, paused, Number(this.get("resumed_at") || 0), rec, rule, rec.at);
    if (reason === "runaway") this.set("pause", JSON.stringify({ at: rec.at, count: rule.runaway_per_hour }));
    if (reason === null && rec.est_usd > 0 && dayTotal + rec.est_usd > BACKSTOP_USD_PER_DAY + 1e-9) {
      reason = "backstop";
    }
    // A yes the record was not written for would let the server buy uncounted.
    if (reason === null && !this.insert({ ...rec, state: "open", cost_usd: null })) {
      throw new Error(`spend record ${rec.id} was not written`);
    }
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

  /** The server's own records from before the front door kept them, and
   * its pause: a household paused there stays paused here. The server sends
   * them at every start, so a pause from before a resume here is not taken
   * again, and a pause of the front door's own is kept. */
  importRows(rows: SpendRow[], pause: { at: number; count: number | null } | null = null,
             resumedAt = 0): number {
    let added = 0;
    for (const r of rows) if (this.insert(r)) added++;
    const own = Number(this.get("resumed_at") || 0);
    const resumed = Math.max(own, Number.isFinite(resumedAt) ? resumedAt : 0);
    if (resumed > own) this.set("resumed_at", String(resumed));
    if (pause && typeof pause.at === "number" && pause.at > resumed && this.get("pause") === null) {
      this.set("pause", JSON.stringify({ at: pause.at, count: pause.count ?? null }));
    }
    return added;
  }

  monthSummary(month: string): { usd: number; limit: number | null; paused: boolean; count: number } {
    const rows = (this.sql.exec("SELECT * FROM spend WHERE month = ?", month).toArray() as Raw[])
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
