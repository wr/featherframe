// @ts-nocheck: node:sqlite and node:fs have no types under the Worker's tsconfig.
// The waitlist's double opt-in (src/accounts.ts, src/admin.ts): a sign-up is
// pending until its emailed link is followed. Run against the real D1 schema
// (every migration, in order) on node's own SQLite.
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { confirmWaitlist, joinWaitlist, login } from "../src/accounts";
import { waitlistRoute } from "../src/admin";
import { httpsRedirect } from "../src/util";

// Just enough of D1 for these queries.
function d1(db: DatabaseSync) {
  const stmt = (sql: string, args: unknown[] = []) => ({
    bind: (...a: unknown[]) => stmt(sql, a),
    first: async <T>() => (db.prepare(sql).get(...(args as never[])) ?? null) as T | null,
    all: async <T>() => ({ results: db.prepare(sql).all(...(args as never[])) as T[] }),
    run: async () => ({ meta: { changes: Number(db.prepare(sql).run(...(args as never[])).changes) } }),
  });
  return {
    prepare: (sql: string) => stmt(sql),
    batch: async (list: ReturnType<typeof stmt>[]) => Promise.all(list.map((s) => s.run())),
  };
}

const MIGRATIONS = new URL("../migrations/", import.meta.url);
let db: DatabaseSync;
let env: any;
let mails: { to: string; subject: string; text: string }[];

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(new URL(f, MIGRATIONS), "utf8"));
  }
  env = { DB: d1(db), ZONE: "featherframe.app", APP_HOST: "cloud.featherframe.app",
          MAIL_FROM: "Featherframe <hello@featherframe.app>", RESEND_API_KEY: "re_test" };
  mails = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const b = JSON.parse(String(init.body));
    mails.push({ to: b.to[0], subject: b.subject, text: b.text });
    return new Response("{}", { status: 200 });
  }));
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const signUp = (email: string, { ip = "203.0.113.1", form = false } = {}) => waitlistRoute(new Request(
  "https://cloud.featherframe.app/api/waitlist", form
    ? { method: "POST", body: new URLSearchParams({ email }), headers: { "CF-Connecting-IP": ip, Origin: "https://featherframe.app" } }
    : { method: "POST", body: JSON.stringify({ email }),
        headers: { "Content-Type": "application/json", "CF-Connecting-IP": ip, Origin: "https://featherframe.app" } }), env);
const row = (email: string) => db.prepare("SELECT * FROM waitlist WHERE email = ?").get(email) as any;
const linkIn = (text: string) => new URL(text.match(/https:\/\/\S+/)![0]);

describe("signing up", () => {
  it("stores the address pending, emails one link, and answers ok with CORS", async () => {
    const res = await signUp("A@Example.com");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("https://featherframe.app");
    const r = row("a@example.com");
    expect(r.confirmed_at).toBeNull();
    expect(r.source).toBe("site");
    expect(mails).toHaveLength(1);
    expect(mails[0].subject).toBe("Confirm your Featherframe updates");
    const link = linkIn(mails[0].text);
    expect(link.pathname).toBe("/api/waitlist/confirm");
    // Only a hash is kept.
    expect(r.token_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(r.token_hash).not.toBe(link.searchParams.get("t"));
  });

  it("rejects a bad address", async () => {
    const res = await signUp("not-an-email");
    expect(res.status).toBe(400);
    expect(mails).toHaveLength(0);
  });

  it("re-sends to a pending address at most once per 10 minutes", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-27T12:00:00Z"));
    await signUp("b@example.com");
    await signUp("b@example.com");
    expect(mails).toHaveLength(1);
    vi.setSystemTime(new Date("2026-09-27T12:10:01Z"));
    await signUp("b@example.com");
    expect(mails).toHaveLength(2);
    // The new link replaces the old one.
    expect((await confirmWaitlist(env, linkIn(mails[0].text))).status).toBe(200);
    expect(row("b@example.com").confirmed_at).toBeNull();
    await confirmWaitlist(env, linkIn(mails[1].text));
    expect(row("b@example.com").confirmed_at).not.toBeNull();
  });

  it("answers a confirmed, invited or signed-up address the same, and emails none of them", async () => {
    await signUp("c@example.com");
    await confirmWaitlist(env, linkIn(mails[0].text));
    db.exec("INSERT INTO invites (email, created_at) VALUES ('d@example.com', 1)");
    db.exec("INSERT INTO households (id, created_at) VALUES ('h', 1)");
    db.exec("INSERT INTO users (id, email, household_id, created_at) VALUES ('u', 'e@example.com', 'h', 1)");
    for (const e of ["c@example.com", "d@example.com", "e@example.com"]) {
      const res = await signUp(e, { ip: `198.51.100.${e.charCodeAt(0)}` });
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual({ ok: true });
    }
    expect(mails).toHaveLength(1);
  });

  it("the form without script gets the check-your-email page", async () => {
    const res = await signUp("f@example.com", { form: true });
    const html = await res.text();
    expect(html).toContain("Almost there");
    expect(html).toContain("Check your email for a link to confirm.");
  });

  it("limits sign-ups per IP", async () => {
    for (let i = 0; i < 5; i++) expect((await signUp(`g${i}@example.com`)).status).toBe(200);
    const res = await signUp("g5@example.com");
    expect(res.status).toBe(429);
    expect((await res.json() as any).ok).toBe(false);
    expect(mails).toHaveLength(5);
  });

  it("limits sign-ups per address, quietly", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const t0 = Date.parse("2026-09-27T12:00:00Z");
    // Eleven minutes apart, so the 10-minute spacing would allow each one.
    for (let i = 0; i < 6; i++) {
      vi.setSystemTime(t0 + i * 11 * 60 * 1000);
      expect((await signUp("h@example.com", { ip: `192.0.2.${i}` })).status).toBe(200);
    }
    // Five in the hour are emailed; the sixth (55 min in) is answered the same, and not emailed.
    expect(mails).toHaveLength(5);
  });
});

