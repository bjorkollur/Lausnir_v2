import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FOOTNOTE_ID_PREFIX, FOOTNOTE_REF_ID_PREFIX } from "../lib/footnotes";

/** Every markdown render in the app goes through here.
 *
 * The corpus needs GFM for two things plain CommonMark cannot express:
 *   - tables — `pdf_parser._table_to_markdown()` emits them, and ~2,500 documents
 *     contain one; without GFM they render as raw `|---|` pipe text
 *   - footnotes — `[^1]` references and definitions extracted from PDFs
 *
 * Kept as a single wrapper so a new call site cannot silently drop the plugins
 * and render a different dialect than the rest of the app.
 */
export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      // Defaults here are English ("Footnotes", "Back to content") and are
      // user-visible, so they have to be localised with the rest of the UI.
      remarkRehypeOptions={{
        footnoteLabel: "Neðanmálsgreinar",
        footnoteBackLabel: "Til baka í texta",
      }}
      components={{
        // Give each footnote reference an id so the note at the end of the
        // document has somewhere to link back to.
        a({ href, children, ...props }) {
          const marker = `#${FOOTNOTE_ID_PREFIX}`;
          if (href?.startsWith(marker)) {
            return (
              <a href={href} id={`${FOOTNOTE_REF_ID_PREFIX}${href.slice(marker.length)}`} {...props}>
                {children}
              </a>
            );
          }
          return <a href={href} {...props}>{children}</a>;
        },
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
