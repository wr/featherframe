// Setting up a frame from the phone (W-888). A frame no one has claimed
// shows a QR code of its setup page, /setup/<code>/<token>: the pairing
// code it shows plus a secret only the QR carries. The page makes the
// account (an email; a setup code unless the kit was registered when it was
// flashed for shipping), picks a BirdWeather station near the phone, claims
// the frame and signs the phone in. An email that already has an account is
// sent a link that adds the frame instead.

import type { Env } from "./index";
import { makeLoginLink, normEmail, rateHit, sendMail, sendVerification, sessionUser, signedIn } from "./accounts";
import { addFrameEmail, loginPage, setupAddPage, setupExpiredPage, setupLimitedPage, setupLinkSentPage, setupPage,
         welcomeEmail } from "./pages";
import { SETUP_TOKEN_LEN, setupToken, setupUrl } from "./pairing";
import { geocode, regionFor, stationById, stationsNear } from "./stations";
import { randomHex, sha256, validTz } from "./util";

const now = () => Math.floor(Date.now() / 1000);

export const SETUP_PER_IP = 5;          // setups tried an hour from one IP
export const SETUP_VIEWS_PER_IP = 60;   // page and station lookups an hour
export const LINKS_PER_ADDRESS = 5;     // add-this-frame emails an hour to one address
const SETUP_CODE_ALPHABET = "ABCDEFGHJKMNPRSTWXYZ";
const SETUP_CODE_LEN = 8;

const PATH = new RegExp(`^/setup/([A-Za-z]{6})/([0-9A-Za-z]{${SETUP_TOKEN_LEN}})/?$`);

interface PairingRow { code: string; device_id: string; key_hash: string; report: string; setup_token: string }

/** "ABCD-EFGH", "abcdefgh ", …: the eight letters, or "". */
export function normSetupCode(raw: unknown): string {
  const s = String(raw || "").toUpperCase().replace(/[^A-Z]/g, "");
  return s.length === SETUP_CODE_LEN ? s : "";
}

export function newSetupCode(): string {
  const out: string[] = [];
  const cap = 256 - (256 % SETUP_CODE_ALPHABET.length);
  while (out.length < SETUP_CODE_LEN) {
    for (const b of crypto.getRandomValues(new Uint8Array(16))) {
      if (b < cap && out.length < SETUP_CODE_LEN) out.push(SETUP_CODE_ALPHABET[b % SETUP_CODE_ALPHABET.length]);
    }
  }
  return out.join("");
}

export function isSetupPath(path: string): boolean {
  return PATH.test(path) || path === "/api/setup/stations" || path === "/setup";
}

export const CODE_TRIES_PER_HOUR = 20;   // typed codes from one IP (W-891)

/** A code typed on the sign-in page (W-891): the same setup page the QR on
 * that frame opens. The six letters are guessable where the QR's secret is
 * not, so tries are limited per IP, a minute (the router) and an hour (here). */
async function typedCode(request: Request, env: Env, ip: string): Promise<Response> {
  if (request.method !== "POST") return Response.redirect(`https://${env.APP_HOST}/login`, 303);
  const origin = request.headers.get("Origin");
  if (origin && origin !== `https://${env.APP_HOST}`) return new Response("forbidden", { status: 403 });
  const form = await request.formData();
  const typed = String(form.get("code") || "");
  const code = typed.toUpperCase().replace(/[^A-Z]/g, "");
  if (!(await rateHit(env, `setup:code:${ip}`, CODE_TRIES_PER_HOUR, 3600))) {
    return loginPage("", "Too many tries. Try again in an hour, or scan the QR code on the frame.", typed);
  }
  if (code.length !== 6) return loginPage("", "A code is six letters, like ABC-DEF.", typed);
  const row = await env.DB.prepare("SELECT code, setup_token FROM pairing WHERE code = ? AND expires_at > ?")
    .bind(code, now()).first<{ code: string; setup_token: string | null }>();
  if (!row) return loginPage("", "No frame is showing that code. Check the code on your frame's screen.", typed);
  let token = row.setup_token;
  if (!token) {
    token = setupToken();
    await env.DB.prepare("UPDATE pairing SET setup_token = ? WHERE code = ?").bind(token, row.code).run();
  }
  return Response.redirect(setupUrl(env.APP_HOST, row.code, token), 303);
}

