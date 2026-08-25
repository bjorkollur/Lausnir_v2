import { compileMatcher } from "./matcher";

export interface Match {
  segmentIndex: number;
  offset: number;
}

/** Find every occurrence of `query` across markdown segments — plain
 * case-insensitive substring by default, or a regex pattern when `useRegex`
 * is true.
 *
 * Segments come from splitMarkdown() — searching the raw segment text (not
 * the rendered DOM) means matches are found even inside not-yet-rendered
 * lazy sections, so the reader can jump to them.
 */
export function findMatches(segments: string[], query: string, useRegex = false): Match[] {
  const matcher = compileMatcher(query, useRegex);
  if (!matcher) return [];

  const matches: Match[] = [];
  segments.forEach((segment, segmentIndex) => {
    for (const span of matcher(segment)) {
      matches.push({ segmentIndex, offset: span.index });
    }
  });
  return matches;
}
