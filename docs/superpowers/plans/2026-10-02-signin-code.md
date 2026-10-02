# Sign-in with a code (W-947) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every form that emails a sign-in answers with a redirect to a code page, so a reloaded tab never sends again. The email carries a 6-digit code beside the link, and the code works only in the browser that asked for it. "Set up a new frame" becomes a link to its own page.

**Architecture:** A new module, `hosted/src/signin.ts`, owns sign-in requests: the `/login` form, `/login/code`, `/login/resend` and `/login/state`. Each request is a row in a new D1 table, `signin_requests`, named by an `ff_signin` cookie. `accounts.ts` keeps links and sessions; the end of a sign-in becomes one function, `finishSignIn`, shared by the link (`auth`) and the code. The setup page's "check your email" answers move onto the same code page.

**Tech Stack:** Cloudflare Worker (TypeScript), D1, vitest on node's SQLite (`hosted/test`), Resend for email.

**Spec:** `docs/superpowers/specs/2026-10-02-cloud-webapp-cache-design.md`, part 1.

## Global Constraints

- Copy is verbatim from the spec. Read `docs/STYLE.md` before changing any string. Sentence case, no emoji, "Featherframe Cloud".
- Every POST that can send an email answers 303. None answers 200 with a page.
- Uninvited, rate-limited and invited addresses get the same status, `Location`, cookie shape and code page. The email goes out in `ctx.waitUntil`, after the answer.
- The code is 6 digits, uniform, from `crypto.getRandomValues`. Its hash is `sha256(id_hash + code)`. 5 tries per request. The request lives 15 minutes, the same as the link.
- Cookie: `ff_signin=<64 hex>; Path=/login; Max-Age=900; HttpOnly; Secure; SameSite=Lax`.
- The new POSTs refuse a foreign `Origin` with 403.
- Hosted tests: `cd hosted && npx vitest run`. Typecheck: `cd hosted && npx tsc --noEmit -p .`. CI does not run hosted tests; run both before every commit.

## Review Focus

1. **Double submit.** A double-tap on "Email me a code" sends two emails and leaves the code from the first unusable (the cookie names the second). The second email's code works; the test pins that the newest code signs in.
2. **The code typed with spaces or a dash** ("123 456", pasted from Mail): non-digits are stripped before comparing. Test that "123 456" signs in.
3. **A code page opened after its cookie expired** (15 minutes): goes to `/login`, never a blank page or a 500. Test with no cookie.
4. **A signed-in owner who scans a frame's QR code:** the setup page's signed-in branch adds the frame as before and sends nothing. The existing test holds it; re-run it unchanged.
5. **An invitation's first sign-in by code** makes the household exactly as the link does: one `finishSignIn`. Test it.

---

### Task 1: Sign-in requests, the code in the email, and `/login` answering with a redirect

**Files:**
- Create: `hosted/migrations/0010_signin_requests.sql`
- Create: `hosted/src/signin.ts`
- Create: `hosted/test/signin.test.ts`
- Modify: `hosted/src/accounts.ts` (`makeLoginLink` → `createLink` + wrapper; move `login` out; drop the `checkEmailPage` import)
- Modify: `hosted/src/pages.ts` (`signInEmail(link, code)`, `addFrameEmail(link, frame, code)`, `codeEntryPage` stub for now, `.linkish` style)
- Modify: `hosted/src/index.ts` (`/login` routes to `signin.login` with `ctx`)
- Modify: `hosted/test/waitlist.test.ts` (`login` now comes from `../src/signin` and takes `ctx`)

**Interfaces:**
- Produces: `createLink(env, email, tz, pairCode = null): Promise<{ url: string; hash: string } | null>` in `accounts.ts`; `makeLoginLink` keeps its signature and returns `createLink(...)?.url ?? null`.
- Produces in `signin.ts`: `SIGNIN_COOKIE`, `SIGNIN_TTL_S`, `CODE_TRIES`, `SETUP_LINKS_PER_HOUR`, `type Kind = "login" | "setup"`, `interface Ask { email; kind; tz; pairCode; frame; back; quiet? }`, `sixDigits()`, `startSignIn(env, ctx, ask): Promise<Response>`, `login(request, env, ctx)`.
- Produces in `pages.ts`: `signInEmail(link: string, code: string)`, `addFrameEmail(link: string, frame: string, code: string)`.

- [ ] **Step 1: Write the migration**

`hosted/migrations/0010_signin_requests.sql`:

```sql
-- A sign-in one browser asked for (W-947). Its ff_signin cookie names the
-- row, so the six digits emailed beside the link work only in that browser,
-- five tries. link_hash and code_hash are NULL when nothing was sent (an
-- address that may not sign in, or asked too often): the page is the same.
CREATE TABLE signin_requests (
  id_hash    TEXT PRIMARY KEY,
  email      TEXT NOT NULL,
  kind       TEXT NOT NULL,
  link_hash  TEXT,
  code_hash  TEXT,
  attempts   INTEGER NOT NULL DEFAULT 0,
  tz         TEXT,
  pair_code  TEXT,
  frame      TEXT,
  back       TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX signin_requests_link ON signin_requests (link_hash);
```

- [ ] **Step 2: Write the failing tests**

`hosted/test/signin.test.ts`:

