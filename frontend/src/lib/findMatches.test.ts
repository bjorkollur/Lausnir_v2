import { describe, it, expect } from "vitest";
import { findMatches } from "./findMatches";

describe("findMatches", () => {
  it("finds a single match within one segment", () => {
    const matches = findMatches(["fyrsti hluti", "seinni hluti með orðið kærunefnd hér"], "kærunefnd");
    expect(matches).toEqual([{ segmentIndex: 1, offset: 23 }]);
  });

  it("finds multiple matches within the same segment", () => {
    const matches = findMatches(["kött kött kött"], "kött");
    expect(matches).toEqual([
      { segmentIndex: 0, offset: 0 },
      { segmentIndex: 0, offset: 5 },
      { segmentIndex: 0, offset: 10 },
    ]);
  });

  it("is case-insensitive", () => {
    const matches = findMatches(["Hæstiréttur dæmdi"], "hæstiréttur");
    expect(matches).toHaveLength(1);
  });

  it("returns no matches for queries under 2 characters", () => {
    expect(findMatches(["einhver texti"], "e")).toEqual([]);
    expect(findMatches(["einhver texti"], "")).toEqual([]);
  });

  it("returns no matches when nothing found", () => {
    expect(findMatches(["einhver texti"], "hvorki")).toEqual([]);
  });

  it("finds matches spread across several segments in order", () => {
    const matches = findMatches(["orð eitt", "orð tvö", "ekkert hér", "orð þrjú"], "orð");
    expect(matches.map((m) => m.segmentIndex)).toEqual([0, 1, 3]);
  });
});

describe("findMatches with useRegex", () => {
  it("matches a regex pattern across segments", () => {
    const matches = findMatches(["mál nr. 12/2020", "eitthvað", "mál nr. 45/2021"], "\\d+/\\d{4}", true);
    expect(matches.map((m) => m.segmentIndex)).toEqual([0, 2]);
  });

  it("is case-insensitive in regex mode", () => {
    const matches = findMatches(["HÆSTIRÉTTUR dæmdi"], "hæstiréttur", true);
    expect(matches).toHaveLength(1);
  });

  it("returns no matches for an invalid regex pattern instead of throwing", () => {
    expect(() => findMatches(["texti"], "(", true)).not.toThrow();
    expect(findMatches(["texti"], "(", true)).toEqual([]);
  });

  it("allows single-character patterns in regex mode", () => {
    const matches = findMatches(["a b a"], "a", true);
    expect(matches).toHaveLength(2);
  });
});
