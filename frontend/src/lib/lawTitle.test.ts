import { describe, it, expect } from "vitest";
import { cleanLawTitle } from "./lawTitle";

describe("cleanLawTitle", () => {
  it("drops amendment brackets and the footnote mark after them", () => {
    expect(cleanLawTitle("[Lög um tekjuskatt]1)")).toBe("Lög um tekjuskatt");
    expect(cleanLawTitle("[Lög um gjald af áfengi, tóbaki, nikótíni o.fl.]1)")).toBe(
      "Lög um gjald af áfengi, tóbaki, nikótíni o.fl.",
    );
  });

  it("drops a footnote mark glued to the last word", () => {
    expect(cleanLawTitle("Lög um launamál1)")).toBe("Lög um launamál");
  });

  it("keeps parentheses that are part of the title", () => {
    expect(cleanLawTitle("Lög um evrópsk samvinnufélög (SCE-félög)")).toBe(
      "Lög um evrópsk samvinnufélög (SCE-félög)",
    );
  });

  it("leaves a clean title alone", () => {
    expect(cleanLawTitle("Stjórnarskrá lýðveldisins Íslands")).toBe("Stjórnarskrá lýðveldisins Íslands");
    expect(cleanLawTitle(null)).toBe("");
  });
});
