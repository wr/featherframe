// @ts-nocheck: node:sqlite and node:fs have no types under the Worker's tsconfig.
// The admin's actions (W-914, src/admin.ts): deleting a household leaves
// nothing that names it or its login but the audit log, an admin can't delete
// their own, and the page and the bearer API run the one table of actions.
// Run against the real D1 schema (every migration, in order) on node's SQLite.
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deleteHousehold } from "../src/accounts";
import { adminRoute, apiRoute } from "../src/admin";
import { sha256 } from "../src/util";

// The front door is a Durable Object; stand its base class in.
vi.mock("cloudflare:workers", () => ({
  DurableObject: class { ctx: unknown; env: unknown; constructor(ctx: unknown, env: unknown) { this.ctx = ctx; this.env = env; } },
}));
// Its viewers' module bundles the /view page, which only the Worker's build reads.
vi.mock("../src/viewers", () => ({ display: vi.fn(), lobbyPng: vi.fn(), shortOf: vi.fn(), trmnlHeaders: vi.fn() }));
const { Household } = await import("../src/household");

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
let db: DatabaseSync;
let env: any;
let destroyed: string[];
let suspended: [string, boolean][];

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(new URL(f, MIGRATIONS), "utf8"));
  }
  destroyed = []; suspended = [];
  env = {
    DB: d1(db), ZONE: "featherframe.app", APP_HOST: HOST, MAIL_FROM: "Featherframe <hello@featherframe.app>",
    RESEND_API_KEY: "re_test", ADMIN_EMAILS: "admin@example.com", ADMIN_TOKEN: "tok",
    HOUSEHOLD: { getByName: (hid: string) => ({
      destroy: async (id: string) => { destroyed.push(id); return 7; },
      suspend: async (on: boolean) => { suspended.push([hid, on]); },
      summary: async () => ({ frames: [], usage: [], month_ms: 0, last_wake: null, source: null, suspended: false, ai: { usd: 0, limit: null, paused: false, count: 0 } }),
    }) },
  };
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
});
afterEach(() => vi.unstubAllGlobals());

const count = (table: string) => (db.prepare(`SELECT count(*) AS n FROM ${table}`).get() as any).n;

/** A household with a login, and something of it in every table that names it. */
async function household(hid: string, email: string, uid = `u-${hid}`) {
  db.prepare("INSERT INTO households (id, created_at, apprise_token) VALUES (?, 1, ?)").run(hid, `tok-${hid}`);
  db.prepare("INSERT INTO users (id, email, household_id, created_at) VALUES (?, ?, ?, 1)").run(uid, email, hid);
  db.prepare("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)").run(`s-${hid}`, uid, 9e9);
  db.prepare("INSERT INTO login_links (token_hash, email, expires_at) VALUES (?, ?, 9e9)").run(`l-${hid}`, email);
  db.prepare("INSERT INTO email_changes (token_hash, user_id, email, expires_at) VALUES (?, ?, 'new@example.com', 9e9)").run(`c-${hid}`, uid);
  db.prepare("INSERT INTO email_verifications (token_hash, user_id, email, expires_at) VALUES (?, ?, ?, 9e9)").run(`v-${hid}`, uid, email);
  db.prepare("INSERT INTO invites (email, created_at, used_at) VALUES (?, 1, 2)").run(email);
  db.prepare("INSERT INTO waitlist (email, created_at, invited_at, confirmed_at) VALUES (?, 1, 2, 1)").run(email);
  db.prepare("INSERT INTO rate_hits (key, at) VALUES (?, ?)").run(`login:${await sha256(email)}`, Math.floor(Date.now() / 1000));
  db.prepare("INSERT INTO rate_hits (key, at) VALUES (?, ?)").run(`verify:${uid}`, Math.floor(Date.now() / 1000));
  db.prepare("INSERT INTO frames (device_id, household_id, key_hash, paired_at) VALUES (?, ?, 'k', 1)").run(`AA${hid}01`, hid);
  db.prepare("INSERT INTO frames (device_id, household_id, key_hash, paired_at) VALUES (?, ?, 'k', 1)").run(`AA${hid}02`, hid);
  db.prepare("INSERT INTO kits (device_id, key_hash, registered_at, used_at, household_id) VALUES (?, 'k', 1, 2, ?)").run(`AA${hid}01`, hid);
}