```ts
// @ts-nocheck: node:sqlite and node:fs have no types under the Worker's tsconfig.
// Signing in with a code beside the link (W-947, src/signin.ts): a sign-in is
// a request one browser made; the code works only there; every form that
// emails answers with a redirect. Run against the real D1 schema on node's SQLite.
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { login } from "../src/signin";
import { sha256 } from "../src/util";

function d1(db: DatabaseSync) {
  const stmt = (sql: string, args: unknown[] = []) => ({
    bind: (...a: unknown[]) => stmt(sql, a),
    first: async <T>() => (db.prepare(sql).get(...(args as never[])) ?? null) as T | null,
    all: async <T>() => ({ results: db.prepare(sql).all(...(args as never[])) as T[] }),
    run: async () => ({ meta: { changes: Number(db.prepare(sql).run(...(args as never[])).changes) } }),
  });
  return {
    prepare: (sql: string) => stmt(sql),
    batch: async (list: ReturnType<typeof stmt>[]) => { const out = []; for (const s of list) out.push(await s.run()); return out; },
  };
}

const MIGRATIONS = new URL("../migrations/", import.meta.url);
const HOST = "cloud.featherframe.app";
const NOW = Math.floor(Date.now() / 1000);
let db: DatabaseSync;
let env: any;
let mails: { to: string; subject: string; text: string }[];
let inits: any[];
let waits: Promise<unknown>[];
const ctx = { waitUntil: (p: Promise<unknown>) => waits.push(p) } as any;

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(new URL(f, MIGRATIONS), "utf8"));
  }
  mails = []; inits = []; waits = [];
  env = {
    DB: d1(db), ZONE: "featherframe.app", APP_HOST: HOST, MAIL_FROM: "Featherframe <hello@featherframe.app>",
    RESEND_API_KEY: "re_test", ADMIN_EMAILS: "",
    HOUSEHOLD: { getByName: (hid: string) => ({
      init: async (...a: unknown[]) => { inits.push([hid, ...a]); },
      adopt: async () => {},
    }) },
  };
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const b = JSON.parse(String(init.body));
    mails.push({ to: b.to[0], subject: b.subject, text: b.text });
    return new Response("{}");
  }));
});
afterEach(() => vi.unstubAllGlobals());

const one = (sql: string, ...a: unknown[]) => db.prepare(sql).get(...a) as any;

function account(email = "w@example.com") {
  db.prepare("INSERT INTO households (id, tz, created_at) VALUES ('h1', 'UTC', ?)").run(NOW);
  db.prepare("INSERT INTO users (id, email, household_id, created_at, verified_at) VALUES ('u1', ?, 'h1', ?, ?)")
    .run(email, NOW, NOW);
}

async function session(uid = "u1") {
  const token = "s".repeat(64);
  db.prepare("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)").run(await sha256(token), uid, NOW + 3600);
  return `ff_session=${token}`;
}

async function ask(email: string, headers: Record<string, string> = {}) {
  const res = await login(new Request(`https://${HOST}/login`, {
    method: "POST", body: new URLSearchParams({ email, tz: "America/New_York" }),
    headers: { Origin: `https://${HOST}`, ...headers },
  }), env, ctx);
  await Promise.all(waits);
  return res;
}

const idOf = (res: Response) => (res.headers.get("Set-Cookie") || "").match(/ff_signin=([0-9a-f]*)/)?.[1] ?? "";
const codeIn = (text: string) => text.match(/code is (\d{6})/)![1];
const linkIn = (text: string) => new URL(text.match(/https:\/\/\S+/)![0]);

describe("emailing a sign-in", () => {
  it("answers with a redirect, sets this browser's cookie, and emails a code beside the link", async () => {
    account();
    const res = await ask("W@example.com");
    expect(res.status).toBe(303);
    expect(res.headers.get("Location")).toBe("/login/code");
    const set = res.headers.get("Set-Cookie")!;
    expect(set).toMatch(/^ff_signin=[0-9a-f]{64}; Path=\/login; Max-Age=900; HttpOnly; Secure; SameSite=Lax$/);
    expect(mails).toHaveLength(1);
    expect(mails[0].subject).toMatch(/^Your Featherframe sign-in code: \d{6}$/);
    expect(mails[0].text).toContain(`Your sign-in code is ${mails[0].subject.slice(-6)}.`);
    expect(linkIn(mails[0].text).pathname).toBe("/auth");
    const row = one("SELECT * FROM signin_requests");
    expect(row).toMatchObject({ email: "w@example.com", kind: "login", attempts: 0, back: "/login" });
    expect(row.code_hash).toBe(await sha256(row.id_hash + codeIn(mails[0].text)));
    expect(row.id_hash).toBe(await sha256(idOf(res)));
  });

  it("answers an uninvited address the same, emails nothing, and puts it on the waitlist", async () => {
    account();
    const known = await ask("w@example.com");
    const stranger = await ask("new@example.com");
    expect(stranger.status).toBe(known.status);
    expect(stranger.headers.get("Location")).toBe(known.headers.get("Location"));
    expect(idOf(stranger)).toMatch(/^[0-9a-f]{64}$/);
    expect(mails.map((m) => m.to)).toEqual(["w@example.com"]);
    expect(one("SELECT code_hash, link_hash FROM signin_requests WHERE email = 'new@example.com'"))
      .toEqual({ code_hash: null, link_hash: null });
    expect(one("SELECT source FROM waitlist WHERE email = 'new@example.com'").source).toBe("login");
  });

  it("over the hourly limit, answers the same and sends nothing", async () => {
    account();
    for (let i = 0; i < 5; i++) await ask("w@example.com");
    const sixth = await ask("w@example.com");
    expect(sixth.status).toBe(303);
    expect(sixth.headers.get("Location")).toBe("/login/code");
    expect(mails).toHaveLength(5);
  });

  it("signed in already: POST and GET /login go to the webapp and send nothing", async () => {
    account();
    const cookie = await session();
    const posted = await ask("w@example.com", { Cookie: cookie });
    expect(posted.status).toBe(303);
    expect(posted.headers.get("Location")).toBe("/");
    expect(mails).toHaveLength(0);
    const got = await login(new Request(`https://${HOST}/login`, { headers: { Cookie: cookie } }), env, ctx);
    expect(got.headers.get("Location")).toBe("/");
  });

  it("refuses a form from another site", async () => {
    account();
    const res = await ask("w@example.com", { Origin: "https://evil.example" });
    expect(res.status).toBe(403);
    expect(mails).toHaveLength(0);
  });

  it("asks again with an address it can't read", async () => {
    const res = await ask("not an email");
    expect(res.status).toBe(200);
    expect(await res.text()).toContain("Enter an email address.");
  });
});
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `cd hosted && npx vitest run test/signin.test.ts`
Expected: FAIL, `Failed to load url ../src/signin`.

- [ ] **Step 4: Add `createLink` to `accounts.ts`**

Replace `makeLoginLink` (accounts.ts:41-51) with:

