// @ts-nocheck: a front door on node's SQLite, as test/spend.test.ts builds one.
// The front door switches the screens itself when only detections arrived and
// the server said what each would show (W-1025).
import { describe, expect, it, vi } from "vitest";
import { FRESH_MS } from "../src/pagecache";
import { nodeSql } from "./sql";

vi.mock("cloudflare:workers", () => ({
  DurableObject: class { ctx: unknown; env: unknown; constructor(ctx: unknown, env: unknown) { this.ctx = ctx; this.env = env; } },
}));
vi.mock("../src/viewers", () => ({ display: vi.fn(), lobbyPng: vi.fn(), shortOf: vi.fn(), trmnlHeaders: vi.fn() }));

const GO = (common: string, sci: string) =>
  JSON.stringify({ type: "detection", metadata: { species: common, scientific_name: sci, confidence: 0.9 } });
const TABLE = {
  valid_until: 9_999_999_999, shown: "haemorhous mexicanus", shown_clean: true, blocked: [], floor: 0, notes: [],
  species: { "turdus migratorius": { common: "american robin", first_today: false, etag: "pic-robin",
    frames: { K1: { etag: "out-robin", file: "frames/drawn/out/robin.fff", push: { etag: "out-robin", rotation: 90, power: "awake", ota: false } } },
    viewers: { V1: { name: "view-robin", file: "frames/drawn/views/robin.png" } } } },
};
const SOURCE = { kind: "birdnet_go", token: "tok" };
// What the server reports after it drew the finch: the frame and the viewer on it.
const FINCH = { frames: { K1: { status: "on", etag: "out-finch", file: "frames/drawn/out/finch.fff",
                                headers: {}, push: { etag: "out-finch", rotation: 90, power: "awake", ota: false } } },
                viewers: { V1: { status: "on", name: "view-finch", file: "frames/drawn/views/finch.png", refresh: 900, short: "VVV" } },
                poll: true, unchanged: ["house finch", "haemorhous mexicanus"], page_build: "b", source: SOURCE };

async function door({ running = false, realWake = false } = {}) {
  const { Household } = await import("../src/household");
  const sql = nodeSql();
  const control = { running: running as boolean | "throws" };
  const sent: string[] = [];
  const woken: string[] = [];
  const asked: string[] = [];
  const sockets = [{ send: (m: string) => sent.push(m), close: () => {} }];
  const h = new Household({ storage: { sql, setAlarm: async () => {}, deleteAlarm: async () => {} },
                            getWebSockets: () => sockets, waitUntil: () => {} } as never,
    { APP_HOST: "cloud.featherframe.app", DATA: { delete: async () => {} },
      SERVER: { getByName: () => ({ running: async () => { if (control.running === "throws") throw new Error("link dropped"); return control.running; }, configure: async () => {}, sleepWhenIdle: async () => {},
                                     fetch: async (url: string) => { asked.push(String(url)); return new Response("{}"); } }) } } as never);
  h.setMeta("hid", "h1"); h.setMeta("key", "k"); h.setMeta("tz", "UTC");
  h.setMeta("source_kind", "birdnet_go"); h.setMeta("apprise_token", "tok");
  if (!realWake) h.wake = async () => { woken.push("wake"); };
  await h.takeState({ ...FINCH, switches: TABLE });
  return { h, sql, sent, woken, asked, sockets, control };
}
const push = (h, body: string) => h.ingest(new Request("https://x/api/ingest/birdnet-go/tok", { method: "POST", body }),
  new URL("https://x/api/ingest/birdnet-go/tok"));
const rows = (sql) => sql.exec("SELECT seq, path, body, taken FROM ingest ORDER BY seq").toArray();
const call = (h, path: string, body?: unknown) => h.internal(new Request(`https://x/_internal/h1/${path}`,
  { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }), path);

