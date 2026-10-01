// The admin page (W-850): the waitlist, invitations, and every household at a
// glance. For a signed-in user whose email is in ADMIN_EMAILS (a secret, comma
// separated); to anyone else it does not exist. The bearer API
// (/_admin/<name>, the ADMIN_TOKEN secret) is the same actions for scripts.
//
// Every admin action is one entry in ACTIONS (W-914): what it is called, where
// it may be asked from, and what it did. Each lands in the audit log (W-863).

import type { Env } from "./index";
import { AS_COOKIE, actAs, adoptHousehold, deleteHousehold, invite, isAdmin, makeLoginLink, normEmail, realSessionUser,
         registerKit, resendInvite, revokeInvite, setEmail, setSuspended, signUpWaitlist, stopActingAs } from "./accounts";
import { adminPage, waitlistThanksPage, type AdminData, type Toast } from "./pages";
import { cloudflareUsage } from "./usage";
import { cookie } from "./util";

const notFound = () => new Response("not found", { status: 404 });

/** An action's fields: the page's form, or the API's JSON body. */
type Fields = Record<string, string>;
/** Who asked: an admin's own login (and household), or the API. */
type Who = { email: string; hid: string | null };
/** What an action did: said in the toast (or the API's answer) and the log. */
type Done = { ok: boolean; message: string; target: string | null;
              data?: Record<string, unknown>; response?: Response };
type Action = { page?: true; api?: true; run: (env: Env, f: Fields, who: Who) => Promise<Done> };

// -- the actions ---------------------------------------------------------------------
type Household = { id: string; email: string | null; name: string };

/** An action on the household named by the `id` field. */
function onHousehold(run: (env: Env, h: Household, f: Fields, who: Who) => Promise<Omit<Done, "target">>): Action["run"] {
  return async (env, f, who) => {
    const id = f.id || "";
    const row = await env.DB.prepare("SELECT u.email FROM households h LEFT JOIN users u ON u.household_id = h.id WHERE h.id = ?")
      .bind(id).first<{ email: string | null }>();
    if (!row) return { ok: false, message: "No such household.", target: id || null };
    const done = await run(env, { id, email: row.email, name: row.email || id }, f, who);
    return { ...done, target: row.email ? `${row.email} (${id})` : id };
  };
}

/** An action on the address in the `email` field. */
function onEmail(run: (env: Env, email: string, f: Fields) => Promise<Omit<Done, "target">>): Action["run"] {
  return async (env, f) => {
    const email = normEmail(f.email);
    if (!email) return { ok: false, message: "Enter an email address.", target: null };
    return { ...(await run(env, email, f)), target: email };
  };
}

const ok = (message: string, data?: Record<string, unknown>) => ({ ok: true, message, data });
const bad = (message: string, data?: Record<string, unknown>) => ({ ok: false, message, data });