/** Every row still naming `hid` or its login, table by table. */
async function leftOf(hid: string, email: string, uid: string) {
  const login = `login:${await sha256(email)}`;
  const q = (sql: string, ...a: string[]) => (db.prepare(sql).get(...a) as any).n;
  return {
    households: q("SELECT count(*) AS n FROM households WHERE id = ?", hid),
    users: q("SELECT count(*) AS n FROM users WHERE household_id = ? OR email = ?", hid, email),
    sessions: q("SELECT count(*) AS n FROM sessions WHERE user_id = ?", uid),
    login_links: q("SELECT count(*) AS n FROM login_links WHERE email = ?", email),
    email_changes: q("SELECT count(*) AS n FROM email_changes WHERE user_id = ?", uid),
    email_verifications: q("SELECT count(*) AS n FROM email_verifications WHERE user_id = ? OR email = ?", uid, email),
    invites: q("SELECT count(*) AS n FROM invites WHERE email = ?", email),
    waitlist: q("SELECT count(*) AS n FROM waitlist WHERE email = ?", email),
    rate_hits: q("SELECT count(*) AS n FROM rate_hits WHERE key = ? OR key = ?", login, `verify:${uid}`),
    frames: q("SELECT count(*) AS n FROM frames WHERE household_id = ?", hid),
    kits: q("SELECT count(*) AS n FROM kits WHERE household_id = ?", hid),
  };
}

const none = { households: 0, users: 0, sessions: 0, login_links: 0, email_changes: 0, email_verifications: 0,
               invites: 0, waitlist: 0, rate_hits: 0, frames: 0, kits: 0 };

describe("deleting a household", () => {
  it("leaves nothing that names it or its login, and says what went", async () => {
    await household("h1", "one@example.com");
    await household("h2", "two@example.com");
    const gone = await deleteHousehold(env, "h1");
    expect(gone).toEqual({ frames: 2, files: 7 });
    expect(destroyed).toEqual(["h1"]);
    expect(await leftOf("h1", "one@example.com", "u-h1")).toEqual(none);
    // The kit may be set up again.
    expect(db.prepare("SELECT used_at, household_id FROM kits WHERE device_id = 'AAh101'").get())
      .toEqual({ used_at: null, household_id: null });
    // The other household is untouched.
    expect(await leftOf("h2", "two@example.com", "u-h2")).toMatchObject({ households: 1, users: 1, sessions: 1, frames: 2,
      invites: 1, waitlist: 1, email_verifications: 1 });
  });

  it("finishes a delete that stopped part way", async () => {
    await household("h1", "one@example.com");
    env.HOUSEHOLD.getByName = () => ({ destroy: async () => { throw new Error("container busy"); } });
    await expect(deleteHousehold(env, "h1")).rejects.toThrow("container busy");
    expect(count("households")).toBe(1);
    env.HOUSEHOLD.getByName = () => ({ destroy: async () => 0 });
    expect(await deleteHousehold(env, "h1")).toEqual({ frames: 0, files: 0 });
    expect(await leftOf("h1", "one@example.com", "u-h1")).toEqual(none);
  });
});

describe("the front door's part", () => {
  function frontDoor(meta: Record<string, string>, pages: string[][]) {
    const calls: string[] = [];
    const sql = {
      exec: (q: string, ...a: unknown[]) => {
        if (q.startsWith("INSERT OR REPLACE INTO meta")) meta[String(a[0])] = String(a[1]);
        const rows = q.startsWith("SELECT v FROM meta") && meta[String(a[0])] ? [{ v: meta[String(a[0])] }] : [];
        return { toArray: () => rows, one: () => rows[0], columnNames: ["wake_ms", "page_ms"] };
      },
    };
    const sockets = [{ send: () => calls.push("told"), close: () => calls.push("closed") }];
    const ctx = { storage: { sql, deleteAlarm: async () => { calls.push("alarm off"); },
                             deleteAll: async () => { calls.push("storage emptied"); } },
                  getWebSockets: () => sockets };
    let page = 0;
    const denv = {
      SERVER: { getByName: (hid: string) => ({ forget: async () => { calls.push(`server ${hid} forgotten`); } }) },
      DATA: {
        list: async ({ prefix }: { prefix: string }) => {
          calls.push(`list ${prefix}`);
          const objects = (pages[page] || []).map((key) => ({ key }));
          page++;
          return { objects, truncated: page < pages.length, cursor: String(page) };
        },
        delete: async (keys: string[]) => { calls.push(`delete ${keys.length}`); },
      },
    };
    return { door: new Household(ctx as never, denv as never), calls, meta };
  }

  it("stops everything that could wake it, then deletes its server, files and storage", async () => {
    const { door, calls, meta } = frontDoor({ hid: "h1", key: "k" }, [["a", "b"], ["c"]]);
    expect(await door.destroy("h1")).toBe(3);
    expect(meta.suspended).toBe("1");
    expect(calls).toEqual(["alarm off", "told", "closed", "server h1 forgotten", "list households/h1/", "delete 2",
                           "list households/h1/", "delete 1", "storage emptied"]);
  });

  it("uses the id it is handed, even if it was never told its own", async () => {
    const { door, calls } = frontDoor({}, [["a"]]);
    expect(await door.destroy("wells")).toBe(1);
    expect(calls).toContain("server wells forgotten");
    expect(calls).toContain("list households/wells/");
  });
});

