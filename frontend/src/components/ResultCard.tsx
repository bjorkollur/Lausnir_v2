import { useState } from "react";
import { Link } from "react-router-dom";
import { LinkSimpleIcon, QuotesIcon } from "@phosphor-icons/react";
import type { SearchResult, Party } from "../api/types";
import { markHtml } from "../lib/sanitize";

const partyLine = (ps: Party[]) => ps.map((p) => p.name).join(", ");

/** One result. The title is the only coloured thing on the row, the snippet is
 *  the only thing at reading size, and everything else recedes. The old card
 *  printed title, source, ISO date, parties, anchor, match tier and a row of
 *  keyword chips at nearly the same weight, so nothing anchored the eye.
 *
 *  The date is not repeated in the metadata line: `urlausn` already reads
 *  "Hérd. Rvk. E-3837/2012 12. mars 2014 – Dómur", and printing 2014-03-12
 *  underneath it put two date formats on one row. */
export function ResultCard({ r }: { r: SearchResult }) {
  const [open, setOpen] = useState(false);
  const parties = [...r.plaintiffs, ...r.defendants];
  const full = partyLine(parties);
  const long = full.length > 140;

  return (
    <article className="group -mx-3 rounded px-3 py-5 transition-colors hover:bg-surface">
      <h3>
        <Link
          to={`/domur/${r.id}`}
          className="text-heading font-medium text-accent decoration-1 underline-offset-[3px] hover:underline"
        >
          {r.urlausn}
        </Link>
      </h3>

      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-meta text-ink-faint">
        <span>{r.source_display}</span>
        {r.has_appeal_links && (
          <span className="inline-flex items-center gap-1" title="Hefur áfrýjunartengingar">
            <LinkSimpleIcon size={13} aria-hidden />
            tengt
          </span>
        )}
        {r.cited_by_count > 0 && (
          <span className="inline-flex items-center gap-1">
            <QuotesIcon size={13} aria-hidden />
            <span className="tabular" aria-hidden>{r.cited_by_count}</span>
            {/* The count reads as a glyph plus a number so the row stays
                scannable; the sentence stays for screen readers, with the
                Icelandic singular intact. */}
            <span className="sr-only">
              {r.cited_by_count === 1
                ? "vitnað í 1 sinni"
                : `vitnað í ${r.cited_by_count} sinnum`}
            </span>
          </span>
        )}
      </div>

      {parties.length > 0 && (
        <p className="mt-1.5 text-meta text-ink-soft">
          {open || !long ? full : `${full.slice(0, 140)}…`}
          {long && (
            <button
              onClick={() => setOpen(!open)}
              className="ml-1.5 text-accent hover:underline underline-offset-2"
            >
              {open ? "minna" : "meira"}
            </button>
          )}
        </p>
      )}

      <div className="mt-3 flex items-baseline gap-3">
        {r.anchor && (
          <span
            data-testid="passage-anchor"
            className="shrink-0 text-micro font-medium uppercase tracking-wide text-ink-faint"
            title={r.section_kind ?? undefined}
          >
            {r.anchor}
          </span>
        )}
        <p
          className="text-body text-ink"
          dangerouslySetInnerHTML={markHtml(r.snippet)}
        />
      </div>

      {(r.match_tier === 1 || r.match_tier === 2) && (
        <p data-testid="match-tier" className="mt-2 text-micro text-ink-faint">
          {r.match_tier === 1 ? "inniheldur flest orðin" : "inniheldur sum orðin"}
        </p>
      )}
    </article>
  );
}
