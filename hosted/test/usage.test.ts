// The admin page's bill (src/usage.ts, W-860).
import { describe, expect, it } from "vitest";
import { bill, containerMeters, measuredContainerMeters, project, serverTime, type Meter } from "../src/usage";

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