```ts
/** A sign-in link for `email` and its token's hash, or null when it may not
 * sign in. `pairCode` ("CODE:device"): following it also adds the frame
 * showing that code (W-888). */
export async function createLink(env: Env, email: string, tz: string | null,
                                 pairCode: string | null = null): Promise<{ url: string; hash: string } | null> {
  const known = await env.DB.prepare("SELECT 1 FROM users WHERE email = ?").bind(email).first();
  const invited = await env.DB.prepare("SELECT 1 FROM invites WHERE email = ? AND used_at IS NULL").bind(email).first();
  if (!known && !invited) return null;
  const token = randomHex(32);
  const hash = await sha256(token);
  await env.DB.prepare("INSERT INTO login_links (token_hash, email, tz, expires_at, pair_code) VALUES (?, ?, ?, ?, ?)")
    .bind(hash, email, tz, now() + LINK_TTL_S, pairCode).run();
  return { url: `https://${env.APP_HOST}/auth?t=${token}`, hash };
}

/** The link alone, for the admin API's `link`. */
export async function makeLoginLink(env: Env, email: string, tz: string | null,
                                    pairCode: string | null = null): Promise<string | null> {
  return (await createLink(env, email, tz, pairCode))?.url ?? null;
}
```

Delete `login()` from `accounts.ts` (lines 53-67); it moves to `signin.ts`. Remove `checkEmailPage`, `loginPage` and `signInEmail` from the `./pages` import at the top of `accounts.ts`, keeping the rest.

- [ ] **Step 5: Change the two emails and add the page stub in `pages.ts`**

Replace `signInEmail` (pages.ts:499-506) with:

```ts
export function signInEmail(link: string, code: string): { subject: string; text: string; html: string } {
  const e = escapeHtml;
  return {
    subject: `Your Featherframe sign-in code: ${code}`,
    text: `Your sign-in code is ${code}.\n\nOr sign in with this link:\n${link}\n\nThe code and the link work once, for 15 minutes. If you didn't ask to sign in, ignore this email.`,
    html: `<p>Your sign-in code is <strong style="font-size:20px;letter-spacing:2px">${e(code)}</strong>.</p>
<p>Or <a href="${e(link)}">sign in with this link</a>.</p>
<p style="color:#827e76">The code and the link work once, for 15 minutes. If you didn't ask to sign in, ignore this email.</p>`,
  };
}
```

Replace `addFrameEmail` (pages.ts:750-758) with:

```ts
export function addFrameEmail(link: string, frame: string, code: string): Mail {
  const e = escapeHtml;
  return {
    subject: `Your code to add a frame to Featherframe: ${code}`,
    text: `Someone scanned the code on a frame (${frame}) and asked to add it to your account. To add it and sign in, enter the code ${code}, or use this link:\n${link}\n\nThe code and the link work once, for 15 minutes. If this wasn't you, ignore this email.`,
    html: `<p>Someone scanned the code on a frame (${e(frame)}) and asked to add it to your account. To add it and sign in, enter the code <strong style="font-size:20px;letter-spacing:2px">${e(code)}</strong>, or <a href="${e(link)}">use this link</a>.</p>
<p style="color:#827e76">The code and the link work once, for 15 minutes. If this wasn't you, ignore this email.</p>`,
  };
}
```

Add to `STYLE` (pages.ts, after the `.muted` rule at line 50):

```css
  button.linkish { display:inline; width:auto; margin:0; padding:0; border:0; border-radius:0; background:none;
    color:var(--accent); font-weight:400; text-decoration:underline; cursor:pointer; }
