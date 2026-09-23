// Signing in (W-845): email magic links, sessions, invites. One login per
// household; signing up is by invitation. Only hashes of secrets are stored.

import type { Env } from "./index";
import { checkEmailPage, inviteEmail, linkExpiredPage, loginPage, signInEmail } from "./pages";
import { cookie, randomHex, sha256, validTz } from "./util";

const LINK_TTL_S = 15 * 60;
const SESSION_TTL_S = 30 * 24 * 60 * 60;
export const SESSION_COOKIE = "ff_session";

const now = () => Math.floor(Date.now() / 1000);

function normEmail(e: unknown): string {
  const s = String(e || "").trim().toLowerCase();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s) && s.length <= 254 ? s : "";
}

export async function sendMail(env: Env, to: string, mail: { subject: string; text: string; html: string }): Promise<boolean> {
  if (!env.RESEND_API_KEY) return false;
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from: env.MAIL_FROM, to: [to], subject: mail.subject, text: mail.text, html: mail.html }),
  });
  if (!r.ok) console.error("resend", r.status, await r.text());
  return r.ok;
}

/** A sign-in link for `email`, or null when it may not sign in. */
export async function makeLoginLink(env: Env, email: string, tz: string | null): Promise<string | null> {
  const known = await env.DB.prepare("SELECT 1 FROM users WHERE email = ?").bind(email).first();
  const invited = await env.DB.prepare("SELECT 1 FROM invites WHERE email = ? AND used_at IS NULL").bind(email).first();
  if (!known && !invited) return null;
  const token = randomHex(32);
  await env.DB.prepare("INSERT INTO login_links (token_hash, email, tz, expires_at) VALUES (?, ?, ?, ?)")
    .bind(await sha256(token), email, tz, now() + LINK_TTL_S).run();
  return `https://${env.APP_HOST}/auth?t=${token}`;
}

export async function login(request: Request, env: Env): Promise<Response> {
  if (request.method === "GET") return loginPage();
  const form = await request.formData();
  const email = normEmail(form.get("email"));
  if (!email) return loginPage("Enter an email address.");
  const link = await makeLoginLink(env, email, validTz(String(form.get("tz") || "")));
  if (link) await sendMail(env, email, signInEmail(link));
  // The same answer either way: the page does not say who has an account.
  return checkEmailPage(email);
}

/** The link from the email: sign in, and on an invitation's first use, make
 * the household. */
export async function auth(request: Request, env: Env, url: URL): Promise<Response> {
  const token = url.searchParams.get("t") || "";
  const row = await env.DB.prepare(
    "SELECT email, tz, expires_at, used_at FROM login_links WHERE token_hash = ?")
    .bind(await sha256(token)).first<{ email: string; tz: string | null; expires_at: number; used_at: number | null }>();
  if (!row || row.used_at || row.expires_at < now()) return linkExpiredPage();
  await env.DB.prepare("UPDATE login_links SET used_at = ? WHERE token_hash = ?").bind(now(), await sha256(token)).run();

  let user = await env.DB.prepare("SELECT id, household_id FROM users WHERE email = ?")
    .bind(row.email).first<{ id: string; household_id: string }>();
  if (!user) {
    const invite = await env.DB.prepare("SELECT 1 FROM invites WHERE email = ? AND used_at IS NULL").bind(row.email).first();
    if (!invite) return linkExpiredPage();
    const hid = randomHex(8);
    const uid = randomHex(8);
    const tz = validTz(row.tz);
    await env.DB.batch([
      env.DB.prepare("INSERT INTO households (id, tz, created_at) VALUES (?, ?, ?)").bind(hid, tz, now()),
      env.DB.prepare("INSERT INTO users (id, email, household_id, created_at) VALUES (?, ?, ?, ?)").bind(uid, row.email, hid, now()),
      env.DB.prepare("UPDATE invites SET used_at = ? WHERE email = ?").bind(now(), row.email),
    ]);
    await env.HOUSEHOLD.getByName(hid).init(hid, tz);
    user = { id: uid, household_id: hid };
  }
  const session = randomHex(32);
  await env.DB.prepare("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)")
    .bind(await sha256(session), user.id, now() + SESSION_TTL_S).run();
  return new Response(null, {
    status: 303,
    headers: {
      Location: "/",
      "Set-Cookie": `${SESSION_COOKIE}=${session}; Path=/; Max-Age=${SESSION_TTL_S}; HttpOnly; Secure; SameSite=Lax`,
    },
  });
}

export async function logout(request: Request, env: Env): Promise<Response> {
  const session = cookie(request, SESSION_COOKIE);
  if (session) await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(await sha256(session)).run();
  return new Response(null, {
    status: 303,
    headers: { Location: "/login", "Set-Cookie": `${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax` },
  });
}

/** The signed-in household, or null. */
export async function sessionHousehold(request: Request, env: Env): Promise<string | null> {
  const session = cookie(request, SESSION_COOKIE);
  if (!session) return null;
  const row = await env.DB.prepare(
    "SELECT u.household_id AS hid FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?")
    .bind(await sha256(session), now()).first<{ hid: string }>();
  return row?.hid ?? null;
}

// -- the admin's side, until there is a page for it ----------------------------
export async function admin(request: Request, env: Env, path: string): Promise<Response> {
  if (request.method !== "POST" || !env.ADMIN_TOKEN ||
      request.headers.get("Authorization") !== `Bearer ${env.ADMIN_TOKEN}`) {
    return new Response("not found", { status: 404 });
  }
  const body = await request.json<{ email?: string; household?: string; send?: boolean }>().catch(() => ({} as { email?: string; household?: string; send?: boolean }));
  const email = normEmail(body.email);
  if (!email) return Response.json({ error: "email" }, { status: 400 });
  if (path === "invite") {
    await env.DB.prepare("INSERT OR IGNORE INTO invites (email, created_at) VALUES (?, ?)").bind(email, now()).run();
    const sent = body.send !== false && await sendMail(env, email, inviteEmail(`https://${env.APP_HOST}/login`));
    return Response.json({ ok: true, invited: email, emailed: sent });
  }
  if (path === "link") {
    // A sign-in link handed to the admin rather than emailed: for support,
    // and for testing without sending anyone mail.
    const link = await makeLoginLink(env, email, null);
    return link ? Response.json({ ok: true, link }) : Response.json({ error: "not invited" }, { status: 404 });
  }
  if (path === "adopt") {
    // Give an existing household (one made before accounts) its login.
    const hid = String(body.household || "");
    const exists = await env.DB.prepare("SELECT 1 FROM households WHERE id = ?").bind(hid).first();
    if (!exists) await env.DB.prepare("INSERT INTO households (id, tz, created_at) VALUES (?, 'UTC', ?)").bind(hid, now()).run();
    await env.DB.prepare("INSERT OR REPLACE INTO users (id, email, household_id, created_at) VALUES (?, ?, ?, ?)")
      .bind(randomHex(8), email, hid, now()).run();
    return Response.json({ ok: true, household: hid, email });
  }
  return new Response("not found", { status: 404 });
}
