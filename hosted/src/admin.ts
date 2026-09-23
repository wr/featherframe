// The admin page (W-850): the waitlist, invitations, and every household at a
// glance. For a signed-in user whose email is in ADMIN_EMAILS (a secret, comma
// separated); to anyone else it does not exist.

import type { Env } from "./index";
import { invite, joinWaitlist, normEmail, sessionUser } from "./accounts";
import { adminPage, waitlistThanksPage, type AdminData } from "./pages";

const notFound = () => new Response("not found", { status: 404 });

function isAdmin(env: Env, email: string): boolean {
  return (env.ADMIN_EMAILS || "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean).includes(email);
}

export async function adminRoute(request: Request, env: Env, url: URL): Promise<Response> {
  const user = await sessionUser(request, env);
  if (!user || !isAdmin(env, user.email)) return notFound();
  if (request.method === "GET" && url.pathname === "/admin") return adminPage(await gather(env), url.searchParams.get("m") || "");
  if (request.method !== "POST") return notFound();
  // A form on this page, never another site's.
  if (request.headers.get("Origin") !== `https://${env.APP_HOST}`) return new Response("forbidden", { status: 403 });
  const form = await request.formData();
  const email = normEmail(form.get("email"));
  if (!email) return back("Enter an email address.");
  if (url.pathname === "/admin/invite") {
    const send = form.get("send") === "1";   // the box, ticked by default
    const sent = await invite(env, email, send);
    return back(send && !sent ? `Invited ${email}, but the email did not go.` : `Invited ${email}.`);
  }
  if (url.pathname === "/admin/waitlist/remove") {
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
    env.DB.prepare(`SELECT h.id, h.created_at, u.email,
        (SELECT count(*) FROM frames f WHERE f.household_id = h.id) AS paired
      FROM households h LEFT JOIN users u ON u.household_id = h.id ORDER BY h.created_at`)
      .all<{ id: string; created_at: number; email: string | null; paired: number }>(),
  ]);
  const rows = await Promise.all(households.results.map(async (h) => {
    try {
      return { ...h, ...(await env.HOUSEHOLD.getByName(h.id).summary()) };
    } catch {
      return { ...h, frames: [], usage: [], last_wake: null, source: null };
    }
  }));
  return { waitlist: waitlist.results, invites: invites.results, households: rows };
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