// -- the page ------------------------------------------------------------------------
async function signIn(email: string, hid: string) {
  if (!db.prepare("SELECT 1 FROM households WHERE id = ?").get(hid)) {
    db.prepare("INSERT INTO households (id, created_at) VALUES (?, 1)").run(hid);
  }
  db.prepare("INSERT INTO users (id, email, household_id, created_at) VALUES (?, ?, ?, 1)").run(`u-${hid}`, email, hid);
  db.prepare("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)")
    .run(await sha256("admin-session"), `u-${hid}`, 9e9);
}

function post(path: string, fields: Record<string, string>, origin = `https://${HOST}`) {
  const url = new URL(`https://${HOST}${path}`);
  return adminRoute(new Request(url, { method: "POST", body: new URLSearchParams(fields),
    headers: { Cookie: "ff_session=admin-session", Origin: origin } }), env, url);
}
const toast = (res: Response) => decodeURIComponent((res.headers.get("Set-Cookie") || "").match(/ff_admin_toast=(?:ok|bad)\.([^;]*)/)?.[1] || "");
const log = () => db.prepare("SELECT admin, action, target, ok, result FROM admin_log ORDER BY id").all() as any[];

describe("the admin page", () => {
  beforeEach(() => signIn("admin@example.com", "mine"));

  it("deletes a household when its email is typed back, and says what went", async () => {
    await household("h1", "one@example.com");
    const wrong = await post("/admin/household/delete", { id: "h1", confirm: "one@example" });
    expect(toast(wrong)).toBe("Not deleted: type one@example.com to delete it.");
    expect(count("households")).toBe(2);
    const res = await post("/admin/household/delete", { id: "h1", confirm: " One@Example.com " });
    expect(res.status).toBe(303);
    expect(res.headers.get("Location")).toBe("/admin");
    expect(toast(res)).toBe("Deleted one@example.com: 2 frames show a pairing code, 7 files removed.");
    expect(destroyed).toEqual(["h1"]);
    expect(log().at(-1)).toEqual({ admin: "admin@example.com", action: "household.delete", target: "one@example.com (h1)",
      ok: 1, result: "Deleted one@example.com: 2 frames show a pairing code, 7 files removed." });
  });

  it("won't delete the admin's own household", async () => {
    const res = await post("/admin/household/delete", { id: "mine", confirm: "admin@example.com" });
    expect(toast(res)).toBe("You can't delete your own household.");
    expect(destroyed).toEqual([]);
    expect(count("households")).toBe(1);
  });

  it("deletes a household with no login by its id", async () => {
    db.exec("INSERT INTO households (id, created_at) VALUES ('orphan', 1)");
    const res = await post("/admin/household/delete", { id: "orphan", confirm: "orphan" });
    expect(toast(res)).toBe("Deleted orphan: 7 files removed.");
    expect(count("households")).toBe(1);
  });

  it("runs the rest of the table by path, and logs each", async () => {
    await household("h1", "one@example.com");
    expect(toast(await post("/admin/household/suspend", { id: "h1" }))).toBe("Suspended one@example.com.");
    expect(toast(await post("/admin/household/resume", { id: "h1" }))).toBe("Resumed one@example.com.");
    expect(suspended).toEqual([["h1", true], ["h1", false]]);
    expect(toast(await post("/admin/household/email", { id: "h1", email: "admin@example.com" })))
      .toBe("admin@example.com already has a login.");
    expect(toast(await post("/admin/household/suspend", { id: "nope" }))).toBe("No such household.");
    db.exec("INSERT INTO waitlist (email, created_at) VALUES ('w@example.com', 1)");
    expect(toast(await post("/admin/waitlist/remove", { email: "w@example.com" }))).toBe("Removed w@example.com from the waitlist.");
    const as = await post("/admin/household/as", { id: "h1" });
    expect(as.headers.get("Location")).toBe("/");
    expect(as.headers.get("Set-Cookie")).toContain("ff_as=h1");
    expect(log().map((l) => [l.action, l.ok])).toEqual([
      ["household.suspend", 1], ["household.resume", 1], ["household.email", 0], ["household.suspend", 0],
      ["waitlist.remove", 1], ["household.as", 1]]);
  });

  it("is not there for anyone else, another site, or an API-only action", async () => {
    expect((await post("/admin/household/delete", { id: "mine" }, "https://evil.example")).status).toBe(403);
    expect((await post("/admin/kit", { device_id: "AABBCCDDEEFF" })).status).toBe(404);
    expect((await post("/admin/nothing", {})).status).toBe(404);
    db.exec("UPDATE users SET email = 'someone@example.com'");
    expect((await post("/admin/household/suspend", { id: "mine" })).status).toBe(404);
  });

  it("offers every household's Delete but the admin's own", async () => {
    await household("h1", "one@example.com");
    const url = new URL(`https://${HOST}/admin`);
    const res = await adminRoute(new Request(url, { headers: { Cookie: "ff_session=admin-session" } }), env, url);
    const html = await res.text();
    expect(html).toContain("Delete one@example.com?");
    expect(html).toContain("Its login, server and data are deleted, and its 2 frames show a pairing code.");
    expect(html).not.toContain("Delete admin@example.com?");
  });
});

