import { compileMatcher } from "./matcher";

const HIGHLIGHT_NAME = "book-search-match";

/** Highlight every occurrence of `query` currently rendered inside `container`,
 * using the native CSS Custom Highlight API — no DOM mutation, so it can't
 * conflict with React's own reconciliation of the markdown it's overlaying.
 *
 * ponytail: no separate "active match" color — scrollIntoView already gets
 * the user to the right spot; add a second highlight name if that's not enough.
 *
 * No-ops silently where the API is unsupported (older Firefox, jsdom in tests).
 */
export function applyHighlights(container: HTMLElement, query: string, useRegex = false): void {
  if (typeof CSS === "undefined" || !("highlights" in CSS) || typeof Highlight === "undefined") {
    return;
  }

  const matcher = compileMatcher(query, useRegex);
  if (!matcher) {
    CSS.highlights.delete(HIGHLIGHT_NAME);
    return;
  }

  const ranges: Range[] = [];
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let node: Node | null;
  while ((node = walker.nextNode())) {
    const text = node.textContent ?? "";
    for (const span of matcher(text)) {
      const range = new Range();
      range.setStart(node, span.index);
      range.setEnd(node, span.index + span.length);
      ranges.push(range);
    }
  }

  CSS.highlights.set(HIGHLIGHT_NAME, new Highlight(...ranges));
}