describe("the front door's own switch", () => {
  it("switches the frames, tells their sockets and leaves a marker, without the server", async () => {
    const { h, sql, sent, woken } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(true);
    expect(h.frameRow("K1").etag).toBe("out-robin");
    expect(h.frameRow("K1").file).toBe("frames/drawn/out/robin.fff");
    expect(JSON.parse(sent.at(-1)).etag).toBe("out-robin");
    expect(h.viewerRow("V1")).toMatchObject({ name: "view-robin", file: "frames/drawn/views/robin.png" });
    const q = rows(sql);
    expect(q.map((r) => r.path)).toEqual(["/api/ingest/birdnet-go/tok", "marker"]);
    expect(JSON.parse(q[1].body)).toMatchObject({ choice: "turdus migratorius", etag: "pic-robin", upto: q[0].seq });
    expect(JSON.parse(h.meta("unchanged"))).toEqual(["american robin", "turdus migratorius"]);
    expect(h.meta("push_news")).toBeNull();
    expect(Number(h.meta("switch_ms"))).toBeGreaterThan(0);
    expect(woken).toEqual([]);
  });

  it("leaves a marker and the screens alone when the species shown is chosen again", async () => {
    const { h, sql, sent } = await door();
    h.setMeta("switches", JSON.stringify({ ...TABLE, species: { ...TABLE.species,
      "haemorhous mexicanus": { common: "house finch", first_today: false, etag: "pic-finch", frames: {}, viewers: {} } } }));
    await push(h, GO("House Finch", "Haemorhous mexicanus"));
    const told = sent.length;
    expect(await h.decideAlone()).toBe(true);
    expect(h.frameRow("K1").etag).toBe("out-finch");
    expect(sent.length).toBe(told);
    expect(JSON.parse(rows(sql).at(-1).body)).toMatchObject({ choice: "same", etag: null });
    expect(h.summary().usage.reduce((n, u) => n + u.switches, 0)).toBe(0);   // a switch is a picture changed, not a decision
    expect(Number(h.meta("switch_ms"))).toBeGreaterThan(0);   // but the gap counts from it
  });

  it("chooses the next batch against what it switched to, not against what the server last reported", async () => {
    const { h, sent } = await door();
    const finch = { common: "house finch", first_today: false, etag: "pic-finch", viewers: {},
      frames: { K1: { etag: "out-finch", file: "frames/drawn/out/finch.fff", push: { etag: "out-finch", rotation: 90, power: "awake", ota: false } } } };
    h.setMeta("switches", JSON.stringify({ ...TABLE, species: { ...TABLE.species, "haemorhous mexicanus": finch } }));
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    expect(h.frameRow("K1").etag).toBe("out-robin");
    // The finch was the shown species in the report; it is not now. A "same" here would leave the robin up.
    h.setMeta("switch_ms", null);
    await push(h, GO("House Finch", "Haemorhous mexicanus"));
    expect(await h.decideAlone()).toBe(true);
    expect(h.frameRow("K1").etag).toBe("out-finch");
    expect(JSON.parse(sent.at(-1)).etag).toBe("out-finch");
    // And the finch again, now shown whole: nothing to switch.
    const told = sent.length;
    await push(h, GO("House Finch", "Haemorhous mexicanus"));
    expect(await h.decideAlone()).toBe(true);
    expect(sent.length).toBe(told);
    expect(h.frameRow("K1").etag).toBe("out-finch");
  });

  it("wakes the server for a species it has no answer for", async () => {
    const { h } = await door();
    await push(h, GO("Blue Jay", "Cyanocitta cristata"));
    expect(await h.decideAlone()).toBe(false);
  });

  it("leaves a running server to decide", async () => {
    const { h } = await door({ running: true });
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(false);
  });

  it("decides nothing once the table has expired", async () => {
    const { h } = await door();
    h.setMeta("switches", JSON.stringify({ ...TABLE, valid_until: 1 }));
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(false);
  });

  it("decides nothing when the table says no time it is good until", async () => {
    const { h } = await door();
    const { valid_until, ...open } = TABLE;
    h.setMeta("switches", JSON.stringify(open));
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(false);
    h.setMeta("switches", JSON.stringify({ ...TABLE, valid_until: "soon" }));
    expect(await h.decideAlone()).toBe(false);
    expect(h.frameRow("K1").etag).toBe("out-finch");
  });

  it("writes the rows and the marker before it tells any socket, and one that throws costs the others nothing", async () => {
    const { h, sql, sockets } = await door();
    const heard: string[] = [];
    sockets.unshift({ send: () => { throw new Error("socket closed"); }, close: () => {} });
    sockets.push({ send: (m: string) => heard.push(m), close: () => {} });
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(true);
    expect(h.frameRow("K1").etag).toBe("out-robin");
    expect(rows(sql).map((r) => r.path)).toEqual(["/api/ingest/birdnet-go/tok", "marker"]);
    expect(heard.map((m) => JSON.parse(m).etag)).toEqual(["out-robin"]);
    expect(h.meta("push_news")).toBeNull();
  });

  it("decides nothing for a server that reports no table (the deploy gap)", async () => {
    const { h } = await door();
    await h.takeState({ frames: {}, viewers: {}, poll: true, page_build: "b", source: SOURCE });
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(false);
  });

  it("decides nothing over a queue the server has already taken", async () => {
    // A page started the server, which took the pushes and went back to sleep
    // before the alarm: there is nothing here left for the front door to choose over.
    const { h, sql } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    await call(h, "ingest/take");
    expect(await h.decideAlone()).toBe(false);
    expect(rows(sql).map((r) => r.path)).toEqual(["/api/ingest/birdnet-go/tok"]);
  });

  it("wakes for a seed row, which is the server's to apply", async () => {
    const { h, sql } = await door();
    sql.exec("INSERT INTO ingest (path, body) VALUES (?, ?)", "/api/hosted/seed", "{}");
    await push(h, GO("American Robin", "Turdus migratorius"));
    expect(await h.decideAlone()).toBe(false);
    expect(rows(sql).map((r) => r.path)).toEqual(["/api/hosted/seed", "/api/ingest/birdnet-go/tok"]);
  });
});

