// Signing in (W-845): email magic links, sessions, invites. One login per
// household; signing up is by invitation. Only hashes of secrets are stored.

import type { Env } from "./index";
import { verifiedPage, verifyEmail, verifyExpiredPage } from "./pages";
import { checkEmailPage, confirmEmailEmail, inviteEmail, linkExpiredPage, loginPage, signInEmail, waitlistConfirmEmail,
         waitlistConfirmedPage, waitlistExpiredPage } from "./pages";
import { cookie, randomHex, sha256, validTz } from "./util";
import { pairLinked } from "./setup";

const LINK_TTL_S = 15 * 60;
const CHANGE_TTL_S = 24 * 60 * 60;
const SESSION_TTL_S = 30 * 24 * 60 * 60;
export const SESSION_COOKIE = "ff_session";

const now = () => Math.floor(Date.now() / 1000);

export function normEmail(e: unknown): string {
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

/** A sign-in link for `email`, or null when it may not sign in. `pairCode`
 * ("CODE:device"): following it also adds the frame showing that code (W-888). */
export async function makeLoginLink(env: Env, email: string, tz: string | null,
                                    pairCode: string | null = null): Promise<string | null> {
  const known = await env.DB.prepare("SELECT 1 FROM users WHERE email = ?").bind(email).first();
  const invited = await env.DB.prepare("SELECT 1 FROM invites WHERE email = ? AND used_at IS NULL").bind(email).first();
  if (!known && !invited) return null;
  const token = randomHex(32);
  await env.DB.prepare("INSERT INTO login_links (token_hash, email, tz, expires_at, pair_code) VALUES (?, ?, ?, ?, ?)")
    .bind(await sha256(token), email, tz, now() + LINK_TTL_S, pairCode).run();
  return `https://${env.APP_HOST}/auth?t=${token}`;
}

export async function login(request: Request, env: Env): Promise<Response> {
  if (request.method === "GET") return loginPage();
  const form = await request.formData();
  const email = normEmail(form.get("email"));
  if (!email) return loginPage("Enter an email address.");
  const link = await makeLoginLink(env, email, validTz(String(form.get("tz") || "")));
  if (link) await sendMail(env, email, signInEmail(link));
  else await joinWaitlist(env, email, "login");   // asked to come in: pending, and not emailed
  // The same answer either way: the page does not say who has an account.
  return checkEmailPage(email);
}

/** The link from the email: sign in, and on an invitation's first use, make
 * the household. */
export async function auth(request: Request, env: Env, url: URL): Promise<Response> {
  const token = url.searchParams.get("t") || "";
  const row = await env.DB.prepare(
    "SELECT email, tz, expires_at, used_at, pair_code FROM login_links WHERE token_hash = ?")
    .bind(await sha256(token)).first<{ email: string; tz: string | null; expires_at: number; used_at: number | null;
                                       pair_code: string | null }>();
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
      env.DB.prepare("INSERT INTO users (id, email, household_id, created_at, verified_at) VALUES (?, ?, ?, ?, ?)")
        .bind(uid, row.email, hid, now(), now()),
      env.DB.prepare("UPDATE invites SET used_at = ? WHERE email = ?").bind(now(), row.email),
    ]);
    await env.HOUSEHOLD.getByName(hid).init(hid, tz);
    user = { id: uid, household_id: hid };
  }
  // Following an emailed link proves the address (W-889).
  await env.DB.prepare("UPDATE users SET verified_at = ? WHERE id = ? AND verified_at IS NULL").bind(now(), user.id).run();
  // A link from the setup page (W-888): the frame whose code was scanned
  // joins this account, if it is still showing that code.
  let landing = "/";
  if (row.pair_code && await pairLinked(env, row.pair_code, user.household_id)) landing = "/?paired=1";
  return signedIn(env, user.id, landing);
}

// -- confirming a new owner's email (W-889) ---------------------------------------
const VERIFY_TTL_S = 7 * 24 * 60 * 60;
export const VERIFY_RESENDS_PER_HOUR = 3;

/** Email `uid` a link that confirms `email`. */
export async function sendVerification(env: Env, uid: string, email: string): Promise<boolean> {
  const token = randomHex(32);
  await env.DB.prepare("INSERT INTO email_verifications (token_hash, user_id, email, expires_at) VALUES (?, ?, ?, ?)")
    .bind(await sha256(token), uid, email, now() + VERIFY_TTL_S).run();
  return sendMail(env, email, verifyEmail(`https://${env.APP_HOST}/account/verify?t=${token}`));
}

