// A pairing code's expiry, as the glass prints it (src/pairing.ts).
import { describe, expect, it } from "vitest";
import { expiryText } from "../src/pairing";

const at = Date.UTC(2026, 8, 25, 14, 32) / 1000;
const req = (timezone?: string) => ({ cf: timezone ? { timezone } : undefined }) as unknown as Request;

describe("expiryText", () => {
  it("is in the asking device's own time zone", () => {
    expect(expiryText(at, req("America/New_York"))).toBe("25 September, 10:32 am");
  });
  it("says UTC when the zone is unknown or unusable", () => {
    expect(expiryText(at, req())).toBe("25 September, 2:32 pm UTC");
    expect(expiryText(at, req("Not/AZone"))).toBe("25 September, 2:32 pm UTC");
  });
});
