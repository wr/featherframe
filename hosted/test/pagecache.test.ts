// The front door's copy of the webapp's reads (W-946, src/pagecache.ts).
import { describe, expect, it } from "vitest";
import { answerRead, cacheKey, FRESH_MS, MAX_BODY, PageCache, READ_WINDOW_MS, routeOf } from "../src/pagecache";
import { nodeSql } from "./sql";

function bucket() {
  const m = new Map<string, Uint8Array>();
  return {
    m,
    async get(k: string) {
      const b = m.get(k);
      return b ? { arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer } : null;
    },
    async put(k: string, v: ArrayBuffer) { m.set(k, new Uint8Array(v)); },
    async delete(k: string | string[]) { for (const x of [k].flat()) m.delete(x); },
  };
}

const T = 1_790_000_000_000;
const U = (p: string) => new URL(`https://cloud.featherframe.app${p}`);
const page = (body: string, type = "text/html; charset=utf-8", status = 200) =>
  new Response(body, { status, headers: { "Content-Type": type, "Cache-Control": "no-cache", "Set-Cookie": "x=1" } });

describe("routeOf", () => {
  it("caches the webapp's reads and nothing else", () => {
    expect(routeOf("/", "2026-10-02")).toEqual({ kind: "changing", keys: [] });
    expect(routeOf("/api/status", "2026-10-02")?.kind).toBe("changing");
    expect(routeOf("/api/battery", "2026-10-02")?.keys).toEqual(["frame", "hours"]);
    expect(routeOf("/api/frames/AA%3ABB/preview.png", "2026-10-02")?.kind).toBe("changing");
    expect(routeOf("/api/collages/2026-10-02.png", "2026-10-02")?.kind).toBe("changing");
    expect(routeOf("/api/collages/2026-10-01.png", "2026-10-02")?.kind).toBe("fixed");
    expect(routeOf("/api/generated/blue-jay.png", "2026-10-02")?.keys).toEqual(["thumb", "v"]);
    expect(routeOf("/api/history/0123456789abcdef.jpg", "2026-10-02")?.kind).toBe("fixed");
    expect(routeOf("/static/examples/a.png", "2026-10-02")?.kind).toBe("fixed");
    for (const p of ["/api/frame", "/api/imagegen/models", "/api/generated/export", "/settings", "/api/flash/ee02/manifest.json"]) {
      expect(routeOf(p, "2026-10-02")).toBeNull();
    }
  });
});

describe("cacheKey", () => {
  it("keeps the query keys that name a read and drops the cache-busters", () => {
    const r = routeOf("/api/generated/x.png", "d")!;
    const a = cacheKey(U("/api/generated/x.png?thumb=1&v=7&t=99"), r, {}, "b1");
    const b = cacheKey(U("/api/generated/x.png?t=12&v=7&thumb=1"), r, {}, "b1");
    expect(a.key).toBe(b.key);
    expect(a.ask).toEqual({ path: "/api/generated/x.png", query: [["thumb", "1"], ["v", "7"]], account: {} });
    expect(cacheKey(U("/api/generated/x.png?thumb=1&v=8"), r, {}, "b1").key).not.toBe(a.key);
  });
  it("drops the page's own flash query, and carries the account and the build", () => {
    const r = routeOf("/", "d")!;
    const acct = { "x-ff-account-email": "w@example.com" };
    expect(cacheKey(U("/?saved=1&open=general"), r, acct, "b1").key).toBe(cacheKey(U("/"), r, acct, "b1").key);
    expect(cacheKey(U("/"), r, { "x-ff-account-email": "x@example.com" }, "b1").key)
      .not.toBe(cacheKey(U("/"), r, acct, "b1").key);
    expect(cacheKey(U("/"), r, acct, "b2").key).not.toBe(cacheKey(U("/"), r, acct, "b1").key);
  });
});