```

Add the page, after `loginPage`, in full now (Task 2 tests it):

```ts
/** Where the code is typed (W-947). The same page for every address. */
export function codeEntryPage(v: { email: string; kind: "login" | "setup"; back: string; error: string; sent: boolean }): Response {
  const e = escapeHtml;
  const setup = v.kind === "setup";
  return page("Check your email · Featherframe", `
    <h1>Check your email</h1>
    <p>${setup ? `If ${e(v.email)} has a Featherframe Cloud account, we sent it a code that adds this frame.`
               : `If ${e(v.email)} has an invitation or an account, we sent it a code.`}</p>
    ${v.error ? `<p class="bad">${e(v.error)}</p>` : v.sent ? `<p>Sent a new code.</p>` : ""}
    <form method="post" action="/login/code" id="code-form">
      <label for="code">Code</label>
      <input type="text" id="code" name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9 ]{6,7}"
        maxlength="7" required autofocus style="letter-spacing:.2em">
      <button type="submit">${setup ? "Add this frame" : "Sign in"}</button>
    </form>
    <p class="muted" style="margin:16px 0">The code and the link in the email work once, for 15 minutes.</p>
    ${setup ? `<p>Built this frame yourself? Featherframe Cloud is invite-only for now. <a href="https://featherframe.app">Join the waitlist</a>, and we'll email you an invitation.</p>` : ""}
    <form method="post" action="/login/resend" style="margin:0"><p style="margin:0"><button type="submit" class="linkish">Send a new code</button>
      · <a href="${e(v.back)}">Use a different email</a> · <a href="https://featherframe.app/help/account">Help</a></p></form>
    <script>(function(){var f=document.getElementById("code-form"),i=document.getElementById("code");
      i.addEventListener("input",function(){if(i.value.replace(/\\D/g,"").length===6){f.requestSubmit?f.requestSubmit():f.submit();}});
      function look(){if(document.hidden)return;fetch("/login/state",{credentials:"same-origin"})
        .then(function(r){return r.json();}).then(function(s){if(s.signedIn)location.replace("/");}).catch(function(){});}
      document.addEventListener("visibilitychange",look);window.addEventListener("focus",look);})();</script>`);
}
```

(`pattern` and `maxlength` allow "123 456", which Review Focus 2 needs. The server strips non-digits.)

- [ ] **Step 6: Write `signin.ts`**

`hosted/src/signin.ts`:

```ts
// Signing in with a code beside the link (W-947). Every "email me" is a
// request one browser made: its ff_signin cookie names the row, so the six
// digits emailed with the link work only in that browser, five tries. Every
// form that sends an email answers with a redirect to /login/code, so a tab
// the browser reloads or restores never sends again.

import type { Env } from "./index";
import { createLink, joinWaitlist, LOGIN_LINKS_PER_HOUR, normEmail, rateHit, realSessionUser,
         sendMail } from "./accounts";
import { addFrameEmail, loginPage, signInEmail } from "./pages";
import { randomHex, sha256, validTz } from "./util";

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
```

- [ ] **Step 7: Route `/login` through `signin.ts`**

In `hosted/src/index.ts`, remove `login` from the `./accounts` import (line 17) and add:

```ts
import { login } from "./signin";
```

Change `if (path === "/login") return login(request, env);` (index.ts:82) to:

```ts
    if (path === "/login") return login(request, env, ctx);
```

- [ ] **Step 8: Point the waitlist test at the new `login`**

In `hosted/test/waitlist.test.ts`, change the import to `import { confirmWaitlist, joinWaitlist } from "../src/accounts";` and add `import { login } from "../src/signin";`. In "joins pending and is not emailed" (waitlist.test.ts:168-176), change the call to pass a context:

```ts
    await login(new Request("https://cloud.featherframe.app/login",
      { method: "POST", body: new URLSearchParams({ email: "k@example.com" }) }), env,
      { waitUntil: () => {} } as any);
```

- [ ] **Step 9: Run the tests and the typecheck**

Run: `cd hosted && npx vitest run && npx tsc --noEmit -p .`
Expected: all pass; tsc exits 0. If `setup.test.ts` fails on `addFrameEmail`'s arity, Task 4 fixes the caller. Pass `""` as the code in `setup.ts:170` for now so the build stays green.

- [ ] **Step 10: Commit**

```bash
git add hosted/migrations/0010_signin_requests.sql hosted/src/signin.ts hosted/src/accounts.ts hosted/src/pages.ts hosted/src/index.ts hosted/src/setup.ts hosted/test/signin.test.ts hosted/test/waitlist.test.ts
git commit -m "Sign-in answers with a redirect and emails a code beside the link (W-947)"
```

---

### Task 2: The code page, checking a code, and one way to finish a sign-in

**Files:**
- Modify: `hosted/src/accounts.ts` (`LinkRow`, `finishSignIn` out of `auth`; `auth` claims the link and drops its request)
- Modify: `hosted/src/signin.ts` (`showCode`, `checkCode`)
- Modify: `hosted/src/index.ts` (route `/login/code`)
- Test: `hosted/test/signin.test.ts`

**Interfaces:**
- Consumes: `startSignIn`, `signinCookie`, `redirect`, `foreign`, `SIGNIN_COOKIE`, `CODE_TRIES` from Task 1; `codeEntryPage(v)` from Task 1.
- Produces: `type LinkRow = { email; tz; expires_at; used_at; pair_code }` and `finishSignIn(env, row: Pick<LinkRow, "email" | "tz" | "pair_code">): Promise<Response>` in `accounts.ts`; `showCode(request, env, url)` and `checkCode(request, env)` in `signin.ts`.

- [ ] **Step 1: Write the failing tests**

Append to `hosted/test/signin.test.ts` (add `auth` to an `import { auth } from "../src/accounts";` line and `checkCode, showCode` to the `../src/signin` import):

```ts
const page = (id: string, q = "") => showCode(
  new Request(`https://${HOST}/login/code${q}`, { headers: { Cookie: `ff_signin=${id}` } }), env,
  new URL(`https://${HOST}/login/code${q}`));

async function enter(id: string, code: string, origin = `https://${HOST}`) {
  return checkCode(new Request(`https://${HOST}/login/code`, {
    method: "POST", body: new URLSearchParams({ code }), headers: { Cookie: `ff_signin=${id}`, Origin: origin },
  }), env);
}

describe("the code page", () => {
  it("is found by this browser's cookie, and a reload sends nothing", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    for (let i = 0; i < 2; i++) {
      const html = await (await page(id)).text();
      expect(html).toContain("If w@example.com has an invitation or an account, we sent it a code.");
      expect(html).toContain('autocomplete="one-time-code"');
      expect(html).toContain("Send a new code");
    }
    expect(mails).toHaveLength(1);
  });

  it("goes to /login with no cookie or someone else's", async () => {
    expect((await showCode(new Request(`https://${HOST}/login/code`), env, new URL(`https://${HOST}/login/code`)))
      .headers.get("Location")).toBe("/login");
    expect((await page("ab".repeat(32))).headers.get("Location")).toBe("/login");
  });

  it("says a code has expired", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    db.prepare("UPDATE signin_requests SET expires_at = ?").run(NOW - 1);
    expect(await (await page(id)).text()).toContain("That code has expired. Send a new code.");
    expect((await enter(id, codeIn(mails[0].text))).headers.get("Location")).toBe("/login/code?e=expired");
  });
});

