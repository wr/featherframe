// @ts-nocheck: node:fs has no types under the Worker's tsconfig.
// The front door's side of the spend guards (W-938): the same rule as the
// server's, held to the same cases, plus the backstop and the alerts.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { alertMail, BACKSTOP_USD_PER_DAY, decide, SpendBook } from "../src/spend";
import { nodeSql } from "./sql";

const CASES = JSON.parse(readFileSync(new URL("../../server/tests/fixtures/spend-cases.json", import.meta.url), "utf8"));

describe("decide", () => {
  for (const c of CASES) {
    it(c.name, () => {
      expect(decide(c.rows, c.paused, c.resumed_at, c.rec, c.rule, c.now)).toBe(c.expect);
    });
  }
});

const T = 1790000000;                       // 2026-09-21 (UTC)
let n = 0;
function rec(over: Record<string, unknown> = {}) {
  n++;
  return { id: `r${n}`, at: T, month: "2026-09", day: "2026-09-21", kind: "plate", subject: `s${n}`,
           auto: true, model: "gpt-image-2.5-sunburst", quality: "max", est_usd: 0.194,
           cost_usd: null, state: "open", ...over };
}
const RULE = { limit_usd: 1000, runaway_per_hour: 6, window_s: null };

describe("SpendBook", () => {
  it("inserts what the rule allows and settles it", () => {
    const book = new SpendBook(nodeSql());
    const r = rec();
    expect(book.reserve(r, RULE).ok).toBe(true);
    book.settle(r.id, "settled", 0.2);
    const snap = book.snapshot(0);
    expect(snap.rows.map((x) => [x.state, x.cost_usd, x.auto])).toEqual([["settled", 0.2, true]]);
  });

  it("trips the pause at the seventh image in an hour and keeps it until resumed", () => {
    // Collages with a date each: a first illustration of a species does not count (W-938).
    const book = new SpendBook(nodeSql());
    const out = [];
    for (let i = 0; i < 8; i++) out.push(book.reserve(rec({ at: T + i * 300, kind: "collage", subject: `d${i}` }), RULE));
    expect(out.map((o) => o.reason)).toEqual([null, null, null, null, null, null, "runaway", "paused"]);
    expect(out[6].alerts.map((a) => a.reason)).toEqual(["paused"]);
    expect(book.snapshot(0).pause).toEqual({ at: T + 1800, count: 6 });
    book.resume(T + 2400);
    expect(book.reserve(rec({ at: T + 2500, kind: "collage", subject: "d9" }), RULE).ok).toBe(true);
  });

  it("refuses past the backstop whatever the request's limit", () => {
    const book = new SpendBook(nodeSql());
    const big = { limit_usd: 1e6, runaway_per_hour: null, window_s: null };
    let spent = 0;
    for (let i = 0; spent + 0.194 <= BACKSTOP_USD_PER_DAY; i++) {
      expect(book.reserve(rec({ at: T + i, auto: false }), big).ok).toBe(true);
      spent += 0.194;
    }
    const last = book.reserve(rec({ at: T + 999, auto: false }), big);
    expect(last.reason).toBe("backstop");
    expect(last.alerts.map((a) => a.reason)).toContain("backstop");
  });

  it("says once a day that a household passed $3", () => {
    const book = new SpendBook(nodeSql());
    const free = { limit_usd: 1e6, runaway_per_hour: null, window_s: null };
    const alerts = [];
    for (let i = 0; i < 20; i++) alerts.push(...book.reserve(rec({ at: T + i, auto: false }), free).alerts);
    expect(alerts.filter((a) => a.reason === "day")).toHaveLength(1);
  });

  it("imports the server's own records once", () => {
    const book = new SpendBook(nodeSql());
    const rows = [rec({ state: "settled", cost_usd: 0.2 }), rec({ state: "settled", cost_usd: 0.1 })];
    expect(book.importRows(rows)).toBe(2);
    expect(book.importRows(rows)).toBe(0);
    expect(book.monthSummary("2026-09").usd).toBeCloseTo(0.3);
  });

  it("sums the month for the admin page", () => {
    const book = new SpendBook(nodeSql());
    book.reserve(rec({ est_usd: 0.5 }), { ...RULE, limit_usd: 10 });
    const s = book.monthSummary("2026-09");
    expect(s).toEqual({ usd: 0.5, limit: 10, paused: false, count: 1 });
  });
});

describe("alertMail", () => {
  it("names the household and the numbers", () => {
    const m = alertMail("h1", "cloud.featherframe.app",
      { reason: "paused", lastHour: 6, today: 1.2, month: 4.5, limit: 10 });
    expect(m.subject).toBe("Featherframe Cloud: h1 AI paused");
    expect(m.text).toContain("h1 bought more AI images in an hour than the pause allows.");
    expect(m.text).toContain("Last hour: 6 images. Today (UTC): $1.20. This month: $4.50 of $10.00.");
    expect(m.text).toContain("https://cloud.featherframe.app/admin");
  });
});
