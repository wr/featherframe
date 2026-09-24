// Viewers on hosted Featherframe (W-849): a TRMNL, an e-reader running one of
// TRMNL's clients, a tablet on the /view page. Each is known by its ID plus a
// key of its own — a TRMNL's access token (handed out here by /api/setup), or
// the key the page keeps in localStorage — exactly as a kit is by X-FF-Key.
//
// One no household has claimed is shown a pairing code: on its image (TRMNL,
// e-readers; drawn by the Lobby at its own size) or in the page's HTML. Once
// its owner types the code, it is its household's: every ask goes to that
// household's front door, which answers from the images its server drew
// ahead of time, so a tablet asking every 20 s never wakes the server.
//
// An image URL is a capability, since a device fetches it bare: it carries
// the first 32 hex of the hash of the device's key (`t`), never the key.

import type { Env } from "./index";
import { LOBBY_DRAWING, pairingCode } from "./pairing";
import { randomHex, sha256 } from "./util";
import viewPage from "../../server/templates/view.html";
import favicon192 from "../../server/static/favicon-192.png";
import touchIcon from "../../server/static/apple-touch-icon.png";

// While a viewer waits to be claimed it asks this often (a TRMNL on battery
// too: pairing is a minute or two, once).
const PAIRING_REFRESH_S = 30;
const PAGE_POLL_S = 20;           // viewers.PAGE_POLL_SECONDS
const ID_RE = /^[0-9A-Za-z:._-]{1,40}$/;

const API = /^\/api\/(setup|display|log|view\/state)$/;
const IMAGE = /^\/api\/viewers\/([^/]+)\/([^/]+)\.png$/;
const PAGE = /^\/(view|view\.webmanifest)$/;

export function isViewerPath(path: string): boolean {
  return API.test(path) || IMAGE.test(path) || PAGE.test(path);
}

/** As the server's viewers.clean_id: its registry key. */
export function cleanId(v: string | null | undefined): string | null {
  const s = (v || "").trim();
  return ID_RE.test(s) ? s.toUpperCase() : null;
}

const cleanKey = (v: string | null | undefined) => {
  const s = (v || "").trim();
  return /^[\x21-\x7e]{8,128}$/.test(s) ? s : "";
};

/** The digits a size starts with, as the device said it. */
const num = (v: string | null | undefined, max = 5) => ((v || "").match(/^\d+/)?.[0] || "").slice(0, max);

export const imageToken = (keyHash: string) => keyHash.slice(0, 32);

/** The last six characters of an id, as the server's frame_short. */
export const shortOf = (id: string) => id.slice(-6);

type Claim = { household_id: string; key_hash: string };

async function claimOf(env: Env, id: string): Promise<Claim | null> {
  return env.DB.prepare("SELECT household_id, key_hash FROM frames WHERE device_id = ?")
    .bind(id).first<Claim>();
}

/** To its household's front door, with what the Worker has checked. */
function toHousehold(env: Env, hid: string, id: string, keyHash: string, request: Request): Promise<Response> {
  const headers = new Headers(request.headers);
  headers.set("X-FF-Household", hid);
  headers.set("X-FF-Viewer", id);
  headers.set("X-FF-Viewer-Token", imageToken(keyHash));
  return env.HOUSEHOLD.getByName(hid).fetch(new Request(request, { headers }));
}

export async function viewerRoute(request: Request, env: Env, url: URL,
                                  ctx: ExecutionContext): Promise<Response> {
  const path = url.pathname;
  if (path === "/view") return servePage();
  if (path === "/view.webmanifest") return manifest();
  if (path === "/api/log") return new Response(null, { status: 204 });
  if (path === "/api/setup") return setup(request, env);

  const image = path.match(IMAGE);
  if (image) return imageRoute(request, env, decodeURIComponent(image[1]), image[2], url.searchParams.get("t") || "", ctx);

  // /api/display (a TRMNL client) or /api/view/state (the page).
  const page = path === "/api/view/state";
  let id = cleanId(page ? url.searchParams.get("viewer") : request.headers.get("ID"));
  const key = cleanKey(page ? url.searchParams.get("key") : request.headers.get("Access-Token"));
  if (!key) {
    // A page from before W-849 (no key yet) reloads itself within 12 h; a
    // TRMNL without a token has not been set up.
    return page ? Response.json({ image: null, dark: false, waiting: true, id: id ? shortOf(id) : "", poll: PAGE_POLL_S })
                : Response.json({ status: 404, error: "Set up first." }, { status: 404 });
  }
  const keyHash = await sha256(key);
  if (!id && !page) {
    // TRMNL's shell clients may send only their token: find them by it.
    const row = await env.DB.prepare("SELECT device_id FROM frames WHERE key_hash = ?").bind(keyHash)
      .first<{ device_id: string }>();
    id = row?.device_id ?? null;
    if (!id) return Response.json({ status: 404, error: "An ID header is required." }, { status: 404 });
  }
  if (!id) return Response.json({ error: "viewer, w and h are required" }, { status: 400 });

  const claim = await claimOf(env, id);
  if (claim && claim.key_hash === keyHash) return toHousehold(env, claim.household_id, id, keyHash, request);

  // No household has it: its pairing code.
  const report = page
    ? { transport: "page", w: num(url.searchParams.get("w")), h: num(url.searchParams.get("h")),
        device: (url.searchParams.get("device") || "").slice(0, 40) }
    : { transport: "trmnl", headers: trmnlHeaders(request) };
  const code = await pairingCode(env, id, keyHash, report);
  const shown = `${code.slice(0, 3)}-${code.slice(3)}`;
  if (page) {
    return Response.json({ image: null, dark: false, waiting: true, id: shortOf(id), code: shown,
                           poll: PAGE_POLL_S }, { headers: { "Cache-Control": "no-store" } });
  }
  const h = (report as { headers: Record<string, string> }).headers;
  const name = `pair-${code}-${(await sha256(LOBBY_DRAWING + JSON.stringify(h))).slice(0, 8)}`;
  return Response.json(display(env, id, name, imageToken(keyHash), PAIRING_REFRESH_S));
}

