// @ts-nocheck: node:sqlite and node:fs have no types under the Worker's tsconfig.
// Setting up a frame from the phone (W-888, src/setup.ts): who may make an
// account, that a kit or a code works once, and what an existing email gets.
// Run against the real D1 schema (every migration, in order) on node's SQLite.
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { auth, confirmVerification, sessionUser } from "../src/accounts";
import { newSetupCode, normSetupCode, setupRoute } from "../src/setup";
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
const HOST = "app.featherframe.app";
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

describe("the setup page", () => {
  it("needs the code's secret, not just its six letters", async () => {
    const f = await frameShowing();
    const url = `https://${HOST}/setup/${f.code}/${"0".repeat(12)}`;
    const res = await setupRoute(new Request(url), env, new URL(url), ctx);
    expect(await res.text()).toContain("This code has expired");
    const ok = `https://${HOST}/setup/${f.code}/${f.token}`;
    expect(await (await setupRoute(new Request(ok), env, new URL(ok), ctx)).text()).toContain("Set up your frame");
  });

  it("asks a kit nobody registered for its setup code, and a registered one for none", async () => {
    const f = await frameShowing();
    const url = `https://${HOST}/setup/${f.code}/${f.token}`;
    expect(await (await setupRoute(new Request(url), env, new URL(url), ctx)).text()).toContain("Setup code");
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at) VALUES (?, ?, 'ee03', ?)").run(f.device, f.keyHash, NOW);
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

  it("takes a setup code once, whatever its dash or case", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO setup_codes (code, created_at) VALUES ('ABCDEFGH', ?)").run(NOW);
    expect((await post(f, { email: "a@example.com", setup_code: "abcd-efgh" })).status).toBe(303);
    expect(one("SELECT used_at FROM setup_codes").used_at).not.toBeNull();
    const g = await frameShowing("GHJKMN", "112233445566");
    const again = await post(g, { email: "b@example.com", setup_code: "ABCD-EFGH" });
    expect(await again.text()).toContain("Check the setup code on the card in the box.");
    expect(one("SELECT count(*) AS n FROM users").n).toBe(1);
  });

  it("says nothing about an email without an invitation", async () => {
    db.prepare("INSERT INTO households (id, tz, created_at) VALUES ('h1', 'UTC', ?)").run(NOW);
    db.prepare("INSERT INTO users (id, email, household_id, created_at) VALUES ('u1', 'old@example.com', 'h1', ?)").run(NOW);
    const f = await frameShowing();
    const res = await post(f, { email: "old@example.com", setup_code: "" });
    expect(await res.text()).toContain("Enter the setup code from the card in the box.");
    await Promise.all(waits);
    expect(mails).toHaveLength(0);
  });

  it("sends an existing account a link that adds the frame, and makes nothing", async () => {
    db.prepare("INSERT INTO households (id, tz, created_at) VALUES ('h1', 'UTC', ?)").run(NOW);
    db.prepare("INSERT INTO users (id, email, household_id, created_at) VALUES ('u1', 'old@example.com', 'h1', ?)").run(NOW);
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at) VALUES (?, ?, 'ee03', ?)").run(f.device, f.keyHash, NOW);
    const res = await post(f, { email: "old@example.com" });
    await Promise.all(waits);
    expect(await res.text()).toContain("You already have an account");
    expect(one("SELECT count(*) AS n FROM households").n).toBe(1);
    expect(one("SELECT used_at FROM kits").used_at).toBeNull();
    expect(mails.map((m) => m.subject)).toEqual(["Add a frame to Featherframe"]);
    // Following it signs in and adds the frame.
    const link = new URL(mails[0].text.match(/https:\/\/\S+/)![0]);
    const signed = await auth(new Request(link), env, link);
    expect(signed.headers.get("Location")).toBe("/?paired=1");
    expect(one("SELECT household_id FROM frames WHERE device_id = ?", f.device).household_id).toBe("h1");
    expect(adopts.map((a) => a[0])).toEqual(["h1"]);
  });

  it("lets only one of two setups take the frame", async () => {
    const f = await frameShowing();
    db.prepare("INSERT INTO kits (device_id, key_hash, kit, registered_at) VALUES (?, ?, 'ee03', ?)").run(f.device, f.keyHash, NOW);
    const [a, b] = await Promise.all([post(f, { email: "a@example.com" }, "198.51.100.1"),
                                      post(f, { email: "b@example.com" }, "198.51.100.2")]);
    expect([a.status, b.status].filter((s) => s === 303)).toHaveLength(1);
    expect(one("SELECT count(*) AS n FROM users").n).toBe(1);
    expect(one("SELECT count(*) AS n FROM frames").n).toBe(1);
  });

  it("is limited per IP", async () => {
    const f = await frameShowing();
    for (let i = 0; i < 5; i++) await post(f, { email: "x", setup_code: "" });
    expect(await (await post(f, { email: "x" })).text()).toContain("Too many tries");
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
  it("normalises a setup code and makes them from the pairing alphabet", () => {
    expect(normSetupCode(" abcd-efgh ")).toBe("ABCDEFGH");
    expect(normSetupCode("ABC")).toBe("");
    expect(newSetupCode()).toMatch(/^[ABCDEFGHJKMNPRSTWXYZ]{8}$/);
  });
  it("spells the setup URL in lower case", () => {
    const t = setupToken();
    expect(t).toMatch(/^[0-9a-z]{12}$/);
    expect(setupUrl(HOST, "ABCDEF", t)).toBe(`https://app.featherframe.app/setup/abcdef/${t}`);
  });
});
