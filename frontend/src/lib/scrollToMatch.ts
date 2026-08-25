import { compileMatcher } from "./matcher";

/** Scroll the Nth (0-indexed) occurrence of `query` within `textContainer` to
 * the middle of `scrollContainer`.
 *
 * Deliberately scoped to one already-rendered segment's container rather than
 * the segment's whole bounding box (segment text often runs many pages
 * without a paragraph break, e.g. scanned-book front matter, so centering on
 * the segment itself can land far from the actual match). Scrolls
 * `scrollContainer` (the reader's own `overflow-y-auto` panel), not the
 * window — the page itself doesn't scroll in this layout.
 */
export function scrollToOccurrence(
  textContainer: HTMLElement,
  query: string,
  occurrenceIndex: number,
  scrollContainer: HTMLElement,
  useRegex = false,
): void {
  const matcher = compileMatcher(query, useRegex);
  if (!matcher) return;

  let count = 0;
  const walker = document.createTreeWalker(textContainer, NodeFilter.SHOW_TEXT);
  let node: Node | null;
  while ((node = walker.nextNode())) {
    const text = node.textContent ?? "";
    for (const span of matcher(text)) {
      if (count === occurrenceIndex) {
        const range = new Range();
        range.setStart(node, span.index);
        range.setEnd(node, span.index + span.length);
        // jsdom (tests) doesn't implement Range/Element layout geometry — no-op there.
        try {
          const rect = range.getBoundingClientRect();
          const containerRect = scrollContainer.getBoundingClientRect();
          const offset = rect.top - containerRect.top - scrollContainer.clientHeight / 2;
          scrollContainer.scrollBy({ top: offset, behavior: "smooth" });
        } catch {
          /* not implemented in this environment */
        }
        return;
      }
      count++;
    }
  }
}
