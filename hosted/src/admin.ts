// The admin page (W-850): the waitlist, invitations, and every household at a
// glance. For a signed-in user whose email is in ADMIN_EMAILS (a secret, comma
// separated); to anyone else it does not exist.

import type { Env } from "./index";
import { AS_COOKIE, actAs, deleteHousehold, invite, isAdmin, joinWaitlist, normEmail, realSessionUser, resendInvite,
         revokeInvite, setEmail, setSuspended, stopActingAs } from "./accounts";
import { adminPage, waitlistThanksPage, type AdminData } from "./pages";
import { cloudflareUsage } from "./usage";
import { cookie } from "./util";

const notFound = () => new Response("not found", { status: 404 });

export async function adminRoute(request: Request, env: Env, url: URL): Promise<Response> {
  // Always the admin's own session: never the household they are looking at.
  const user = await realSessionUser(request, env);
  if (!user || !isAdmin(env, user.email)) return notFound();
  if (request.method === "GET" && url.pathname === "/admin") {
    return adminPage(await gather(env), url.searchParams.get("m") || "", !!cookie(request, AS_COOKIE));
  }
  if (request.method !== "POST") return notFound();
  // A form on this page, never another site's.
  if (request.headers.get("Origin") !== `https://${env.APP_HOST}`) return new Response("forbidden", { status: 403 });
  const path = url.pathname;
  if (path === "/admin/as/stop") return stopActingAs();
  const form = await request.formData();

  const household = path.match(/^\/admin\/household\/(as|email|suspend|resume|delete)$/);
  if (household) {
    const hid = String(form.get("id") || "");
    const row = await env.DB.prepare("SELECT h.id, u.email FROM households h LEFT JOIN users u ON u.household_id = h.id WHERE h.id = ?")
      .bind(hid).first<{ id: string; email: string | null }>();
    if (!row) return back("No such household.");
    const who = row.email || hid;
    switch (household[1]) {
      case "as":
        if (!row.email) return back(`${hid} has no login to look through.`);
        return actAs(hid);
      case "email": {
        const email = normEmail(form.get("email"));
        if (!email) return back("Enter an email address.");
        const done = await setEmail(env, hid, email);
        return back(done === "ok" ? `${who} now signs in as ${email}.`
          : done === "taken" ? `${email} already has a login.` : `${hid} has no login.`);
      }
      case "suspend":
        await setSuspended(env, hid, true);
        return back(`Suspended ${who}.`);
      case "resume":
        await setSuspended(env, hid, false);
        return back(`Resumed ${who}.`);
      case "delete":
        // Typed, not clicked: the household's email (or id) must be given back.
        if (String(form.get("confirm") || "").trim().toLowerCase() !== who.toLowerCase()) {
          return back(`Not deleted: type ${who} to delete it.`);
        }
        try {
          await deleteHousehold(env, hid);
        } catch (err) {
          console.error("delete household", hid, err);
          return back(`Deleting ${who} stopped part way: ${String(err)}. Try again.`);
        }
        return back(`Deleted ${who}.`);
    }
  }

  const email = normEmail(form.get("email"));
  if (!email) return back("Enter an email address.");
  if (path === "/admin/invite") {
    const send = form.get("send") === "1";   // the box, ticked by default
    const sent = await invite(env, email, send);
    return back(send && !sent ? `Invited ${email}, but the email did not go.` : `Invited ${email}.`);
  }
  if (path === "/admin/invite/resend") {
    const sent = await resendInvite(env, email);
    return back(sent === "sent" ? `Sent ${email} their invitation again.`
      : sent === "failed" ? `The email to ${email} did not go.` : `${email} has no open invitation.`);
  }
  if (path === "/admin/invite/revoke") {
    return back(await revokeInvite(env, email) ? `Revoked ${email}'s invitation.` : `${email} has no open invitation.`);
  }
  if (path === "/admin/waitlist/remove") {
    await env.DB.prepare("DELETE FROM waitlist WHERE email = ?").bind(email).run();
    return back(`Removed ${email} from the waitlist.`);
  }
  return notFound();
}

function back(message: string): Response {
  return new Response(null, { status: 303, headers: { Location: `/admin?m=${encodeURIComponent(message)}` } });
}

async function gather(env: Env): Promise<AdminData> {
  const [waitlist, invites, households] = await Promise.all([
    env.DB.prepare("SELECT email, source, created_at, invited_at FROM waitlist WHERE invited_at IS NULL ORDER BY created_at")
      .all<AdminData["waitlist"][number]>(),
    env.DB.prepare("SELECT email, created_at, used_at FROM invites ORDER BY created_at DESC")
      .all<AdminData["invites"][number]>(),
    env.DB.prepare(`SELECT h.id, h.created_at, h.suspended_at, u.email,
        (SELECT count(*) FROM frames f WHERE f.household_id = h.id) AS paired
      FROM households h LEFT JOIN users u ON u.household_id = h.id ORDER BY h.created_at`)
      .all<{ id: string; created_at: number; suspended_at: number | null; email: string | null; paired: number }>(),
  ]);
  const rows = await Promise.all(households.results.map(async (h) => {
    try {
      return { ...h, ...(await env.HOUSEHOLD.getByName(h.id).summary()) };
    } catch {
      return { ...h, frames: [], usage: [], month_ms: 0, last_wake: null, source: null, suspended: false };
    }
  }));
  const usage = await cloudflareUsage(env, rows.reduce((a, h) => a + h.month_ms, 0));
  return { waitlist: waitlist.results, invites: invites.results, households: rows, usage };
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

