export const LARGE_DOC_THRESHOLD = 50_000;

/** Split markdown into non-overlapping, paragraph-safe segments of ~targetWords each.
 *
 * Unlike the backend's document_chunks (which overlap by design for FTS relevance
 * context), a reader must never show the same text twice, so this carries no overlap.
 */
export function splitMarkdown(text: string, targetWords = 500): string[] {
  if (!text || !text.trim()) return [];

  const words = text.trim().split(/\s+/);
  if (words.length < targetWords) return [text];

  const paragraphs = text.split("\n\n").filter((p) => p.trim() !== "");
  const segments: string[] = [];
  let current: string[] = [];
  let currentWords = 0;

  for (const para of paragraphs) {
    const paraWords = para.trim().split(/\s+/).length;
    current.push(para);
    currentWords += paraWords;
    if (currentWords >= targetWords) {
      segments.push(current.join("\n\n"));
      current = [];
      currentWords = 0;
    }
  }
  if (current.length > 0) {
    segments.push(current.join("\n\n"));
  }
  return segments;
}
