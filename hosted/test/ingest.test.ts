// Which BirdNET-Go webhook bodies are news (W-865): only a detection wakes the
// household's server; the same channel's warnings do not.
import { describe, expect, it } from "vitest";
import { birdweatherNews, changesNothing, isDetection, pushedNames } from "../src/util";

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

describe("what a push names, and whether it changes anything (W-984)", () => {
  const go = (species: string, scientific: string) =>
    JSON.stringify({ type: "detection", metadata: { species, scientific_name: scientific } });

  it("reads BirdNET-Go's metadata and BirdNET-Pi's Apprise message", () => {
    expect(pushedNames("birdnet_go", go("Eastern Chipmunk", "Tamias striatus")))
      .toEqual(["eastern chipmunk", "tamias striatus"]);
    const apprise = JSON.stringify({ title: "x", message: 'New: {"comname": "Blue Jay", "sciname": "Cyanocitta cristata"} heard' });
    expect(pushedNames("apprise", apprise)).toEqual(["blue jay", "cyanocitta cristata"]);
    expect(pushedNames("apprise", JSON.stringify({ common: "Blue Jay" }))).toEqual(["blue jay"]);
    expect(pushedNames("birdnet_go", "not json")).toEqual([]);
  });

  it("is quiet only for what the server named", () => {
    const shown = JSON.stringify(["eastern chipmunk", "tamias striatus", "house sparrow"]);
    expect(changesNothing(shown, ["eastern chipmunk", "tamias striatus"])).toBe(true);
    expect(changesNothing(shown, ["house sparrow"])).toBe(true);
    expect(changesNothing(shown, ["blue jay", "cyanocitta cristata"])).toBe(false);
    expect(changesNothing(shown, [])).toBe(false);              // names nothing: news
    expect(changesNothing(JSON.stringify("*"), ["blue jay"])).toBe(true);
    expect(changesNothing(null, ["eastern chipmunk"])).toBe(false);   // no report yet
  });

  it("looks at every BirdWeather detection since the last look", () => {
    const row = (id: number, commonName: string) => ({ id, species: { commonName, scientificName: "" } });
    const shown = JSON.stringify(["eastern chipmunk"]);
    expect(birdweatherNews([row(5, "Blue Jay")], null, shown)).toEqual({ news: false, last: "5" });
    expect(birdweatherNews([row(7, "Eastern Chipmunk"), row(6, "Eastern Chipmunk"), row(5, "Blue Jay")], "5", shown))
      .toEqual({ news: false, last: "7" });
    expect(birdweatherNews([row(7, "Eastern Chipmunk"), row(6, "Blue Jay"), row(5, "Blue Jay")], "5", shown))
      .toEqual({ news: true, last: "7" });
    expect(birdweatherNews([row(5, "Blue Jay")], "5", shown)).toEqual({ news: false, last: "5" });
    // A full page of new ones may have missed some: news.
    expect(birdweatherNews([row(9, "Eastern Chipmunk"), row(8, "Eastern Chipmunk")], "5", shown).news).toBe(true);
  });
});