describe("checking a code", () => {
  it("signs in with the right code, once", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    const res = await enter(id, codeIn(mails[0].text));
    expect(res.headers.get("Location")).toBe("/");
    const cookies = res.headers.get("Set-Cookie")!;
    expect(cookies).toContain("ff_session=");
    expect(cookies).toContain("ff_signin=; Path=/login; Max-Age=0");
    expect(one("SELECT used_at FROM login_links").used_at).not.toBeNull();
    expect((await enter(id, codeIn(mails[0].text))).headers.get("Location")).toBe("/login");
  });

  it("takes the code with a space in it", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    const c = codeIn(mails[0].text);
    expect((await enter(id, `${c.slice(0, 3)} ${c.slice(3)}`)).headers.get("Location")).toBe("/");
  });

  it("counts wrong codes; the fifth ends it, even before the right one", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    const right = codeIn(mails[0].text);
    const wrong = right === "000000" ? "111111" : "000000";
    for (let i = 0; i < 4; i++) expect((await enter(id, wrong)).headers.get("Location")).toBe("/login/code?e=wrong");
    expect((await enter(id, wrong)).headers.get("Location")).toBe("/login/code?e=tries");
    expect((await enter(id, right)).headers.get("Location")).toBe("/login/code?e=tries");
    expect(await (await page(id, "?e=wrong")).text()).toContain("That code doesn't match.");
  });

  it("answers any code for an address that was sent nothing as a wrong one", async () => {
    const id = idOf(await ask("new@example.com"));
    expect((await enter(id, "123456")).headers.get("Location")).toBe("/login/code?e=wrong");
  });

  it("works only in the browser that asked", async () => {
    account();
    await ask("w@example.com");
    expect((await enter("cd".repeat(32), codeIn(mails[0].text))).headers.get("Location")).toBe("/login");
  });

  it("the newest of two requests is the one that works", async () => {
    account();
    await ask("w@example.com");
    const second = idOf(await ask("w@example.com"));
    expect((await enter(second, codeIn(mails[1].text))).headers.get("Location")).toBe("/");
  });

  it("the link and the code use each other up", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    const link = linkIn(mails[0].text);
    expect((await auth(new Request(link), env, link)).headers.get("Location")).toBe("/");
    expect((await enter(id, codeIn(mails[0].text))).headers.get("Location")).toBe("/login");

    const id2 = idOf(await ask("w@example.com"));
    expect((await enter(id2, codeIn(mails[1].text))).headers.get("Location")).toBe("/");
    const link2 = linkIn(mails[1].text);
    expect(await (await auth(new Request(link2), env, link2)).text()).toContain("That link has expired");
  });

  it("an invitation's first sign-in by code makes the household, as the link does", async () => {
    db.prepare("INSERT INTO invites (email, created_at) VALUES ('new@example.com', ?)").run(NOW);
    const id = idOf(await ask("new@example.com"));
    expect((await enter(id, codeIn(mails[0].text))).headers.get("Location")).toBe("/");
    const u = one("SELECT household_id, verified_at FROM users WHERE email = 'new@example.com'");
    expect(u.verified_at).not.toBeNull();
    expect(inits.map((i) => i[0])).toEqual([u.household_id]);
    expect(one("SELECT used_at FROM invites").used_at).not.toBeNull();
  });

  it("refuses a code posted from another site", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    expect((await enter(id, codeIn(mails[0].text), "https://evil.example")).status).toBe(403);
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd hosted && npx vitest run test/signin.test.ts`
Expected: FAIL, `showCode` and `checkCode` are not exported.

- [ ] **Step 3: Factor `finishSignIn` out of `auth` in `accounts.ts`**

Replace `auth()` (accounts.ts, the function starting "The link from the email: sign in") with:

```ts
export type LinkRow = { email: string; tz: string | null; expires_at: number; used_at: number | null;
                        pair_code: string | null };

/** The link from the email. Using it uses up the code sent beside it (W-947). */
export async function auth(request: Request, env: Env, url: URL): Promise<Response> {
  const hash = await sha256(url.searchParams.get("t") || "");
  const row = await env.DB.prepare(
    "SELECT email, tz, expires_at, used_at, pair_code FROM login_links WHERE token_hash = ?")
    .bind(hash).first<LinkRow>();
  if (!row || row.used_at || row.expires_at < now()) return linkExpiredPage();
  const used = await env.DB.prepare("UPDATE login_links SET used_at = ? WHERE token_hash = ? AND used_at IS NULL")
    .bind(now(), hash).run();
  if (!used.meta.changes) return linkExpiredPage();
  await env.DB.prepare("DELETE FROM signin_requests WHERE link_hash = ?").bind(hash).run();
  return finishSignIn(env, row);
}

/** Sign in as a used link's email, whether by the link or its code: on an
 * invitation's first use the household is made. */
export async function finishSignIn(env: Env, row: Pick<LinkRow, "email" | "tz" | "pair_code">): Promise<Response> {
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
  // Following an emailed link, or typing its code, proves the address (W-889).
  await env.DB.prepare("UPDATE users SET verified_at = ? WHERE id = ? AND verified_at IS NULL").bind(now(), user.id).run();
  // A link from the setup page (W-888): the frame whose code was scanned
  // joins this account, if it is still showing that code.
  let landing = "/";
  if (row.pair_code && await pairLinked(env, row.pair_code, user.household_id)) landing = "/?paired=1";
  return signedIn(env, user.id, landing);
}
```

- [ ] **Step 4: Add `showCode` and `checkCode` to `signin.ts`**

Add `cookie` to the `./util` import, `finishSignIn, type LinkRow` to the `./accounts` import, `codeEntryPage` to the `./pages` import, and append:

```ts
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
```

- [ ] **Step 5: Route `/login/code`**

In `hosted/src/index.ts`, extend the `./signin` import to `import { checkCode, login, showCode } from "./signin";` and add after the `/login` line:

```ts
    if (path === "/login/code") return request.method === "POST" ? checkCode(request, env) : showCode(request, env, url);
```

- [ ] **Step 6: Run the tests and the typecheck**

Run: `cd hosted && npx vitest run && npx tsc --noEmit -p .`
Expected: all pass, tsc exits 0. The existing `setup.test.ts` link test ("sends an existing account a link…") still passes through `auth`.

- [ ] **Step 7: Commit**

```bash
git add hosted/src/accounts.ts hosted/src/signin.ts hosted/src/index.ts hosted/test/signin.test.ts
git commit -m "Type the emailed code to sign in, in the browser that asked (W-947)"
```

---

### Task 3: Send a new code, `/login/state`, and the rate limiter

**Files:**
- Modify: `hosted/src/signin.ts` (`resendCode`, `signInState`)
- Modify: `hosted/src/index.ts` (routes)
- Modify: `hosted/src/ratelimit.ts` (GET pages on `RL_PAGE`)
- Test: `hosted/test/signin.test.ts`, `hosted/test/setup.test.ts` ("rate limits")

**Interfaces:**
- Consumes: `issue`, `requestOf`, `redirect`, `signinCookie`, `foreign` from Tasks 1–2.
- Produces: `resendCode(request, env, ctx)`, `signInState(request, env)`.

- [ ] **Step 1: Write the failing tests**

Append to `hosted/test/signin.test.ts` (add `resendCode, signInState` to the `../src/signin` import):

```ts
async function resend(id: string) {
  const res = await resendCode(new Request(`https://${HOST}/login/resend`, {
    method: "POST", headers: { Cookie: `ff_signin=${id}`, Origin: `https://${HOST}` },
  }), env, ctx);
  await Promise.all(waits);
  return res;
}

describe("sending a new code", () => {
  it("sends a new code; the old code stops working, the old link does not", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    const res = await resend(id);
    expect(res.headers.get("Location")).toBe("/login/code?sent=1");
    expect(res.headers.get("Set-Cookie")).toContain(`ff_signin=${id}; Path=/login; Max-Age=900`);
    expect(mails).toHaveLength(2);
    expect(await (await page(id, "?sent=1")).text()).toContain("Sent a new code.");
    const old = codeIn(mails[0].text), fresh = codeIn(mails[1].text);
    if (old !== fresh) expect((await enter(id, old)).headers.get("Location")).toBe("/login/code?e=wrong");
    const link = linkIn(mails[0].text);
    expect((await auth(new Request(link), env, link)).headers.get("Location")).toBe("/");
  });

  it("resets the tries", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    for (let i = 0; i < 5; i++) await enter(id, "000000");
    await resend(id);
    expect((await enter(id, codeIn(mails[1].text))).headers.get("Location")).toBe("/");
  });

  it("over the hourly limit, says so and sends nothing", async () => {
    account();
    let id = "";
    for (let i = 0; i < 5; i++) id = idOf(await ask("w@example.com"));
    expect((await resend(id)).headers.get("Location")).toBe("/login/code?e=limited");
    expect(mails).toHaveLength(5);
    expect(await (await page(id, "?e=limited")).text())
      .toContain("Already sent a few times. Check your email, or try again in an hour.");
  });

  it("answers an uninvited address the same, and sends nothing", async () => {
    const id = idOf(await ask("new@example.com"));
    expect((await resend(id)).headers.get("Location")).toBe("/login/code?sent=1");
    expect(mails).toHaveLength(0);
  });
});