describe("PageCache", () => {
  const make = () => { const b = bucket(); return { b, c: new PageCache(nodeSql(), b, "households/h/cache/") }; };
  const route = routeOf("/", "d")!;

  it("stores a 200 and answers it, with the server's headers and none of its cookies", async () => {
    const { c } = make();
    const { key, ask } = cacheKey(U("/"), route, {}, "b");
    const res = await c.store(key, route.kind, ask, page("<p>hi</p>"), T);
    expect(await res.text()).toBe("<p>hi</p>");
    const e = c.find(key)!;
    expect(c.fresh(e, T + FRESH_MS - 1)).toBe(true);
    expect(c.fresh(e, T + FRESH_MS)).toBe(false);
    const again = await c.respond(e, "GET");
    expect(await again!.text()).toBe("<p>hi</p>");
    expect(again!.headers.get("Content-Type")).toBe("text/html; charset=utf-8");
    expect(again!.headers.get("Set-Cookie")).toBeNull();
  });

  it("stores nothing but a 200 under 5 MB", async () => {
    const { c } = make();
    const { key, ask } = cacheKey(U("/"), route, {}, "b");
    await c.store(key, route.kind, ask, page("gone", "text/plain", 404), T);
    expect(c.find(key)).toBeNull();
    await c.store(key, route.kind, ask, new Response(new Uint8Array(MAX_BODY + 1)), T);
    expect(c.find(key)).toBeNull();
  });

  it("treats a copy missing from R2 as a miss", async () => {
    const { b, c } = make();
    const { key, ask } = cacheKey(U("/"), route, {}, "b");
    await c.store(key, route.kind, ask, page("x"), T);
    b.m.clear();
    expect(await c.respond(c.find(key)!, "GET")).toBeNull();
  });

  it("a POST's clear takes the changing copies and keeps the fixed ones", async () => {
    const { b, c } = make();
    const fixed = routeOf("/api/history/0123456789abcdef.png", "d")!;
    const k1 = cacheKey(U("/"), route, {}, "b"), k2 = cacheKey(U("/api/history/0123456789abcdef.png"), fixed, {}, "b");
    await c.store(k1.key, route.kind, k1.ask, page("p"), T);
    await c.store(k2.key, fixed.kind, k2.ask, page("h", "image/png"), T);
    await c.clear("changing");
    expect(c.find(k1.key)?.object).toBeNull();      // the row stays, for the next refresh
    expect(c.find(k2.key)?.object).not.toBeNull();
    expect(b.m.size).toBe(1);
    await c.clear("all");
    expect(b.m.size).toBe(0);
  });

  it("refreshes what was read in the last 2 hours and empty rows, all in one generation", async () => {
    const { c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    const ks = cacheKey(U("/api/status"), routeOf("/api/status", "d")!, {}, "b");
    const kb = cacheKey(U("/api/battery?frame=A&hours=24"), routeOf("/api/battery", "d")!, {}, "b");
    await c.store(kp.key, "changing", kp.ask, page("old page"), T);
    await c.store(kb.key, "changing", kb.ask, page("{}", "application/json"), T);
    c.touch(kp.key, T);
    c.touch(kb.key, T - READ_WINDOW_MS - 1);
    c.note(ks.key, "changing", ks.ask, T);            // a miss: a row with no copy yet
    const asked: string[] = [];
    const ok = await c.refresh(async (a) => { asked.push(a.path); return page(`new ${a.path}`); }, T + 1);
    expect(ok).toBe(true);
    expect(asked.sort()).toEqual(["/", "/api/status"]);
    expect(await (await c.respond(c.find(kp.key)!, "GET"))!.text()).toBe("new /");
    expect(await (await c.respond(c.find(ks.key)!, "GET"))!.text()).toBe("new /api/status");
    expect(c.find(kp.key)!.generation).toBe(c.find(ks.key)!.generation);
  });

  it("keeps every copy as it was when one ask fails", async () => {
    const { c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    const ks = cacheKey(U("/api/status"), routeOf("/api/status", "d")!, {}, "b");
    await c.store(kp.key, "changing", kp.ask, page("old page"), T);
    await c.store(ks.key, "changing", ks.ask, page("old status"), T);
    c.touch(kp.key, T); c.touch(ks.key, T);
    const ok = await c.refresh(async (a) => a.path === "/" ? page("new page") : page("down", "text/plain", 500), T + 1);
    expect(ok).toBe(false);
    expect(await (await c.respond(c.find(kp.key)!, "GET"))!.text()).toBe("old page");
  });

  it("a refresh whose rows a save cleared meanwhile leaves nothing behind in R2", async () => {
    const { b, c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    await c.store(kp.key, "changing", kp.ask, page("old"), T);
    c.touch(kp.key, T);
    const ok = await c.refresh(async () => { await c.clear("changing"); return page("new"); }, T + 1);
    expect(ok).toBe(true);
    expect(c.find(kp.key)?.object).toBeNull();
    expect(b.m.size).toBe(0);
  });

  it("a save empties the copies but keeps the rows, so the next refresh fills them all", async () => {
    const { c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    const kh = cacheKey(U("/api/history"), routeOf("/api/history", "d")!, {}, "b");
    await c.store(kp.key, "changing", kp.ask, page("p"), T);
    await c.store(kh.key, "changing", kh.ask, page("h"), T);
    c.touch(kp.key, T); c.touch(kh.key, T);
    await c.clear("changing");
    expect(c.stale(T + 1)).toBe(true);
    const asked: string[] = [];
    await c.refresh(async (a) => { asked.push(a.path); return page("x"); }, T + 2);
    expect(asked.sort()).toEqual(["/", "/api/history"]);
  });

  it("refreshing / brings the page's parts, read or not", async () => {
    const { c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    c.note(kp.key, "changing", kp.ask, T);
    const parts = ["/api/status", "/api/tasks", "/api/history", "/api/frames/AA/preview.png"]
      .map((p) => ({ ...cacheKey(U(p), routeOf(p, "d")!, {}, "b"), kind: "changing" as const }));
    const asked: string[] = [];
    await c.refresh(async (a) => { asked.push(a.path); return page("x"); }, T + 1, parts);
    expect(asked.sort()).toEqual(["/", "/api/frames/AA/preview.png", "/api/history", "/api/status", "/api/tasks"]);
    for (const p of parts) expect(c.find(p.key)?.object).toBeTruthy();
  });

  it("a refresh never overwrites a newer copy stored while it was asking", async () => {
    const { c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    await c.store(kp.key, "changing", kp.ask, page("before the save"), T);
    c.touch(kp.key, T);
    await c.refresh(async (a) => {
      await c.store(kp.key, "changing", kp.ask, page("after the save"), T + 1);   // a miss filled meanwhile
      return page("asked before the save");
    }, T + 2);
    expect(await (await c.respond(c.find(kp.key)!, "GET"))!.text()).toBe("after the save");
  });

  it("an ask that throws leaves nothing behind and changes nothing", async () => {
    const { b, c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    const ks = cacheKey(U("/api/status"), routeOf("/api/status", "d")!, {}, "b");
    await c.store(kp.key, "changing", kp.ask, page("old page"), T);
    await c.store(ks.key, "changing", ks.ask, page("old status"), T);
    c.touch(kp.key, T); c.touch(ks.key, T);
    const before = b.m.size;
    let n = 0;
    expect(await c.refresh(async () => { if (n++) throw new Error("gone"); return page("new"); }, T + 1)).toBe(false);
    expect(b.m.size).toBe(before);
    expect(await (await c.respond(c.find(kp.key)!, "GET"))!.text()).toBe("old page");
  });

  it("knows when the copies someone reads are stale", async () => {
    const { c } = make();
    const kp = cacheKey(U("/"), route, {}, "b");
    await c.store(kp.key, "changing", kp.ask, page("p"), T);
    c.touch(kp.key, T);
    expect(c.readSince(T - READ_WINDOW_MS)).toBe(true);
    expect(c.stale(T + FRESH_MS - 1)).toBe(false);
    expect(c.stale(T + FRESH_MS)).toBe(true);
  });
});

describe("answerRead", () => {
  function deps(over: Record<string, unknown> = {}) {
    const b = bucket();
    const calls = { ask: 0, proxy: 0, look: 0 };
    const d = {
      cache: new PageCache(nodeSql(), b, "p/"), now: T, build: "b", today: "2026-10-02",
      running: async () => false,
      ask: async () => { calls.ask++; return page("from server"); },
      proxy: async () => { calls.proxy++; return page("proxied"); },
      look: async () => { calls.look++; },
      loading: () => new Response("loading"),
      ...over,
    };
    return { d, calls };
  }
  const get = (p: string, headers: Record<string, string> = {}) => new Request(`https://cloud.featherframe.app${p}`, { headers });

  it("leaves to the server what it does not cache: writes, live reads, other paths", async () => {
    const { d } = deps();
    expect(await answerRead(new Request("https://cloud.featherframe.app/settings", { method: "POST" }), U("/settings"), d)).toBeNull();
    expect(await answerRead(get("/api/status?live=1"), U("/api/status?live=1"), d)).toBeNull();
    expect(await answerRead(get("/api/frame"), U("/api/frame"), d)).toBeNull();
  });

  it("answers a fresh copy without the server, and a stale one while asking for a look", async () => {
    const { d, calls } = deps({ running: async () => true });
    expect(await (await answerRead(get("/"), U("/"), d))!.text()).toBe("from server");   // a miss, server up
    expect(calls.ask).toBe(1);
    expect(await (await answerRead(get("/"), U("/"), { ...d, now: T + 1 }))!.text()).toBe("from server");
    expect(calls).toMatchObject({ ask: 1, look: 0 });
    await answerRead(get("/"), U("/"), { ...d, now: T + FRESH_MS });
    expect(calls).toMatchObject({ ask: 1, look: 1 });
  });

  it("with the server asleep, answers / with the loading page and asks for a look", async () => {
    const { d, calls } = deps();
    expect(await (await answerRead(get("/?welcome=1"), U("/?welcome=1"), d))!.text()).toBe("loading");
    expect(calls).toMatchObject({ ask: 0, proxy: 0, look: 1 });
    // …and leaves a row the next refresh fills.
    const key = cacheKey(U("/"), routeOf("/", "d")!, {}, "b").key;
    expect(d.cache.find(key)?.object).toBeNull();
  });

  it("with the server asleep, asks it for any other read and keeps the answer", async () => {
    const { d, calls } = deps();
    expect(await (await answerRead(get("/api/battery?frame=A&hours=24"), U("/api/battery?frame=A&hours=24"), d))!.text())
      .toBe("proxied");
    expect(calls.proxy).toBe(1);
    await answerRead(get("/api/battery?hours=24&frame=A"), U("/api/battery?hours=24&frame=A"), { ...d, now: T + 1 });
    expect(calls.proxy).toBe(1);
  });

  it("keys / by the account it was drawn for", async () => {
    const { d, calls } = deps({ running: async () => true });
    await answerRead(get("/", { "X-FF-Account-Email": "w@example.com" }), U("/"), d);
    await answerRead(get("/", { "X-FF-Account-Email": "w@example.com", "X-FF-Account-Unverified": "1" }), U("/"), d);
    expect(calls.ask).toBe(2);
  });

  it("answers a HEAD with headers and no body", async () => {
    const { d } = deps({ running: async () => true });
    await answerRead(get("/api/status"), U("/api/status"), d);
    const res = await answerRead(new Request("https://cloud.featherframe.app/api/status", { method: "HEAD" }), U("/api/status"), d);
    expect(res!.body).toBeNull();
  });
});
