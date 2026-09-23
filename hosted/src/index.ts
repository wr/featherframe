// Featherframe, hosted (W-841).
//
// One host, app.featherframe.app (W-845). The apex is the marketing page and
// is not routed here; plates.featherframe.app is the plate library's bucket.
//
//   the page      the signed-in owner's household (session cookie → D1)
//   a frame       the household it is paired to (X-Device-Id + its own key,
//                 X-FF-Key → D1); a frame no one has claimed is shown a
//                 pairing code, drawn by the Lobby Container
//   _internal     a household's server talking to its front door
//
// Per household: Household (household.ts), the front door that answers the
// frames while the server sleeps, and HouseholdServer (containers.ts), the
// box's own Python server in a Container.

import { admin, auth, login, logout, sessionHousehold } from "./accounts";
import { adminRoute, waitlistRoute } from "./admin";
import { isViewerPath, pageIcon, viewerRoute } from "./viewers";
import { pairingCode } from "./pairing";
import { Household } from "./household";
import { HouseholdServer, Lobby } from "./containers";
import { deviceId, frameKey, sha256 } from "./util";

export { Household, HouseholdServer, Lobby };

export interface Env {
  HOUSEHOLD: DurableObjectNamespace<Household>;
  SERVER: DurableObjectNamespace<HouseholdServer>;
  LOBBY: DurableObjectNamespace<Lobby>;
  DATA: R2Bucket;
  PLATES: R2Bucket;
  DB: D1Database;
  ZONE: string;           // featherframe.app
  APP_HOST: string;       // app.featherframe.app
  MAIL_FROM: string;
  ADMIN_TOKEN: string;    // secret
  ADMIN_EMAILS: string;   // secret: who sees /admin, comma separated
  RESEND_API_KEY: string; // secret
}

const FRAME_PATHS = /^\/api\/(frame|frame\/push|firmware)$/;
// While it waits to be claimed a frame asks this often, so pairing shows at once.
const PAIRING_POLL_S = 10;

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.hostname === `plates.${env.ZONE}`) return plates(request, env, url);
    if (url.hostname !== env.APP_HOST) return new Response("not found", { status: 404 });
    const path = url.pathname;

    // The script face and the page's icons are the same bytes for everyone:
    // served here, so a tablet on /view (no session) has them, and a page
    // load never wakes a server for them.
    if (path === "/_ff/script.ttf" || path === "/fonts/script.ttf") {
      return plates(request, env, new URL("/assets/script.ttf", url));
    }
    const icon = pageIcon(path);
    if (icon) return icon;
    if (path === "/login") return login(request, env);
    if (path === "/auth") return auth(request, env, url);
    if (path === "/logout" && request.method === "POST") return logout(request, env);
    if (path.startsWith("/_admin/")) return admin(request, env, path.slice("/_admin/".length));
    if (path === "/admin" || path.startsWith("/admin/")) return adminRoute(request, env, url);
    if (path === "/api/waitlist") return waitlistRoute(request, env);

    const internal = path.match(/^\/_internal\/([0-9a-z]{1,32})\//);
    if (internal) return toHousehold(env, internal[1], request);

    if (FRAME_PATHS.test(path)) return frame(request, env, url);
    if (isViewerPath(path)) return viewerRoute(request, env, url);

    const apprise = path.match(/^\/api\/ingest\/apprise\/([^/]+)$/);
    if (apprise && request.method === "POST") {
      const row = await env.DB.prepare("SELECT id FROM households WHERE apprise_token = ?")
        .bind(decodeURIComponent(apprise[1])).first<{ id: string }>();
      return row ? toHousehold(env, row.id, request) : Response.json({ error: "bad token" }, { status: 403 });
    }

    const hid = await sessionHousehold(request, env);
    if (!hid) {
      if (request.method === "GET" && (request.headers.get("Accept") || "").includes("text/html")) {
        return Response.redirect(`https://${env.APP_HOST}/login`, 303);
      }
      return Response.json({ error: "sign in" }, { status: 401 });
    }
    if (path === "/api/pair" && request.method === "POST") return pair(request, env, hid);
    if (path === "/api/pair/usb" && request.method === "POST") return pairUsb(request, env, hid);
    return toHousehold(env, hid, request);
  },
} satisfies ExportedHandler<Env>;

