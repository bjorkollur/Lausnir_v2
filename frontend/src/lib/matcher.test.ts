import { describe, it, expect } from "vitest";
import { isValidRegex, compileMatcher } from "./matcher";

describe("isValidRegex", () => {
  it("accepts valid patterns", () => {
    expect(isValidRegex("\\d+/\\d{4}")).toBe(true);
    expect(isValidRegex("a")).toBe(true);
  });

  it("rejects malformed patterns", () => {
    expect(isValidRegex("(")).toBe(false);
    expect(isValidRegex("[")).toBe(false);
  });
});

describe("compileMatcher", () => {
  it("returns null for an invalid regex pattern", () => {
    expect(compileMatcher("(", true)).toBeNull();
  });

  it("returns a working matcher for a valid regex pattern", () => {
    const matcher = compileMatcher("\\d+", true);
    expect(matcher).not.toBeNull();
    expect(matcher!("bls. 12 og 345")).toEqual([
      { index: 5, length: 2 },
      { index: 11, length: 3 },
    ]);
  });

  it("plain-text mode never returns null, even for regex-special characters", () => {
    const matcher = compileMatcher("a(b", false);
    expect(matcher).not.toBeNull();
  });
});
