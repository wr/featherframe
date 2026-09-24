// The admin page's bill (src/usage.ts, W-860).
import { describe, expect, it } from "vitest";
import { bill, containerMeters, project, type Meter } from "../src/usage";

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
});
