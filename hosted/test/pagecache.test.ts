// The front door's copy of the webapp's reads (W-946, src/pagecache.ts).
import { describe, expect, it } from "vitest";
import { cacheKey, FRESH_MS, MAX_BODY, PageCache, READ_WINDOW_MS, routeOf } from "../src/pagecache";
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
    expect(c.find(k1.key)).toBeNull();
    expect(c.find(k2.key)).not.toBeNull();
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