/** Hand a request to its household's front door, saying whose it is. */
function toHousehold(env: Env, hid: string, request: Request): Promise<Response> {
  const headers = new Headers(request.headers);
  headers.set("X-FF-Household", hid);         // set here, never taken from the client
  return env.HOUSEHOLD.getByName(hid).fetch(new Request(request, { headers }));
}

async function plates(request: Request, env: Env, url: URL): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") return new Response(null, { status: 405 });
  const obj = await env.PLATES.get(url.pathname.replace(/^\/+/, ""));
  if (!obj) return new Response("not found", { status: 404 });
  const h = new Headers();
  obj.writeHttpMetadata(h);
  h.set("ETag", obj.httpEtag);
  return new Response(request.method === "HEAD" ? null : obj.body, { headers: h });
}

// -- frames ------------------------------------------------------------------
async function frame(request: Request, env: Env, url: URL): Promise<Response> {
  const id = deviceId(request);
  const key = frameKey(request);
  if (id) {
    const row = await env.DB.prepare("SELECT household_id, key_hash FROM frames WHERE device_id = ?")
      .bind(id).first<{ household_id: string; key_hash: string | null }>();
    // Its own key, or a frame paired before it had one.
    if (row && (!row.key_hash || (key && row.key_hash === await sha256(key)))) {
      return toHousehold(env, row.household_id, request);
    }
  }
  // No household has this frame (or this is not the frame it claims to be).
  if (url.pathname === "/api/frame" && request.method === "GET" && id && key && !url.searchParams.get("view")) {
    return pairingScreen(request, env, id, key);
  }
  if (url.pathname === "/api/firmware") return new Response(null, { status: 304 });
  return new Response("this frame has not been added here", {
    status: 403, headers: { "Cache-Control": "no-store", "X-FF-Frame": "pending" },
  });
}

/** What a frame no one has claimed is shown: its pairing code, drawn for its
 * own panel by the Lobby (cached in R2, one per code and panel). */