describe("/login/state", () => {
  it("says whether this browser is signed in", async () => {
    account();
    const no = await signInState(new Request(`https://${HOST}/login/state`), env);
    expect(await no.json()).toEqual({ signedIn: false });
    expect(no.headers.get("Cache-Control")).toBe("no-store");
    const yes = await signInState(new Request(`https://${HOST}/login/state`, { headers: { Cookie: await session() } }), env);
    expect(await yes.json()).toEqual({ signedIn: true });
  });
});
```

In `hosted/test/setup.test.ts` "puts sign-in, setup and pairing on the strict limiter" (setup.test.ts:290-300), add after `expect(r("/login").name).toBe("RL_PAGE");`:

```ts
    expect(r("/login/code").name).toBe("RL_PAGE");
    expect(r("/login/state").name).toBe("RL_PAGE");
    expect(r("/login/code", "POST").name).toBe("RL_AUTH");
    expect(r("/login/resend", "POST").name).toBe("RL_AUTH");
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd hosted && npx vitest run test/signin.test.ts test/setup.test.ts`
Expected: FAIL. `resendCode` and `signInState` are not exported, and `/login/code` GET is `RL_AUTH`.

- [ ] **Step 3: Add `resendCode` and `signInState` to `signin.ts`**

```ts
/** POST /login/resend: the same request, a new code and a new link. The
 * old email's link keeps working until it expires; its code does not. */
export async function resendCode(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  if (foreign(request, env)) return new Response("forbidden", { status: 403 });
  const row = await requestOf(request, env);
  if (!row) return redirect("/login");
  const ask: Ask = { email: row.email, kind: row.kind, tz: row.tz, pairCode: row.pair_code,
                     frame: row.frame || "", back: row.back || "/login" };
  const { linkHash, codeHash, limited } = await issue(env, ctx, ask, row.id_hash);
  if (limited) return redirect("/login/code?e=limited");
  await env.DB.prepare(
    "UPDATE signin_requests SET link_hash = ?, code_hash = ?, attempts = 0, expires_at = ? WHERE id_hash = ?")
    .bind(linkHash, codeHash, now() + SIGNIN_TTL_S, row.id_hash).run();
  return redirect("/login/code?sent=1", signinCookie(cookie(request, SIGNIN_COOKIE)!));
}

/** GET /login/state: whether this browser is signed in, for a code page left
 * open while the emailed link signed it in from another tab. */
export async function signInState(request: Request, env: Env): Promise<Response> {
  return Response.json({ signedIn: !!(await realSessionUser(request, env)) },
                       { headers: { "Cache-Control": "no-store" } });
}
```

- [ ] **Step 4: Route them**

In `hosted/src/index.ts`, extend the import to `import { checkCode, login, resendCode, showCode, signInState } from "./signin";` and add after the `/login/code` line:

```ts
    if (path === "/login/resend" && request.method === "POST") return resendCode(request, env, ctx);
    if (path === "/login/state" && request.method === "GET") return signInState(request, env);
```

- [ ] **Step 5: Put the sign-in pages' GETs on `RL_PAGE`**

In `hosted/src/ratelimit.ts`, replace the `AUTH_PATHS` condition (ratelimit.ts:14-15) with:

```ts
// Pages an owner only looks at (the sign-in form, the code page and its
// signed-in check, the admin page) are ordinary browsing.
const PAGE_GETS = /^\/(login|login\/code|login\/state|admin)$/i;

  if (AUTH_PATHS.test(path) && !(request.method === "GET" && PAGE_GETS.test(path))) return { name: "RL_AUTH", key: `ip:${ip}` };
```

(The `PAGE_GETS` constant goes beside `AUTH_PATHS` at the top of the file; the `if` replaces the existing one inside `limiterFor`.)

- [ ] **Step 6: Run the tests and the typecheck**

Run: `cd hosted && npx vitest run && npx tsc --noEmit -p .`
Expected: all pass, tsc exits 0.

- [ ] **Step 7: Commit**

```bash
git add hosted/src/signin.ts hosted/src/index.ts hosted/src/ratelimit.ts hosted/test/signin.test.ts hosted/test/setup.test.ts
git commit -m "Send a new code, and a code page that follows a sign-in in another tab (W-947)"
```

---

### Task 4: The setup page's emails go through the code page, and "Set up a new frame" is a link

**Files:**
- Modify: `hosted/src/setup.ts` (known and uninvited branches, the race branch, `typedCode`)
- Modify: `hosted/src/pages.ts` (`loginPage`, new `setupCodePage`; delete `checkEmailPage` and `setupLinkSentPage`)
- Modify: `hosted/src/accounts.ts` (`deleteHousehold` drops the login's sign-in requests)
- Modify: `AGENTS.md` (the Hosted paragraph names the code)
- Test: `hosted/test/setup.test.ts`, `hosted/test/signin.test.ts`

**Interfaces:**
- Consumes: `startSignIn(env, ctx, ask)`, `showCode`, `checkCode` from Tasks 1–2.
- Produces: `setupCodePage(error = "", code = ""): Response`; `loginPage(error = ""): Response` (the code parameters are gone).

- [ ] **Step 1: Write the failing tests**

In `hosted/test/setup.test.ts`, import `showCode, checkCode` from `../src/signin` and add the helpers after `post()`:

```ts
const codePage = async (res: Response) => (await showCode(
  new Request(`https://${HOST}/login/code`, { headers: { Cookie: res.headers.get("Set-Cookie")!.split(";")[0] } }),
  env, new URL(`https://${HOST}/login/code`))).text();
