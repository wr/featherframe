// The admin page's bill (src/usage.ts, W-860).
import { afterEach, describe, expect, it, vi } from "vitest";
import { bill, cloudflareUsage, containerMeters, lastDays, measuredContainerMeters, project, serverTime, spendByDay,
         type Flows, type Meter } from "../src/usage";

const m = (used: number | null, included: number, price: number, unit = ""): Meter =>
  ({ group: "g", label: "l", unit, used, included, price });

describe("bill", () => {
  it("is the plan alone inside every allowance", () => {
    expect(bill([m(5e6, 10e6, 0.3 / 1e6), m(null, 1, 100)])).toBe(5);
  });
  it("adds only what is past an allowance", () => {
    expect(bill([m(12e6, 10e6, 0.3 / 1e6)])).toBeCloseTo(5.6);
  });
});

describe("project", () => {
  it("carries a flow on to the month's end and leaves storage where it is", () => {
    const mid = new Date(Date.UTC(2026, 8, 16));   // half of September gone
    const [flow, stored] = project([m(10, 100, 1), m(3, 5, 1, "GB")], mid);
    expect(flow.used).toBeCloseTo(20);
    expect(stored.used).toBe(3);
  });
});

describe("containerMeters", () => {
  it("counts an hour of a basic container", () => {
    const [cpu, mem, disk] = containerMeters(3600e3);
    expect(cpu.used).toBeCloseTo(15);   // 1/4 vCPU for 60 min
    expect(mem.used).toBeCloseTo(1);
    expect(disk.used).toBeCloseTo(4);
  });
  it("bills measured CPU for the time it was busy, memory and disk for the time it ran", () => {
    // An hour running, a quarter of it busy on a quarter vCPU (W-915).
    const [cpu, mem, disk] = measuredContainerMeters({ cpuS: 225, memByteS: 3600 * 2 ** 30, diskByteS: 3600 * 4e9 });
    expect(cpu.used).toBeCloseTo(3.75);
    expect(mem.used).toBeCloseTo(1);
    expect(disk.used).toBeCloseTo(4);
  });
});

describe("serverTime", () => {
  const day = (d: string, wakes: number, wake_ms: number, page_ms: number, server_ms = wake_ms + page_ms) =>
    ({ day: d, wakes, server_ms, wake_ms, page_ms });
  it("keeps wakes apart from the page", () => {
    expect(serverTime([day("2026-09-29", 40, 800e3, 0), day("2026-09-28", 45, 900e3, 600e3)]))
      .toEqual({ wakes: 85, wake_ms: 1700e3, page_ms: 600e3, since: null });
  });
  it("leaves out a day from before the split and says from when it counts", () => {
    expect(serverTime([day("2026-09-29", 40, 800e3, 0), day("2026-09-27", 50, 0, 0, 9e6)], "2026-09-29"))
      .toEqual({ wakes: 40, wake_ms: 800e3, page_ms: 0, since: "2026-09-29" });
  });
  it("counts from today when every day is from before the split", () => {
    expect(serverTime([day("2026-09-27", 50, 0, 0, 9e6)], "2026-09-28"))
      .toEqual({ wakes: 0, wake_ms: 0, page_ms: 0, since: "2026-09-28" });
  });
});

describe("spend by day (W-923)", () => {
  const d = (o: Record<string, number>) => new Map(Object.entries(o));
  const none = { wReq: null, wCpu: null, doReq: null, doActive: null, d1Read: null, d1Written: null, r2A: null, r2B: null };
  afterEach(() => vi.unstubAllGlobals());

  it("prices each day's use past the allowance, containers apart", () => {
    const f: Flows = { ...none, wReq: d({ "2026-09-30": 1e6 }),
      containers: { cpuS: d({ "2026-09-30": 60 }), memByteS: d({}), diskByteS: d({}) } };
    const [quiet, busy] = spendByDay(f, ["2026-09-29", "2026-09-30"]);
    expect(quiet).toEqual({ day: "2026-09-29", containers: 0, other: 0 });
    expect(busy.containers).toBeCloseTo(0.0012);   // a vCPU-minute
    expect(busy.other).toBeCloseTo(0.30);          // a million requests
  });
  it("draws nothing without the containers' own count", () => {
    expect(spendByDay({ ...none, wReq: d({ "2026-09-30": 1e6 }), containers: null }, ["2026-09-30"])).toEqual([]);
  });
  it("counts only this month's days into the meters, and prices the last 30", async () => {
    const two = (sum: object) => [{ dimensions: { date: "2026-09-30" }, sum }, { dimensions: { date: "2026-10-01" }, sum }];
    const account = {
      workersInvocationsAdaptive: two({ requests: 1e6, cpuTimeUs: 2e6 }),
      durableObjectsInvocationsAdaptiveGroups: two({ requests: 10 }),
      durableObjectsPeriodicGroups: two({ activeTime: 0 }),
      durableObjectsStorageGroups: [{ max: { storedBytes: 0 } }],
      d1AnalyticsAdaptiveGroups: two({ rowsRead: 3, rowsWritten: 1 }),
      d1StorageAdaptiveGroups: [],
      r2OperationsAdaptiveGroups: [
        { dimensions: { date: "2026-10-01", actionType: "PutObject" }, sum: { requests: 5 } },
        { dimensions: { date: "2026-10-01", actionType: "GetObject" }, sum: { requests: 7 } },
        { dimensions: { date: "2026-10-01", actionType: "DeleteObject" }, sum: { requests: 9 } }],
      r2StorageAdaptiveGroups: [],
      containersUsageAdaptiveGroups: two({ cpuTimeSec: 60, allocatedMemory: 0, allocatedDisk: 0 }),
    };
    const fetch = vi.fn(async () => Response.json({ data: { viewer: { accounts: [account] } } }));
    vi.stubGlobal("fetch", fetch);
    const now = new Date(Date.UTC(2026, 9, 1, 12));
    const u = await cloudflareUsage({ CF_API_TOKEN: "t", CF_ACCOUNT_ID: "a" } as any, 0, now);
    const used = (g: string, l: string) => u.meters.find((m) => m.group === g && m.label === l)!.used;
    expect(used("Workers", "Requests")).toBe(1e6);
    expect(used("Workers", "CPU")).toBe(2000);
    expect(used("R2", "Class A")).toBe(5);
    expect(used("R2", "Class B")).toBe(7);
    expect(used("Containers", "CPU")).toBeCloseTo(1);
    expect(u.meters.map((m) => m.group)).toEqual(["Workers", "Workers", "Durable Objects", "Durable Objects",
      "Durable Objects", "D1", "D1", "D1", "R2", "R2", "R2", "Containers", "Containers", "Containers"]);
    expect(u.measured).toBe(true);
    expect(u.days.map((x) => x.day)).toEqual(lastDays(30, now));
    expect(u.days.at(-2)!.containers).toBeCloseTo(0.0012);
    // The flows are asked from 30 days back; R2's stored level only this month.
    const sent = fetch.mock.calls.map((c: any) => JSON.parse(c[1].body));
    expect(sent.every((b: any) => !b.variables.s || b.variables.s === "2026-09-02T00:00:00Z")).toBe(true);
    expect(sent.find((b: any) => b.query.includes("r2StorageAdaptiveGroups")).query).toContain("datetime_geq: $m");
  });
});