// -- the API ---------------------------------------------------------------------
function api(name: string, body: unknown, token = "tok") {
  return apiRoute(new Request(`https://${HOST}/_admin/${name}`, { method: "POST", body: JSON.stringify(body),
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } }), env, name);
}

describe("the admin API", () => {
  it("registers a kit and answers with its result (register_kit.py reads it)", async () => {
    const res = await api("kit", { device_id: "10B41DE886D8", key_hash: "ab".repeat(32), kit: "ee03", note: "bench" });
    expect(await res.json()).toEqual({ ok: true, result: "Registered ee03 E886D8." });
    expect(count("kits")).toBe(1);
    expect((await api("kit", { device_id: "000000000000", key_hash: "ab".repeat(32) })).status).toBe(400);
    expect(log().map((l) => [l.admin, l.action, l.target, l.ok])).toEqual([
      ["api", "kit", "10B41DE886D8", 1], ["api", "kit", "000000000000", 0]]);
  });

  it("invites, emailing unless told not to, and makes a sign-in link only for an invited address", async () => {
    expect((await api("link", { email: "x@example.com" })).status).toBe(404);
    expect(await (await api("invite", { email: "X@example.com", send: false })).json())
      .toEqual({ ok: true, result: "Invited x@example.com.", invited: "x@example.com", emailed: false });
    expect(fetch).not.toHaveBeenCalled();
    const link = await (await api("link", { email: "x@example.com" })).json();
    expect(link.link).toMatch(/^https:\/\/cloud\.featherframe\.app\/auth\?t=[0-9a-f]{64}$/);
    await api("invite", { email: "y@example.com" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("gives a household its login", async () => {
    expect(await (await api("adopt", { email: "z@example.com", household: "wells" })).json())
      .toEqual({ ok: true, result: "Gave wells the login z@example.com.", household: "wells", email: "z@example.com" });
    expect(db.prepare("SELECT household_id FROM users WHERE email = 'z@example.com'").get()).toEqual({ household_id: "wells" });
    expect((await api("adopt", { email: "z@example.com", household: "../x" })).status).toBe(400);
  });

  it("is not there without the token, or for a page-only action", async () => {
    expect((await api("invite", { email: "x@example.com" }, "wrong")).status).toBe(404);
    expect((await api("household.delete", { id: "h1" })).status).toBe(404);
    expect((await api("nothing", {})).status).toBe(404);
  });
});

describe("the households table", () => {
  it("keeps every row's actions inside its card (W-951)", async () => {
    // Six columns need ~950 px: the page is wider than that, and a card that
    // is outgrown scrolls sideways rather than clipping its actions away.
    const { adminPage } = await import("../src/pages");
    const html = await adminPage({ waitlist: [], invites: [], log: [], kits: [], households: [],
      growth: { signups: [], invites: [], households: [] },
      usage: { meters: [], bill: 5, projected: 5, month: "2026-10", live: true, measured: true, days: [] },
    } as never, null).text();
    expect(html).toContain("main.wide { max-width:1040px; }");
    expect(html).toMatch(/main\.wide \.card \{[^}]*overflow-x:auto/);
    expect(html).not.toMatch(/main\.wide \.card \{[^}]*overflow:hidden/);
  });
});

describe("the admin page's actions", () => {
  it("spin once pressed, and send nothing more until the next page (W-955)", async () => {
    const { adminPage } = await import("../src/pages");
    const html = await adminPage({ waitlist: [], invites: [], log: [], kits: [], households: [],
      growth: { signups: [], invites: [], households: [] },
      usage: { meters: [], bill: 5, projected: 5, month: "2026-10", live: true, measured: true, days: [] },
    } as never, null).text();
    const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
    const admin = scripts.find((js) => js.includes('addEventListener("submit"'));
    expect(admin).toBeDefined();
    expect(() => new Function(admin!)).not.toThrow();              // it parses
    expect(admin).toContain("if(sent){e.preventDefault();return}");
    expect(html).toMatch(/button\.busy::after \{[^}]*animation:ff-spin/);
  });
});