```

Replace the body of "sends an existing account a link that adds the frame, and makes nothing" (setup.test.ts:168-186) with:

```ts
    db.prepare("INSERT INTO households (id, tz, created_at) VALUES ('h1', 'UTC', ?)").run(NOW);
    db.prepare("INSERT INTO users (id, email, household_id, created_at) VALUES ('u1', 'old@example.com', 'h1', ?)").run(NOW);
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at) VALUES (?, ?, 'ee03', ?)").run(f.device, f.keyHash, NOW);
    const res = await post(f, { email: "old@example.com" });
    await Promise.all(waits);
    expect(res.status).toBe(303);
    expect(res.headers.get("Location")).toBe("/login/code");
    const page = await codePage(res);
    expect(page).toContain("If old@example.com has a Featherframe Cloud account, we sent it a code that adds this frame.");
    expect(page).toContain(">Add this frame</button>");
    expect(page).toContain('href="https://featherframe.app/help/account"');
    expect(page).toContain(`href="/setup/${f.code}/${f.token}"`);
    expect(one("SELECT count(*) AS n FROM households").n).toBe(1);
    expect(one("SELECT used_at FROM kits").used_at).toBeNull();
    expect(mails.map((m) => m.subject)).toEqual([expect.stringMatching(/^Your code to add a frame to Featherframe: \d{6}$/)]);
    // Typing the code signs in and adds the frame.
    const code = mails[0].text.match(/enter the code (\d{6})/)![1];
    const signed = await checkCode(new Request(`https://${HOST}/login/code`, {
      method: "POST", body: new URLSearchParams({ code }),
      headers: { Cookie: res.headers.get("Set-Cookie")!.split(";")[0], Origin: `https://${HOST}` },
    }), env);
    expect(signed.headers.get("Location")).toBe("/?paired=1");
    expect(one("SELECT household_id FROM frames WHERE device_id = ?", f.device).household_id).toBe("h1");
    expect(adopts.map((a) => a[0])).toEqual(["h1"]);
```

Add, in the same `describe`:

```ts
  it("the add-a-frame link still works on its own", async () => {
    db.prepare("INSERT INTO households (id, tz, created_at) VALUES ('h1', 'UTC', ?)").run(NOW);
    db.prepare("INSERT INTO users (id, email, household_id, created_at) VALUES ('u1', 'old@example.com', 'h1', ?)").run(NOW);
    const f = await frameShowing();
    await post(f, { email: "old@example.com" });
    await Promise.all(waits);
    const link = new URL(mails[0].text.match(/https:\/\/\S+/)![0]);
    expect((await auth(new Request(link), env, link)).headers.get("Location")).toBe("/?paired=1");
  });
```

In "answers an uninvited email as it answers an account, and puts it on the waitlist" (setup.test.ts:153-166), compare the code pages instead of the POST bodies:

```ts
    const strangerRes = await post(f, { email: "new@example.com" });
    const g = await frameShowing("GHJKMN", "112233445566");
    const ownerRes = await post(g, { email: "old@example.com" }, "203.0.113.2");
    expect(strangerRes.status).toBe(ownerRes.status);
    expect(strangerRes.headers.get("Location")).toBe(ownerRes.headers.get("Location"));
    const stranger = (await codePage(strangerRes)).replace(/\/setup\/[a-z]+\/[0-9a-z]+/gi, "BACK");
    const owner = (await codePage(ownerRes)).replace(/\/setup\/[a-z]+\/[0-9a-z]+/gi, "BACK");
    expect(stranger.replace(/new@example\.com/g, "X")).toBe(owner.replace(/old@example\.com/g, "X"));
```

(Keep the waitlist, users and mail assertions that follow. The mail check becomes `expect(mails.map((m) => m.to)).toEqual(["old@example.com"]);`, unchanged.)

In `describe("a code typed on the sign-in page")` (setup.test.ts:304), add:

```ts
  it("has its own page, and the sign-in page links to it", async () => {
    const page = await (await setupRoute(new Request(`https://${HOST}/setup`), env, new URL(`https://${HOST}/setup`), ctx)).text();
    expect(page).toContain("<h1>Set up a new frame</h1>");
    expect(page).toContain("Enter the code on your frame's screen.");
    expect(page).toContain('href="/login"');
    const { loginPage } = await import("../src/pages");
    const signIn = await loginPage().text();
    expect(signIn).toContain('<a href="/setup">Set up a new frame</a>');
    expect(signIn).toContain(">Email me a code</button>");
    expect(signIn).not.toContain('name="code"');
  });