function clientIp(request: Request): string {
  return request.headers.get("CF-Connecting-IP") || "unknown";
}

async function livePairing(env: Env, code: string, token: string): Promise<PairingRow | null> {
  return env.DB.prepare(
    "SELECT code, device_id, key_hash, report, setup_token FROM pairing WHERE code = ? AND lower(setup_token) = ? AND expires_at > ?")
    .bind(code.toUpperCase(), token.toLowerCase(), now()).first<PairingRow>();
}

/** The frame showing `row`'s code joins household `hid`: the code is taken
 * (only one claim can take it) and the frame registered. False when someone
 * else took it first. */
async function claim(env: Env, row: PairingRow, hid: string): Promise<boolean> {
  const taken = await env.DB.prepare("DELETE FROM pairing WHERE code = ? AND device_id = ? AND key_hash = ?")
    .bind(row.code, row.device_id, row.key_hash).run();
  if (!taken.meta.changes) return false;
  await env.DB.prepare("INSERT OR REPLACE INTO frames (device_id, household_id, key_hash, paired_at) VALUES (?, ?, ?, ?)")
    .bind(row.device_id, hid, row.key_hash, now()).run();
  return true;
}

/** A sign-in link's "add this frame" (`CODE:device`), followed: the frame is
 * added if it still shows that code. */
export async function pairLinked(env: Env, pairCode: string, hid: string): Promise<boolean> {
  const [code, device] = pairCode.split(":");
  const row = await env.DB.prepare(
    "SELECT code, device_id, key_hash, report, setup_token FROM pairing WHERE code = ? AND device_id = ? AND expires_at > ?")
    .bind(code, device, now()).first<PairingRow>();
  if (!row || !(await claim(env, row, hid))) return false;
  await env.HOUSEHOLD.getByName(hid).adopt(row.device_id, JSON.parse(row.report || "{}"));
  return true;
}

async function registeredKit(env: Env, row: PairingRow): Promise<boolean> {
  return !!(await env.DB.prepare("SELECT 1 FROM kits WHERE device_id = ? AND key_hash = ? AND used_at IS NULL")
    .bind(row.device_id, row.key_hash).first());
}

type Cf = { country?: string; postalCode?: string; city?: string; latitude?: string; longitude?: string };
const cfOf = (request: Request): Cf => (request as { cf?: Cf }).cf || {};

function miles(request: Request): boolean {
  return cfOf(request).country === "US";
}

/** What the page starts with in its place field: the phone's network's own
 * postal code (or town), shown so a wrong guess is plain to see. */
function placeGuess(request: Request): string {
  const cf = cfOf(request);
  return (cf.postalCode || cf.city || "").slice(0, 40);
}

const SOURCES = ["birdweather", "apprise", "birdnet_go"];


