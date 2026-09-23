// The household's front door (W-843): see index.ts.

import { DurableObject } from "cloudflare:workers";
import type { Env } from "./index";
import { localIso, randomHex } from "./util";

// The household's server is woken only for news (W-847). The front door looks
// for it: a BirdWeather station every POLL_MS, or a push (Apprise, a webhook)
// the moment it lands. However much news there is, at most one wake per
// MIN_GAP_MS; and once a day regardless, in case anything was missed.
const POLL_MS = 2 * 60 * 1000;
const MIN_GAP_MS = 5 * 60 * 1000;
const SAFETY_MS = 24 * 60 * 60 * 1000;
// A page used this recently keeps the server up after a wake.
const PAGE_ACTIVE_MS = 60 * 1000;
const MAX_INGEST_BYTES = 16 * 1024;
// A frame's check-ins are kept one per this window until the server takes
// them: the battery log keeps one row per 5 min anyway.
const CHECKIN_BUCKET_S = 300;

type FrameRow = {
  id: string; status: string; etag: string | null; file: string | null;
  headers: string | null; push: string | null;
};

export class Household extends DurableObject<Env> {
  sql: SqlStorage;

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
    `);
  }

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
    if (request.method === "POST" && /^\/api\/ingest\/apprise(\/[^/]*)?$/.test(url.pathname)) {
      return this.ingest(request, url);
    }
    return this.proxy(request);
  }

  /** A new household (made at sign-up), or one given its login later. */
  async init(hid: string, tz: string): Promise<void> {
    this.setMeta("hid", hid);
    if (!this.meta("key")) this.setMeta("key", randomHex(32));
    this.setMeta("tz", tz);
    await this.wake();
  }

  /** A frame its owner just paired (W-845): the server adds it on a wake now,
   * so the glass goes from its code to a picture without a second step. */
  async adopt(deviceId: string, headers: Record<string, string>): Promise<void> {
    const body = { headers: { ...headers, "x-device-id": deviceId }, result: "403", etag: null,
      ip: null, ua: null, at: localIso(this.meta("tz") || "UTC"), add: true };
    this.sql.exec("INSERT OR REPLACE INTO checkins (frame_id, bucket, body) VALUES (?, ?, ?)",
      deviceId, -1, JSON.stringify(body));
    this.setMeta("news", "1");
    this.setMeta("wake_ms", "0");        // pairing is worth a wake at once
    await this.ctx.storage.setAlarm(Date.now() + 500);
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
    this.setMeta("page_ms", String(Date.now()));
    const stub = await this.server();
    const headers = new Headers(request.headers);
    headers.delete("Cookie");               // the session is the Worker's, not the server's
    headers.delete("X-FF-Household");
    headers.set("X-FF-Hosted", "1");
    return stub.fetch(new Request(request, { headers }));
  }

  /** Start the server (its lifespan pulls), hand it the pushes that landed
   * while it slept, run one tick (which reports), and stop it again unless
   * someone is on the page. */
  async wake(): Promise<void> {
    this.setMeta("wake_ms", String(Date.now()));
    this.setMeta("news", "0");
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
      if (Date.now() - Number(this.meta("page_ms") || 0) > PAGE_ACTIVE_MS) await stub.stop();
    } catch (err) {
      console.error("wake failed", err);
    }
    await this.schedule();
  }

  /** Is there news a wake should be spent on? */
  async lookForNews(): Promise<void> {
    if (this.meta("source_kind") !== "birdweather" || this.meta("poll") === "0") return;
    const station = this.meta("bw_station");
    if (!station) return;
    try {
      const r = await fetch(`https://app.birdweather.com/api/v1/stations/${encodeURIComponent(station)}/detections?limit=1`);
      if (!r.ok) return;
      const body = await r.json<{ detections?: { id?: number }[] } | { id?: number }[]>();
      const rows = Array.isArray(body) ? body : (body.detections || []);
      const id = rows.length && rows[0].id != null ? String(rows[0].id) : null;
      if (id && id !== this.meta("bw_last_id")) {
        // The first look only learns where the station is: the server read
        // everything up to now on its own last wake.
        if (this.meta("bw_last_id") !== null) this.setMeta("news", "1");
        this.setMeta("bw_last_id", id);
      }
    } catch (err) {
      console.error("birdweather look failed", err);
    }
  }

  async alarm(): Promise<void> {
    const now = Date.now();
    await this.lookForNews();
    const lastWake = Number(this.meta("wake_ms") || 0);
    const named = Number(this.meta("next_wake_epoch") || 0) * 1000;
    const due = (named > 0 && named <= now)
      || (this.meta("news") === "1" && now - lastWake >= MIN_GAP_MS)
      || now - lastWake >= SAFETY_MS;
    if (due) await this.wake();
    else await this.schedule();
  }

  async schedule(): Promise<void> {
    const now = Date.now();
    const lastWake = Number(this.meta("wake_ms") || 0);
    const named = Number(this.meta("next_wake_epoch") || 0) * 1000;
    const times = [lastWake + SAFETY_MS];
    if (named > now) times.push(named);
    if (this.meta("news") === "1") times.push(Math.max(now, lastWake + MIN_GAP_MS));
    if (this.meta("source_kind") === "birdweather" && this.meta("poll") !== "0") times.push(now + POLL_MS);
    await this.ctx.storage.setAlarm(Math.max(now + 1000, Math.min(...times)));
  }

  /** A push from BirdNET-Pi (Apprise) or BirdNET-Go, kept for the server and
   * turned into a wake. The token is the server's own (it checks it again). */
  async ingest(request: Request, url: URL): Promise<Response> {
    if (this.meta("source_kind") !== "apprise") {
      return Response.json({ error: "detection source is not Apprise" }, { status: 409 });
    }
    const token = url.pathname.split("/")[4] || "";
    const want = this.meta("apprise_token") || "";
    if (want && token !== want) return Response.json({ error: "bad token" }, { status: 403 });
    const body = await request.text();
    if (body.length > MAX_INGEST_BYTES) return Response.json({ error: "body too large" }, { status: 413 });
    this.sql.exec("INSERT INTO ingest (path, body) VALUES (?, ?)", url.pathname, body);
    // In quiet hours a detection changes nothing: it waits for the next wake.
    if (this.meta("poll") !== "0") this.setMeta("news", "1");
    await this.schedule();
    return Response.json({ ok: true, queued: true });
  }

  // -- the frames, answered without the server ---------------------------------
  frameRow(id: string): FrameRow | null {
    const r = this.sql.exec<FrameRow>("SELECT * FROM frames WHERE id = ?", id).toArray();
    return r.length ? r[0] : null;
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

  async frame(request: Request): Promise<Response> {
    const id = (request.headers.get("X-Device-Id") || "").trim().slice(0, 40) || "legacy";
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
    return new Response("not found", { status: 404 });
  }

  async takeState(state: {
    frames: Record<string, { status: string; etag?: string | null; file?: string;
      headers?: Record<string, string>; push?: unknown }>;
    next_wake_epoch?: number | null;
    poll?: boolean;
    source?: { kind?: string; station?: string; token?: string };
  }): Promise<void> {
    const before = new Map(this.sql.exec<FrameRow>("SELECT * FROM frames").toArray().map((r) => [r.id, r]));
    this.sql.exec("DELETE FROM frames");
    for (const [id, f] of Object.entries(state.frames || {})) {
      const push = f.push ? JSON.stringify(f.push) : null;
      this.sql.exec("INSERT INTO frames (id, status, etag, file, headers, push) VALUES (?, ?, ?, ?, ?, ?)",
        id, f.status, f.etag ?? null, f.file ?? null, f.headers ? JSON.stringify(f.headers) : null, push);
      // Every socket for this frame hears a changed message; a frame no longer
      // on is let go, and keeps polling to be let in.
      for (const ws of this.ctx.getWebSockets(id)) {
        if (f.status !== "on" || !push) ws.close(1008, "not on");
        else if (before.get(id)?.push !== push) ws.send(push);
      }
    }
    for (const id of before.keys()) {
      if (!(id in (state.frames || {}))) for (const ws of this.ctx.getWebSockets(id)) ws.close(1008, "gone");
    }
    this.setMeta("next_wake_epoch", state.next_wake_epoch ? String(state.next_wake_epoch) : null);
    this.setMeta("poll", state.poll === false ? "0" : "1");
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
    await this.schedule();
  }
}