describe("the report that follows", () => {
  const report = (over = {}) => ({ ...FINCH, unchanged: ["house finch", "haemorhous mexicanus"], switches: TABLE, ...over });

  it("keeps the switched frames when a report comes from before its marker, and asks for a wake", async () => {
    const { h, sql } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    const marker = rows(sql).at(-1).seq;
    h.setMeta("news", null);
    // A save's report: the server took the queue before the marker was written.
    await h.takeState(report({ applied_seq: marker - 1 }));
    expect(h.frameRow("K1").etag).toBe("out-robin");
    expect(h.viewerRow("V1").name).toBe("view-robin");
    expect(JSON.parse(h.meta("unchanged"))).toEqual(["american robin", "turdus migratorius"]);
    expect(h.meta("news")).toBe("1");
  });

  it("keeps them for a report from a run that has taken the marker but not yet applied it", async () => {
    const { h, sql } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    const marker = rows(sql).at(-1).seq;
    await call(h, "ingest/take");
    h.setMeta("news", null);
    await h.takeState(report({ applied_seq: marker - 1 }));
    expect(h.frameRow("K1").etag).toBe("out-robin");
    expect(h.meta("news")).toBe("1");
  });

  it("takes a report that has applied the marker as it comes, frames and all", async () => {
    const { h, sql } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    const marker = rows(sql).at(-1).seq;
    h.setMeta("news", null);
    await h.takeState(report({ applied_seq: marker }));
    expect(h.frameRow("K1").etag).toBe("out-finch");
    expect(h.viewerRow("V1").name).toBe("view-finch");
    expect(JSON.parse(h.meta("unchanged"))).toEqual(["house finch", "haemorhous mexicanus"]);
    expect(h.meta("news")).toBeNull();
    expect(JSON.parse(h.meta("switches")).valid_until).toBe(TABLE.valid_until);
  });

  it("takes a report from a server that says nothing of the queue (an older image) as it comes", async () => {
    const { h } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    h.setMeta("news", null);
    await h.takeState(report());
    expect(h.frameRow("K1").etag).toBe("out-finch");
    expect(h.meta("news")).toBeNull();
  });

  it("forgets the table a report does not carry, and keeps the mismatches the server counted", async () => {
    const { h } = await door();
    await h.takeState({ ...FINCH, switches: {}, switch_mismatches: 3 });
    expect(h.meta("switches")).toBeNull();
    expect(h.meta("switch_mismatches")).toBe("3");
    await h.takeState({ ...FINCH });
    expect(h.meta("switch_mismatches")).toBeNull();
  });
});

describe("the queue", () => {
  it("hands the server everything queued and forgets it only once acknowledged", async () => {
    const { h, sql } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    const taken = await (await call(h, "ingest/take")).json();
    expect(taken.rows.map((r) => r.path)).toEqual(["/api/ingest/birdnet-go/tok", "marker"]);
    expect(rows(sql).every((r) => r.taken === 1)).toBe(true);
    await call(h, "ingest/ack", { upto: taken.rows.at(-1).seq });
    expect(rows(sql)).toEqual([]);
  });

  it("hands over again what was taken and never acknowledged", async () => {
    const { h } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    const first = await (await call(h, "ingest/take")).json();
    const again = await (await call(h, "ingest/take")).json();
    expect(again.rows).toEqual(first.rows);
  });

  it("keeps what landed after the take, through the acknowledgement of what came before", async () => {
    const { h, sql } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    const taken = await (await call(h, "ingest/take")).json();
    await push(h, GO("Blue Jay", "Cyanocitta cristata"));
    await call(h, "ingest/ack", { upto: taken.rows.at(-1).seq });
    expect(rows(sql).map((r) => [r.taken, JSON.parse(r.body).metadata.species])).toEqual([[0, "Blue Jay"]]);
  });

  it("sends the server nothing at a wake: it takes the queue itself", async () => {
    const { h, sql, asked } = await door({ realWake: true });
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.wake();
    expect(asked).toEqual(["http://server/api/hosted/run"]);
    expect(rows(sql).map((r) => r.taken)).toEqual([0]);
    expect(h.meta("push_news")).toBeNull();
    expect(h.meta("news")).toBe("0");
  });
});

