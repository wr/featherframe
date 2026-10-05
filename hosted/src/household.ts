// The household's front door (W-843): see index.ts.

import { DurableObject } from "cloudflare:workers";
import { sendMail } from "./accounts";
import type { Env } from "./index";
import { releaseFrame } from "./setup";
import { alertMail, type Alert, reserveBody, settleBody, SpendBook, type SpendRow } from "./spend";
import { birdweatherNews, changesNothing, firmwareWaiting, isDetection, localIso, pushedNames, randomHex } from "./util";
import { display, lobbyPng, shortOf, trmnlHeaders } from "./viewers";
import { lastDays, type UsageDay } from "./usage";
import { due, nextAlarm, type WakeState } from "./wakes";
import { answerRead, type Ask, cacheKey, FRESH_MS, type Kind, PageCache, routeOf, SHOWN_AGE_MS } from "./pagecache";
import { loadingPage } from "./loading";

// The household's server is woken only for news (W-847). The front door looks
// for it: a BirdWeather station every POLL_MS, or a push (BirdNET-Pi's Apprise,
// BirdNET-Go's webhook) the moment it lands. However much news there is, at
// most one wake per MIN_GAP_MS; and once a day regardless, in case anything
// was missed.
// A page used this recently keeps the server up after a wake.
const PAGE_ACTIVE_MS = 60 * 1000;
const MAX_INGEST_BYTES = 16 * 1024;
// A BirdWeather look reads this many of the station's newest detections, to
// tell whether any since the last look would change a picture (W-984).
const BW_LOOK_ROWS = 25;
// A frame's check-ins are kept one per this window until the server takes
// them: the battery log keeps one row per 5 min anyway.
const CHECKIN_BUCKET_S = 300;

// A paired viewer (W-849), as the server last reported it: its image (drawn
// ahead of time and pushed to R2) and how often to tell it to come back.
type ViewerRow = {
  id: string; status: string; name: string | null; file: string | null;
  refresh: number | null; paper: number | null; short: string | null;
};
const PAGE_POLL_S = 20;             // viewers.PAGE_POLL_SECONDS
const VIEWER_WAIT_S = 30;           // paired, not drawn for yet: soon

// A frame that stops being on (removed, ignored) is told so before its socket
// closes: any message makes the firmware ask /api/frame at once, where it
// finds its pairing code. A bare close is not a wake, and a frame on the
// collage would sit on its old picture until its timer (hours).
const GONE = JSON.stringify({ etag: null, rotation: null, power: null, ota: false });
function goodbye(ws: WebSocket, reason: string): void {
  try { ws.send(GONE); } catch { /* already closed */ }
  try { ws.close(1008, reason); } catch { /* already closed */ }
}

type FrameRow = {
  id: string; status: string; etag: string | null; file: string | null;
  headers: string | null; push: string | null;
};

