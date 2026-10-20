// @ts-nocheck: the runtime is stood in for here.
// The owner's AI keys on Featherframe Cloud (W-937): sealed at the front
// door, added to the server's requests by the gate, and only for what
// Featherframe asks.
import { afterEach, describe, expect, it, vi } from "vitest";
import { nodeSql } from "./sql";

vi.mock("cloudflare:workers", () => ({
  DurableObject: class { ctx: unknown; env: unknown; constructor(ctx: unknown, env: unknown) { this.ctx = ctx; this.env = env; } },
  WorkerEntrypoint: class { ctx: unknown; env: unknown; constructor(ctx: unknown, env: unknown) { this.ctx = ctx; this.env = env; } },
}));
vi.mock("../src/viewers", () => ({ display: vi.fn(), lobbyPng: vi.fn(), shortOf: vi.fn(), trmnlHeaders: vi.fn() }));

const { route, toVendor } = await import("../src/gate");
const { VendorGate } = await import("../src/vendorgate");
const { seal, unseal } = await import("../src/vault");

const SECRET = btoa(String.fromCharCode(...new Uint8Array(32).map((_, i) => i * 7 + 1)));
const OTHER = btoa(String.fromCharCode(...new Uint8Array(32).fill(9)));
const KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789";
const T = 1790000000;

describe("what the gate lets through", () => {
  const ok = (host, method, path) => "vendor" in route(host, method, path);
  it("lets through what Featherframe asks each vendor", () => {
    expect(ok("openai.vendor.internal", "POST", "/v1/images/edits")).toBe(true);
    expect(ok("openai.vendor.internal", "POST", "/v1/images/generations")).toBe(true);
    expect(ok("openai.vendor.internal", "POST", "/v1/chat/completions")).toBe(true);
    expect(ok("openai.vendor.internal", "POST", "/v1/responses")).toBe(true);
    expect(ok("openai.vendor.internal", "GET", "/v1/models")).toBe(true);
    expect(ok("gemini.vendor.internal", "POST", "/v1beta/models/gemini-2.5-flash-image:generateContent")).toBe(true);
    expect(ok("gemini.vendor.internal", "GET", "/v1beta/models")).toBe(true);
    expect(ok("anthropic.vendor.internal", "POST", "/v1/messages")).toBe(true);
    expect(ok("replicate.vendor.internal", "POST", "/v1/models/black-forest-labs/flux-kontext-pro/predictions")).toBe(true);
    expect(ok("replicate.vendor.internal", "POST", "/v1/predictions")).toBe(true);
    expect(ok("replicate.vendor.internal", "GET", "/v1/predictions/abc123")).toBe(true);
    expect(ok("replicate.vendor.internal", "GET", "/v1/account")).toBe(true);
  });
  it("refuses anything else, and any other host", () => {
    expect(route("openai.vendor.internal", "GET", "/v1/files")).toEqual({ status: 404, reason: "not a path Featherframe asks" });
    expect(ok("openai.vendor.internal", "POST", "/v1/fine_tuning/jobs")).toBe(false);
    expect(ok("openai.vendor.internal", "DELETE", "/v1/models")).toBe(false);
    expect(ok("openai.vendor.internal", "GET", "/v1/images/edits")).toBe(false);
    expect(ok("gemini.vendor.internal", "POST", "/v1beta/models/x:streamGenerateContent")).toBe(false);
    expect(ok("replicate.vendor.internal", "POST", "/v1/predictions/abc/cancel")).toBe(false);
    expect(ok("evil.vendor.internal", "GET", "/v1/models")).toBe(false);
    expect(ok("api.openai.com", "GET", "/v1/models")).toBe(false);
  });
  it("hands the vendor its own host, the key, and nothing of ours", () => {
    const req = new Request("http://gemini.vendor.internal/v1beta/models?pageSize=5", {
      headers: { "X-FF-Key": "image", "X-FF-Purchase": "r1", Authorization: "Bearer stolen", "x-api-key": "x", Accept: "application/json" },
    });
    const out = toVendor(req, "gemini", KEY);
    expect(out.url).toBe("https://generativelanguage.googleapis.com/v1beta/models?pageSize=5");
    expect(out.headers.get("x-goog-api-key")).toBe(KEY);
    for (const h of ["authorization", "x-api-key", "x-ff-key", "x-ff-purchase"]) expect(out.headers.get(h)).toBeNull();
    expect(out.headers.get("accept")).toBe("application/json");
    expect(toVendor(new Request("http://replicate.vendor.internal/v1/account"), "replicate", KEY)
      .headers.get("authorization")).toBe(`Token ${KEY}`);
  });
});

describe("a sealed key", () => {
  it("opens only for its household and slot, under its secret", async () => {
    const sealed = await seal(SECRET, KEY, "h1:image");
    expect(sealed.startsWith("v1:") && !sealed.includes(KEY)).toBe(true);
    expect(await unseal(SECRET, sealed, "h1:image")).toBe(KEY);
    await expect(unseal(SECRET, sealed, "h2:image")).rejects.toThrow();
    await expect(unseal(SECRET, sealed, "h1:text")).rejects.toThrow();
    await expect(unseal(OTHER, sealed, "h1:image")).rejects.toThrow();
    await expect(seal("", KEY, "h1:image")).rejects.toThrow(/AI_KEY_SECRET/);
  });
});

