// The admin page's charts (src/charts.ts, W-923).
import { describe, expect, it } from "vitest";
import { chart, dayLabel, fmt, growth, serverByDay, ticks } from "../src/charts";

const day = (d: string, wake_ms: number, page_ms: number, server_ms = wake_ms + page_ms) =>
  ({ day: d, wakes: 1, server_ms, wake_ms, page_ms });

describe("serverByDay", () => {
  it("adds every household's day together, and a day from before the split as its total", () => {
    const days = ["2026-09-26", "2026-09-27", "2026-09-28"];
    const out = serverByDay([
      { usage: [day("2026-09-28", 60e3, 30e3), day("2026-09-27", 0, 0, 90e3), day("2026-08-01", 5e3, 0)] },
      { usage: [day("2026-09-28", 40e3, 0)] },
    ], days);
    expect(out).toEqual({ wake: [0, 0, 100e3], page: [0, 0, 30e3], total: [0, 90e3, 0] });
  });
});

describe("growth", () => {
  const now = new Date(Date.UTC(2026, 9, 1, 12));
  const t = (iso: string) => Date.parse(iso) / 1000;
  it("runs from the first day anything began to today, counting each list as it grew", () => {
    const g = growth([[t("2026-09-28T10:00:00Z"), t("2026-09-28T23:00:00Z"), t("2026-09-30T08:00:00Z")],
                      [t("2026-09-29T09:00:00Z")]], now);
    expect(g.days).toEqual(["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]);
    expect(g.counts).toEqual([[2, 2, 3, 3], [0, 1, 1, 1]]);
  });
  it("is empty with nothing on record", () => {
    expect(growth([[], []], now)).toEqual({ days: [], counts: [[], []] });
  });
});

describe("ticks", () => {
  it("steps money in round amounts, a cent at least", () => {
    expect(ticks(0.25, "usd")).toMatchObject({ top: 0.3, step: 0.1 });
    expect(ticks(0.003, "usd")).toMatchObject({ top: 0.01, step: 0.01 });
    expect(ticks(0, "usd")).toMatchObject({ top: 0.01 });
  });
  it("steps a count in whole numbers", () => {
    expect(ticks(3, "count")).toMatchObject({ top: 3, step: 1 });
    expect(ticks(15, "count")).toMatchObject({ top: 15, step: 5 });
  });
  it("steps time in minutes, and in hours past two", () => {
    expect(ticks(85 * 60e3, "ms")).toMatchObject({ top: 100 * 60e3, step: 50 * 60e3, scale: 60e3 });
    expect(ticks(7 * 3600e3, "ms")).toMatchObject({ top: 8 * 3600e3, step: 2 * 3600e3, scale: 3600e3 });
  });
});

describe("labels", () => {
  it("writes a day as people do", () => {
    expect(dayLabel("2026-09-24")).toBe("24 Sep");
    expect(dayLabel("2026-10-01")).toBe("1 Oct");
  });
  it("writes a value for its unit", () => {
    expect(fmt(0.123, "usd")).toBe("$0.12");
    expect(fmt(0.001, "usd")).toBe("<$0.01");
    expect(fmt(34 * 60e3, "ms")).toBe("34 min");
    expect(fmt(95 * 60e3, "ms")).toBe("1.6 h");
    expect(fmt(4, "count")).toBe("4");
  });
});

describe("chart", () => {
  const spec = {
    label: "Test", kind: "stack" as const, unit: "usd" as const, days: ["2026-09-30", "2026-10-01"],
    series: [{ label: "A <b>", color: "--r3", values: [0.1, 0] }, { label: "B", color: "--r1", values: [0.05, 0.02] }],
  };
  it("draws a column per day, rounds only the top of each, and escapes what it is given", () => {
    const html = chart(spec);
    expect(html).toContain('<div class="grid top"><span>$0.15</span>');
    expect(html).toContain("<span>30 Sep</span><span>1 Oct</span>");
    expect(html.match(/class="col"/g)).toHaveLength(2);
    expect(html.match(/class="seg[^"]*cap/g)).toHaveLength(2);
    expect(html).toContain("A &lt;b&gt;");
    expect(html).not.toContain("A <b>");
  });
  it("leaves out a day's empty part, and keeps every day in its table", () => {
    const html = chart(spec);
    const second = html.split('class="col"')[2].split("</div></div>")[0];
    expect(second.match(/class="seg/g)).toHaveLength(1);
    expect(html).toContain("<tr><td>1 Oct</td><td>$0.00</td><td>$0.02</td></tr>");
  });
  it("keeps only the days a running count changed in its table", () => {
    const html = chart({ label: "G", kind: "line", unit: "count", days: ["2026-09-29", "2026-09-30", "2026-10-01"],
      latest: true, series: [{ label: "Sign-ups", color: "--r3", values: [1, 1, 2] }] });
    expect(html.match(/<tr><td>/g)).toHaveLength(2);
    expect(html).toContain("Sign-ups <b>2</b>");
  });
});
