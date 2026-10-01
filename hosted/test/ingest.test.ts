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

// A kit's update check reaches the server only when there is something for it
// (W-915): every 15 min per kit, it was starting the server all night.
import { firmwareWaiting } from "../src/util";

describe("firmwareWaiting", () => {
  it("wakes the server for a release the server said is waiting", () => {
    expect(firmwareWaiting(JSON.stringify({ etag: "a", ota: true }), [])).toBe(true);
  });
  it("wakes it for a dev image at the top of the data dir", () => {
    expect(firmwareWaiting(JSON.stringify({ ota: false }), ["firmware.bin"])).toBe(true);
    expect(firmwareWaiting(null, ["firmware-ee02.bin"])).toBe(true);
  });
  it("answers itself otherwise, a release's own files included", () => {
    expect(firmwareWaiting(JSON.stringify({ etag: "a", ota: false }), ["firmware/0.2.10/ee03.bin"])).toBe(false);
    expect(firmwareWaiting(null, [])).toBe(false);
  });
  it("leaves a message it cannot read to the server", () => {
    expect(firmwareWaiting("{not json", [])).toBe(true);
  });
});