const ACTIONS: Record<string, Action> = {
  "invite": { page: true, api: true, run: onEmail(async (env, email, f) => {
    const send = f.send === "1";
    const sent = await invite(env, email, send);
    return send && !sent ? bad(`Invited ${email}, but the email did not go.`, { invited: email, emailed: false })
      : ok(`Invited ${email}.`, { invited: email, emailed: sent });
  }) },
  "invite.resend": { page: true, run: onEmail(async (env, email) => {
    const sent = await resendInvite(env, email);
    return sent === "sent" ? ok(`Sent ${email} their invitation again.`)
      : bad(sent === "failed" ? `The email to ${email} did not go.` : `${email} has no open invitation.`);
  }) },
  "invite.revoke": { page: true, run: onEmail(async (env, email) =>
    await revokeInvite(env, email) ? ok(`Revoked ${email}'s invitation.`) : bad(`${email} has no open invitation.`)) },
  "waitlist.remove": { page: true, run: onEmail(async (env, email) => {
    await env.DB.prepare("DELETE FROM waitlist WHERE email = ?").bind(email).run();
    return ok(`Removed ${email} from the waitlist.`);
  }) },

  "household.as": { page: true, run: onHousehold(async (_env, h) =>
    h.email ? { ...ok(`Logged in as ${h.name}.`), response: actAs(h.id) } : bad(`${h.id} has no login to look through.`)) },
  "household.email": { page: true, run: onHousehold(async (env, h, f) => {
    const email = normEmail(f.email);
    if (!email) return bad("Enter an email address.");
    const set = await setEmail(env, h.id, email);
    return set === "ok" ? ok(`${h.name} now signs in as ${email}.`)
      : bad(set === "taken" ? `${email} already has a login.` : `${h.id} has no login.`);
  }) },
  "household.suspend": { page: true, run: onHousehold(async (env, h) => {
    await setSuspended(env, h.id, true);
    return ok(`Suspended ${h.name}.`);
  }) },
  "household.resume": { page: true, run: onHousehold(async (env, h) => {
    await setSuspended(env, h.id, false);
    return ok(`Resumed ${h.name}.`);
  }) },
  "household.delete": { page: true, run: onHousehold(async (env, h, f, who) => {
    // The admin's own login would go with it, and the admin page with that.
    if (h.id === who.hid) return bad("You can't delete your own household.");
    // Typed, not clicked: the household's email (or id) is given back.
    if ((f.confirm || "").trim().toLowerCase() !== h.name.toLowerCase()) return bad(`Not deleted: type ${h.name} to delete it.`);
    let gone: { frames: number; files: number };
    try {
      gone = await deleteHousehold(env, h.id);
    } catch (err) {
      console.error("delete household", h.id, err);
      return bad(`Deleting ${h.name} stopped part way: ${String(err)}. Delete it again to finish.`);
    }
    const parts = [
      gone.frames ? `${gone.frames} ${gone.frames === 1 ? "frame shows" : "frames show"} a pairing code` : "",
      gone.files ? `${gone.files} ${gone.files === 1 ? "file" : "files"} removed` : "",
    ].filter(Boolean);
    return ok(`Deleted ${h.name}${parts.length ? `: ${parts.join(", ")}` : ""}.`, gone);
  }) },
  "as.stop": { page: true, run: async () =>
    ({ ...ok("Back to your own page."), target: null, response: stopActingAs() }) },

  // The API's own. A sign-in link handed over rather than emailed: for
  // support, and for testing without sending anyone mail.
  "link": { api: true, run: onEmail(async (env, email) => {
    const link = await makeLoginLink(env, email, null);
    return link ? ok(`Made a sign-in link for ${email}.`, { link }) : bad(`${email} is not invited.`, { status: 404 });
  }) },
  "adopt": { api: true, run: async (env, f) => {
    const email = normEmail(f.email);
    const hid = f.household || "";
    const target = email && hid ? `${email} (${hid})` : null;
    if (!email || !/^[0-9a-z]{1,32}$/.test(hid)) return { ...bad("Give an email and a household id."), target };
    await adoptHousehold(env, hid, email);
    return { ...ok(`Gave ${hid} the login ${email}.`, { household: hid, email }), target };
  } },
  "kit": { api: true, run: async (env, f) => {
    const kit = await registerKit(env, f);
    return kit ? { ...ok(`Registered ${kit.kit} ${kit.id.slice(-6)}.`), target: kit.id }
      : { ...bad("Give device_id and key_hash (64 hex)."), target: f.device_id || null };
  } },
};

/** Run an action by name and log it. Null when there is no such action. */
async function perform(env: Env, name: string, via: "page" | "api", f: Fields, who: Who): Promise<Done | null> {
  const action = ACTIONS[name];
  if (!action?.[via]) return null;
  const done = await action.run(env, f, who);
  await logAction(env, who.email, name, done.target, done.ok, done.message);
  return done;
}

