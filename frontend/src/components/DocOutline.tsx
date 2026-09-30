import { useMemo } from "react";

export interface OutlineEntry {
  /** Heading text as it appears in the document. */
  text: string;
  /** 1 for `#`, 2 for `##` and so on. Only 1-3 are shown. */
  level: number;
  /** Which lazily-rendered segment holds it, so the panel can reveal it. */
  segmentIndex: number;
}

const HEADING = /^(#{1,3})\s+(.+?)\s*$/gm;

/** Headings, in document order, read out of the markdown rather than the DOM.
 *  A long document renders in lazily-mounted segments, so most of its headings
 *  are not in the DOM yet when the outline is drawn. */
export function outlineFrom(segments: string[]): OutlineEntry[] {
  const out: OutlineEntry[] = [];
  segments.forEach((segment, segmentIndex) => {
    for (const m of segment.matchAll(HEADING)) {
      const text = m[2].replace(/[*_`]/g, "").trim();
      if (text) out.push({ text, level: m[1].length, segmentIndex });
    }
  });
  return out;
}

export function DocOutline({
  segments,
  activeText,
  onJump,
}: {
  segments: string[];
  activeText: string | null;
  onJump: (entry: OutlineEntry) => void;
}) {
  const entries = useMemo(() => outlineFrom(segments), [segments]);

  // Two headings do not make an outline; they make a list of two things. The
  // column still has to exist though: returning null would drop a grid child
  // and slide the reading column into the outline's 12rem track.
  // No aria-hidden: DocPanel's tests use [aria-hidden="true"] to count lazy
  // section placeholders, and an empty column is not one.
  if (entries.length < 3) return <div className="hidden xl:block" />;

  return (
    <nav aria-label="Efnisyfirlit" className="hidden xl:block">
      <div className="sticky top-24 max-h-[calc(100dvh-8rem)] overflow-y-auto pr-2">
        <h2 className="mb-3 text-micro font-medium uppercase tracking-[0.1em] text-ink-faint">
          Efnisyfirlit
        </h2>
        <ul className="space-y-0.5 border-l border-border">
          {entries.map((e, i) => (
            <li key={`${e.segmentIndex}-${i}-${e.text}`}>
              <button
                type="button"
                onClick={() => onJump(e)}
                style={{ paddingLeft: `${(Math.min(e.level, 3) - 1) * 10 + 12}px` }}
                className={`-ml-px block w-full border-l py-1 pr-2 text-left text-meta transition-colors ${
                  activeText === e.text
                    ? "border-accent text-ink"
                    : "border-transparent text-ink-soft hover:border-border-strong hover:text-ink"
                }`}
              >
                {e.text}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
