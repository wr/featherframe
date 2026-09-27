// Which BirdNET-Go webhook bodies are news (W-865): only a detection wakes the
// household's server; the same channel's warnings do not.
import { describe, expect, it } from "vitest";
import { isDetection } from "../src/util";

describe("isDetection", () => {
  it("takes BirdNET-Go's default detection body", () => {
    expect(isDetection(JSON.stringify({ type: "detection", metadata: { species: "Blue Jay" } }))).toBe(true);
  });
  it("drops its warnings, errors and anything unreadable", () => {
    expect(isDetection(JSON.stringify({ type: "warning", title: "Stream disconnected" }))).toBe(false);
    expect(isDetection("null")).toBe(false);
    expect(isDetection("not json")).toBe(false);
  });
});