describe("confirming", () => {
  it("confirms once; the link then reads as expired", async () => {
    await signUp("i@example.com");
    const link = linkIn(mails[0].text);
    const ok = await confirmWaitlist(env, link);
    expect(await ok.text()).toContain("I'll write once, when the frames have shipped.");
    const r = row("i@example.com");
    expect(r.confirmed_at).not.toBeNull();
    expect(r.token_hash).toBeNull();
    expect(await (await confirmWaitlist(env, link)).text()).toContain("That link has expired");
  });

  it("expires a link after 7 days", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-01T00:00:00Z"));
    await signUp("j@example.com");
    vi.setSystemTime(new Date("2026-09-08T00:00:01Z"));
    expect(await (await confirmWaitlist(env, linkIn(mails[0].text))).text()).toContain("Sign up again at");
    expect(row("j@example.com").confirmed_at).toBeNull();
  });

  it("an unknown token is an expired link", async () => {
    const res = await confirmWaitlist(env, new URL("https://cloud.featherframe.app/api/waitlist/confirm?t=nope"));
    expect(await res.text()).toContain("That link has expired");
  });
});

describe("an uninvited sign-in", () => {
  it("joins pending and is not emailed", async () => {
    await login(new Request("https://cloud.featherframe.app/login",
      { method: "POST", body: new URLSearchParams({ email: "k@example.com" }) }), env);
    expect(row("k@example.com")).toMatchObject({ source: "login", confirmed_at: null });
    expect(mails).toHaveLength(0);
    // A later sign-up from the site does send the link.
    await signUp("k@example.com");
    expect(mails).toHaveLength(1);
  });

  it("joinWaitlist keeps an existing row as it is", async () => {
    await signUp("l@example.com");
    await confirmWaitlist(env, linkIn(mails[0].text));
    await joinWaitlist(env, "l@example.com", "login");
    expect(row("l@example.com").source).toBe("site");
    expect(row("l@example.com").confirmed_at).not.toBeNull();
  });
});

describe("the migration", () => {
  it("counts rows from before confirmation as confirmed", () => {
    const old = new DatabaseSync(":memory:");
    const files = readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort();
    for (const f of files.filter((f) => f < "0006")) old.exec(readFileSync(new URL(f, MIGRATIONS), "utf8"));
    old.exec("INSERT INTO waitlist (email, source, created_at) VALUES ('m@example.com', 'site', 42)");
    for (const f of files.filter((f) => f >= "0006")) old.exec(readFileSync(new URL(f, MIGRATIONS), "utf8"));
    expect(old.prepare("SELECT confirmed_at FROM waitlist").get()).toEqual({ confirmed_at: 42 });
  });
});

describe("httpsRedirect", () => {
  it("sends plain http to https with a 301, path and query kept", () => {
    const res = httpsRedirect(new URL("http://cloud.featherframe.app/api/frame?x=1"))!;
    expect(res.status).toBe(301);
    expect(res.headers.get("Location")).toBe("https://cloud.featherframe.app/api/frame?x=1");
  });
  it("leaves https and local dev alone", () => {
    expect(httpsRedirect(new URL("https://cloud.featherframe.app/"))).toBeNull();
    expect(httpsRedirect(new URL("http://localhost:8787/"))).toBeNull();
  });
});
