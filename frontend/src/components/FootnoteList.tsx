import { FOOTNOTE_ID_PREFIX, FOOTNOTE_REF_ID_PREFIX, type Footnote } from "../lib/footnotes";

/** The document's footnotes, rendered once at the end.
 *
 * Rendered here rather than by remark-gfm so that all notes gather in one place
 * regardless of how many lazily-mounted sections the body was split into — see
 * lib/footnotes.ts for why GFM cannot do this across separate parses.
 */
export function FootnoteList({ notes }: { notes: Footnote[] }) {
  if (notes.length === 0) return null;

  return (
    <section className="mt-8 border-t border-border pt-4">
      <h2 className="font-bold mb-2">Neðanmálsgreinar</h2>
      <ol className="space-y-1 text-sm text-ink-soft">
        {notes.map((n) => (
          <li key={n.num} id={`${FOOTNOTE_ID_PREFIX}${n.num}`} className="flex gap-2">
            <a
              href={`#${FOOTNOTE_REF_ID_PREFIX}${n.num}`}
              className="shrink-0 text-accent hover:underline"
              aria-label={`Til baka í texta, neðanmálsgrein ${n.num}`}
            >
              {n.num}.
            </a>
            <span>{n.text}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