export async function setupRoute(request: Request, env: Env, url: URL, ctx: ExecutionContext): Promise<Response> {
  const ip = await sha256(clientIp(request));
  if (url.pathname === "/api/setup/stations") return stations(request, env, url, ip);
  if (url.pathname === "/setup") return typedCode(request, env, ip);
  const m = url.pathname.match(PATH)!;
  const [code, token] = [m[1].toUpperCase(), m[2].toLowerCase()];
  if (request.method === "GET") {
    if (!(await rateHit(env, `setup:view:${ip}`, SETUP_VIEWS_PER_IP, 3600))) return setupLimitedPage();
    const row = await livePairing(env, code, token);
    if (!row) return setupExpiredPage();
    const user = await sessionUser(request, env);
    if (user?.hid) return setupAddPage(code, token, user.email);
    return setupPage({ code, token, needsCode: !(await registeredKit(env, row)), miles: miles(request),
                       place: placeGuess(request), country: cfOf(request).country || "" });
  }
  if (request.method !== "POST") return new Response(null, { status: 405 });
  const origin = request.headers.get("Origin");
  if (origin && origin !== `https://${env.APP_HOST}`) return new Response("forbidden", { status: 403 });
  if (!(await rateHit(env, `setup:post:${ip}`, SETUP_PER_IP, 3600))) return setupLimitedPage();
  const row = await livePairing(env, code, token);
  if (!row) return setupExpiredPage();

  // Signed in: this frame joins the account, as Pair a frame does.
  const user = await sessionUser(request, env);
  if (user?.hid) {
    if (!(await claim(env, row, user.hid))) return setupExpiredPage();
    await env.HOUSEHOLD.getByName(user.hid).adopt(row.device_id, JSON.parse(row.report || "{}"));
    return Response.redirect(`https://${env.APP_HOST}/?paired=1`, 303);
  }

  const form = await request.formData();
  const email = normEmail(form.get("email"));
  const typed = String(form.get("setup_code") || "");
  const setupCode = normSetupCode(typed);
  const kit = await registeredKit(env, row);
  const source = SOURCES.includes(String(form.get("source"))) ? String(form.get("source")) : "birdweather";
  const again = (error: string) => setupPage({ code, token, needsCode: !kit, error, email: String(form.get("email") || ""),
                                               setupCode: typed, miles: miles(request), source,
                                               place: placeGuess(request), country: cfOf(request).country || "" });
  if (!email) return again("Enter an email address.");
  // The invitation first: without one, nothing is said about the email.
  if (!kit) {
    if (!typed.trim()) return again("Enter the setup code from the card in the box.");
    const ok = setupCode && await env.DB.prepare("SELECT 1 FROM setup_codes WHERE code = ? AND used_at IS NULL")
      .bind(setupCode).first();
    if (!ok) return again("Check the setup code on the card in the box.");
  }

  const tz = validTz(String(form.get("tz") || ""));
  const known = await env.DB.prepare("SELECT 1 FROM users WHERE email = ?").bind(email).first();
  if (known) {
    // Their account, their inbox: the link adds the frame once followed.
    if (await rateHit(env, `setup:link:${await sha256(email)}`, LINKS_PER_ADDRESS, 3600)) {
      const link = await makeLoginLink(env, email, tz, `${row.code}:${row.device_id}`);
      if (link) ctx.waitUntil(sendMail(env, email, addFrameEmail(link, row.device_id.slice(-6))));
    }
    return setupLinkSentPage(email);
  }

  // A new account. The invitation is taken first, and given back if the
  // frame's code was taken by someone else in the meantime.
  const hid = randomHex(8);
  const uid = randomHex(8);
  const t = now();
  const use = kit
    ? env.DB.prepare("UPDATE kits SET used_at = ?, household_id = ? WHERE device_id = ? AND key_hash = ? AND used_at IS NULL")
        .bind(t, hid, row.device_id, row.key_hash)
    : env.DB.prepare("UPDATE setup_codes SET used_at = ?, household_id = ? WHERE code = ? AND used_at IS NULL")
        .bind(t, hid, setupCode);
  const release = kit
    ? env.DB.prepare("UPDATE kits SET used_at = NULL, household_id = NULL WHERE device_id = ? AND household_id = ?")
        .bind(row.device_id, hid)
    : env.DB.prepare("UPDATE setup_codes SET used_at = NULL, household_id = NULL WHERE code = ? AND household_id = ?")
        .bind(setupCode, hid);
  if (!(await use.run()).meta.changes) return kit ? setupExpiredPage() : again("Check the setup code on the card in the box.");
  try {
    await env.DB.batch([
      env.DB.prepare("INSERT INTO households (id, tz, created_at) VALUES (?, ?, ?)").bind(hid, tz, t),
      env.DB.prepare("INSERT INTO users (id, email, household_id, created_at) VALUES (?, ?, ?, ?)").bind(uid, email, hid, t),
    ]);
  } catch {
    // The same email set up a moment ago.
    await release.run();
    return setupLinkSentPage(email);
  }
  if (!(await claim(env, row, hid))) {
    await env.DB.batch([
      release,
      env.DB.prepare("DELETE FROM users WHERE id = ?").bind(uid),
      env.DB.prepare("DELETE FROM households WHERE id = ?").bind(hid),
    ]);
    return setupExpiredPage();
  }
  // Anyone on the waitlist with this email is in now.
  await env.DB.prepare("UPDATE waitlist SET invited_at = coalesce(invited_at, ?) WHERE email = ?").bind(t, email).run();

  // The detection source: a BirdWeather station, or the owner's own
  // detector, which the page's Detection source section then walks through.
  const station = source === "birdweather" ? await chosenStation(String(form.get("station") || "")) : null;
  const seed: Record<string, string> | null = station
    ? { detection_backend: "birdweather", birdweather_station_id: station.id,
        ...(regionFor(station.continent) ? { region: regionFor(station.continent)! } : {}) }
    : source !== "birdweather" ? { detection_backend: source } : null;
  await env.HOUSEHOLD.getByName(hid).setUp(hid, tz, seed, row.device_id, JSON.parse(row.report || "{}"));

  const km = Number(form.get("km"));
  const distance = !station || !Number.isFinite(km) || String(form.get("km") || "") === "" ? ""
    : miles(request) ? `${Math.round(km * 0.621371)} mi` : `${Math.round(km)} km`;
  ctx.waitUntil(sendMail(env, email, welcomeEmail(station ? { name: station.name, distance } : null)));
  // A separate email proves the address; until then the page asks for it.
  ctx.waitUntil(sendVerification(env, uid, email));
  return signedIn(env, uid, source === "birdweather" ? "/?welcome=1" : "/?welcome=1&open=source");
}

