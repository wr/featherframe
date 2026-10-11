// When the front door wakes the household's server (W-847), now with a look
// at a stale page (W-946): never inside a request, never before MIN_GAP_MS.
import { describe, expect, it } from "vitest";
import { due, MIN_GAP_MS, nextAlarm, onlyDetections, POLL_MS, SAFETY_MS, type WakeState } from "../src/wakes";

const T = 1_790_000_000_000;
const s = (o: Partial<WakeState> = {}): WakeState =>
  ({ now: T, lastWake: T - 60_000, lastSwitch: 0, named: 0, news: false, pushNews: false, look: false,
    birdweather: false, ...o });

describe("due", () => {
  it("wakes for a named time that has come", () => {
    expect(due(s({ named: T - 1 }))).toBe(true);
    expect(due(s({ named: T + 1 }))).toBe(false);
  });
  it("wakes for news or a look only once MIN_GAP_MS has passed", () => {
    expect(due(s({ news: true }))).toBe(false);
    expect(due(s({ look: true }))).toBe(false);
    expect(due(s({ look: true, lastWake: T - MIN_GAP_MS }))).toBe(true);
    expect(due(s({ news: true, lastWake: T - MIN_GAP_MS }))).toBe(true);
  });
  it("wakes once a day regardless", () => {
    expect(due(s({ lastWake: T - SAFETY_MS }))).toBe(true);
  });
});

describe("nextAlarm", () => {
  it("sets a look's alarm to when the gap ends, never sooner than a second", () => {
    expect(nextAlarm(s({ look: true }))).toBe(T - 60_000 + MIN_GAP_MS);
    expect(nextAlarm(s({ look: true, lastWake: T - MIN_GAP_MS - 1 }))).toBe(T + 1000);
  });
  it("gives two looks the same alarm", () => {
    expect(nextAlarm(s({ look: true }))).toBe(nextAlarm(s({ look: true })));
  });
  it("alarms for a named time that came due before anything alarmed for it", () => {
    // It came due while the front door was deciding over a push (W-1025): the
    // alarm that follows is for it, a second away, not for the next event.
    expect(nextAlarm(s({ named: T - 1 }))).toBe(T + 1000);
    expect(nextAlarm(s({ named: T + 5000 }))).toBe(T + 5000);
  });
  it("retries a named time a wake has since failed to move after the gap, not every second", () => {
    expect(nextAlarm(s({ named: T - 120_000, lastWake: T - 60_000 }))).toBe(T - 60_000 + MIN_GAP_MS);
  });
  it("keeps the BirdWeather poll and the daily wake as before", () => {
    expect(nextAlarm(s({ birdweather: true }))).toBe(T + POLL_MS);
    expect(nextAlarm(s())).toBe(T - 60_000 + SAFETY_MS);
  });
});

describe("detections alone (W-1025)", () => {
  it("wait out the gap from the later of the last wake and the last switch", () => {
    expect(due(s({ pushNews: true, lastWake: T - MIN_GAP_MS, lastSwitch: T - 60_000 }))).toBe(false);
    expect(due(s({ pushNews: true, lastWake: T - MIN_GAP_MS, lastSwitch: T - MIN_GAP_MS }))).toBe(true);
    expect(nextAlarm(s({ pushNews: true, lastWake: T - MIN_GAP_MS, lastSwitch: T - 60_000 })))
      .toBe(T - 60_000 + MIN_GAP_MS);
  });
  it("are the only reason only when nothing else is due", () => {
    const ripe = { pushNews: true, lastWake: T - MIN_GAP_MS };
    expect(onlyDetections(s(ripe))).toBe(true);
    expect(onlyDetections(s({ ...ripe, news: true }))).toBe(false);       // a pairing
    expect(onlyDetections(s({ ...ripe, look: true }))).toBe(false);
    expect(onlyDetections(s({ ...ripe, named: T - 1 }))).toBe(false);
    expect(onlyDetections(s({ ...ripe, lastWake: T - SAFETY_MS }))).toBe(false);
  });
});
