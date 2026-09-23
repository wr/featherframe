// Featherframe, hosted (W-841).
//
// A household is `<household>.featherframe.app`. Two Durable Objects serve it:
//
//   Household        the front door (W-843). It answers the frames — GET
//                    /api/frame from its own table and R2, the push socket —
//                    so the household's server can sleep. It keeps what the
//                    frames said until the server takes it, and wakes the
//                    server on the server's own schedule.
//   HouseholdServer  the household's own Featherframe server (W-844): the
//                    box's Python server, unchanged, in a Container. Its data
//                    dir lives in R2 behind the front door's internal API
//                    (server/featherframe/hosted.py is the other side of it).
//
// The page and everything else is proxied to the server, behind the
// household's password until accounts exist (W-845). The apex is the
// marketing page and is not routed here; `plates.` is the shared plate
// library (W-842), served from its bucket.

import { Container } from "@cloudflare/containers";
import { DurableObject } from "cloudflare:workers";

export interface Env {
  HOUSEHOLD: DurableObjectNamespace<Household>;
  SERVER: DurableObjectNamespace<HouseholdServer>;
  DATA: R2Bucket;
  PLATES: R2Bucket;
  ZONE: string;          // "featherframe.app"
  ADMIN_TOKEN: string;   // secret: provisions a household
}

// How often a sleeping household's server is woken to look for new
// detections (its source is polled from inside it).
const WAKE_CADENCE_MS = 5 * 60 * 1000;
// A frame's check-ins are kept one per this window until the server takes
// them: the battery log keeps one row per 5 min anyway.
const CHECKIN_BUCKET_S = 300;
const RESERVED = new Set(["www", "plates", "app", "api", "admin"]);

// ---------------------------------------------------------------- the Worker
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const host = url.hostname.toLowerCase();
    if (!host.endsWith("." + env.ZONE)) return new Response("not found", { status: 404 });
    const label = host.slice(0, -(env.ZONE.length + 1));
    if (label === "plates") return plates(request, env, url);
    if (RESERVED.has(label) || !/^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$/.test(label)) {
      return new Response("not found", { status: 404 });
    }
    const headers = new Headers(request.headers);
    headers.set("X-FF-Household", label);
    return env.HOUSEHOLD.getByName(label).fetch(new Request(request, { headers }));
  },
} satisfies ExportedHandler<Env>;

async function plates(request: Request, env: Env, url: URL): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") return new Response(null, { status: 405 });
  const key = url.pathname.replace(/^\/+/, "");
  const obj = await env.PLATES.get(key);
  if (!obj) return new Response("not found", { status: 404 });
  const h = new Headers();
  obj.writeHttpMetadata(h);
  h.set("ETag", obj.httpEtag);
  return new Response(request.method === "HEAD" ? null : obj.body, { headers: h });
}

// ------------------------------------------------------- the household server
export class HouseholdServer extends Container<Env> {
  defaultPort = 8080;
  // A wake is one request (POST /api/hosted/run) that is answered when its
  // tick is done, a first AI plate included; after that there is nothing to
  // stay up for. The page keeps it awake while it is open.
  sleepAfter = "30s";

  constructor(ctx: DurableObjectState<{}>, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      const vars = await ctx.storage.get<Record<string, string>>("vars");
      if (vars) this.envVars = vars;
    });
  }

  /** The household it serves: its front door's address and key, its zone. */
  async configure(vars: Record<string, string>): Promise<void> {
    this.envVars = vars;
    await this.ctx.storage.put("vars", vars);
  }
}

// ------------------------------------------------------------ the front door
type FrameRow = {
  id: string; status: string; etag: string | null; file: string | null;
  headers: string | null; push: string | null;
};

