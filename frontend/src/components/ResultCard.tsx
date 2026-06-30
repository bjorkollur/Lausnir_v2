import { useState } from "react";
import { Link } from "react-router-dom";
import type { SearchResult, Party } from "../api/types";
import { markHtml } from "../lib/sanitize";

const partyLine = (ps: Party[]) => ps.map((p) => p.name).join(", ");

export function ResultCard({ r }: { r: SearchResult }) {
  const [open, setOpen] = useState(false);
  const parties = [...r.plaintiffs, ...r.defendants];
  return (
    <article className="py-4 border-b border-[var(--border)]">
      <div className="flex items-baseline gap-2 flex-wrap">
        <Link to={`/domur/${r.id}`} className="text-[var(--accent)] font-semibold text-[17px] hover:underline underline-offset-2">
          {r.urlausn}
        </Link>
        {r.has_appeal_links && <span className="text-xs text-[var(--ink-faint)]" title="Hefur áfrýjunartengingar">⛓ tengt</span>}
      </div>
      <div className="text-sm text-[var(--ink-soft)]">{r.source_display}{r.document_date ? ` · ${r.document_date}` : ""}</div>
      {parties.length > 0 && (() => {
        const full = partyLine(parties);
        return (
          <p className="text-sm text-[var(--ink-soft)] mt-1">
            {open ? full : full.slice(0, 120)}
            {full.length > 120 && (
              <button onClick={() => setOpen(!open)} className="ml-1 text-[var(--accent)] hover:underline underline-offset-2">
                {open ? "Sjá minna" : "Sjá meira"}
              </button>
            )}
          </p>
        );
      })()}
      <p className="text-sm text-[var(--ink)] mt-2 leading-relaxed" dangerouslySetInnerHTML={markHtml(r.snippet)} />
      {r.keywords.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {r.keywords.map((k) => (
            <span key={k} className="text-sm text-[var(--ink-soft)] bg-[var(--canvas)] border border-[var(--border)] rounded-md px-2.5 py-0.5">{k}</span>
          ))}
        </div>
      )}
    </article>
  );
}
