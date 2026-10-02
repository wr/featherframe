// @ts-nocheck: node:sqlite and node:fs have no types under the Worker's tsconfig.
// Signing in with a code beside the link (W-947, src/signin.ts): a sign-in is
// a request one browser made; the code works only there; every form that
// emails answers with a redirect. Run against the real D1 schema on node's SQLite.
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { auth } from "../src/accounts";
import { checkCode, login, resendCode, showCode, signInState } from "../src/signin";
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
    expect(await (await page(id, "?e=wrong")).text()).toContain("That code doesn&#39;t match.");
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

describe("found in review", () => {
  it("a mangled error in the URL is no error, never a 500", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    for (const e of ["constructor", "__proto__", "toString", "nonsense"]) {
      const res = await page(id, `?e=${e}`);
      expect(res.status).toBe(200);
      expect(await res.text()).not.toContain('class="bad"');
    }
  });

  it("lets the browser submit a code with a dash or a space: the server reads the digits", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    expect(await (await page(id)).text()).not.toContain(" pattern=");
    const c = codeIn(mails[0].text);
    expect((await enter(id, `${c.slice(0, 3)}-${c.slice(3)}`)).headers.get("Location")).toBe("/");
  });

  it("the code page sends a signed-in browser to the webapp", async () => {
    account();
    const id = idOf(await ask("w@example.com"));
    const res = await showCode(new Request(`https://${HOST}/login/code`,
      { headers: { Cookie: `ff_signin=${id}; ${await session()}` } }), env, new URL(`https://${HOST}/login/code`));
    expect(res.headers.get("Location")).toBe("/");
  });
});
