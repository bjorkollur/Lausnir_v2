export interface Footnote {
  num: number;
  text: string;
}

/** Anchor id prefix for a footnote's entry in the list at the end of the document. */
export const FOOTNOTE_ID_PREFIX = "nmg-";
/** Anchor id prefix for the reference in the running text (target of the back-link). */
export const FOOTNOTE_REF_ID_PREFIX = "nmgv-";

const DEFINITION = /^\[\^(\d+)\]:\s*(.*)$/;
const REFERENCE = /\[\^(\d+)\](?!:)/g;

/** Split GFM footnote markup into body text and a list of notes.
 *
 * The notes are rendered once, at the end of the document, rather than by
 * remark-gfm. Two reasons:
 *
 *   - A long document is rendered as several independently-parsed lazy sections,
 *     and GFM can only resolve a reference against a definition in the *same*
 *     parse. Left to it, a reference whose definition lives further down renders
 *     as literal `[^12]` text.
 *   - GFM silently drops a definition nothing references, which would delete the
 *     note's text outright.
 *
 * References are rewritten to ordinary markdown links pointing at the list, so
 * they keep working across section boundaries — plain anchors resolve anywhere
 * on the page, no matter which parse produced them.
 */
export function extractFootnotes(text: string): { body: string; notes: Footnote[] } {
  if (!text) return { body: "", notes: [] };

  const found = new Map<number, string>();
  const kept: string[] = [];
  for (const line of text.split("\n")) {
    const m = line.match(DEFINITION);
    if (m) {
      const num = Number(m[1]);
      if (!found.has(num)) found.set(num, m[2].trim());
    } else {
      kept.push(line);
    }
  }

  if (found.size === 0) return { body: text, notes: [] };

  // A reference with no definition is left as-is rather than turned into a link
  // that would lead nowhere.
  const body = kept
    .join("\n")
    .replace(REFERENCE, (whole, n: string) =>
      found.has(Number(n))
        ? `[${n}](#${FOOTNOTE_ID_PREFIX}${n})`
        : whole,
    );

  const notes = [...found.entries()]
    .sort(([a], [b]) => a - b)
    .map(([num, noteText]) => ({ num, text: noteText }));

  return { body, notes };
}