async function chosenStation(id: string): Promise<{ id: string; name: string; continent: string } | null> {
  const s = await stationById(id);
  return s ? { id: String(s.id), name: (s.name || "").trim() || `Station ${s.id}`, continent: s.continent || "" } : null;
}

async function stations(request: Request, env: Env, url: URL, ip: string): Promise<Response> {
  const code = url.searchParams.get("c") || "";
  const token = url.searchParams.get("t") || "";
  if (!(await rateHit(env, `setup:view:${ip}`, SETUP_VIEWS_PER_IP, 3600))) {
    return Response.json({ stations: [] }, { status: 429 });
  }
  // Only for a frame waiting to be set up: this is not an open proxy.
  if (!/^[A-Za-z]{6}$/.test(code) || !(await livePairing(env, code, token))) {
    return Response.json({ stations: [] }, { status: 404 });
  }
  const lat = Number(url.searchParams.get("lat"));
  const lon = Number(url.searchParams.get("lon"));
  const given = url.searchParams.has("lat") && Number.isFinite(lat) && Math.abs(lat) <= 90
    && Number.isFinite(lon) && Math.abs(lon) <= 180 ? { lat, lon } : null;
  const typed = (url.searchParams.get("place") || "").trim();
  let at = given;
  let label = "";
  if (!at && typed) {
    const found = await geocode(typed, cfOf(request).country || "");
    if (!found) return Response.json({ stations: [], found: false });
    at = { lat: found.lat, lon: found.lon };
    label = found.label;
  }
  if (!at) return Response.json({ stations: [] });
  const list = await stationsNear(at.lat, at.lon);
  return Response.json({
    stations: list.map((st) => ({ id: st.id, name: st.name, km: st.km, species: st.species, state: st.state })),
    place: label,
  });
}
