// Signing in with a code beside the link (W-947). Every "email me" is a
// request one browser made: its ff_signin cookie names the row, so the six
// digits emailed with the link work only in that browser, five tries. Every
// form that sends an email answers with a redirect to /login/code, so a tab
// the browser reloads or restores never sends again.

import type { Env } from "./index";
import { createLink, finishSignIn, joinWaitlist, type LinkRow, LOGIN_LINKS_PER_HOUR, normEmail, rateHit,
         realSessionUser, sendMail } from "./accounts";
import { addFrameEmail, codeEntryPage, loginPage, signInEmail } from "./pages";
import { cookie, randomHex, sha256, validTz } from "./util";

export const SIGNIN_COOKIE = "ff_signin";
export const SIGNIN_TTL_S = 15 * 60;
export const CODE_TRIES = 5;
export const SETUP_LINKS_PER_HOUR = 5;   // add-this-frame emails an hour to one address

export type Kind = "login" | "setup";
export interface Ask {
  email: string;
  kind: Kind;
  tz: string | null;
  pairCode: string | null;   // "CODE:device": the link and the code also add that frame
  frame: string;             // the frame's short id, for the add-a-frame email
  back: string;              // where "Use a different email" goes
  quiet?: boolean;           // send nothing, whoever the address is
}

const now = () => Math.floor(Date.now() / 1000);

/** Six digits, every value equally likely. */
export function sixDigits(): string {
  const buf = new Uint32Array(1);
  for (;;) {
    crypto.getRandomValues(buf);
    if (buf[0] < 4_294_000_000) return String(buf[0] % 1_000_000).padStart(6, "0");
  }
}

export function signinCookie(id: string, maxAge = SIGNIN_TTL_S): string {
  return `${SIGNIN_COOKIE}=${id}; Path=/login; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Lax`;
}

export function redirect(location: string, setCookie?: string): Response {
  const headers = new Headers({ Location: location, "Cache-Control": "no-store" });
  if (setCookie) headers.append("Set-Cookie", setCookie);
  return new Response(null, { status: 303, headers });
}

export function foreign(request: Request, env: Env): boolean {
  const origin = request.headers.get("Origin");
  return !!origin && origin !== `https://${env.APP_HOST}`;
}

/** A link and a code for `ask`, emailed after the answer leaves. Nothing is
 * sent to an address that may not sign in, or that asked too often: the
 * caller answers the same either way. */
export async function issue(env: Env, ctx: ExecutionContext, ask: Ask, idHash: string):
    Promise<{ linkHash: string | null; codeHash: string | null; limited: boolean }> {
  const none = { linkHash: null, codeHash: null, limited: false };
  if (ask.quiet) return none;
  const setup = ask.kind === "setup";
  const key = `${setup ? "setup:link" : "login"}:${await sha256(ask.email)}`;
  if (!(await rateHit(env, key, setup ? SETUP_LINKS_PER_HOUR : LOGIN_LINKS_PER_HOUR, 3600))) {
    return { ...none, limited: true };
  }
  const link = await createLink(env, ask.email, ask.tz, ask.pairCode);
  if (!link) {
    // Asked to come in: on the waitlist, pending, and not emailed.
    if (!setup) await joinWaitlist(env, ask.email, "login");
    return none;
  }
  const code = sixDigits();
  ctx.waitUntil(sendMail(env, ask.email, setup ? addFrameEmail(link.url, ask.frame, code) : signInEmail(link.url, code)));
  return { linkHash: link.hash, codeHash: await sha256(idHash + code), limited: false };
}

/** Every form that emails a sign-in: a new request for this browser, then
 * the code page. */
export async function startSignIn(env: Env, ctx: ExecutionContext, ask: Ask): Promise<Response> {
  const id = randomHex(32);
  const idHash = await sha256(id);
  const { linkHash, codeHash } = await issue(env, ctx, ask, idHash);
  const t = now();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM signin_requests WHERE expires_at < ?").bind(t - 24 * 60 * 60),
    env.DB.prepare(`INSERT INTO signin_requests (id_hash, email, kind, link_hash, code_hash, attempts, tz,
      pair_code, frame, back, created_at, expires_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)`)
      .bind(idHash, ask.email, ask.kind, linkHash, codeHash, ask.tz, ask.pairCode, ask.frame, ask.back,
            t, t + SIGNIN_TTL_S),
  ]);
  return redirect("/login/code", signinCookie(id));
}

