// The admin page (W-850): the waitlist, invitations, and every household at a
// glance. For a signed-in user whose email is in ADMIN_EMAILS (a secret, comma
// separated); to anyone else it does not exist.

import type { Env } from "./index";
import { AS_COOKIE, actAs, logAction, deleteHousehold, invite, isAdmin, joinWaitlist, normEmail, realSessionUser, resendInvite,
         revokeInvite, setEmail, setSuspended, stopActingAs } from "./accounts";
import { adminPage, waitlistThanksPage, type AdminData, type Toast } from "./pages";
import { cloudflareUsage } from "./usage";
import { cookie } from "./util";

const notFound = () => new Response("not found", { status: 404 });

export async function adminRoute(request: Request, env: Env, url: URL): Promise<Response> {
  // Always the admin's own session: never the household they are looking at.
  const user = await realSessionUser(request, env);
  if (!user || !isAdmin(env, user.email)) return notFound();
  if (request.method === "GET" && url.pathname === "/admin") {
    const res = adminPage(await gather(env), readToast(request), !!cookie(request, AS_COOKIE));
    res.headers.append("Set-Cookie", `${TOAST_COOKIE}=; Path=/admin; Max-Age=0; HttpOnly; Secure; SameSite=Lax`);
    return res;
  }
  if (request.method !== "POST") return notFound();
  // A form on this page, never another site's.
  if (request.headers.get("Origin") !== `https://${env.APP_HOST}`) return new Response("forbidden", { status: 403 });
  const done = await perform(env, url.pathname, request);
  if (!done) return notFound();
  await logAction(env, user.email, done.action, done.target, done.ok, done.message);
  const res = done.response ?? new Response(null, { status: 303, headers: { Location: "/admin" } });
  if (done.response === undefined || url.pathname === "/admin/as/stop") {
    res.headers.append("Set-Cookie", toastCookie(done.ok, done.message));
  }
  return res;
}

type Done = { action: string; target: string | null; ok: boolean; message: string; response?: Response };

/** One admin action: what it was, to whom, how it went (the toast), and the
 * response when it is not the page again. */
async function perform(env: Env, path: string, request: Request): Promise<Done | null> {
  if (path === "/admin/as/stop") {
    return { action: "as.stop", target: null, ok: true, message: "Back to your own page.", response: stopActingAs() };
  }
  const form = await request.formData();
  const household = path.match(/^\/admin\/household\/(as|email|suspend|resume|delete)$/);
  if (household) {
    const action = `household.${household[1]}`;
    const hid = String(form.get("id") || "");
    const row = await env.DB.prepare("SELECT h.id, u.email FROM households h LEFT JOIN users u ON u.household_id = h.id WHERE h.id = ?")
      .bind(hid).first<{ id: string; email: string | null }>();
    const r = (ok: boolean, message: string, response?: Response): Done =>
      ({ action, target: row?.email ? `${row.email} (${hid})` : hid || null, ok, message, response });
    if (!row) return r(false, "No such household.");
    const who = row.email || hid;
    switch (household[1]) {
      case "as":
        if (!row.email) return r(false, `${hid} has no login to look through.`);
        return r(true, `Logged in as ${who}.`, actAs(hid));
      case "email": {
        const email = normEmail(form.get("email"));
        if (!email) return r(false, "Enter an email address.");
        const set = await setEmail(env, hid, email);
        return set === "ok" ? r(true, `${who} now signs in as ${email}.`)
          : r(false, set === "taken" ? `${email} already has a login.` : `${hid} has no login.`);
      }
      case "suspend":
        await setSuspended(env, hid, true);
        return r(true, `Suspended ${who}.`);
      case "resume":
        await setSuspended(env, hid, false);
        return r(true, `Resumed ${who}.`);
      case "delete":
        // Typed, not clicked: the household's email (or id) must be given back.
        if (String(form.get("confirm") || "").trim().toLowerCase() !== who.toLowerCase()) {
          return r(false, `Not deleted: type ${who} to delete it.`);
        }
        try {
          await deleteHousehold(env, hid);
        } catch (err) {
          console.error("delete household", hid, err);
          return r(false, `Deleting ${who} stopped part way: ${String(err)}. Try again.`);
        }
        return r(true, `Deleted ${who}.`);
    }
  }

  const actions: Record<string, string> = {
    "/admin/invite": "invite", "/admin/invite/resend": "invite.resend",
    "/admin/invite/revoke": "invite.revoke", "/admin/waitlist/remove": "waitlist.remove",
  };
  const action = actions[path];
  if (!action) return null;
  const email = normEmail(form.get("email"));
  const r = (ok: boolean, message: string): Done => ({ action, target: email || null, ok, message });
  if (!email) return r(false, "Enter an email address.");
  switch (action) {
    case "invite": {
      const send = form.get("send") === "1";   // the box, ticked by default
      const sent = await invite(env, email, send);
      return send && !sent ? r(false, `Invited ${email}, but the email did not go.`) : r(true, `Invited ${email}.`);
    }
    case "invite.resend": {
      const sent = await resendInvite(env, email);
      return sent === "sent" ? r(true, `Sent ${email} their invitation again.`)
        : r(false, sent === "failed" ? `The email to ${email} did not go.` : `${email} has no open invitation.`);
    }
    case "invite.revoke":
      return await revokeInvite(env, email) ? r(true, `Revoked ${email}'s invitation.`)
        : r(false, `${email} has no open invitation.`);
    default:
      await env.DB.prepare("DELETE FROM waitlist WHERE email = ?").bind(email).run();
      return r(true, `Removed ${email} from the waitlist.`);
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
  const [waitlist, invites, households, log] = await Promise.all([
    env.DB.prepare("SELECT email, source, created_at, invited_at FROM waitlist WHERE invited_at IS NULL ORDER BY created_at")
      .all<AdminData["waitlist"][number]>(),
    env.DB.prepare("SELECT email, created_at, used_at FROM invites ORDER BY created_at DESC")
      .all<AdminData["invites"][number]>(),
    env.DB.prepare(`SELECT h.id, h.created_at, h.suspended_at, u.email,
        (SELECT count(*) FROM frames f WHERE f.household_id = h.id) AS paired
      FROM households h LEFT JOIN users u ON u.household_id = h.id ORDER BY h.created_at`)
      .all<{ id: string; created_at: number; suspended_at: number | null; email: string | null; paired: number }>(),
    env.DB.prepare("SELECT at, admin, action, target, ok, result FROM admin_log ORDER BY id DESC LIMIT 100")
      .all<AdminData["log"][number]>(),
  ]);
  const rows = await Promise.all(households.results.map(async (h) => {
    try {
      return { ...h, ...(await env.HOUSEHOLD.getByName(h.id).summary()) };
    } catch {
      return { ...h, frames: [], usage: [], month_ms: 0, last_wake: null, source: null, suspended: false };
    }
  }));
  const usage = await cloudflareUsage(env, rows.reduce((a, h) => a + h.month_ms, 0));
  return { waitlist: waitlist.results, invites: invites.results, households: rows, usage, log: log.results };
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
                : waitlistThanksPage("", "Enter an email address.");
  }
  await joinWaitlist(env, email, "site");
  return json ? Response.json({ ok: true }, { headers: cors }) : waitlistThanksPage(email);
}