```

In `hosted/test/signin.test.ts`, add:

```ts
describe("deleting a household", () => {
  it("drops its login's sign-in requests", async () => {
    account();
    await ask("w@example.com");
    env.HOUSEHOLD = { getByName: () => ({ destroy: async () => 0 }) };
    const { deleteHousehold } = await import("../src/accounts");
    await deleteHousehold(env, "h1");
    expect(one("SELECT count(*) AS n FROM signin_requests").n).toBe(0);
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd hosted && npx vitest run test/setup.test.ts test/signin.test.ts`
Expected: FAIL. The setup POST answers 200 "Check your email", `GET /setup` redirects, and the sign-in page still has the code form.

- [ ] **Step 3: Change `setup.ts`**

Imports: drop `makeLoginLink` and `sendMail` from the `./accounts` import (keep `sendVerification`, `joinWaitlist`, `normEmail`, `rateHit`, `sessionUser`, `signedIn`). Replace `loginPage` and `setupLinkSentPage` in the `./pages` import with `setupCodePage`, and drop `addFrameEmail`. Add `import { startSignIn } from "./signin";`. Delete `export const LINKS_PER_ADDRESS = 5;` (setup.ts:21). It is now `SETUP_LINKS_PER_HOUR` in `signin.ts`.

In `typedCode` (setup.ts:36-54), replace the GET redirect and the three `loginPage(...)` calls:

```ts
  if (request.method !== "POST") return setupCodePage();
  ...
  if (!(await rateHit(env, `setup:code:${ip}`, CODE_TRIES_PER_HOUR, 3600))) {
    return setupCodePage("Too many tries. Try again in an hour, or scan the QR code on the frame.", typed);
  }
  if (code.length !== 6) return setupCodePage("A code is six letters, like ABC-DEF.", typed);
  ...
  if (!row) return setupCodePage("No frame is showing that code. Check the code on your frame's screen.", typed);
```

In `setupRoute`, define once, after `const tz = validTz(...)` (setup.ts:161):

```ts
  // The code page's "Use a different email" comes back here.
  const ask = { email, kind: "setup" as const, tz, frame: row.device_id.slice(-6), back: url.pathname };
```

Replace the `known` branch (setup.ts:164-173):

```ts
  if (known) {
    // Their account, their inbox: the code or the link adds the frame (W-947).
    return startSignIn(env, ctx, { ...ask, pairCode: `${row.code}:${row.device_id}` });
  }
```

Replace the uninvited branch's `return setupLinkSentPage(email);` (setup.ts:182):

```ts
    return startSignIn(env, ctx, { ...ask, pairCode: null });
```

Replace the race branch's `return setupLinkSentPage(email);` (setup.ts:213):

```ts
    return startSignIn(env, ctx, { ...ask, pairCode: null, quiet: true });
```

- [ ] **Step 4: Change `pages.ts`**

Replace `loginPage` (pages.ts:461-483) with:

```ts
export function loginPage(error = ""): Response {
  const e = escapeHtml;
  return page("Sign in · Featherframe", `
    <h1>Sign in</h1>
    ${error ? `<p class="bad">${e(error)}</p>` : ""}
    <form method="post" action="/login">
      <label for="email">Email</label>
      <input type="email" id="email" name="email" autocomplete="email" required autofocus>
      <input type="hidden" name="tz" id="tz">
      <button type="submit">Email me a code</button>
    </form>
    <p class="muted" style="margin:16px 0 0"><a href="/setup">Set up a new frame</a></p>
    <script>try{document.getElementById("tz").value=Intl.DateTimeFormat().resolvedOptions().timeZone}catch(e){}</script>`);
}

/** A frame's six letters, typed (W-891): its own page since W-947. */
export function setupCodePage(error = "", code = ""): Response {
  const e = escapeHtml;
  return page("Set up a new frame · Featherframe", `
    <h1>Set up a new frame</h1>
    <p>Enter the code on your frame's screen.</p>
    ${error ? `<p class="bad">${e(error)}</p>` : ""}
    <form method="post" action="/setup">
      <label for="code">Code</label>
      <input type="text" id="code" name="code" required autocomplete="off" autocapitalize="characters" spellcheck="false"
        maxlength="9" placeholder="ABC-DEF" value="${e(code)}" autofocus style="text-transform:uppercase;letter-spacing:.08em">
      <button type="submit">Continue</button>
    </form>
    <p class="muted" style="margin:16px 0 0"><a href="/login">Sign in</a></p>`);
}
```

Delete `checkEmailPage` (pages.ts:485-490) and `setupLinkSentPage` (pages.ts:684-693).

- [ ] **Step 5: Drop a deleted household's sign-in requests**

In `deleteHousehold` (accounts.ts), add to the `ofUser` list after the `email_verifications` delete:

```ts
    env.DB.prepare("DELETE FROM signin_requests WHERE email = ?").bind(user.email),
```

- [ ] **Step 6: Name the code in AGENTS.md**

In AGENTS.md's Hosted paragraph, replace `routes the page by session (magic link from
Resend, \`accounts.ts\`;` with:

```
routes the page by session (a magic link from Resend, or the six-digit
code emailed beside it, typed in the browser that asked, `accounts.ts`,
`signin.ts`, W-947; every form that emails answers with a redirect to
`/login/code`, so a reloaded tab sends nothing;
```

- [ ] **Step 7: Run the tests, the typecheck, and the copy check**

Run: `cd hosted && npx vitest run && npx tsc --noEmit -p .`
Expected: all pass, tsc exits 0.

Run: `cd .. && grep -rn "setupLinkSentPage\|checkEmailPage\|LINKS_PER_ADDRESS\|Email me a link" hosted/src hosted/test`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add hosted/src/setup.ts hosted/src/pages.ts hosted/src/accounts.ts hosted/test/setup.test.ts hosted/test/signin.test.ts AGENTS.md
git commit -m "The setup page's emails send a code too, and Set up a new frame has its own page (W-947)"
```

---

### Task 5: Ship and check on Cloud

**Files:** none new.

- [ ] **Step 1: Run everything once more**

Run: `cd hosted && npx vitest run && npx tsc --noEmit -p .` and, from the repo root, `make test`.
Expected: all pass.

- [ ] **Step 2: Push and open the PR**

Branch: `wells/w-947-sign-in-a-code-beside-the-link-no-repeat-email-from-a`. Title: "Sign-in: a code beside the link, no repeat email from a reloaded tab, and Set up a new frame as a link (W-947)". The body says what changed and why, how it was verified, and what is left (the phone checks in Step 5). End it with `Refs W-947`.

- [ ] **Step 3: Merge once CI is green, then migrate and deploy**

```bash
gh pr merge --squash --delete-branch
```

From an up-to-date `origin/main` checkout:

```bash
cd hosted
npx wrangler d1 migrations apply featherframe --remote
npx wrangler deploy
```

Expected: migration `0010_signin_requests.sql` applied; deploy succeeds.

- [ ] **Step 4: Check it on Cloud without sending anyone an email**

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://cloud.featherframe.app/login/code
curl -s https://cloud.featherframe.app/login | grep -c "Email me a code"
curl -s https://cloud.featherframe.app/setup | grep -c "Set up a new frame"
```

Expected: `303 https://cloud.featherframe.app/login`, `1`, and at least `1`.

- [ ] **Step 5: Hand Wells the phone checks**

Ask Wells to check on his phone:
1. Sign out, sign in by code in Safari. The keyboard should offer the code from Mail.
2. In Firefox, request a sign-in, open the link from Mail, then open the tab switcher. No second email should arrive.

Record what he finds in a Linear comment on `W-947`. If Mail's code isn't offered, nothing else changes; the spec makes no claim that depends on it.