/** The headers a TRMNL client describes itself with (viewers.trmnl_report). */
export function trmnlHeaders(request: Request): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of ["model", "fw-version", "width", "height", "battery-voltage", "percent-charged", "rssi"]) {
    const v = request.headers.get(k);
    if (v) out[k] = v.slice(0, 40);
  }
  return out;
}

/** /api/display's answer, as the server's own. */
export function display(env: Env, id: string, name: string, token: string, refresh: number) {
  return {
    status: 0, filename: name, image_url_timeout: 0, refresh_rate: refresh,
    image_url: `https://${env.APP_HOST}/api/viewers/${encodeURIComponent(id)}/${name}.png?t=${token}`,
    update_firmware: false, firmware_url: null, reset_firmware: false, special_function: "none",
  };
}

/** A TRMNL's first ask: a key of its own. A device asking again (reset, or
 * moved from another server) gets a new one, and pairs again. */
async function setup(request: Request, env: Env): Promise<Response> {
  const id = cleanId(request.headers.get("ID"));
  if (!id) {
    return Response.json({ status: 404, api_key: "", friendly_id: "", image_url: "",
                           message: "An ID header (the device's MAC) is required." }, { status: 404 });
  }
  const key = randomHex(16);
  await pairingCode(env, id, await sha256(key), { transport: "trmnl", headers: trmnlHeaders(request) });
  return Response.json({ status: 200, api_key: key, friendly_id: id.replace(/:/g, "").slice(-6), image_url: "",
                         message: "Welcome to Featherframe" });
}

async function imageRoute(request: Request, env: Env, rawId: string, name: string, t: string,
                          ctx: ExecutionContext): Promise<Response> {
  const id = cleanId(rawId);
  if (!id || !/^[0-9a-f]{32}$/.test(t)) return new Response("not found", { status: 404 });
  const claim = await claimOf(env, id);
  if (claim && imageToken(claim.key_hash) === t) return toHousehold(env, claim.household_id, id, claim.key_hash, request);
  // Its pairing code, while it waits.
  const m = name.match(/^pair-([A-Z]{6})-/);
  if (!m) return new Response("not found", { status: 404 });
  const row = await env.DB.prepare(
    "SELECT report FROM pairing WHERE code = ? AND device_id = ? AND substr(key_hash, 1, 32) = ? AND expires_at > ?")
    .bind(m[1], id, t, Math.floor(Date.now() / 1000)).first<{ report: string }>();
  if (!row) return new Response("not found", { status: 404 });
  const h = (JSON.parse(row.report || "{}").headers || {}) as Record<string, string>;
  return lobbyPng(env, `${m[1].slice(0, 3)}-${m[1].slice(3)}`, h, name, (p) => ctx.waitUntil(p));
}

/** A viewer's pairing code (or, with no code, the waiting plate) drawn by the
 * Lobby for its own screen, cached in R2 by name. */
export async function lobbyPng(env: Env, code: string, h: Record<string, string>, name: string,
                               keep: (p: Promise<unknown>) => void): Promise<Response> {
  const cacheKey = `lobby/viewers/${name}.png`;
  let obj = await env.DATA.get(cacheKey);
  if (!obj) {
    const q = new URLSearchParams({ code, w: h.width || "", h: h.height || "", model: h.model || "" });
    // Kept even if the device stops waiting (as a kit's code, index.ts).
    const drawn = (async () => {
      const r = await env.LOBBY.getByName("lobby").fetch(`http://lobby/render-view?${q}`);
      if (!r.ok) return false;
      await env.DATA.put(cacheKey, await r.arrayBuffer());
      return true;
    })();
    keep(drawn);
    if (!(await drawn)) return new Response("pairing screen unavailable", { status: 503 });
    obj = await env.DATA.get(cacheKey);
    if (!obj) return new Response("pairing screen unavailable", { status: 503 });
  }
  return new Response(obj.body, { headers: { "Content-Type": "image/png", ETag: `"${name}"`, "Cache-Control": "no-cache" } });
}

// -- the page itself: bundled here, so opening it wakes nothing ----------------------
function servePage(): Response {
  return new Response(viewPage.replace("{{ poll_seconds }}", String(PAGE_POLL_S)),
    { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache" } });
}

function manifest(): Response {
  return Response.json({
    name: "Featherframe", short_name: "Featherframe", start_url: "/view",
    display: "fullscreen", orientation: "any", background_color: "#ffffff", theme_color: "#ffffff",
    icons: [{ src: "/static/favicon-192.png", sizes: "192x192", type: "image/png" }],
  }, { headers: { "Content-Type": "application/manifest+json" } });
}

/** The page's two icons, for a browser with no session (a tablet on a wall). */
export function pageIcon(path: string): Response | null {
  const body = path === "/static/favicon-192.png" ? favicon192
    : path === "/static/apple-touch-icon.png" ? touchIcon : null;
  return body ? new Response(body, { headers: { "Content-Type": "image/png", "Cache-Control": "max-age=86400" } }) : null;
}