/** /login: the form, and what it sends. */
export async function login(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  // Signed in already: a tab reloaded or restored sends nothing (W-947).
  if (await realSessionUser(request, env)) return redirect("/");
  if (request.method === "GET") return loginPage();
  if (request.method !== "POST") return new Response(null, { status: 405 });
  if (foreign(request, env)) return new Response("forbidden", { status: 403 });
  const form = await request.formData();
  const email = normEmail(form.get("email"));
  if (!email) return loginPage("Enter an email address.");
  return startSignIn(env, ctx, { email, kind: "login", tz: validTz(String(form.get("tz") || "")),
                                 pairCode: null, frame: "", back: "/login" });
}

type Row = {
  id_hash: string; email: string; kind: Kind; link_hash: string | null; code_hash: string | null;
  attempts: number; tz: string | null; pair_code: string | null; frame: string | null; back: string | null;
  expires_at: number;
};

const ERRORS: Record<string, string> = {
  wrong: "That code doesn't match.",
  tries: "Too many tries. Send a new code.",
  expired: "That code has expired. Send a new code.",
  limited: "Already sent a few times. Check your email, or try again in an hour.",
};

export async function requestOf(request: Request, env: Env): Promise<Row | null> {
  const id = cookie(request, SIGNIN_COOKIE) || "";
  if (!/^[0-9a-f]{64}$/.test(id)) return null;
  return env.DB.prepare("SELECT * FROM signin_requests WHERE id_hash = ?").bind(await sha256(id)).first<Row>();
}

/** GET /login/code: where the code is typed. */
export async function showCode(request: Request, env: Env, url: URL): Promise<Response> {
  if (await realSessionUser(request, env)) return redirect("/");
  const row = await requestOf(request, env);
  if (!row) return redirect("/login");
  const e = row.expires_at <= now() ? "expired" : url.searchParams.get("e") || "";
  return codeEntryPage({ email: row.email, kind: row.kind, back: row.back || "/login",
                         error: ERRORS[e] || "", sent: !e && url.searchParams.get("sent") === "1" });
}

/** POST /login/code. A request that sent nothing (an address that may not
 * sign in) answers every code as a wrong one. */
export async function checkCode(request: Request, env: Env): Promise<Response> {
  if (foreign(request, env)) return new Response("forbidden", { status: 403 });
  const row = await requestOf(request, env);
  if (!row) return redirect("/login");
  if (row.expires_at <= now()) return redirect("/login/code?e=expired");
  if (row.attempts >= CODE_TRIES) return redirect("/login/code?e=tries");
  const code = String((await request.formData()).get("code") || "").replace(/\D/g, "");
  const right = !!row.code_hash && code.length === 6 && (await sha256(row.id_hash + code)) === row.code_hash;
  if (!right) {
    await env.DB.prepare("UPDATE signin_requests SET attempts = attempts + 1 WHERE id_hash = ?").bind(row.id_hash).run();
    return redirect(`/login/code?e=${row.attempts + 1 >= CODE_TRIES ? "tries" : "wrong"}`);
  }
  // The code and its link are one sign-in: whichever is used first takes both.
  const taken = await env.DB.prepare("DELETE FROM signin_requests WHERE id_hash = ?").bind(row.id_hash).run();
  const used = await env.DB.prepare(
    "UPDATE login_links SET used_at = ? WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?")
    .bind(now(), row.link_hash, now()).run();
  if (!taken.meta.changes || !used.meta.changes) return redirect("/login");
  const link = await env.DB.prepare("SELECT email, tz, pair_code FROM login_links WHERE token_hash = ?")
    .bind(row.link_hash).first<Pick<LinkRow, "email" | "tz" | "pair_code">>();
  const res = await finishSignIn(env, link!);
  res.headers.append("Set-Cookie", signinCookie("", 0));
  return res;
}