// -- the page --------------------------------------------------------------------
export async function adminRoute(request: Request, env: Env, url: URL): Promise<Response> {
  // Always the admin's own session: never the household they are looking at.
  const user = await realSessionUser(request, env);
  if (!user || !isAdmin(env, user.email)) return notFound();
  if (request.method === "GET" && url.pathname === "/admin") {
    const res = adminPage(await gather(env), readToast(request), !!cookie(request, AS_COOKIE), user.hid);
    res.headers.append("Set-Cookie", `${TOAST_COOKIE}=; Path=/admin; Max-Age=0; HttpOnly; Secure; SameSite=Lax`);
    return res;
  }
  if (request.method !== "POST") return notFound();
  // A form on this page, never another site's.
  if (request.headers.get("Origin") !== `https://${env.APP_HOST}`) return new Response("forbidden", { status: 403 });
  // /admin/household/delete is household.delete.
  const name = url.pathname.slice("/admin/".length).replaceAll("/", ".");
  const fields: Fields = {};
  const form = await request.formData().catch(() => null);   // a POST with no form: no fields
  for (const [k, v] of form ?? []) if (typeof v === "string") fields[k] = v;
  const done = await perform(env, name, "page", fields, { email: user.email, hid: user.hid });
  if (!done) return notFound();
  const res = done.response ?? new Response(null, { status: 303, headers: { Location: "/admin" } });
  // Log in as lands on the household's page, which has no toast; stopping lands here.
  if (!done.response || name === "as.stop") res.headers.append("Set-Cookie", toastCookie(done.ok, done.message));
  return res;
}

// -- the API -----------------------------------------------------------------------
export async function apiRoute(request: Request, env: Env, name: string): Promise<Response> {
  if (request.method !== "POST" || !env.ADMIN_TOKEN ||
      request.headers.get("Authorization") !== `Bearer ${env.ADMIN_TOKEN}`) {
    return notFound();
  }
  const body = await request.json<Record<string, unknown>>().catch(() => ({} as Record<string, unknown>));
  const fields: Fields = {};
  for (const [k, v] of Object.entries(body ?? {})) if (v !== null && typeof v !== "object") fields[k] = String(v);
  // The API emails an invitation unless told not to; the page's box posts 1 when ticked.
  fields.send = body?.send === false ? "0" : "1";
  const done = await perform(env, name, "api", fields, { email: "api", hid: null });
  if (!done) return notFound();
  const { status, ...data } = done.data ?? {};
  return done.ok ? Response.json({ ok: true, result: done.message, ...data })
    : Response.json({ ok: false, error: done.message, ...data }, { status: typeof status === "number" ? status : 400 });
}

