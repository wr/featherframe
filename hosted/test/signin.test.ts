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