describe("the alarm", () => {
  const ripe = (h) => h.setMeta("wake_ms", String(Date.now() - 6 * 60_000));

  it("switches alone when detections are all that is due", async () => {
    const { h, woken } = await door();
    ripe(h);
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.alarm();
    expect(h.frameRow("K1").etag).toBe("out-robin");
    expect(woken).toEqual([]);
  });

  it("wakes the server for a species it cannot answer", async () => {
    const { h, woken } = await door();
    ripe(h);
    await push(h, GO("Blue Jay", "Cyanocitta cristata"));
    await h.alarm();
    expect(h.frameRow("K1").etag).toBe("out-finch");
    expect(woken).toEqual(["wake"]);
  });

  it("wakes the server when deciding alone fails, and never drops the alarm", async () => {
    const { h, woken, control } = await door();
    ripe(h);
    await push(h, GO("American Robin", "Turdus migratorius"));
    control.running = "throws";
    await h.alarm();
    expect(h.frameRow("K1").etag).toBe("out-finch");
    expect(woken).toEqual(["wake"]);
  });

  it("wakes the server, whatever the batch, when something else is due", async () => {
    const { h, woken } = await door();
    ripe(h);
    await push(h, GO("American Robin", "Turdus migratorius"));
    h.setMeta("news", "1");                       // a pairing
    await h.alarm();
    expect(h.frameRow("K1").etag).toBe("out-finch");
    expect(woken).toEqual(["wake"]);
  });

  it("waits out the gap from its own last switch", async () => {
    const { h, woken } = await door();
    ripe(h);
    h.setMeta("switch_ms", String(Date.now() - 60_000));
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.alarm();
    expect(h.frameRow("K1").etag).toBe("out-finch");
    expect(woken).toEqual([]);
  });
});

describe("what the admin page counts", () => {
  it("counts a switch on its day, and the mismatches the server reports", async () => {
    const { h } = await door();
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    await h.takeState({ ...FINCH, switches: TABLE, switch_mismatches: 2 });
    const s = h.summary();
    expect(s.mismatches).toBe(2);
    expect(s.usage[0]).toMatchObject({ switches: 1, wakes: 0 });
  });
});

describe("the page's copies", () => {
  it("ages the page's copies to a stale time instead of deleting them", async () => {
    const { h, sql } = await door();
    const recent = Date.now() - 1000;
    const insert = (key: string, kind: string, object: string, filled: number) => sql.exec(
      "INSERT INTO page_cache (key, kind, ask, object, headers, filled_at, read_at) VALUES (?, ?, '{}', ?, '{}', ?, ?)",
      key, kind, object, filled, filled);
    insert("k", "changing", "o", recent);
    insert("old", "changing", "q", 5);
    insert("f", "fixed", "p", recent);
    const before = Date.now();
    await push(h, GO("American Robin", "Turdus migratorius"));
    await h.decideAlone();
    const after = Date.now();
    const got = Object.fromEntries(sql.exec("SELECT key, object, filled_at FROM page_cache").toArray()
      .map((r) => [r.key, r]));
    expect(got.k.object).toBe("o");                                    // kept, to be served
    expect(got.k.filled_at).toBeGreaterThanOrEqual(before - FRESH_MS);   // 15 min old: stale, and said so
    expect(got.k.filled_at).toBeLessThanOrEqual(after - FRESH_MS);
    expect(got.old).toMatchObject({ object: "q", filled_at: 5 });      // already older: as it was
    expect(got.f).toMatchObject({ object: "p", filled_at: recent });
  });

  it("leaves them alone when the screens did not change", async () => {
    const { h, sql } = await door();
    h.setMeta("switches", JSON.stringify({ ...TABLE, species: { "haemorhous mexicanus": { common: "house finch",
      first_today: false, etag: "pic-finch", frames: {}, viewers: {} } } }));
    sql.exec("INSERT INTO page_cache (key, kind, ask, object, headers, filled_at, read_at) VALUES ('k', 'changing', '{}', 'o', '{}', 5, 5)");
    await push(h, GO("House Finch", "Haemorhous mexicanus"));
    expect(await h.decideAlone()).toBe(true);
    expect(sql.exec("SELECT filled_at FROM page_cache").toArray()).toEqual([{ filled_at: 5 }]);
  });
});