/** The link from that email. */
export async function confirmVerification(env: Env, url: URL): Promise<Response> {
  const token = url.searchParams.get("t") || "";
  if (!/^[0-9a-f]{64}$/.test(token)) return verifyExpiredPage();
  const row = await env.DB.prepare(
    "SELECT user_id, email FROM email_verifications WHERE token_hash = ? AND expires_at > ? AND used_at IS NULL")
    .bind(await sha256(token), now()).first<{ user_id: string; email: string }>();
  if (!row) return verifyExpiredPage();
  await env.DB.batch([
    env.DB.prepare("UPDATE email_verifications SET used_at = ? WHERE token_hash = ?").bind(now(), await sha256(token)),
    // Only while the account still has the address the link was sent to.
    env.DB.prepare("UPDATE users SET verified_at = coalesce(verified_at, ?) WHERE id = ? AND email = ?")
      .bind(now(), row.user_id, row.email),
  ]);
  return verifiedPage();
}

/** The banner's Resend. */
export async function resendVerification(request: Request, env: Env, user: SessionUser): Promise<Response> {
  const origin = request.headers.get("Origin");
  if (origin && origin !== `https://${env.APP_HOST}`) return new Response("forbidden", { status: 403 });
  let q = "verify_sent=1";
  if (user.verified) q = "verified=1";
  else if (!(await rateHit(env, `verify:${user.uid}`, VERIFY_RESENDS_PER_HOUR, 3600))) q = "verify_limited=1";
  else if (!(await sendVerification(env, user.uid, user.email))) q = "verify_failed=1";
  return Response.redirect(`https://${env.APP_HOST}/?${q}`, 303);
}

/** A new session for `uid`, and the page it lands on. */
export async function signedIn(env: Env, uid: string, location: string): Promise<Response> {
  const session = randomHex(32);
  await env.DB.prepare("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)")
    .bind(await sha256(session), uid, now() + SESSION_TTL_S).run();
  return new Response(null, {
    status: 303,
    headers: {
      Location: location,
      "Set-Cookie": `${SESSION_COOKIE}=${session}; Path=/; Max-Age=${SESSION_TTL_S}; HttpOnly; Secure; SameSite=Lax`,
    },
  });
}

export async function logout(request: Request, env: Env): Promise<Response> {
  // An admin looking at someone's page (W-860) signs out of theirs, not out.
  if (cookie(request, AS_COOKIE)) return stopActingAs();
  const session = cookie(request, SESSION_COOKIE);
  if (session) await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(await sha256(session)).run();
  return new Response(null, {
    status: 303,
    headers: { Location: "/login", "Set-Cookie": `${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax` },
  });
}

export interface SessionUser {
  uid: string; email: string; hid: string; suspended: boolean;
  /** Its email proven by an emailed link (W-889). */
  verified: boolean;
  /** The admin's own email, while an admin is looking at this household (W-860). */
  as?: string;
}

const USER_SQL = `SELECT u.id AS uid, u.email AS email, u.household_id AS hid,
  h.suspended_at IS NOT NULL AS suspended, u.verified_at IS NOT NULL AS verified
  FROM users u JOIN households h ON h.id = u.household_id`;

/** Who is signed in, themselves: never another household an admin is looking at. */
export async function realSessionUser(request: Request, env: Env): Promise<SessionUser | null> {
  const session = cookie(request, SESSION_COOKIE);
  if (!session) return null;
  const row = await env.DB.prepare(
    `${USER_SQL} JOIN sessions s ON s.user_id = u.id WHERE s.token_hash = ? AND s.expires_at > ?`)
    .bind(await sha256(session), now()).first<SessionUser>();
  return row ? { ...row, suspended: !!row.suspended, verified: !!row.verified } : null;
}

/** The signed-in user and their household, or null. For an admin who chose
 * "Log in as" (W-860), that household's login instead: the cookie names a
 * household and counts only beside an admin's own session. */
export async function sessionUser(request: Request, env: Env): Promise<SessionUser | null> {
  const user = await realSessionUser(request, env);
  const as = cookie(request, AS_COOKIE);
  if (!user || !as || !isAdmin(env, user.email)) return user;
  const target = await env.DB.prepare(`${USER_SQL} WHERE u.household_id = ?`).bind(as).first<SessionUser>();
  return target ? { ...target, suspended: !!target.suspended, verified: !!target.verified, as: user.email } : user;
}

