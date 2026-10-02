// @ts-nocheck: node:sqlite and node:fs have no types under the Worker's tsconfig.
// Setting up a frame from the phone (W-888, src/setup.ts): who may make an
// account, that a kit or a code works once, and what an existing email gets.
// Run against the real D1 schema (every migration, in order) on node's SQLite.
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { auth, confirmVerification, sessionUser } from "../src/accounts";
import { releaseFrame, setupRoute } from "../src/setup";
import { checkCode, showCode } from "../src/signin";
import { rankStations, regionFor } from "../src/stations";
import { setupToken, setupUrl } from "../src/pairing";
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
let setUps: any[];
let adopts: any[];
let waits: Promise<unknown>[];
const ctx = { waitUntil: (p: Promise<unknown>) => waits.push(p) } as any;

const STATION = { id: "7033", name: "BanksRd-PUC", continent: "North America",
                  latestDetectionAt: new Date().toISOString(), coords: { lat: 41.862, lon: -72.836 }, counts: { species: 22 } };

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(new URL(f, MIGRATIONS), "utf8"));
  }
  mails = []; setUps = []; adopts = []; waits = [];
  env = {
    DB: d1(db), ZONE: "featherframe.app", APP_HOST: HOST, MAIL_FROM: "Featherframe <hello@featherframe.app>",
    RESEND_API_KEY: "re_test", ADMIN_EMAILS: "",
    HOUSEHOLD: { getByName: (hid: string) => ({
      setUp: async (...a: unknown[]) => { setUps.push([hid, ...a]); },
      adopt: async (...a: unknown[]) => { adopts.push([hid, ...a]); },
    }) },
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    if (String(url).includes("nominatim")) {
      return Response.json(String(url).includes("nowhere") ? [] :
        [{ lat: "41.79", lon: "-72.87", address: { town: "Avon", state: "Connecticut" } }]);
    }
    if (String(url).includes("resend")) {
      const b = JSON.parse(String(init.body));
      mails.push({ to: b.to[0], subject: b.subject, text: b.text });
      return new Response("{}");
    }
    const q = JSON.parse(String(init.body));
    if (q.variables?.id) return Response.json({ data: { station: q.variables.id === "7033" ? STATION : null } });
    return Response.json({ data: { stations: { nodes: [STATION] } } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

/** A frame showing a code, as pairingScreen leaves it. */
async function frameShowing(code = "ABCDEF", device = "AABBCCDDEEFF", key = "k".repeat(0) + "ab".repeat(16)) {
  const token = setupToken();
  db.prepare("INSERT INTO pairing (code, device_id, key_hash, report, expires_at, setup_token) VALUES (?, ?, ?, ?, ?, ?)")
    .run(code, device, await sha256(key), JSON.stringify({ "x-panel": "ED103TC2" }), NOW + 3600, token);
  return { code, token, device, keyHash: await sha256(key) };
}

function post(f: { code: string; token: string }, fields: Record<string, string>, ip = "203.0.113.1", headers = {}) {
  return setupRoute(new Request(`https://${HOST}/setup/${f.code}/${f.token}`, {
    method: "POST", body: new URLSearchParams(fields),
    headers: { Origin: `https://${HOST}`, "CF-Connecting-IP": ip, ...headers },
  }), env, new URL(`https://${HOST}/setup/${f.code}/${f.token}`), ctx);
}

const one = (sql: string, ...a: unknown[]) => db.prepare(sql).get(...a) as any;

const codePage = async (res: Response) => (await showCode(
  new Request(`https://${HOST}/login/code`, { headers: { Cookie: res.headers.get("Set-Cookie")!.split(";")[0] } }),
  env, new URL(`https://${HOST}/login/code`))).text();

describe("the setup page", () => {
  it("needs the code's secret, not just its six letters", async () => {
    const f = await frameShowing();
    const url = `https://${HOST}/setup/${f.code}/${"0".repeat(12)}`;
    const res = await setupRoute(new Request(url), env, new URL(url), ctx);
    expect(await res.text()).toContain("This code has expired");
    const ok = `https://${HOST}/setup/${f.code}/${f.token}`;
    expect(await (await setupRoute(new Request(ok), env, new URL(ok), ctx)).text()).toContain("Set up your frame");
  });

  it("asks no one for a second code", async () => {
    const f = await frameShowing();
    const url = `https://${HOST}/setup/${f.code}/${f.token}`;
    expect(await (await setupRoute(new Request(url), env, new URL(url), ctx)).text()).not.toContain("Setup code");
  });
});

describe("setting up", () => {
  it("makes the account for a registered kit, claims the frame, seeds the source and signs in", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at) VALUES (?, ?, 'ee03', ?)").run(f.device, f.keyHash, NOW);
    const res = await post(f, { email: "New@Example.com", station: "7033", km: "4", tz: "America/New_York" });
    await Promise.all(waits);
    expect(res.status).toBe(303);
    expect(res.headers.get("Location")).toBe("/?welcome=1");
    expect(res.headers.get("Set-Cookie")).toMatch(/^ff_session=/);
    const user = one("SELECT * FROM users WHERE email = 'new@example.com'");
    expect(one("SELECT household_id FROM frames WHERE device_id = ?", f.device).household_id).toBe(user.household_id);
    expect(one("SELECT used_at, household_id FROM kits").household_id).toBe(user.household_id);
    expect(one("SELECT count(*) AS n FROM pairing").n).toBe(0);
    expect(setUps).toHaveLength(1);
    const [hid, , tz, seed, device] = setUps[0];
    expect([hid, tz, device]).toEqual([user.household_id, "America/New_York", f.device]);
    expect(seed).toEqual({ detection_backend: "birdweather", birdweather_station_id: "7033", region: "north-america" });
    expect(mails.map((m) => m.subject).sort()).toEqual(["Confirm your email for Featherframe", "Welcome to Featherframe!"]);
    expect(mails.find((m) => m.subject.startsWith("Welcome"))!.text)
      .toContain("heard by BanksRd-PUC, a BirdWeather station 4 km from you");
    expect(mails.find((m) => m.subject.startsWith("Welcome"))!.text).toContain("Help: featherframe.app/help");
    // Signed in, but not yet confirmed; the emailed link confirms it.
    expect(one("SELECT verified_at FROM users").verified_at).toBeNull();
    const link = new URL(mails.find((m) => m.subject.startsWith("Confirm"))!.text.match(/https:\/\/\S+/)![0]);
    expect(link.pathname).toBe("/account/verify");
    expect(await (await confirmVerification(env, link)).text()).toContain("Email confirmed");
    expect(one("SELECT verified_at FROM users").verified_at).not.toBeNull();
  });

  it("seeds the owner's own detector, and lands on its setup steps", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at) VALUES (?, ?, 'ee03', ?)").run(f.device, f.keyHash, NOW);
    const res = await post(f, { email: "go@example.com", source: "birdnet_go", station: "7033" });
    await Promise.all(waits);
    expect(res.headers.get("Location")).toBe("/?welcome=1&open=source");
    expect(setUps[0][3]).toEqual({ detection_backend: "birdnet_go" });
    expect(mails.find((m) => m.subject.startsWith("Welcome"))!.text).toContain("connect your detector");
  });

  it("lets an invited email set up a frame nobody registered, once", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO invites (email, created_at) VALUES ('diy@example.com', ?)").run(NOW);
    expect((await post(f, { email: "DIY@example.com", station: "7033" })).status).toBe(303);
    expect(one("SELECT used_at FROM invites").used_at).not.toBeNull();
    expect(one("SELECT household_id FROM frames WHERE device_id = ?", f.device).household_id)
      .toBe(one("SELECT household_id FROM users WHERE email = 'diy@example.com'").household_id);
  });

  it("answers an uninvited email as it answers an account, and puts it on the waitlist", async () => {
    db.prepare("INSERT INTO households (id, tz, created_at) VALUES ('h1', 'UTC', ?)").run(NOW);
    db.prepare("INSERT INTO users (id, email, household_id, created_at) VALUES ('u1', 'old@example.com', 'h1', ?)").run(NOW);
    const f = await frameShowing();
    const strangerRes = await post(f, { email: "new@example.com" });
    const g = await frameShowing("GHJKMN", "112233445566");
    const ownerRes = await post(g, { email: "old@example.com" }, "203.0.113.2");
    expect(strangerRes.status).toBe(ownerRes.status);
    expect(strangerRes.headers.get("Location")).toBe(ownerRes.headers.get("Location"));
    const stranger = (await codePage(strangerRes)).replace(/\/setup\/[a-z]+\/[0-9a-z]+/gi, "BACK");
    const owner = (await codePage(ownerRes)).replace(/\/setup\/[a-z]+\/[0-9a-z]+/gi, "BACK");
    expect(stranger.replace(/new@example\.com/g, "X")).toBe(owner.replace(/old@example\.com/g, "X"));
    expect(one("SELECT source FROM waitlist WHERE email = 'new@example.com'").source).toBe("setup");
    expect(one("SELECT count(*) AS n FROM users").n).toBe(1);
    await Promise.all(waits);
    expect(mails.map((m) => m.to)).toEqual(["old@example.com"]);
  });

  it("sends an existing account a link that adds the frame, and makes nothing", async () => {
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
  });

  it("the add-a-frame link still works on its own", async () => {
    db.prepare("INSERT INTO households (id, tz, created_at) VALUES ('h1', 'UTC', ?)").run(NOW);
    db.prepare("INSERT INTO users (id, email, household_id, created_at) VALUES ('u1', 'old@example.com', 'h1', ?)").run(NOW);
    const f = await frameShowing();
    await post(f, { email: "old@example.com" });
    await Promise.all(waits);
    const link = new URL(mails[0].text.match(/https:\/\/\S+/)![0]);
    expect((await auth(new Request(link), env, link)).headers.get("Location")).toBe("/?paired=1");
  });

  it("asks a new account on BirdWeather for its station, and makes nothing without one", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at) VALUES (?, ?, 'ee03', ?)").run(f.device, f.keyHash, NOW);
    const res = await post(f, { email: "rural@example.com", source: "birdweather", station: "" });
    expect(res.status).toBe(200);
    expect(await res.text()).toContain("Choose a BirdWeather station, or another detection source.");
    expect(one("SELECT count(*) AS n FROM users").n).toBe(0);
    expect(one("SELECT used_at FROM kits").used_at).toBeNull();
    // Its own detector needs no station.
    expect((await post(f, { email: "rural@example.com", source: "birdnet_go" }, "198.51.100.20")).status).toBe(303);
  });

  it("lets only one of two setups take the frame", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at) VALUES (?, ?, 'ee03', ?)").run(f.device, f.keyHash, NOW);
    const [a, b] = await Promise.all([post(f, { email: "a@example.com", station: "7033" }, "198.51.100.1"),
                                      post(f, { email: "b@example.com", station: "7033" }, "198.51.100.2")]);
    expect([a.status, b.status].filter((s) => s === 303)).toHaveLength(1);
    expect(one("SELECT count(*) AS n FROM users").n).toBe(1);
    expect(one("SELECT count(*) AS n FROM frames").n).toBe(1);
  });

  it("is limited per IP", async () => {
    const f = await frameShowing();
    for (let i = 0; i < 5; i++) await post(f, { email: "x" });
    expect(await (await post(f, { email: "x" })).text()).toContain("Too many tries");
  });

  it("lets a removed kit be set up again by someone new", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at) VALUES (?, ?, 'ee03', ?)").run(f.device, f.keyHash, NOW);
    expect((await post(f, { email: "giver@example.com", station: "7033" }, "198.51.100.10")).status).toBe(303);
    const giver = one("SELECT household_id FROM users WHERE email = 'giver@example.com'").household_id;
    await releaseFrame(env, f.device, giver);
    expect(one("SELECT count(*) AS n FROM frames").n).toBe(0);
    // The frame shows a new code; the new owner has no invitation of their own.
    const g = await frameShowing("GHJKMN", f.device);
    expect((await post(g, { email: "friend@example.com", station: "7033" }, "198.51.100.11")).status).toBe(303);
    const friend = one("SELECT household_id FROM users WHERE email = 'friend@example.com'").household_id;
    expect(one("SELECT household_id FROM frames WHERE device_id = ?", f.device).household_id).toBe(friend);
    expect(one("SELECT household_id FROM kits").household_id).toBe(friend);
  });

  it("frees only a kit that was that household's", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at, used_at, household_id) VALUES (?, ?, 'ee03', ?, ?, 'h1')")
      .run(f.device, f.keyHash, NOW, NOW);
    await releaseFrame(env, f.device, "h2");
    expect(one("SELECT household_id FROM kits").household_id).toBe("h1");
  });
});

