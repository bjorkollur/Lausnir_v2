import { useState } from "react";
import { Link } from "react-router-dom";
import type { CitationRef } from "../api/types";

/** One direction of a document's citations: „Vitnar í (n)“ or „Vitnað í þennan
 * dóm (n)“. The caller owns the items — it holds the first page from
 * /api/document/:id and appends further pages through `onMore`, which is left
 * out when there is nothing more to fetch. */
export function CitationList({
  title, items, total, onMore,
}: {
  title: string;
  items: CitationRef[];
  total: number;
  onMore?: () => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  const loadMore = async () => {
    if (!onMore || busy) return;
    setBusy(true);
    try {
      await onMore();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-4 last:mb-0">
      <h3 className="text-sm font-semibold text-slate-700 mb-1">
        {title} ({total})
      </h3>
      <ul className="space-y-2">
        {items.map((c) => (
          // A pair can be cited from several passages, but the API collapses
          // each pair to one row — document_id is a stable key here.
          <li key={c.document_id} className="text-sm">
            <Link to={`/domur/${c.document_id}`} className="text-indigo-700 hover:underline">
              {c.urlausn}
            </Link>
            {c.also_appeal && (
              <span className="ml-1 text-xs text-slate-500">(í áfrýjunarkeðju)</span>
            )}
            {c.same_case && <span className="ml-1 text-xs text-slate-500">(sama mál)</span>}
            <div className="text-xs text-slate-500 leading-snug">{c.raw_text}</div>
          </li>
        ))}
      </ul>
      {onMore && items.length < total && (
        <button
          type="button"
          onClick={() => void loadMore()}
          disabled={busy}
          className="mt-2 text-sm text-indigo-700 hover:underline disabled:text-slate-400"
        >
          Sýna fleiri
        </button>
      )}
    </div>
  );
}