async function door() {
  const { Household } = await import("../src/household");
  const sql = nodeSql();
  const h = new Household({ storage: { sql }, getWebSockets: () => [], waitUntil: () => {} } as never,
    { AI_KEY_SECRET: SECRET, APP_HOST: "cloud.featherframe.app" } as never);
  h.setMeta("hid", "h1");
  h.setMeta("key", "k");
  return { h, sql };
}
const keyCall = (h, slot, method, body?) => h.internal(new Request(`https://x/_internal/h1/keys/${slot}`,
  { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }), `keys/${slot}`);
const RULE = { limit_usd: 10, runaway_per_hour: 6, window_s: null };
const rec = (id) => ({ id, at: Date.now() / 1000, month: "2026-10", day: "2026-10-19", kind: "plate", subject: id,
  auto: false, model: "gpt-image-2.5-sunburst", quality: "medium", est_usd: 0.1, cost_usd: null, state: "open" });

describe("the front door's keys", () => {
  it("keeps a key sealed, and answers the gate only for its own household and an open purchase", async () => {
    const { h, sql } = await door();
    expect((await keyCall(h, "image", "PUT", { key: KEY })).status).toBe(204);
    const stored = sql.db.prepare("SELECT sealed FROM ai_keys WHERE slot = 'image'").get().sealed;
    expect(stored.includes(KEY)).toBe(false);
    const got = await h.vendorKey("h1", "image", null);
    expect(got.ok && await unseal(SECRET, got.sealed, "h1:image")).toBe(KEY);
    expect(await h.vendorKey("h2", "image", null)).toEqual({ ok: false, status: 404, reason: "no household" });
    expect(await h.vendorKey("h1", "text", null)).toEqual({ ok: false, status: 404, reason: "no key" });
    // A paid call needs a purchase the spend book has open.
    expect(await h.vendorKey("h1", "image", "")).toMatchObject({ ok: false, status: 403 });
    expect(await h.vendorKey("h1", "image", "r1")).toMatchObject({ ok: false, status: 403 });
    await h.internal(new Request("https://x/_internal/h1/spend/reserve",
      { method: "POST", body: JSON.stringify({ record: rec("r1"), rule: RULE }) }), "spend/reserve");
    expect((await h.vendorKey("h1", "image", "r1")).ok).toBe(true);
    await h.internal(new Request("https://x/_internal/h1/spend/settle",
      { method: "POST", body: JSON.stringify({ id: "r1", state: "settled", cost_usd: 0.1 }) }), "spend/settle");
    expect(await h.vendorKey("h1", "image", "r1")).toMatchObject({ ok: false, status: 403 });
    expect((await keyCall(h, "image", "DELETE")).status).toBe(204);
    expect(await h.vendorKey("h1", "image", null)).toMatchObject({ ok: false, reason: "no key" });
  });
  it("turns away what is not a key or not a slot", async () => {
    const { h } = await door();
    expect((await keyCall(h, "image", "PUT", { key: "" })).status).toBe(400);
    expect((await keyCall(h, "image", "PUT", { key: 7 })).status).toBe(400);
    expect((await keyCall(h, "other", "PUT", { key: KEY })).status).toBe(404);
    expect((await keyCall(h, "image", "GET")).status).toBe(404);   // never read back
  });
});

describe("the gate", () => {
  afterEach(() => vi.unstubAllGlobals());

  async function gate() {
    const { h } = await door();
    await keyCall(h, "image", "PUT", { key: KEY });
    const sent: Request[] = [];
    vi.stubGlobal("fetch", async (r: Request) => { sent.push(r); return new Response("{\"data\":[]}", { status: 200 }); });
    const g = new VendorGate({ props: { hid: "h1" } } as never,
      { AI_KEY_SECRET: SECRET, HOUSEHOLD: { getByName: (id) => { expect(id).toBe("h1"); return h; } } } as never);
    return { g, h, sent };
  }

  it("adds the household's key and passes the vendor's answer back", async () => {
    const { g, sent } = await gate();
    const res = await g.fetch(new Request("http://openai.vendor.internal/v1/models", { headers: { "X-FF-Key": "image" } }));
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("{\"data\":[]}");
    expect(sent[0].url).toBe("https://api.openai.com/v1/models");
    expect(sent[0].headers.get("authorization")).toBe(`Bearer ${KEY}`);
  });

  it("marks its own refusals and sends nothing on", async () => {
    const { g, sent } = await gate();
    const asks = [
      new Request("http://openai.vendor.internal/v1/files", { headers: { "X-FF-Key": "image" } }),
      new Request("http://openai.vendor.internal/v1/models"),
      new Request("http://openai.vendor.internal/v1/models", { headers: { "X-FF-Key": "text" } }),
      new Request("http://openai.vendor.internal/v1/images/generations",
        { method: "POST", headers: { "X-FF-Key": "image" }, body: "{}" }),
    ];
    const answers = [];
    for (const a of asks) {
      const r = await g.fetch(a);
      answers.push([r.status, r.headers.get("X-FF-Gate")]);
    }
    expect(answers).toEqual([[404, "not a path Featherframe asks"], [400, "no key named"],
                             [404, "no key"], [403, "no open purchase"]]);
    expect(sent).toEqual([]);
  });
});