describe("finding stations", () => {
  const ask = async (f: { code: string; token: string }, q: string) => {
    const url = `https://${HOST}/api/setup/stations?c=${f.code}&t=${f.token}${q}`;
    return (await setupRoute(new Request(url, { headers: { "CF-Connecting-IP": "203.0.113.9" } }), env, new URL(url), ctx)).json();
  };
  it("places a ZIP code or town and lists what is near it, for a frame showing its code only", async () => {
    const f = await frameShowing();
    const got = await ask(f, "&place=06001");
    expect(got.place).toBe("Avon, Connecticut");
    expect(got.stations[0]).toMatchObject({ id: "7033", name: "BanksRd-PUC", state: "" });
    expect((await ask(f, "&place=nowhere")).found).toBe(false);
    expect((await ask({ code: f.code, token: "0".repeat(12) }, "&place=06001")).stations).toEqual([]);
  });
});

describe("stations", () => {
  const day = 24 * 3600 * 1000;
  const at = Date.parse("2026-09-27T12:00:00Z");
  const node = (id: string, lat: number, lon: number, ago: number, species = 5) =>
    ({ id, name: `S${id}`, continent: "Europe", coords: { lat, lon },
       latestDetectionAt: new Date(at - ago).toISOString(), counts: { species } });

  it("keeps stations heard from this week, nearest first", () => {
    const got = rankStations([node("far", 51.9, 0.1, day), node("old", 51.5, 0.0, 9 * day),
                              node("near", 51.51, 0.01, day), node("mute", 51.5, 0.0, day, 0)],
                             { lat: 51.5, lon: 0 }, at);
    expect(got.map((s) => s.id)).toEqual(["near", "far"]);
    expect(got[0].km).toBe(1);
  });

  it("reads a continent as a Region", () => {
    expect(regionFor("North America")).toBe("north-america");
    expect(regionFor("Europe")).toBe("europe");
    expect(regionFor("Oceania")).toBe("australia");
    expect(regionFor("Asia")).toBe("asia");
    expect(regionFor("South America")).toBeNull();
  });
});

