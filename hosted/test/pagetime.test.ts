// @ts-nocheck: node:fs has no types under the Worker's tsconfig.
// The page's ages (server/templates/page-time.js) are the server's _ago,
// held to the same cases (W-946).
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const SRC = readFileSync(new URL("../../server/templates/page-time.js", import.meta.url), "utf8");
const ffAgo = new Function(`${SRC}; return ffAgo;`)();
const CASES = JSON.parse(readFileSync(new URL("../../server/tests/fixtures/page-time-cases.json", import.meta.url), "utf8"));

describe("ffAgo", () => {
  for (const c of CASES) it(`${c.secs} s`, () => expect(ffAgo(1_790_000_000 - c.secs, 1_790_000_000)).toBe(c.text));
});
