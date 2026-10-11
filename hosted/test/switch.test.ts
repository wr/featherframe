// @ts-nocheck: node:fs has no types under the Worker's tsconfig.
// The front door's port of the server's batch rule (W-1025), held to the same
// fixture as featherframe/switching.py.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { choose, kindOf, parse, type SwitchTable } from "../src/switch";

const CASES = JSON.parse(readFileSync(new URL("../../server/tests/fixtures/switch-cases.json", import.meta.url), "utf8"));

// A table may name another it "extends": that one with its own fields laid
// over it, each replacing the base's whole (the fixture's "_doc").
const table = (name: string): SwitchTable => {
  const { extends: base, ...rest } = CASES.tables[name];
  return base ? { ...table(base), ...rest } : rest;
};

describe("parse", () => {
  for (const c of CASES.parse) {
    it(c.name, () => expect(parse(c.kind, JSON.stringify(c.body))).toEqual(c.expect));
  }
  it("reads nothing from a body that is not JSON", () => {
    expect(parse("birdnet_go", "not json")).toBeNull();
    expect(parse("apprise", "")).toBeNull();
  });
});

describe("choose", () => {
  // Read as the file says: no defaults filled in, so an absent note or
  // first_days is the rule's to treat as null.
  for (const c of CASES.choose) {
    it(c.name, () => expect(choose(table(c.table), c.queued)).toBe(c.expect));
  }
  it("a species named like an inherited property is not in the table", () => {
    const push = { common: "", scientific: "constructor", confidence: 0.9 };
    expect(choose(table("base"), [{ push }])).toBe("wake");
  });
});

describe("kindOf", () => {
  it("names a push by its path", () => {
    expect(kindOf("/api/ingest/birdnet-go/tok")).toBe("birdnet_go");
    expect(kindOf("/api/ingest/apprise")).toBe("apprise");
    expect(kindOf("/api/hosted/seed")).toBeNull();
  });
  it("does not take a longer name for a push", () => {
    expect(kindOf("/api/ingest/apprise-x/tok")).toBeNull();
    expect(kindOf("/api/ingest/token")).toBeNull();
  });
});
