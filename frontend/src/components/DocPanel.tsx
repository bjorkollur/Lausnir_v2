import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import type { DocumentDetail, Party } from "../api/types";

const partyNames = (ps: Party[]) => ps.map((p) => p.name).join(", ");

export function DocPanel({ doc }: { doc: DocumentDetail }) {
  return (
    <div className="bg-[#f5f7fb] flex-1 overflow-y-auto py-8">
      <article className="mx-auto max-w-2xl bg-white rounded-lg p-8 shadow-sm">
        <header className="text-center space-y-1 mb-6">
          <h1 className="text-2xl font-bold uppercase">{doc.source_display}</h1>
          {doc.case_number && (
            <div className="font-semibold">Mál nr. {doc.case_number}</div>
          )}
          {doc.document_date && (
            <div className="text-slate-600">{doc.document_date}</div>
          )}
          {doc.plaintiffs.length > 0 && (
            <div className="font-semibold pt-2">{partyNames(doc.plaintiffs)}</div>
          )}
          {doc.defendants.length > 0 && (
            <>
              <div className="text-slate-600">gegn</div>
              <div className="font-semibold">{partyNames(doc.defendants)}</div>
            </>
          )}
        </header>

        {doc.keywords.length > 0 && (
          <section className="mb-6">
            <h2 className="font-bold mb-2">Lykilorð</h2>
            <div className="flex flex-wrap gap-1.5">
              {doc.keywords.map((k) => (
                <span
                  key={k}
                  className="text-sm text-slate-600 bg-slate-100 rounded-full px-3 py-0.5"
                >
                  {k}
                </span>
              ))}
            </div>
          </section>
        )}

        {doc.summary && (
          <section className="mb-6">
            <h2 className="font-bold mb-2">Reifun</h2>
            <p className="italic text-slate-800 leading-relaxed">{doc.summary}</p>
          </section>
        )}

        <section className="prose prose-slate max-w-none prose-headings:font-bold prose-headings:text-base">
          <ReactMarkdown>{doc.markdown ?? doc.body_text ?? ""}</ReactMarkdown>
        </section>

        {doc.appeal_links.length > 0 && (
          <section className="mt-8 border-t border-slate-200 pt-4">
            <h2 className="font-bold mb-2">Tengd mál</h2>
            <ul className="space-y-1">
              {doc.appeal_links.map((l) => (
                <li key={l.document_id} className="text-sm">
                  <span className="text-slate-500">
                    {l.relation === "appealed_to" ? "Áfrýjað frá: " : "Áfrýjað til: "}
                  </span>
                  <Link
                    to={`/domur/${l.document_id}`}
                    className="text-indigo-700 hover:underline"
                  >
                    {l.urlausn}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}
      </article>
    </div>
  );
}
