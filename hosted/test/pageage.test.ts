// @ts-nocheck: node:fs has no types under the Worker's tsconfig.
// How old the page's status is (server/templates/page-age.js, W-954).
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (f: string) => readFileSync(new URL(`../../server/templates/${f}`, import.meta.url), "utf8");
const ffPageAge = new Function(`${read("page-time.js")}\n${read("page-age.js")}; return ffPageAge;`)();

const T = 1_790_000_000_000;
const MIN = 60_000;
const date = (ms: number) => new Date(ms).toUTCString();

describe("ffPageAge", () => {
  it("never applies the front door's copy over a newer live answer, so the age does not come back", () => {
    const a = ffPageAge();
    expect(a.answer(String(T - 12 * MIN), date(T), T)).toBe(true);
    expect(a.note(T)).toEqual({ text: "Updated 12 min ago", act: true });
    // A live answer (a firmware update under way): current, and said so.
    expect(a.answer(null, date(T + 2000), T + 2000)).toBe(true);
    expect(a.note(T + 2000)).toEqual({ text: "Updated just now", act: false });
    // The next ordinary poll gets the old copy back: not applied.
    expect(a.answer(String(T - 12 * MIN), date(T + 30_000), T + 30_000)).toBe(false);
    expect(a.note(T + 30_000)).toBeNull();
    // The age counts from the live answer.
    expect(a.note(T + 2000 + 6 * MIN)).toEqual({ text: "Updated 6 min ago", act: true });
  });

  it("an old page that becomes current says so, then the note goes", () => {
    const a = ffPageAge();
    a.answer(String(T - 8 * MIN), date(T), T);
    expect(a.note(T)!.text).toBe("Updated 8 min ago");
    expect(a.answer(String(T + MIN - 1000), date(T + MIN), T + MIN)).toBe(true);
    expect(a.note(T + MIN)).toEqual({ text: "Updated just now", act: false });
    expect(a.note(T + MIN + a.CONFIRM)).toBeNull();
  });

  it("a current page shows nothing until it is 5 minutes old, and confirms nothing", () => {
    const a = ffPageAge();
    a.answer(String(T - MIN), date(T), T);
    expect(a.note(T)).toBeNull();
    a.answer(String(T), date(T + 30_000), T + 30_000);
    expect(a.note(T + 30_000)).toBeNull();
    expect(a.note(T + 5 * MIN + 1000)).toEqual({ text: "Updated 5 min ago", act: true });
  });

  it("Update now reloads on a current answer, and shows the age again after 2 minutes", () => {
    const a = ffPageAge();
    a.answer(String(T - 10 * MIN), date(T), T);
    a.update(T);
    expect(a.note(T)).toEqual({ text: "Updating…", act: false });
    a.answer(String(T - 10 * MIN), date(T + 3000), T + 3000);   // the same copy
    expect(a.reload(T + 3000)).toBe(false);
    a.answer(String(T + 5000), date(T + 6000), T + 6000);       // refilled by the wake
    expect(a.reload(T + 6000)).toBe(true);

    const b = ffPageAge();
    b.answer(String(T - 10 * MIN), date(T), T);
    b.update(T);
    expect(b.isUpdating(T + 2 * MIN + 1)).toBe(false);
    expect(b.note(T + 2 * MIN + 1)).toEqual({ text: "Updated 12 min ago", act: true });
  });

  it("an answer with neither a copy time nor a Date is now", () => {
    const a = ffPageAge();
    expect(a.answer(null, null, T)).toBe(true);
    expect(a.answer(null, "", T + 1000)).toBe(true);
    expect(a.note(T + 1000)).toBeNull();
  });
});
