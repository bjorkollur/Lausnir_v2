/** A law's title as the lagasafn index prints it, without the amendment marks
 *  the full text carries: "[Lög um tekjuskatt]1)" → "Lög um tekjuskatt".
 *
 *  Square brackets enclose text an amending act changed and "1)" points at the
 *  footnote naming that act. In the body both are information; in a title in a
 *  list or a heading they are noise, and the footnote they point at is not on
 *  the page. Only a number-and-parenthesis glued to the preceding word or
 *  bracket is taken, so "(EES-reglur)" and similar survive. */
export function cleanLawTitle(title: string | null | undefined): string {
  if (!title) return "";
  return title
    .replace(/(?<=[^\s\d(])\d+\)/g, "")
    .replace(/[[\]]/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}