async function pairingScreen(request: Request, env: Env, id: string, key: string): Promise<Response> {
  const keyHash = await sha256(key);
  const report: Record<string, string> = {};
  request.headers.forEach((v, k) => {
    if (k.startsWith("x-panel") || k === "x-board" || k === "x-ff-rotation") report[k] = v;
  });
  const row = { code: await pairingCode(env, id, keyHash, report) };
  const shown = `${row.code.slice(0, 3)}-${row.code.slice(3)}`;
  const variant = (await sha256(JSON.stringify(report))).slice(0, 16);
  const cacheKey = `lobby/${row.code}/${variant}.fff`;
  const etag = `pair-${row.code}-${variant.slice(0, 8)}`;
  const headers = new Headers({
    ETag: `"${etag}"`, "Cache-Control": "no-cache",
    "X-Poll-Seconds": String(PAIRING_POLL_S), "X-FF-Pair-Code": shown,
  });
  const inm = (request.headers.get("If-None-Match") || "").replace(/"/g, "").trim();

  let obj = await env.DATA.get(cacheKey);
  if (!obj) {
    const q = new URLSearchParams({
      code: shown, panel: report["x-panel"] || "", w: report["x-panel-width"] || "",
      h: report["x-panel-height"] || "", fmt: report["x-panel-format"] || "",
      rot: report["x-panel-rotations"] || "", cur: report["x-ff-rotation"] || "",
    });
    const r = await env.LOBBY.getByName("lobby").fetch(`http://lobby/render?${q}`);
    if (!r.ok) return new Response("pairing screen unavailable", { status: 503, headers });
    await env.DATA.put(cacheKey, await r.arrayBuffer(),
      { customMetadata: { rotation: r.headers.get("X-FF-Rotation") || "" } });
    obj = await env.DATA.get(cacheKey);
    if (!obj) return new Response("pairing screen unavailable", { status: 503, headers });
  }
  if (obj.customMetadata?.rotation) headers.set("X-FF-Rotation", obj.customMetadata.rotation);
  if (inm === etag) return new Response(null, { status: 304, headers });
  headers.set("Content-Type", "application/octet-stream");
  headers.set("Content-Length", String(obj.size));
  return new Response(obj.body, { headers });
}

/** A frame on this page's USB (W-848): the page asked it who it is over
 * Improv (and pointed it here); it is this household's, no code to type. */
async function pairUsb(request: Request, env: Env, hid: string): Promise<Response> {
  const origin = request.headers.get("Origin");
  if (origin && origin !== `https://${env.APP_HOST}`) {
    return Response.json({ error: "cross-origin request refused" }, { status: 403 });
  }
  const b = await request.json<Record<string, unknown>>().catch(() => ({} as Record<string, unknown>));
  const str = (k: string, max = 64) => String(b[k] ?? "").trim().slice(0, max);
  const id = str("id", 40);
  const key = str("key").toLowerCase();
  if (!/^[0-9A-Za-z:_-]{4,40}$/.test(id) || !/^[0-9a-f]{16,64}$/.test(key)) {
    return Response.json({ ok: false, error: "The frame did not say who it is." }, { status: 400 });
  }
  const now = Math.floor(Date.now() / 1000);
  await env.DB.batch([
    env.DB.prepare("INSERT OR REPLACE INTO frames (device_id, household_id, key_hash, paired_at) VALUES (?, ?, ?, ?)")
      .bind(id, hid, await sha256(key), now),
    env.DB.prepare("DELETE FROM pairing WHERE device_id = ?").bind(id),
  ]);
  // What it said about itself, as its own headers would: a new row starts
  // with its panel and the way up it hangs (W-851).
  const report: Record<string, string> = {
    "x-panel": str("panel"), "x-panel-width": str("w", 6), "x-panel-height": str("h", 6),
    "x-panel-format": str("fmt", 16), "x-panel-rotations": str("rots", 16),
    "x-ff-rotation": str("rotation", 4), "x-board": str("board"),
  };
  await env.HOUSEHOLD.getByName(hid).adopt(id, report);
  return Response.json({ ok: true, frame: id });
}

/** The owner typed the code on their frame's glass. */
async function pair(request: Request, env: Env, hid: string): Promise<Response> {
  const origin = request.headers.get("Origin");
  if (origin && origin !== `https://${env.APP_HOST}`) {
    return Response.json({ error: "cross-origin request refused" }, { status: 403 });
  }
  let code = "";
  if ((request.headers.get("Content-Type") || "").includes("json")) {
    code = String((await request.json<{ code?: string }>().catch(() => ({ code: "" }))).code || "");
  } else {
    code = String((await request.formData()).get("code") || "");
  }
  code = code.toUpperCase().replace(/[^A-Z]/g, "");
  if (code.length !== 6) return Response.json({ ok: false, error: "A code is six letters." }, { status: 400 });
  const now = Math.floor(Date.now() / 1000);
  const row = await env.DB.prepare("SELECT device_id, key_hash, report FROM pairing WHERE code = ? AND expires_at > ?")
    .bind(code, now).first<{ device_id: string; key_hash: string; report: string }>();
  if (!row) return Response.json({ ok: false, error: "No frame is showing that code." }, { status: 404 });
  await env.DB.batch([
    env.DB.prepare("INSERT OR REPLACE INTO frames (device_id, household_id, key_hash, paired_at) VALUES (?, ?, ?, ?)")
      .bind(row.device_id, hid, row.key_hash, now),
    env.DB.prepare("DELETE FROM pairing WHERE device_id = ?").bind(row.device_id),
  ]);
  await env.HOUSEHOLD.getByName(hid).adopt(row.device_id, JSON.parse(row.report || "{}"));
  return Response.json({ ok: true, frame: row.device_id });
}