// Paths a device or a viewer asks for without the owner's password.
const OPEN_PATHS = [/^\/api\/frame$/, /^\/api\/frame\/push$/, /^\/api\/firmware$/,
  /^\/api\/setup$/, /^\/api\/display$/, /^\/api\/log$/, /^\/api\/view\.png$/,
  /^\/api\/view\/state$/, /^\/api\/viewers\//, /^\/view$/, /^\/view\.webmanifest$/,
  /^\/static\//, /^\/fonts\//, /^\/favicon/];

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

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const hid = request.headers.get("X-FF-Household") || "";
    if (url.pathname === "/_admin/provision") return this.provision(request, hid);
    if (this.meta("hid") !== hid || !this.meta("key")) return new Response("not found", { status: 404 });

    if (url.pathname.startsWith("/_internal/")) {
      if (request.headers.get("Authorization") !== `Bearer ${this.meta("key")}`) {
        return new Response("forbidden", { status: 403 });
      }
      return this.internal(request, url.pathname.slice("/_internal/".length));
    }
    if (url.pathname === "/api/frame/push" && request.headers.get("Upgrade") === "websocket") {
      return this.pushSocket(request);
    }
    if (url.pathname === "/api/frame" && request.method === "GET" && !url.searchParams.get("view")) {
      return this.frame(request);
    }
    if (!OPEN_PATHS.some((re) => re.test(url.pathname)) && !(await this.authorised(request))) {
      return new Response("Featherframe", {
        status: 401, headers: { "WWW-Authenticate": 'Basic realm="Featherframe", charset="UTF-8"' },
      });
    }
    return this.proxy(request);
  }

  // -- provisioning (until accounts, W-845) ---------------------------------
  async provision(request: Request, hid: string): Promise<Response> {
    if (request.method !== "POST" || !this.env.ADMIN_TOKEN ||
        request.headers.get("Authorization") !== `Bearer ${this.env.ADMIN_TOKEN}`) {
      return new Response("not found", { status: 404 });
    }
    const body = await request.json<{ password?: string; tz?: string }>();
    if (!body.password || body.password.length < 8) return new Response("password: 8+ characters", { status: 400 });
    this.setMeta("hid", hid);
    if (!this.meta("key")) this.setMeta("key", randomKey());
    this.setMeta("pw", await hashPassword(body.password));
    this.setMeta("tz", body.tz || "UTC");
    await this.wake();
    return Response.json({ ok: true, household: `${hid}.${this.env.ZONE}` });
  }

  async authorised(request: Request): Promise<boolean> {
    const h = request.headers.get("Authorization") || "";
    if (!h.startsWith("Basic ")) return false;
    let decoded = "";
    try { decoded = atob(h.slice(6)); } catch { return false; }
    const pw = decoded.slice(decoded.indexOf(":") + 1);
    return (await hashPassword(pw, this.meta("pw") || "")) === this.meta("pw");
  }

  // -- the household's server -------------------------------------------------
  async server() {
    const stub = this.env.SERVER.getByName(this.meta("hid")!);
    await stub.configure({
      FEATHERFRAME_HOSTED_URL: `https://${this.meta("hid")}.${this.env.ZONE}/_internal`,
      FEATHERFRAME_HOSTED_KEY: this.meta("key")!,
      TZ: this.meta("tz") || "UTC",
    });
    return stub;
  }

  async proxy(request: Request): Promise<Response> {
    const stub = await this.server();
    const headers = new Headers(request.headers);
    headers.delete("Authorization");
    headers.delete("X-FF-Household");
    return stub.fetch(new Request(request, { headers }));
  }

  /** Start the server (its lifespan pulls), run one tick (which reports),
   * then sleep until it next has something to do. */
  async wake(): Promise<void> {
    try {
      const stub = await this.server();
      await stub.fetch("http://server/api/hosted/run", { method: "POST" });
    } catch (err) {
      console.error("wake failed", err);
    }
    await this.schedule();
  }

  async alarm(): Promise<void> {
    await this.wake();
  }

  async schedule(): Promise<void> {
    // The server's own next moment, and — unless it said a detection could
    // change nothing now (quiet hours) — a look for new detections.
    const now = Date.now();
    const next = Number(this.meta("next_wake_epoch") || 0) * 1000;
    const times = [next > now ? next : 0, this.meta("poll") === "0" ? 0 : now + WAKE_CADENCE_MS]
      .filter((t) => t > 0);
    // Nothing named at all: look again in a day rather than never.
    await this.ctx.storage.setAlarm(times.length ? Math.min(...times) : now + 86_400_000);
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
    if (!row || row.status !== "on") {
      this.queueCheckin(request, id, "403", null);
      return new Response("this frame has not been added here", {
        status: 403,
        headers: { "Cache-Control": "no-store", "X-FF-Frame": row?.status === "ignored" ? "ignored" : "pending" },
      });
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
    await this.schedule();
  }
}

// ---------------------------------------------------------------- helpers
function randomKey(): string {
  const b = crypto.getRandomValues(new Uint8Array(32));
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

/** "salt$hash", PBKDF2-SHA256. With `stored`, hashes with its salt. */
async function hashPassword(pw: string, stored = ""): Promise<string> {
  const salt = stored.includes("$") ? stored.split("$")[0] : randomKey().slice(0, 32);
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: new TextEncoder().encode(salt), iterations: 100_000 }, key, 256);
  return `${salt}$${[...new Uint8Array(bits)].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

/** Now as the household's server reads a clock: naive ISO in its own zone. */
export function localIso(tz: string, d = new Date()): string {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
}
