// @ts-nocheck: a front door on node's SQLite, as test/spend.test.ts builds one.
// A check-in says whether the frame's /api/firmware would get past the front
// door (W-921): a battery frame asks only then, so a wake is one request.
import { describe, expect, it, vi } from "vitest";
import { nodeSql } from "./sql";

vi.mock("cloudflare:workers", () => ({
  DurableObject: class { ctx: unknown; env: unknown; constructor(ctx: unknown, env: unknown) { this.ctx = ctx; this.env = env; } },
}));
vi.mock("../src/viewers", () => ({ display: vi.fn(), lobbyPng: vi.fn(), shortOf: vi.fn(), trmnlHeaders: vi.fn() }));

async function door(ota = false) {
  const { Household } = await import("../src/household");
  const sql = nodeSql();
  const h = new Household({ storage: { sql, setAlarm: async () => {}, deleteAlarm: async () => {} },
                            getWebSockets: () => [], waitUntil: () => {} } as never,
    { APP_HOST: "cloud.featherframe.app", DATA: { delete: async () => {} },
      SERVER: { getByName: () => ({ running: async () => false, configure: async () => {}, sleepWhenIdle: async () => {},
                                     fetch: async () => new Response("{}") }) } } as never);
  h.setMeta("hid", "h1"); h.setMeta("key", "k"); h.setMeta("tz", "UTC");
  h.wake = async () => {};
  await h.takeState({
    frames: { K1: { status: "on", etag: "out", file: "frames/drawn/out/a.fff", headers: { "X-Power-Mode": "sleep" },
                    push: { etag: "out", rotation: 90, power: "sleep", ota } } },
    viewers: {}, poll: true, unchanged: [], page_build: "b", source: { kind: "birdweather", station: "1" },
  });
  return { h, sql };
}
const checkIn = (h, id = "K1") => h.frame(new Request("https://x/api/frame",
  { headers: { "X-Device-Id": id, "If-None-Match": '"out"' } }));
const file = (sql, path: string) => sql.exec("INSERT INTO files (path, sha) VALUES (?, ?)", path, "x");

describe("X-FF-Update", () => {
  it("says nothing waits, beside the server's own headers", async () => {
    const { h } = await door();
    const r = await checkIn(h);
    expect(r.status).toBe(304);
    expect(r.headers.get("X-FF-Update")).toBe("0");
    expect(r.headers.get("X-Power-Mode")).toBe("sleep");
  });

  it("says an update waits when the server's message for the frame says so", async () => {
    const { h } = await door(true);
    expect((await checkIn(h)).headers.get("X-FF-Update")).toBe("1");
  });

  it("says one may wait for a dev image at the top of the data dir, and not for a kept release", async () => {
    const { h, sql } = await door();
    file(sql, "firmware/0.2.12/ee03.bin");
    file(sql, "frames/drawn/out/a.fff");
    expect((await checkIn(h)).headers.get("X-FF-Update")).toBe("0");
    file(sql, "firmware-ee02.bin");
    expect((await checkIn(h)).headers.get("X-FF-Update")).toBe("1");
  });

  it("answers a frame the server has not added yet", async () => {
    const { h } = await door(true);
    const r = await checkIn(h, "K2");
    expect(r.status).toBe(503);
    expect(r.headers.get("X-FF-Update")).toBe("0");
  });
});
