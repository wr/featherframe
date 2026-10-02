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

  /** A save empties the changing copies but keeps their rows (and when they
   * were read), so the next refresh fills them all again; a new page build
   * drops everything. */
  async clear(which: "changing" | "all"): Promise<void> {
    const where = which === "all" ? "" : " WHERE kind = 'changing'";
    const objects = this.sql.exec(`SELECT object FROM page_cache${where}`).toArray()
      .map((r) => r.object as string | null).filter((o): o is string => !!o);
    if (which === "all") this.sql.exec("DELETE FROM page_cache");
    else this.sql.exec("UPDATE page_cache SET object = NULL, headers = NULL, filled_at = NULL WHERE kind = 'changing'");
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
   * every row with no copy, and, when `/` is among them, the page's `parts`
   * (its status, tasks, history and previews), read or not. Only when every
   * ask answers 200 do the rows move to the new copies, together (no await
   * between the updates, so no other request sees half of them); else
   * nothing changes. A row a save emptied, or a miss filled anew, while the
   * asks were out keeps what it has. True when it swapped. */
  async refresh(ask: (a: Ask) => Promise<Response>, now: number,
                parts: { key: string; kind: Kind; ask: Ask }[] = []): Promise<boolean> {
    const due = `kind = 'changing' AND (read_at >= ? OR object IS NULL)`;
    const pageDue = this.sql.exec(`SELECT ask FROM page_cache WHERE ${due}`, now - READ_WINDOW_MS).toArray()
      .some((r) => (JSON.parse(String(r.ask)) as Ask).path === "/");
    const extra = pageDue ? parts : [];
    for (const p of extra) {
      this.sql.exec(`INSERT INTO page_cache (key, kind, ask, read_at) VALUES (?, ?, ?, ?) ON CONFLICT (key) DO NOTHING`,
        p.key, p.kind, JSON.stringify(p.ask), now);
    }
    const keys = extra.map((p) => p.key);
    const rows = this.sql.exec(`SELECT * FROM page_cache WHERE ${due}${keys.length ? ` OR key IN (${keys.map(() => "?").join(",")})` : ""}`,
      now - READ_WINDOW_MS, ...keys).toArray() as Entry[];
    if (!rows.length) return true;
    const fresh: { e: Entry; object: string; headers: string }[] = [];
    const drop = async () => {
      if (fresh.length) await this.bucket.delete(fresh.map((f) => this.prefix + f.object));
    };
    try {
      for (const e of rows) {
        const res = await ask(JSON.parse(e.ask) as Ask);
        const body = await res.arrayBuffer();
        // The server gone (asleep, or stopped part way): nothing changes.
        if (res.status === 503) { await drop(); return false; }
        // One read it cannot draw now (a frame's preview not drawn yet): that
        // row keeps what it has; the page and the rest still move on.
        if (res.status !== 200 || body.byteLength > MAX_BODY) continue;
        const object = randomHex(16);
        await this.bucket.put(this.prefix + object, body);
        fresh.push({ e, object, headers: JSON.stringify(kept(res.headers)) });
      }
    } catch (err) {
      console.error("cache refresh ask", err);
      await drop();
      return false;
    }
    const generation = Number(this.sql.exec("SELECT coalesce(max(generation), 0) AS g FROM page_cache")
      .toArray()[0].g) + 1;
    const gone: string[] = [];
    for (const f of fresh) {
      const r = this.sql.exec(`UPDATE page_cache SET object = ?, headers = ?, generation = ?, filled_at = ?
        WHERE key = ? AND object IS ?`, f.object, f.headers, generation, now, f.e.key, f.e.object);
      r.toArray();                       // run it to the end, so rowsWritten is final
      if (r.rowsWritten) { if (f.e.object) gone.push(f.e.object); }
      else gone.push(f.object);          // the row moved on while we asked: keep what it has
    }
    const unread = this.sql.exec(
      "SELECT object FROM page_cache WHERE read_at < ?", now - UNREAD_DROP_MS).toArray() as { object: string | null }[];
    this.sql.exec("DELETE FROM page_cache WHERE read_at < ?", now - UNREAD_DROP_MS);
    gone.push(...unread.map((r) => r.object).filter((o): o is string => !!o));
    if (gone.length) await this.bucket.delete(gone.map((o) => this.prefix + o));
    return true;
  }
}

function kept(h: Headers): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of KEEP_HEADERS) { const v = h.get(k); if (v) out[k] = v; }
  return out;
}

export interface ReadDeps {
  cache: PageCache;
  now: number;
  build: string;              // the server's page build (meta page_build), "" before the first report
  today: string;              // the household's date, YYYY-MM-DD
  running(): Promise<boolean>; // the server is up and not on its way out
  ask(a: Ask): Promise<Response>;   // straight to the server: not page activity
  proxy(): Promise<Response>;       // today's path, which starts the server
  look(urgent?: boolean): Promise<void>;  // a wake by the alarm; urgent: there is no copy at all
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
    await d.look(true);                  // no page at all: quiet hours or not, it needs the server
    return d.loading();
  }
  return d.cache.store(key, route.kind, ask, await d.proxy(), d.now);
}
