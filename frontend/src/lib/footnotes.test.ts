import { describe, it, expect } from "vitest";
import { extractFootnotes } from "./footnotes";

describe("extractFootnotes", () => {
  it("lifts definitions out of the body", () => {
    const { body, notes } = extractFootnotes("Texti[^1]\n\n[^1]: Skýring.");
    expect(body).not.toContain("[^1]: Skýring.");
    expect(notes).toEqual([{ num: 1, text: "Skýring." }]);
  });

  it("rewrites references as links into the footnote list", () => {
    const { body } = extractFootnotes("barns.[^12] Meira\n\n[^12]: Skýring.");
    expect(body).toContain("barns.[12](#nmg-12)");
    expect(body).not.toContain("[^12]");
  });

  it("returns notes ordered by number regardless of definition order", () => {
    const { notes } = extractFootnotes(
      "a[^2] b[^1]\n\n[^2]: Önnur.\n[^1]: Fyrsta.",
    );
    expect(notes.map((n) => n.num)).toEqual([1, 2]);
  });

  it("keeps a note whose marker was never found in the text", () => {
    // The extractor can miss a superscript; dropping the note would delete its
    // text from the document entirely.
    const { notes } = extractFootnotes("Texti[^1]\n\n[^1]: Notuð.\n[^9]: Ónotuð.");
    expect(notes.map((n) => n.num)).toEqual([1, 9]);
  });

  it("leaves a reference with no definition untouched", () => {
    const { body } = extractFootnotes("Texti[^7] meira\n\n[^1]: Önnur.");
    expect(body).toContain("[^7]");
  });

  it("keeps the first definition when a number is repeated", () => {
    const { notes } = extractFootnotes("a[^1]\n\n[^1]: Fyrsta.\n[^1]: Tvítekin.");
    expect(notes).toEqual([{ num: 1, text: "Fyrsta." }]);
  });

  it("passes footnote-free text through unchanged", () => {
    const text = "Venjulegur texti\n\nönnur málsgrein";
    expect(extractFootnotes(text)).toEqual({ body: text, notes: [] });
  });

  it("handles empty input", () => {
    expect(extractFootnotes("")).toEqual({ body: "", notes: [] });
  });
});