// -- the admin (W-850, W-860) ---------------------------------------------------
export const AS_COOKIE = "ff_as";
const AS_TTL_S = 60 * 60;

export function isAdmin(env: Env, email: string): boolean {
  return (env.ADMIN_EMAILS || "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean).includes(email);
}

/** Open a household's page as its owner. */
export function actAs(hid: string): Response {
  return new Response(null, { status: 303, headers: {
    Location: "/", "Set-Cookie": `${AS_COOKIE}=${hid}; Path=/; Max-Age=${AS_TTL_S}; HttpOnly; Secure; SameSite=Lax`,
  } });
}

export function stopActingAs(): Response {
  return new Response(null, { status: 303, headers: {
    Location: "/admin", "Set-Cookie": `${AS_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`,
  } });
}

/** Move a login to `email` at once, with no link to confirm it. */
export async function setEmail(env: Env, hid: string, email: string): Promise<"ok" | "taken" | "none"> {
  const user = await env.DB.prepare("SELECT id FROM users WHERE household_id = ?").bind(hid).first<{ id: string }>();
  if (!user) return "none";
  const taken = await env.DB.prepare("SELECT 1 FROM users WHERE email = ? AND id != ?").bind(email, user.id).first();
  if (taken) return "taken";
  await env.DB.batch([
    env.DB.prepare("UPDATE users SET email = ? WHERE id = ?").bind(email, user.id),
    env.DB.prepare("UPDATE email_changes SET used_at = ? WHERE user_id = ? AND used_at IS NULL").bind(now(), user.id),
  ]);
  return "ok";
}

export async function setSuspended(env: Env, hid: string, on: boolean): Promise<void> {
  await env.DB.prepare("UPDATE households SET suspended_at = ? WHERE id = ?").bind(on ? now() : null, hid).run();
  await env.HOUSEHOLD.getByName(hid).suspend(on);
}

/** A household and everything of it: its login, sessions and invitation (so
 * the email can be invited again), its frames' registry rows (so they show a
 * pairing code), then its data, front door and server. */
export async function deleteHousehold(env: Env, hid: string): Promise<void> {
  const user = await env.DB.prepare("SELECT id, email FROM users WHERE household_id = ?")
    .bind(hid).first<{ id: string; email: string }>();
  const ofUser = user ? [
    env.DB.prepare("DELETE FROM sessions WHERE user_id = ?").bind(user.id),
    env.DB.prepare("DELETE FROM email_changes WHERE user_id = ?").bind(user.id),
    env.DB.prepare("DELETE FROM login_links WHERE email = ?").bind(user.email),
    env.DB.prepare("DELETE FROM invites WHERE email = ?").bind(user.email),
  ] : [];
  await env.DB.batch([...ofUser, env.DB.prepare("DELETE FROM frames WHERE household_id = ?").bind(hid)]);
  // Its frames are let go before the front door forgets them, so the one
  // message they are sent finds a pairing code, not a household that is gone.
  await env.HOUSEHOLD.getByName(hid).destroy();
  await env.DB.batch([
    // A kit set up into this household may be set up again (W-888); a
    // setup code, once used, stays used.
    env.DB.prepare("UPDATE kits SET used_at = NULL, household_id = NULL WHERE household_id = ?").bind(hid),
    env.DB.prepare("DELETE FROM users WHERE household_id = ?").bind(hid),
    env.DB.prepare("DELETE FROM households WHERE id = ?").bind(hid),
  ]);
}

// -- changing the email (W-773) -------------------------------------------------
/** Ask to move this login to `email`: a link to the new address confirms it.
 * "taken" when another login already has it. */
export async function startEmailChange(env: Env, uid: string, email: string): Promise<"sent" | "taken"> {
  const taken = await env.DB.prepare("SELECT 1 FROM users WHERE email = ?").bind(email).first();
  if (taken) return "taken";
  // One pending change at a time: an older link stops working.
  await env.DB.prepare("UPDATE email_changes SET used_at = ? WHERE user_id = ? AND used_at IS NULL")
    .bind(now(), uid).run();
  const token = randomHex(32);
  await env.DB.prepare("INSERT INTO email_changes (token_hash, user_id, email, expires_at) VALUES (?, ?, ?, ?)")
    .bind(await sha256(token), uid, email, now() + CHANGE_TTL_S).run();
  await sendMail(env, email, confirmEmailEmail(`https://${env.APP_HOST}/account/email?t=${token}`));
  return "sent";
}

/** The address this login asked to move to and has not confirmed, or null. */
export async function pendingEmail(env: Env, uid: string): Promise<string | null> {
  const row = await env.DB.prepare(
    "SELECT email FROM email_changes WHERE user_id = ? AND used_at IS NULL AND expires_at > ? ORDER BY expires_at DESC LIMIT 1")
    .bind(uid, now()).first<{ email: string }>();
  return row?.email ?? null;
}

/** The link from that email: the login's email is now the new one. */
export async function confirmEmailChange(env: Env, url: URL): Promise<Response> {
  const hash = await sha256(url.searchParams.get("t") || "");
  const row = await env.DB.prepare("SELECT user_id, email, expires_at, used_at FROM email_changes WHERE token_hash = ?")
    .bind(hash).first<{ user_id: string; email: string; expires_at: number; used_at: number | null }>();
  if (!row || row.used_at || row.expires_at < now()) return linkExpiredPage();
  const taken = await env.DB.prepare("SELECT 1 FROM users WHERE email = ? AND id != ?").bind(row.email, row.user_id).first();
  if (taken) return linkExpiredPage();
  await env.DB.batch([
    env.DB.prepare("UPDATE email_changes SET used_at = ? WHERE token_hash = ?").bind(now(), hash),
    env.DB.prepare("UPDATE users SET email = ? WHERE id = ?").bind(row.email, row.user_id),
  ]);
  return new Response(null, { status: 303, headers: { Location: "/?email_changed=1" } });
}

/** The page's settings form, on its way to the household's server: the
 * email in it is the account's, which is ours to change, not the server's. */
export async function settingsForm(request: Request, env: Env, user: SessionUser,
                                   forward: (r: Request) => Promise<Response>): Promise<Response> {
  const body = await request.text();
  const wanted = normEmail(new URLSearchParams(body).get("owner_email"));
  const change = wanted && wanted !== user.email ? await startEmailChange(env, user.uid, wanted) : null;
  const response = await forward(new Request(request, { body }));
  const location = response.headers.get("Location");
  if (!change || response.status !== 303 || !location || !location.includes("saved=1")) return response;
  const extra = change === "sent" ? `&email_sent=${encodeURIComponent(wanted)}` : "&email_taken=1";
  const headers = new Headers(response.headers);
  headers.set("Location", location + extra);
  return new Response(null, { status: 303, headers });
}

/** The signed-in household, or null. */
export async function sessionHousehold(request: Request, env: Env): Promise<string | null> {
  return (await sessionUser(request, env))?.hid ?? null;
}

// -- the waitlist (W-850) ------------------------------------------------------
// Double opt-in: an address is pending until the link emailed to it is
// followed. Only a hash of the link's token is kept.
export const WAITLIST_TOKEN_TTL_S = 7 * 24 * 60 * 60;
export const WAITLIST_RESEND_S = 10 * 60;
export const WAITLIST_PER_IP = 5;        // sign-ups an hour from one IP
export const WAITLIST_PER_ADDRESS = 5;   // and for one address

/** Join the waitlist, pending, and send no email: an uninvited sign-in attempt. */
export async function joinWaitlist(env: Env, email: string, source: string): Promise<void> {
  const known = await env.DB.prepare("SELECT 1 FROM users WHERE email = ?").bind(email).first();
  if (known) return;
  await env.DB.prepare("INSERT OR IGNORE INTO waitlist (email, source, created_at) VALUES (?, ?, ?)")
    .bind(email, source, now()).run();
}

/** A sign-up from the marketing page: store the address as pending and email
 * it a link to confirm. Says nothing about the address to the caller: a
 * confirmed, invited or known address is simply not emailed. "limited" when
 * this IP has signed up too often. */
export async function signUpWaitlist(env: Env, email: string, ip: string, source = "site"): Promise<"ok" | "limited"> {
  if (!(await rateHit(env, `waitlist:ip:${await sha256(ip)}`, WAITLIST_PER_IP, 3600))) return "limited";
  if (!(await rateHit(env, `waitlist:email:${await sha256(email)}`, WAITLIST_PER_ADDRESS, 3600))) return "ok";
  const known = await env.DB.prepare("SELECT 1 FROM users WHERE email = ? UNION SELECT 1 FROM invites WHERE email = ?")
    .bind(email, email).first();
  if (known) return "ok";
  const t = now();
  const token = randomHex(32);
  const hash = await sha256(token);
  const expires = t + WAITLIST_TOKEN_TTL_S;
  // New, or still pending and not emailed in the last 10 minutes: one of these
  // two claims the send, so two quick sign-ups send one email.
  let claimed = (await env.DB.prepare(
    `INSERT OR IGNORE INTO waitlist (email, source, created_at, token_hash, token_expires_at, sent_at)
     VALUES (?, ?, ?, ?, ?, ?)`).bind(email, source, t, hash, expires, t).run()).meta.changes > 0;
  if (!claimed) {
    claimed = (await env.DB.prepare(
      `UPDATE waitlist SET token_hash = ?, token_expires_at = ?, sent_at = ?
       WHERE email = ? AND confirmed_at IS NULL AND invited_at IS NULL AND (sent_at IS NULL OR sent_at <= ?)`)
      .bind(hash, expires, t, email, t - WAITLIST_RESEND_S).run()).meta.changes > 0;
  }
  if (!claimed) return "ok";
  const sent = await sendMail(env, email, waitlistConfirmEmail(`https://${env.APP_HOST}/api/waitlist/confirm?t=${token}`));
  // Not sent: the next sign-up may try again at once.
  if (!sent) await env.DB.prepare("UPDATE waitlist SET sent_at = NULL WHERE email = ? AND token_hash = ?").bind(email, hash).run();
  return "ok";
}

/** The link from the confirmation email: confirmed, and the link used up. */
export async function confirmWaitlist(env: Env, url: URL): Promise<Response> {
  const token = url.searchParams.get("t") || "";
  if (!/^[0-9a-f]{64}$/.test(token)) return waitlistExpiredPage();
  const r = await env.DB.prepare(
    `UPDATE waitlist SET confirmed_at = ?, token_hash = NULL, token_expires_at = NULL
     WHERE token_hash = ? AND token_expires_at > ? AND confirmed_at IS NULL`)
    .bind(now(), await sha256(token), now()).run();
  return r.meta.changes > 0 ? waitlistConfirmedPage() : waitlistExpiredPage();
}

/** Count one more hit on `key`; false (and not counted) once `limit` hits
 * fall within the last `windowS` seconds. */
export async function rateHit(env: Env, key: string, limit: number, windowS: number): Promise<boolean> {
  const t = now();
  const row = await env.DB.prepare("SELECT count(*) AS n FROM rate_hits WHERE key = ? AND at > ?")
    .bind(key, t - windowS).first<{ n: number }>();
  if ((row?.n ?? 0) >= limit) return false;
  await env.DB.batch([
    env.DB.prepare("INSERT INTO rate_hits (key, at) VALUES (?, ?)").bind(key, t),
    env.DB.prepare("DELETE FROM rate_hits WHERE at < ?").bind(t - 24 * 60 * 60),
  ]);
  return true;
}

/** Invite someone: they may sign up with this email. Emails them unless told
 * not to; says whether the email went. */
export async function invite(env: Env, email: string, send: boolean): Promise<boolean> {
  await env.DB.batch([
    env.DB.prepare("INSERT OR IGNORE INTO invites (email, created_at) VALUES (?, ?)").bind(email, now()),
    env.DB.prepare("UPDATE waitlist SET invited_at = ? WHERE email = ?").bind(now(), email),
  ]);
  return send && sendMail(env, email, inviteEmail(`https://${env.APP_HOST}/login`));
}

/** An invitation no one has used yet, taken back. */
export async function revokeInvite(env: Env, email: string): Promise<boolean> {
  const r = await env.DB.prepare("DELETE FROM invites WHERE email = ? AND used_at IS NULL").bind(email).run();
  return r.meta.changes > 0;
}

/** An unused invitation's email again; its "sent" is now. */
export async function resendInvite(env: Env, email: string): Promise<"sent" | "failed" | "none"> {
  const r = await env.DB.prepare("UPDATE invites SET created_at = ? WHERE email = ? AND used_at IS NULL")
    .bind(now(), email).run();
  if (!r.meta.changes) return "none";
  return (await sendMail(env, email, inviteEmail(`https://${env.APP_HOST}/login`))) ? "sent" : "failed";
}

// -- the audit log (W-863) --------------------------------------------------------
export async function logAction(env: Env, admin: string, action: string, target: string | null,
                                ok: boolean, result: string): Promise<void> {
  try {
    await env.DB.prepare("INSERT INTO admin_log (at, admin, action, target, ok, result) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(Math.floor(Date.now() / 1000), admin, action, target, ok ? 1 : 0, result).run();
  } catch (err) {
    // The action is done either way: a log that cannot be written is said, not thrown.
    console.error("admin log", action, target, err);
  }
}

// -- kits and setup codes (W-888) ------------------------------------------------
/** A kit flashed for shipping: its owner may set it up with no setup code.
 * Registering it again (a new key after an erase) keeps whether it was used. */
async function registerKit(env: Env, b: { device_id?: string; key_hash?: string; kit?: string; note?: string }): Promise<Response> {
  const id = String(b.device_id || "").trim();
  const hash = String(b.key_hash || "").trim().toLowerCase();
  if (!/^[0-9A-Za-z:_-]{4,40}$/.test(id) || !/^[0-9a-f]{64}$/.test(hash)) {
    return Response.json({ error: "device_id and key_hash (64 hex)" }, { status: 400 });
  }
  const kit = ["ee02", "ee03"].includes(String(b.kit)) ? String(b.kit) : "other";
  const note = String(b.note || "").slice(0, 120);
  await env.DB.prepare(`INSERT INTO kits (device_id, key_hash, kit, note, registered_at) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT (device_id) DO UPDATE SET key_hash = excluded.key_hash, kit = excluded.kit,
    note = coalesce(nullif(excluded.note, ''), kits.note), registered_at = excluded.registered_at`)
    .bind(id, hash, kit, note, now()).run();
  const result = `Registered ${kit} ${id.slice(-6)}.`;
  await logAction(env, "api", "kit", id, true, result);
  return Response.json({ ok: true, result });
}

export async function makeSetupCodes(env: Env, count: number, note: string): Promise<string[]> {
  const { newSetupCode } = await import("./setup");
  const n = Math.max(1, Math.min(100, Math.floor(count)));
  const codes = Array.from({ length: n }, () => newSetupCode());
  await env.DB.batch(codes.map((c) => env.DB.prepare(
    "INSERT OR IGNORE INTO setup_codes (code, note, created_at) VALUES (?, ?, ?)").bind(c, note.slice(0, 120) || null, now())));
  return codes;
}

// -- the admin's side, until there is a page for it ----------------------------
export async function admin(request: Request, env: Env, path: string): Promise<Response> {
  if (request.method !== "POST" || !env.ADMIN_TOKEN ||
      request.headers.get("Authorization") !== `Bearer ${env.ADMIN_TOKEN}`) {
    return new Response("not found", { status: 404 });
  }
  const body = await request.json<{ email?: string; household?: string; send?: boolean; device_id?: string;
    key_hash?: string; kit?: string; note?: string; count?: number }>().catch(() => ({} as Record<string, never>));
  if (path === "kit") return registerKit(env, body);
  if (path === "setup-codes") {
    const codes = await makeSetupCodes(env, Number(body.count) || 10, String(body.note || ""));
    await logAction(env, "api", "setup-codes", null, true, `Made ${codes.length} setup codes.`);
    return Response.json({ ok: true, codes: codes.map((c) => `${c.slice(0, 4)}-${c.slice(4)}`) });
  }
  const email = normEmail(body.email);
  if (!email) return Response.json({ error: "email" }, { status: 400 });
  if (path === "invite") {
    const sent = await invite(env, email, body.send !== false);
    await logAction(env, "api", "invite", email, true, sent ? `Invited ${email}.` : `Invited ${email}, no email sent.`);
    return Response.json({ ok: true, invited: email, emailed: sent });
  }
  if (path === "link") {
    // A sign-in link handed to the admin rather than emailed: for support,
    // and for testing without sending anyone mail.
    const link = await makeLoginLink(env, email, null);
    await logAction(env, "api", "link", email, !!link, link ? `Made a sign-in link for ${email}.` : `${email} is not invited.`);
    return link ? Response.json({ ok: true, link }) : Response.json({ error: "not invited" }, { status: 404 });
  }
  if (path === "adopt") {
    // Give an existing household (one made before accounts) its login.
    const hid = String(body.household || "");
    const exists = await env.DB.prepare("SELECT 1 FROM households WHERE id = ?").bind(hid).first();
    if (!exists) await env.DB.prepare("INSERT INTO households (id, tz, created_at) VALUES (?, 'UTC', ?)").bind(hid, now()).run();
    await env.DB.prepare("INSERT OR REPLACE INTO users (id, email, household_id, created_at, verified_at) VALUES (?, ?, ?, ?, ?)")
      .bind(randomHex(8), email, hid, now(), now()).run();
    await logAction(env, "api", "adopt", `${email} (${hid})`, true, `Gave ${hid} the login ${email}.`);
    return Response.json({ ok: true, household: hid, email });
  }
  return new Response("not found", { status: 404 });
}
