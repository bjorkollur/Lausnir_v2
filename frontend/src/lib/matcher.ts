export interface MatchSpan {
  index: number;
  length: number;
}

export type Matcher = (text: string) => MatchSpan[];

/** Build a matcher for either plain case-insensitive substring search or a
 * regex pattern — shared by findMatches/highlightMatches/scrollToMatch so
 * "does this text contain the query" is defined in exactly one place.
 *
 * Returns null when `useRegex` is true and the pattern fails to compile;
 * callers should treat that as "no matches, tell the user the pattern is
 * invalid" rather than throw.
 */
export function compileMatcher(query: string, useRegex: boolean): Matcher | null {
  if (useRegex) {
    const pattern = query.trim();
    if (pattern.length === 0) return () => [];
    let re: RegExp;
    try {
      re = new RegExp(pattern, "gi");
    } catch {
      return null;
    }
    return (text: string) => {
      const spans: MatchSpan[] = [];
      re.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = re.exec(text))) {
        if (m[0].length === 0) {
          re.lastIndex++; // avoid an infinite loop on zero-width matches
          continue;
        }
        spans.push({ index: m.index, length: m[0].length });
      }
      return spans;
    };
  }

  const q = query.trim().toLowerCase();
  if (q.length < 2) return () => [];
  return (text: string) => {
    const spans: MatchSpan[] = [];
    const lower = text.toLowerCase();
    let from = 0;
    for (;;) {
      const idx = lower.indexOf(q, from);
      if (idx === -1) break;
      spans.push({ index: idx, length: q.length });
      from = idx + q.length;
    }
    return spans;
  };
}

/** Whether `query` compiles as a regex — used to show a distinct "invalid
 * pattern" message instead of "no results". */
export function isValidRegex(query: string): boolean {
  try {
    new RegExp(query);
    return true;
  } catch {
    return false;
  }
}
