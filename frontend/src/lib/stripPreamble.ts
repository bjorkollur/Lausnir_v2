/** The generated .md opens with a metadata header that the reader already shows
 *  as structured fields (see engine/processors/renderer.py::to_markdown):
 *
 *      ##### <url>
 *      # Dómur Hæstaréttar – 26/2026
 *      ## 15. september 2026
 *
 *      ### Aðilar
 *      … gegn …
 *      ### Lykilorð
 *      …
 *      ### Reifun
 *      …
 *      <body starts here>
 *
 *  Rendering it verbatim printed the court, date, parties, keywords and reifun
 *  a second time, with a bare URL as the first line of the "text". This removes
 *  the header and returns the body.
 *
 *  Anything that does not have the generated shape is returned untouched: the
 *  API also serves documents whose markdown was not built by that renderer.
 */
const META_HEADINGS = new Set(["Aðilar", "Lykilorð", "Reifun"]);

export function stripDocumentPreamble(markdown: string): string {
  if (!markdown) return markdown;
  const lines = markdown.split("\n");
  let i = 0;

  if (lines[i]?.startsWith("##### ")) i++;      // source url
  if (!lines[i]?.startsWith("# ")) return markdown;  // no generated title: leave it alone
  i++;
  if (lines[i]?.startsWith("## ")) i++;         // date

  // Metadata sections, in whatever subset this document has.
  for (;;) {
    let j = i;
    while (j < lines.length && lines[j].trim() === "") j++;
    const heading = /^###\s+(.+?)\s*$/.exec(lines[j] ?? "");
    if (!heading || !META_HEADINGS.has(heading[1])) break;
    j++;
    while (j < lines.length && !/^#{1,6}\s/.test(lines[j])) j++;
    i = j;
  }

  return lines.slice(i).join("\n").replace(/^\n+/, "");
}