export class Household extends DurableObject<Env> {
  sql: SqlStorage;
  spend: SpendBook;
  /** Sends an alert; a field so a test can stand in for Resend. */
  mailer = (to: string, mail: { subject: string; text: string; html: string }) => sendMail(this.env, to, mail);

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
      CREATE TABLE IF NOT EXISTS frames (id TEXT PRIMARY KEY, status TEXT, etag TEXT,
        file TEXT, headers TEXT, push TEXT);
      CREATE TABLE IF NOT EXISTS checkins (frame_id TEXT, bucket INTEGER, body TEXT,
        PRIMARY KEY (frame_id, bucket));
      CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, sha TEXT);
      CREATE TABLE IF NOT EXISTS ingest (seq INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT, body TEXT);
      CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, at INTEGER);
      CREATE TABLE IF NOT EXISTS viewers (id TEXT PRIMARY KEY, status TEXT, name TEXT, file TEXT,
        refresh INTEGER, paper INTEGER, short TEXT);
      CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, wakes INTEGER NOT NULL DEFAULT 0,
        server_ms INTEGER NOT NULL DEFAULT 0, wake_ms INTEGER NOT NULL DEFAULT 0,
        page_ms INTEGER NOT NULL DEFAULT 0);
    `);
    // Wake time and page time apart (W-907); server_ms stays their sum. A day
    // from before has only the sum, and both of these at 0.
    const cols = new Set(this.sql.exec("SELECT * FROM usage LIMIT 0").columnNames);
    for (const c of ["wake_ms", "page_ms"]) {
      if (!cols.has(c)) this.sql.exec(`ALTER TABLE usage ADD COLUMN ${c} INTEGER NOT NULL DEFAULT 0`);
    }
    // The household's AI spend (W-938): the server reserves each paid call here first.
    this.spend = new SpendBook(this.sql);
  }

  private _cache?: PageCache;
  /** The webapp's reads, kept (W-946); bodies under the household's own prefix. */
  get cache(): PageCache {
    return this._cache ??= new PageCache(this.sql, this.env.DATA, `households/${this.meta("hid")}/cache/`);
  }
  refreshing: Promise<void> | null = null;
  refreshAgain = false;

  meta(k: string): string | null {
    const r = this.sql.exec<{ v: string }>("SELECT v FROM meta WHERE k = ?", k).toArray();
    return r.length ? r[0].v : null;
  }
  setMeta(k: string, v: string | null): void {
    if (v === null) this.sql.exec("DELETE FROM meta WHERE k = ?", k);
    else this.sql.exec("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", k, v);
  }

  /** A request the Worker has already placed here: a frame of this
   * household's, the signed-in owner's page, or this household's server. */
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const hid = request.headers.get("X-FF-Household") || "";
    if (!hid || this.meta("hid") !== hid || !this.meta("key")) return new Response("not found", { status: 404 });

    const internal = `/_internal/${hid}/`;
    if (url.pathname.startsWith(internal)) {
      if (request.headers.get("Authorization") !== `Bearer ${this.meta("key")}`) {
        return new Response("forbidden", { status: 403 });
      }
      return this.internal(request, url.pathname.slice(internal.length));
    }
    if (url.pathname === "/api/frame/push" && request.headers.get("Upgrade") === "websocket") {
      return this.pushSocket(request);
    }
    if (url.pathname === "/api/frame" && request.method === "GET" && !url.searchParams.get("view")) {
      return this.frame(request);
    }
    if (url.pathname === "/api/firmware" && request.method === "GET" && !this.firmwareFor(request)) {
      return new Response("no firmware hosted", { status: 404, headers: { "Cache-Control": "no-store" } });
    }
    // A viewer the Worker has already checked is this household's (W-849).
    const viewer = request.headers.get("X-FF-Viewer");
    if (viewer) return this.viewer(request, url, viewer, request.headers.get("X-FF-Viewer-Token") || "");
    if (request.method === "POST" && /^\/api\/ingest\/(apprise|birdnet-go)(\/[^/]*)?$/.test(url.pathname)) {
      return this.ingest(request, url);
    }
    if (url.pathname === "/api/warm" && request.method === "POST") return this.warm();
    // The page's Update now (W-950): a wake at once, as the loading page asks.
    if (url.pathname === "/api/page/update" && request.method === "POST") {
      await this.look(true);
      return new Response(null, { status: 204 });
    }
    if (url.pathname === "/api/page/ready" && request.method === "GET") return this.pageReady(request, url);
    const cached = await answerRead(request, url, this.readDeps(request));
    if (cached) return cached;
    return this.proxy(request);
  }

  // -- what the admin page shows (W-850) ---------------------------------------
  /** Roughly how long the server ran, by UTC day: a wake, or the time a page
   * kept it up, each counted apart (W-907). The Container's own clock is not
   * ours to read. */
  addUsage(kind: "wake" | "page", ms: number): void {
    const day = new Date().toISOString().slice(0, 10);
    const t = Math.round(ms);
    const wake = kind === "wake";
    this.sql.exec(`INSERT INTO usage (day, wakes, server_ms, wake_ms, page_ms) VALUES (?, ?, ?, ?, ?)
      ON CONFLICT (day) DO UPDATE SET wakes = wakes + excluded.wakes, server_ms = server_ms + excluded.server_ms,
        wake_ms = wake_ms + excluded.wake_ms, page_ms = page_ms + excluded.page_ms`,
      day, wake ? 1 : 0, t, wake ? t : 0, wake ? 0 : t);
    this.sql.exec("DELETE FROM usage WHERE day < ?", new Date(Date.now() - 40 * 86400e3).toISOString().slice(0, 10));
  }

  summary(): { frames: { id: string; status: string; seen: number | null }[];
               usage: UsageDay[]; month_ms: number;
               last_wake: number | null; source: string | null; suspended: boolean;
               ai: { usd: number; limit: number | null; paused: boolean; count: number } } {
    const seen = new Map(this.sql.exec<{ id: string; at: number }>("SELECT id, at FROM seen").toArray()
      .map((r) => [r.id, r.at]));
    const frames = this.sql.exec<{ id: string; status: string }>("SELECT id, status FROM frames ORDER BY id")
      .toArray().map((f) => ({ id: f.id, status: f.status, seen: seen.get(f.id) ?? null }));
    // 30 days for the admin page's chart (W-923); its table's column is the last 7.
    const usage = this.sql.exec<UsageDay>(
      "SELECT day, wakes, server_ms, wake_ms, page_ms FROM usage WHERE day >= ? ORDER BY day DESC", lastDays(30)[0]).toArray();
    const wake = Number(this.meta("wake_ms") || 0);
    const month_ms = this.sql.exec<{ ms: number }>("SELECT coalesce(sum(server_ms), 0) AS ms FROM usage WHERE day >= ?",
      new Date().toISOString().slice(0, 8) + "01").one().ms;
    return { frames, usage, month_ms, last_wake: wake || null, source: this.meta("source_kind"),
             suspended: !!this.meta("suspended"),
             ai: this.spend.monthSummary(new Date().toISOString().slice(0, 7)) };
  }

  /** A new household (made at sign-up), or one given its login later. */
  async init(hid: string, tz: string): Promise<void> {
    this.setMeta("hid", hid);
    if (!this.meta("key")) this.setMeta("key", randomHex(32));
    this.setMeta("tz", tz);
    await this.wake();
  }

  /** A household set up from the phone (W-888): made, its detection source
   * and Region queued ahead of the first wake, and its first frame added on
   * that wake. One wake, on an alarm, so the setup page answers at once. */
  async setUp(hid: string, tz: string, seed: Record<string, string> | null,
              deviceId: string, report: Record<string, unknown>): Promise<void> {
    this.setMeta("hid", hid);
    if (!this.meta("key")) this.setMeta("key", randomHex(32));
    this.setMeta("tz", tz);
    // A settings save of those fields, taken before /api/hosted/run.
    if (seed) this.sql.exec("INSERT INTO ingest (path, body) VALUES (?, ?)", "/api/hosted/seed", JSON.stringify(seed));
    await this.adopt(deviceId, report);
  }

  /** A frame its owner just paired (W-845): the server adds it on a wake now,
   * so the glass goes from its code to a picture without a second step. */
  async adopt(deviceId: string, headers: Record<string, unknown>): Promise<void> {
    const at = localIso(this.meta("tz") || "UTC");
    // A viewer's report says what it is (W-849); a kit's is its own headers.
    const transport = headers.transport;
    const body = transport === "trmnl" || transport === "page"
      ? { id: deviceId, viewer: headers, ip: null, at, add: true }
      : { headers: { ...headers, "x-device-id": deviceId }, result: "403", etag: null,
          ip: null, ua: null, at, add: true };
    this.sql.exec("INSERT OR REPLACE INTO checkins (frame_id, bucket, body) VALUES (?, ?, ?)",
      deviceId, -1, JSON.stringify(body));
    this.setMeta("news", "1");
    this.setMeta("wake_ms", "0");        // pairing is worth a wake at once
    await this.cache.clear("changing");     // the reload after pairing shows the new frame
    await this.ctx.storage.setAlarm(Date.now() + 500);
  }

  // -- the admin's controls (W-860) ---------------------------------------------
  /** A suspended household's server is not woken: its frames keep the last
   * picture it drew, and pushes wait. Resuming picks the schedule up again. */
  async suspend(on: boolean): Promise<void> {
    this.setMeta("suspended", on ? "1" : null);
    if (!on) return this.schedule();
    await this.ctx.storage.deleteAlarm();
    const hid = this.meta("hid");
    if (hid) await this.env.SERVER.getByName(hid).stop();
  }

  /** Forget this household (W-914): its frames are told, its server stopped
   * and forgotten, its data in R2 deleted, and this storage emptied. `hid` is
   * the Worker's, not read from here: a household given its login by the
   * admin API may never have been told its own. Says how many files went. */
  async destroy(hid: string): Promise<number> {
    // Nothing wakes the server again while it is being stopped.
    this.setMeta("suspended", "1");
    await this.ctx.storage.deleteAlarm();
    for (const ws of this.ctx.getWebSockets()) goodbye(ws, "gone");
    // Stopped before its data is listed: its last push lands first, then goes too.
    await this.env.SERVER.getByName(hid).forget();
    const prefix = `households/${hid}/`;
    let files = 0;
    let cursor: string | undefined;
    do {
      const page = await this.env.DATA.list({ prefix, cursor });
      if (page.objects.length) await this.env.DATA.delete(page.objects.map((o) => o.key));
      files += page.objects.length;
      cursor = page.truncated ? page.cursor : undefined;
    } while (cursor);
    await this.ctx.storage.deleteAll();
    return files;
  }

  // -- the household's server -------------------------------------------------
  async server() {
    const stub = this.env.SERVER.getByName(this.meta("hid")!);
    await stub.configure({
      FEATHERFRAME_HOSTED_URL: `https://${this.env.APP_HOST}/_internal/${this.meta("hid")}`,
      FEATHERFRAME_HOSTED_KEY: this.meta("key")!,
      TZ: this.meta("tz") || "UTC",
    });
    return stub;
  }

  async proxy(request: Request): Promise<Response> {
    // A page in use keeps the server up: count the time between its requests.
    const lastPage = Number(this.meta("page_ms") || 0);
    if (lastPage && Date.now() - lastPage < PAGE_ACTIVE_MS) this.addUsage("page", Date.now() - lastPage);
    this.setMeta("page_ms", String(Date.now()));
    const stub = await this.server();
    // Read before the body is handed on: forwarding the request uses it up.
    const removing = await this.removedFrame(request);
    const headers = new Headers(request.headers);
    headers.delete("Cookie");               // the session is the Worker's, not the server's
    headers.delete("X-FF-Household");
    headers.set("X-FF-Hosted", "1");
    const forward = new Request(request, { headers });
    // The link to the Container can drop under a request (the library answers
    // 500 "Container suddenly disconnected"; seen once, mid first render after
    // a cold start). A read is safe to ask again, once; a write is not.
    const again = request.method === "GET" || request.method === "HEAD";
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
  }

  // -- the webapp's reads, from the cache (W-946) ------------------------------
  readDeps(request: Request) {
    return {
      cache: this.cache, now: Date.now(), build: this.meta("page_build") || "",
      today: localIso(this.meta("tz") || "UTC").slice(0, 10),
      // Asked without configure(): a read must not cost the server DO a write.
      running: async () => this.env.SERVER.getByName(this.meta("hid")!).running(),
      ask: (a: Ask) => this.askServer(a),
      proxy: () => this.proxy(request),
      look: (urgent?: boolean) => this.look(urgent),
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

  /** Ask the server again for the copies someone reads, one refresh at a
   * time; one asked for while another runs runs once more after it, so a
   * refresh begun before a save is followed by one after it. */
  async refreshCache(): Promise<void> {
    if (this.refreshing) { this.refreshAgain = true; return this.refreshing; }
    this.refreshing = (async () => {
      do {
        this.refreshAgain = false;
        try { await this.cache.refresh((a) => this.askIfUp(a), Date.now(), this.pageParts()); }
        catch (err) { console.error("cache refresh", err); }
      } while (this.refreshAgain);
    })().finally(() => { this.refreshing = null; });
    return this.refreshing;
  }

  /** A refresh never starts the server: asleep (or stopping), it answers 503
   * and the refresh leaves every copy as it was. */
  async askIfUp(a: Ask): Promise<Response> {
    if (!(await this.env.SERVER.getByName(this.meta("hid")!).running())) return new Response("asleep", { status: 503 });
    return this.askServer(a);
  }

  /** What the page asks for as it loads, refreshed with `/` read or not: its
   * status, tasks and history, and each frame's preview (the picture itself
   * when there is no frame). */
  pageParts(): { key: string; kind: Kind; ask: Ask }[] {
    const build = this.meta("page_build") || "";
    const ids = [
      ...this.sql.exec<{ id: string }>("SELECT id FROM frames WHERE status = 'on'").toArray(),
      ...this.sql.exec<{ id: string }>("SELECT id FROM viewers WHERE status = 'on'").toArray(),
    ].map((r) => r.id);
    const paths = ["/api/status", "/api/tasks", "/api/history",
      ...(ids.length ? ids.map((id) => `/api/frames/${encodeURIComponent(id)}/preview.png`) : ["/api/preview.png"])];
    const today = localIso(this.meta("tz") || "UTC").slice(0, 10);
    return paths.map((p) => {
      const url = new URL(p, "https://page");
      const route = routeOf(url.pathname, today)!;
      return { ...cacheKey(url, route, {}, build), kind: route.kind };
    });
  }

  /** The frame a page request removes from this household, if it is one: a
   * row's Remove (`POST /api/frames/<id>` `{"forget": true}`) or Forget on an
   * ignored kit (`POST /api/frames`, `action=forget`). Read from a copy. */
  async removedFrame(request: Request): Promise<string | null> {
    if (request.method !== "POST") return null;
    const path = new URL(request.url).pathname;
    try {
      const one = path.match(/^\/api\/frames\/([^/]+)$/);
      if (one) {
        const body = await request.clone().json<{ forget?: unknown }>();
        return body && body.forget ? decodeURIComponent(one[1]).slice(0, 40) : null;
      }
      if (path === "/api/frames") {
        const form = await request.clone().formData();
        return form.get("action") === "forget" ? String(form.get("id") || "").slice(0, 40) || null : null;
      }
    } catch {
      // Not a body we read: the server answers it as it would.
    }
    return null;
  }

  /** A frame removed on the page is no longer this household's: the registry
   * lets it go, so its next ask is shown a new pairing code rather than kept
   * waiting here for an add that will not come, and its kit can be set up
   * again by whoever has it next. */
  async unpair(deviceId: string): Promise<void> {
    await releaseFrame(this.env, deviceId, this.meta("hid")!);
  }

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
   * the request. In quiet hours, only when something is waiting for it,
   * unless there is no page at all (`urgent`): the loading page needs one. */
  async look(urgent = false): Promise<void> {
    if (this.meta("suspended")) return;
    if (this.meta("poll") === "0" && !urgent) {
      const waiting = this.sql.exec<{ n: number }>(
        "SELECT (SELECT count(*) FROM checkins) + (SELECT count(*) FROM ingest) AS n").one().n;
      if (!waiting) return;
    }
    this.setMeta("look", "1");
    // No page at all: the owner is looking at the loading page, so the wake
    // does not wait out the gap (as a frame just paired does not). And the
    // wake refills any copy the page would call old (W-952), not only those
    // past FRESH_MS: Update now reloads only on a fresh one.
    if (urgent) { this.setMeta("wake_ms", "0"); this.setMeta("look_now", "1"); }
    await this.schedule();
  }

  /** Start the server (its lifespan pulls), hand it the pushes that landed
   * while it slept, run one tick (which reports), and stop it again once it
   * is idle, unless someone is on the page. */
  async wake(): Promise<void> {
    if (this.meta("suspended")) return;
    const t0 = Date.now();
    this.setMeta("wake_ms", String(t0));
    this.setMeta("news", "0");
    this.setMeta("look", null);
    const age = this.meta("look_now") ? SHOWN_AGE_MS : FRESH_MS;
    this.setMeta("look_now", null);
    try {
      const stub = await this.server();
      const queued = this.sql.exec<{ seq: number; path: string; body: string }>(
        "SELECT seq, path, body FROM ingest ORDER BY seq").toArray();
      for (const q of queued) {
        await stub.fetch(`http://server${q.path}`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: q.body,
        });
        this.sql.exec("DELETE FROM ingest WHERE seq = ?", q.seq);
      }
      await stub.fetch("http://server/api/hosted/run", { method: "POST" });
      // Copies someone reads that this wake did not refresh, while it is up.
      if (this.cache.stale(Date.now(), age)) await this.refreshCache();
      if (Date.now() - Number(this.meta("page_ms") || 0) > PAGE_ACTIVE_MS) await stub.sleepWhenIdle();
    } catch (err) {
      console.error("wake failed", err);
    }
    this.addUsage("wake", Date.now() - t0);
    await this.schedule();
  }

  /** Is there news a wake should be spent on? */
  async lookForNews(): Promise<void> {
    if (this.meta("source_kind") !== "birdweather" || this.meta("poll") === "0") return;
    const station = this.meta("bw_station");
    if (!station) return;
    try {
      const r = await fetch(`https://app.birdweather.com/api/v1/stations/${encodeURIComponent(station)}/detections?limit=${BW_LOOK_ROWS}`);
      if (!r.ok) return;
      type Row = { id?: number; species?: { commonName?: string; scientificName?: string } };
      const body = await r.json<{ detections?: Row[] } | Row[]>();
      const rows = Array.isArray(body) ? body : (body.detections || []);
      // The first look only learns where the station is: the server read
      // everything up to now on its own last wake. A detection the server
      // said would change nothing waits for the next wake (W-984).
      const look = birdweatherNews(rows, this.meta("bw_last_id"), this.meta("unchanged"));
      if (look.news) this.setMeta("news", "1");
      this.setMeta("bw_last_id", look.last);
    } catch (err) {
      console.error("birdweather look failed", err);
    }
  }

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

  /** A push from BirdNET-Pi (Apprise) or BirdNET-Go, kept for the server and
   * turned into a wake. The token is the server's own (it checks it again). */
  async ingest(request: Request, url: URL): Promise<Response> {
    const [, , , path, token = ""] = url.pathname.split("/");
    const kind = path === "birdnet-go" ? "birdnet_go" : path;
    if (this.meta("source_kind") !== kind) {
      return Response.json({ error: `detection source is not ${path}` }, { status: 409 });
    }
    const want = this.meta("apprise_token") || "";
    if (want && token !== want) return Response.json({ error: "bad token" }, { status: 403 });
    const body = await request.text();
    if (body.length > MAX_INGEST_BYTES) return Response.json({ error: "body too large" }, { status: 413 });
    // BirdNET-Go's channel also carries its warnings and errors; only a
    // detection is news, so nothing else is kept or wakes the server.
    if (kind === "birdnet_go" && !isDetection(body)) return Response.json({ ok: false, ignored: true });
    this.sql.exec("INSERT INTO ingest (path, body) VALUES (?, ?)", url.pathname, body);
    // In quiet hours a detection changes nothing, nor does one the server
    // named (the species shown, a blocked one; W-984): it waits for the next
    // wake.
    if (this.meta("poll") !== "0" && !changesNothing(this.meta("unchanged"), pushedNames(kind, body))) {
      this.setMeta("news", "1");
    }
    await this.schedule();
    return Response.json({ ok: true, queued: true });
  }

  // -- the frames, answered without the server ---------------------------------
  frameRow(id: string): FrameRow | null {
    const r = this.sql.exec<FrameRow>("SELECT * FROM frames WHERE id = ?", id).toArray();
    return r.length ? r[0] : null;
  }

  /** Is there an image for the frame asking? Only then is the server woken. */
  firmwareFor(request: Request): boolean {
    const id = (request.headers.get("X-Device-Id") || "").trim().slice(0, 40);
    const push = id ? this.frameRow(id)?.push ?? null : null;
    const files = this.sql.exec<{ path: string }>(
      "SELECT path FROM files WHERE path LIKE 'firmware%.bin'").toArray().map((r) => r.path);
    return firmwareWaiting(push, files);
  }

  queueCheckin(request: Request, frameId: string, result: string, etag: string | null): void {
    const headers: Record<string, string> = {};
    request.headers.forEach((v, k) => { if (k.startsWith("x-") && k !== "x-ff-household") headers[k] = v; });
    const body = {
      headers, result, etag, ip: request.headers.get("CF-Connecting-IP"),
      ua: request.headers.get("User-Agent"), at: localIso(this.meta("tz") || "UTC"),
    };
    const bucket = Math.floor(Date.now() / 1000 / CHECKIN_BUCKET_S);
    this.sql.exec("INSERT OR REPLACE INTO checkins (frame_id, bucket, body) VALUES (?, ?, ?)",
      frameId, bucket, JSON.stringify(body));
  }

  // -- viewers, answered without the server (W-849) ----------------------------
  viewerRow(id: string): ViewerRow | null {
    const r = this.sql.exec<ViewerRow>("SELECT * FROM viewers WHERE id = ?", id).toArray();
    return r.length ? r[0] : null;
  }

  /** A TRMNL client's /api/display, the page's /api/view/state, or either's
   * image. The ask is kept for the server, as a kit's check-in is. */
  async viewer(request: Request, url: URL, id: string, token: string): Promise<Response> {
    const row = this.viewerRow(id);
    const drawn = row?.status === "on" && row.name && row.file ? row : null;
    this.sql.exec("INSERT OR REPLACE INTO seen (id, at) VALUES (?, ?)", id, Date.now());
    const path = url.pathname;

    if (path.startsWith("/api/viewers/")) {
      // Whatever name it asks for, it is shown what it should show now.
      if (!drawn) return lobbyPng(this.env, "", trmnlHeaders(request), `waiting-${shortOf(id)}`, (p) => this.ctx.waitUntil(p));
      const inm = (request.headers.get("If-None-Match") || "").replace(/^W\//, "").replace(/"/g, "").trim();
      const headers = { ETag: `"${drawn.name}"`, "Cache-Control": "no-cache" };
      if (inm === drawn.name) return new Response(null, { status: 304, headers });
      const obj = await this.env.DATA.get(`households/${this.meta("hid")}/data/${drawn.file}`);
      if (!obj) return new Response("no frame yet", { status: 404 });
      return new Response(obj.body, { headers: { ...headers, "Content-Type": "image/png" } });
    }

    const page = path === "/api/view/state";
    const v = page
      ? { transport: "page", w: url.searchParams.get("w") || "", h: url.searchParams.get("h") || "",
          device: (url.searchParams.get("device") || "").slice(0, 40) }
      : { transport: "trmnl", headers: trmnlHeaders(request) };
    this.queueViewer(request, id, v);
    const short = row?.short || shortOf(id);
    if (page) {
      if (!drawn) return Response.json({ image: null, dark: false, waiting: true, id: short, poll: PAGE_POLL_S },
                                       { headers: { "Cache-Control": "no-store" } });
      return Response.json({
        image: `/api/viewers/${encodeURIComponent(id)}/${drawn.name}.png?t=${token}`,
        dark: false, paper: !!drawn.paper, poll: PAGE_POLL_S,
      }, { headers: { "Cache-Control": "no-store" } });
    }
    if (!drawn) return Response.json(display(this.env, id, `waiting-${short}`, token, VIEWER_WAIT_S));
    return Response.json(display(this.env, id, drawn.name!, token, drawn.refresh || 900));
  }

  queueViewer(request: Request, id: string, v: Record<string, unknown>): void {
    const body = { id, viewer: v, ip: request.headers.get("CF-Connecting-IP"),
                   at: localIso(this.meta("tz") || "UTC") };
    const bucket = Math.floor(Date.now() / 1000 / CHECKIN_BUCKET_S);
    this.sql.exec("INSERT OR REPLACE INTO checkins (frame_id, bucket, body) VALUES (?, ?, ?)",
      id, bucket, JSON.stringify(body));
  }

  async frame(request: Request): Promise<Response> {
    const id = (request.headers.get("X-Device-Id") || "").trim().slice(0, 40) || "legacy";
    this.sql.exec("INSERT OR REPLACE INTO seen (id, at) VALUES (?, ?)", id, Date.now());
    const row = this.frameRow(id);
    if (row?.status === "ignored") {
      this.queueCheckin(request, id, "403", null);
      return new Response("this frame has not been added here", {
        status: 403, headers: { "Cache-Control": "no-store", "X-FF-Frame": "ignored" },
      });
    }
    if (!row || row.status !== "on") {
      // The Worker only sends a frame here once it is paired to this
      // household (W-848): the server just has not added it yet. "Try again"
      // — a 403 would send the firmware looking for another server on its LAN.
      this.queueCheckin(request, id, "403", null);
      return new Response("not added yet", { status: 503, headers: { "Cache-Control": "no-store" } });
    }
    const headers = new Headers(JSON.parse(row.headers || "{}"));
    if (!row.etag || !row.file) return new Response("no frame yet", { status: 503, headers });
    headers.set("ETag", `"${row.etag}"`);
    headers.set("Cache-Control", "no-cache");
    const inm = (request.headers.get("If-None-Match") || "").replace(/^W\//, "").replace(/"/g, "").trim();
    if (inm === row.etag) {
      this.queueCheckin(request, id, "304", row.etag);
      return new Response(null, { status: 304, headers });
    }
    const obj = await this.env.DATA.get(`households/${this.meta("hid")}/data/${row.file}`);
    if (!obj) return new Response("no frame yet", { status: 503, headers });
    this.queueCheckin(request, id, "frame", row.etag);
    headers.set("Content-Type", "application/octet-stream");
    headers.set("Content-Length", String(obj.size));
    return new Response(obj.body, { headers });
  }

  pushSocket(request: Request): Response {
    const id = (request.headers.get("X-Device-Id") || "").trim().slice(0, 40);
    const row = id ? this.frameRow(id) : null;
    if (!row || row.status !== "on" || !row.push) return new Response("not on", { status: 403 });
    const pair = new WebSocketPair();
    this.ctx.acceptWebSocket(pair[1], [id]);
    pair[1].send(row.push);
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  async webSocketMessage(): Promise<void> { /* the frame sends nothing */ }
  async webSocketClose(ws: WebSocket, code: number): Promise<void> {
    try { ws.close(code, "bye"); } catch { /* already closed */ }
  }

  // -- the server's side ----------------------------------------------------------
  async internal(request: Request, path: string): Promise<Response> {
    const prefix = `households/${this.meta("hid")}/data/`;
    if (path === "files" && request.method === "GET") {
      const files: Record<string, string> = {};
      for (const r of this.sql.exec<{ path: string; sha: string }>("SELECT path, sha FROM files")) {
        files[r.path] = r.sha;
      }
      return Response.json({ files });
    }
    if (path.startsWith("files/")) {
      const rel = decodeURIComponent(path.slice("files/".length));
      if (!rel || rel.split("/").includes("..")) return new Response("bad path", { status: 400 });
      if (request.method === "GET") {
        const obj = await this.env.DATA.get(prefix + rel);
        return obj ? new Response(obj.body) : new Response("not found", { status: 404 });
      }
      if (request.method === "PUT") {
        const sha = request.headers.get("X-SHA256") || "";
        await this.env.DATA.put(prefix + rel, request.body, { customMetadata: { sha } });
        this.sql.exec("INSERT OR REPLACE INTO files (path, sha) VALUES (?, ?)", rel, sha);
        return new Response(null, { status: 204 });
      }
      if (request.method === "DELETE") {
        await this.env.DATA.delete(prefix + rel);
        this.sql.exec("DELETE FROM files WHERE path = ?", rel);
        return new Response(null, { status: 204 });
      }
    }
    if (path === "state" && request.method === "POST") {
      await this.takeState(await request.json());
      return Response.json({ ok: true });
    }
    if (path === "checkins/take" && request.method === "POST") {
      const rows = this.sql.exec<{ body: string }>(
        "SELECT body FROM checkins ORDER BY bucket, frame_id").toArray();
      this.sql.exec("DELETE FROM checkins");
      return Response.json({ checkins: rows.map((r) => JSON.parse(r.body)) });
    }
    if (path.startsWith("spend/")) return this.spendRoute(request, path.slice("spend/".length));
    return new Response("not found", { status: 404 });
  }

  // -- AI spend (W-938) ----------------------------------------------------------
  async spendRoute(request: Request, op: string): Promise<Response> {
    if (op === "reserve" && request.method === "POST") {
      const body = reserveBody(await request.json().catch(() => null));
      if (!body) return Response.json({ error: "bad reserve" }, { status: 400 });
      let out: ReturnType<SpendBook["reserve"]>;
      try {
        out = this.spend.reserve(body.record, body.rule);
      } catch (e) {
        console.error("spend reserve", e);   // the server refuses on a 500
        return Response.json({ error: "not recorded" }, { status: 500 });
      }
      // The server waits on this answer before it buys: the mail goes after it.
      if (out.alerts.length) this.ctx.waitUntil(Promise.allSettled(out.alerts.map((a) => this.alert(a))));
      return Response.json(out.ok ? { ok: true } : { ok: false, reason: out.reason });
    }
    if (op === "settle" && request.method === "POST") {
      const body = settleBody(await request.json().catch(() => null));
      if (!body) return Response.json({ error: "bad settle" }, { status: 400 });
      this.spend.settle(body.id, body.state, body.cost_usd);
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
      const { rows, pause, resumed_at } = await request.json<{ rows: SpendRow[];
        pause?: { at: number; count: number | null } | null; resumed_at?: number }>();
      return Response.json({ added: this.spend.importRows(Array.isArray(rows) ? rows : [], pause ?? null,
                                                          Number(resumed_at) || 0) });
    }
    return new Response("not found", { status: 404 });
  }

  /** One email per admin, all at once; SpendBook already keeps it to once a reason a day. */
  async alert(a: Alert): Promise<void> {
    const hid = this.meta("hid") || "?";
    const mail = alertMail(hid, this.env.APP_HOST, a);
    const to = (this.env.ADMIN_EMAILS || "").split(",").map((e) => e.trim()).filter(Boolean);
    const sent = await Promise.allSettled(to.map(async (t) => this.mailer(t, mail)));
    for (const s of sent) if (s.status === "rejected") console.error("spend alert", s.reason);
  }

  async takeState(state: {
    frames: Record<string, { status: string; etag?: string | null; file?: string;
      headers?: Record<string, string>; push?: unknown }>;
    viewers?: Record<string, { status: string; name?: string; file?: string; refresh?: number;
      paper?: boolean; short?: string }>;
    next_wake_epoch?: number | null;
    poll?: boolean;
    unchanged?: string[] | "*";
    page_build?: string;
    source?: { kind?: string; station?: string; token?: string };
  }): Promise<void> {
    const shown = (): string => JSON.stringify([
      this.sql.exec("SELECT id, status, etag, headers FROM frames ORDER BY id").toArray(),
      this.sql.exec("SELECT id, status, name FROM viewers ORDER BY id").toArray()]);
    const shownBefore = shown();
    const buildBefore = this.meta("page_build");
    const before = new Map(this.sql.exec<FrameRow>("SELECT * FROM frames").toArray().map((r) => [r.id, r]));
    this.sql.exec("DELETE FROM frames");
    for (const [id, f] of Object.entries(state.frames || {})) {
      const push = f.push ? JSON.stringify(f.push) : null;
      this.sql.exec("INSERT INTO frames (id, status, etag, file, headers, push) VALUES (?, ?, ?, ?, ?, ?)",
        id, f.status, f.etag ?? null, f.file ?? null, f.headers ? JSON.stringify(f.headers) : null, push);
      // Every socket for this frame hears a changed message; a frame no longer
      // on is let go, and keeps polling to be let in.
      for (const ws of this.ctx.getWebSockets(id)) {
        if (f.status !== "on" || !push) goodbye(ws, "not on");
        else if (before.get(id)?.push !== push) ws.send(push);
      }
    }
    for (const id of before.keys()) {
      if (!(id in (state.frames || {}))) for (const ws of this.ctx.getWebSockets(id)) goodbye(ws, "gone");
    }
    this.sql.exec("DELETE FROM viewers");
    for (const [id, v] of Object.entries(state.viewers || {})) {
      this.sql.exec("INSERT INTO viewers (id, status, name, file, refresh, paper, short) VALUES (?, ?, ?, ?, ?, ?, ?)",
        id, v.status, v.name ?? null, v.file ?? null, v.refresh ?? null, v.paper ? 1 : 0, v.short ?? null);
    }
    this.setMeta("next_wake_epoch", state.next_wake_epoch ? String(state.next_wake_epoch) : null);
    this.setMeta("poll", state.poll === false ? "0" : "1");
    this.setMeta("unchanged", state.unchanged == null ? null : JSON.stringify(state.unchanged));
    const src = state.source || {};
    if (src.kind !== this.meta("source_kind") || (src.station || null) !== this.meta("bw_station")) {
      this.setMeta("bw_last_id", null);    // a new source: learn where it is first
    }
    this.setMeta("source_kind", src.kind || null);
    this.setMeta("bw_station", src.station || null);
    if ((src.token ?? null) !== this.meta("apprise_token")) {
      await this.env.DB.prepare("UPDATE households SET apprise_token = ? WHERE id = ?")
        .bind(src.token || null, this.meta("hid")).run();
    }
    this.setMeta("apprise_token", src.token ?? null);
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
    await this.schedule();
  }
}