describe("codes", () => {
  it("spells the setup URL in lower case", () => {
    const t = setupToken();
    expect(t).toMatch(/^[0-9a-z]{12}$/);
    expect(setupUrl(HOST, "ABCDEF", t)).toBe(`https://cloud.featherframe.app/setup/abcdef/${t}`);
  });
});

describe("rate limits", () => {
  it("puts sign-in, setup and pairing on the strict limiter, frames on their own", async () => {
    const { limiterFor } = await import("../src/ratelimit");
    const r = (path: string, method = "GET", headers: Record<string, string> = {}) =>
      limiterFor(new Request(`https://${HOST}${path}`, { method, headers: { "CF-Connecting-IP": "198.51.100.7", ...headers } }),
                 new URL(`https://${HOST}${path}`), (p) => /^\/api\/frame/.test(p));
    expect(r("/login", "POST").name).toBe("RL_AUTH");
    expect(r("/login").name).toBe("RL_PAGE");
    expect(r("/login/code").name).toBe("RL_PAGE");
    expect(r("/login/state").name).toBe("RL_PAGE");
    expect(r("/login/code", "POST").name).toBe("RL_AUTH");
    expect(r("/login/resend", "POST").name).toBe("RL_AUTH");
    expect(r("/auth?t=x").name).toBe("RL_AUTH");
    expect(r("/setup/abcdef/0123456789ab", "POST").name).toBe("RL_AUTH");
    expect(r("/api/pair", "POST").name).toBe("RL_AUTH");
    expect(r("/api/frame", "GET", { "X-Device-Id": "AABBCC" })).toEqual({ name: "RL_FRAME", key: "frame:AABBCC" });
    expect(r("/").name).toBe("RL_PAGE");
  });
});

describe("a code typed on the sign-in page", () => {
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

  const typeCode = (code: string, ip = "192.0.2.5") => setupRoute(new Request(`https://${HOST}/setup`, {
    method: "POST", body: new URLSearchParams({ code }), headers: { Origin: `https://${HOST}`, "CF-Connecting-IP": ip },
  }), env, new URL(`https://${HOST}/setup`), ctx);

  it("opens the same setup page the frame's QR does, whatever its dash or case", async () => {
    const f = await frameShowing();
    const res = await typeCode("abc-def");
    expect(res.status).toBe(303);
    expect(res.headers.get("Location")).toBe(`https://${HOST}/setup/abcdef/${f.token}`);
  });

  it("says when no frame shows it, and stops after 20 tries an hour", async () => {
    expect(await (await typeCode("XYZ-XYZ")).text()).toContain("No frame is showing that code");
    expect(await (await typeCode("AB")).text()).toContain("A code is six letters");
    for (let i = 0; i < 18; i++) await typeCode("XYZ-XYZ");
    expect(await (await typeCode("XYZ-XYZ")).text()).toContain("Too many tries");
  });
});
