import { describe, it, expect } from "vitest";
import { formatCount } from "./formatNumber";

describe("formatCount", () => {
  it("groups thousands with a period, as Icelandic does", () => {
    expect(formatCount(44502)).toBe("44.502");
    expect(formatCount(1297)).toBe("1.297");
    expect(formatCount(92894)).toBe("92.894");
    expect(formatCount(1781516)).toBe("1.781.516");
  });

  it("leaves short numbers alone", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(5)).toBe("5");
    expect(formatCount(999)).toBe("999");
  });

  it("does not depend on the runtime carrying Icelandic locale data", () => {
    // The whole point: no Intl, so a runtime without is-IS cannot fall back to
    // the en-US comma.
    expect(formatCount(44502)).not.toContain(",");
  });

  it("handles negatives and non-integers without inventing digits", () => {
    expect(formatCount(-1234)).toBe("-1.234");
    expect(formatCount(1234.7)).toBe("1.234");
  });
});
