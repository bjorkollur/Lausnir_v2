import { useState } from "react";
import { Link } from "react-router-dom";
import type { SearchResult, Party } from "../api/types";
import { markHtml } from "../lib/sanitize";

const partyLine = (ps: Party[]) => ps.map((p) => p.name).join(", ");

export function ResultCard({ r }: { r: SearchResult }) {
  const [open, setOpen] = useState(false);
  const parties = [...r.plaintiffs, ...r.defendants];
  return (
    <article className="py-4 border-b border-slate-100">
      <div className="flex items-baseline gap-2 flex-wrap">
        <Link to={`/domur/${r.id}`} className="text-indigo-700 font-semibold text-[17px] hover:underline">
          {r.urlausn}
        </Link>
        {r.has_appeal_links && <span className="text-xs text-slate-500" title="Hefur áfrýjunartengingar">⛓ tengt</span>}
      </div>
      <div className="text-sm text-slate-500">{r.source_display}{r.document_date ? ` · ${r.document_date}` : ""}</div>
      {parties.length > 0 && (
        <p className="text-sm text-slate-700 mt-1">
          {open ? partyLine(parties) : partyLine(parties).slice(0, 120)}
          {partyLine(parties).length > 120 && (
            <button onClick={() => setOpen(!open)} className="ml-1 text-indigo-600">
              {open ? "Sjá minna" : "Sjá meira"}
            </button>
          )}
        </p>
      )}
      <p className="text-sm text-slate-800 mt-2 leading-relaxed" dangerouslySetInnerHTML={markHtml(r.snippet)} />
      {r.keywords.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {r.keywords.map((k) => (
            <span key={k} className="text-sm text-slate-600 bg-slate-100 border border-slate-200 rounded-full px-3 py-0.5">{k}</span>
          ))}
        </div>
      )}
    </article>
  );
}
