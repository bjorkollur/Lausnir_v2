import { useMemo, useRef } from "react";
import { CaretDownIcon } from "@phosphor-icons/react";

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

function OutlineList({ entries, activeText, onJump }:
  { entries: OutlineEntry[]; activeText: string | null; onJump: (entry: OutlineEntry) => void }) {
  return (
    <ul className="space-y-0.5 border-l border-border">
      {entries.map((e, i) => (
        <li key={`${e.segmentIndex}-${i}-${e.text}`}>
          <button
            type="button"
            onClick={() => onJump(e)}
            aria-current={activeText === e.text ? "location" : undefined}
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
  );
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
        <OutlineList entries={entries} activeText={activeText} onJump={onJump} />
      </div>
    </nav>
  );
}

/** Below xl there is no outline column, and a long judgment on a phone had no
 *  way to reach "Niðurstaða" but to scroll for it. The same outline, folded. */
export function DocOutlineCompact({
  segments,
  activeText,
  onJump,
}: {
  segments: string[];
  activeText: string | null;
  onJump: (entry: OutlineEntry) => void;
}) {
  const entries = useMemo(() => outlineFrom(segments), [segments]);
  const details = useRef<HTMLDetailsElement>(null);
  if (entries.length < 3) return null;

  return (
    <details ref={details} className="group mt-6 rounded-md border border-border bg-surface xl:hidden">
      <summary className="flex h-11 cursor-pointer list-none items-center justify-between px-4 text-meta text-ink [&::-webkit-details-marker]:hidden">
        <span>
          Efnisyfirlit <span className="tabular text-ink-faint">({entries.length})</span>
        </span>
        <CaretDownIcon size={12} weight="bold" aria-hidden className="text-ink-faint transition-transform group-open:rotate-180" />
      </summary>
      <nav aria-label="Efnisyfirlit" className="max-h-[60vh] overflow-y-auto px-4 pb-4">
        <OutlineList
          entries={entries}
          activeText={activeText}
          onJump={(e) => {
            if (details.current) details.current.open = false;
            onJump(e);
          }}
        />
      </nav>
    </details>
  );
}
