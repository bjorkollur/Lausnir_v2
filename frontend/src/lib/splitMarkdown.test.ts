import { describe, it, expect } from "vitest";
import { splitMarkdown } from "./splitMarkdown";

function words(n: number, wordsPerParagraph = 50): string {
  const paragraphs: string[] = [];
  for (let i = 0; i < n; i += wordsPerParagraph) {
    const count = Math.min(wordsPerParagraph, n - i);
    const para = Array.from({ length: count }, (_, j) => `word${i + j}`).join(" ");
    paragraphs.push(para);
  }
  return paragraphs.join("\n\n");
}

describe("splitMarkdown", () => {
  it("returns empty array for empty text", () => {
    expect(splitMarkdown("")).toEqual([]);
  });

  it("returns empty array for whitespace-only text", () => {
    expect(splitMarkdown("   \n\n  ")).toEqual([]);
  });

  it("returns the whole text as one segment when under the target word count", () => {
    const text = words(80);
    expect(splitMarkdown(text)).toEqual([text]);
  });

  it("splits long text into multiple segments", () => {
    const text = words(2000);
    const segments = splitMarkdown(text);
    expect(segments.length).toBeGreaterThan(1);
  });

  it("keeps every word from the source across the produced segments", () => {
    const text = words(1500);
    const segments = splitMarkdown(text);
    const sourceWords = new Set(text.split(/\s+/));
    const segmentWords = new Set(segments.join(" ").split(/\s+/));
    for (const w of sourceWords) {
      expect(segmentWords.has(w)).toBe(true);
    }
  });

  it("produces no empty segments", () => {
    const text = words(1500);
    for (const s of splitMarkdown(text)) {
      expect(s.trim()).not.toBe("");
    }
  });

  it("respects a custom targetWords value", () => {
    const text = words(300, 30);
    const segments = splitMarkdown(text, 100);
    expect(segments.length).toBeGreaterThan(1);
  });

  it("keeps a single huge paragraph (no blank-line breaks) as one segment", () => {
    const text = Array.from({ length: 1000 }, (_, i) => `word${i}`).join(" ");
    expect(splitMarkdown(text)).toEqual([text]);
  });
});
