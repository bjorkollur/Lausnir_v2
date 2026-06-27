import { Link } from "react-router-dom";
import type { DocumentDetail } from "../api/types";

export function DocHeader({ doc }: { doc: DocumentDetail }) {
  return (
    <div className="flex items-center gap-3 border-b border-slate-200 px-6 py-3">
      <Link to="/" aria-label="Til baka" className="text-slate-500">←</Link>
      <div className="font-semibold">{doc.urlausn}</div>
      <span className="text-sm text-slate-500">
        {doc.source_display}{doc.verdict_type ? ` – ${doc.verdict_type}` : ""}
      </span>
      {doc.url && (
        <a
          href={doc.url}
          target="_blank"
          rel="noreferrer"
          className="text-indigo-600 text-sm"
          title="Opna frumrit"
        >
          ↗
        </a>
      )}
    </div>
  );
}