// -- the audit log (W-863) --------------------------------------------------------
async function logAction(env: Env, admin: string, action: string, target: string | null,
                         ok: boolean, result: string): Promise<void> {
  try {
    await env.DB.prepare("INSERT INTO admin_log (at, admin, action, target, ok, result) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(Math.floor(Date.now() / 1000), admin, action, target, ok ? 1 : 0, result).run();
  } catch (err) {
    // The action is done either way: a log that cannot be written is said, not thrown.
    console.error("admin log", action, target, err);
  }
}

// -- the toast (W-863): said once, on the next load of the page -----------------
const TOAST_COOKIE = "ff_admin_toast";

function toastCookie(ok: boolean, message: string): string {
  const value = `${ok ? "ok" : "bad"}.${encodeURIComponent(message.slice(0, 300))}`;
  return `${TOAST_COOKIE}=${value}; Path=/admin; Max-Age=60; HttpOnly; Secure; SameSite=Lax`;
}

function readToast(request: Request): Toast | null {
  const raw = cookie(request, TOAST_COOKIE);
  const m = raw?.match(/^(ok|bad)\.(.+)$/);
  if (!m) return null;
  try {
    return { ok: m[1] === "ok", message: decodeURIComponent(m[2]) };
  } catch {
    return null;
  }
}

async function gather(env: Env): Promise<AdminData> {
  const [waitlist, invites, households, log, kits, signups, invited] = await Promise.all([
    env.DB.prepare("SELECT email, source, created_at, invited_at, confirmed_at FROM waitlist WHERE invited_at IS NULL ORDER BY created_at")
      .all<AdminData["waitlist"][number]>(),
    env.DB.prepare("SELECT email, created_at, used_at FROM invites ORDER BY created_at DESC")
      .all<AdminData["invites"][number]>(),
    env.DB.prepare(`SELECT h.id, h.created_at, h.suspended_at, u.email,
        (SELECT count(*) FROM frames f WHERE f.household_id = h.id) AS paired
      FROM households h LEFT JOIN users u ON u.household_id = h.id ORDER BY h.created_at`)
      .all<{ id: string; created_at: number; suspended_at: number | null; email: string | null; paired: number }>(),
    env.DB.prepare("SELECT at, admin, action, target, ok, result FROM admin_log ORDER BY id DESC LIMIT 100")
      .all<AdminData["log"][number]>(),
    env.DB.prepare(`SELECT k.device_id, k.kit, k.note, k.registered_at, k.used_at, u.email FROM kits k
      LEFT JOIN users u ON u.household_id = k.household_id ORDER BY k.registered_at DESC`).all<AdminData["kits"][number]>(),
    env.DB.prepare("SELECT confirmed_at AS t FROM waitlist WHERE confirmed_at IS NOT NULL").all<{ t: number }>(),
    env.DB.prepare("SELECT created_at AS t FROM invites").all<{ t: number }>(),
  ]);
  const rows = await Promise.all(households.results.map(async (h) => {
    try {
      return { ...h, ...(await env.HOUSEHOLD.getByName(h.id).summary()) };
    } catch {
      return { ...h, frames: [], usage: [], month_ms: 0, last_wake: null, source: null, suspended: false };
    }
  }));
  const usage = await cloudflareUsage(env, rows.reduce((a, h) => a + h.month_ms, 0));
  return { waitlist: waitlist.results, invites: invites.results, households: rows, usage, log: log.results,
           kits: kits.results,
           growth: { signups: signups.results.map((r) => r.t), invites: invited.results.map((r) => r.t),
                     households: households.results.map((h) => h.created_at) } };
}

// -- the marketing page's form ---------------------------------------------------
// featherframe.app posts here: a plain form (answered with a page) or JSON from
// its script (answered with JSON, with CORS for that origin).
export async function waitlistRoute(request: Request, env: Env): Promise<Response> {
  const origin = request.headers.get("Origin") || "";
  const allowed = [`https://${env.ZONE}`, `https://www.${env.ZONE}`].includes(origin) ? origin : "";
  const cors: Record<string, string> = allowed
    ? { "Access-Control-Allow-Origin": allowed, "Access-Control-Allow-Methods": "POST",
        "Access-Control-Allow-Headers": "Content-Type", Vary: "Origin" }
    : {};
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (request.method !== "POST") return new Response(null, { status: 405 });
  const json = (request.headers.get("Content-Type") || "").includes("json");
  let raw: unknown = "";
  try {
    raw = json ? (await request.json<{ email?: string }>()).email : (await request.formData()).get("email");
  } catch { /* an empty or odd body: no email */ }
  const email = normEmail(raw);
  if (!email) {
    return json ? Response.json({ ok: false, error: "Enter an email address." }, { status: 400, headers: cors })
                : waitlistThanksPage("Enter an email address.");
  }
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (await signUpWaitlist(env, email, ip) === "limited") {
    const error = "Too many sign-ups from here. Try again in an hour.";
    return json ? Response.json({ ok: false, error }, { status: 429, headers: cors }) : waitlistThanksPage(error, 429);
  }
  // The same answer for a new, pending or confirmed address.
  return json ? Response.json({ ok: true }, { headers: cors }) : waitlistThanksPage();
}
