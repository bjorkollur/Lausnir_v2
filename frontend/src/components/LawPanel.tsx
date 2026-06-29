import { Link } from "react-router-dom";
import type { LawDetail, Provision } from "../api/types";

function grLabel(p: Provision): string {
  let label = `${p.num}. gr.`;
  if (p.suffix) label += ` ${p.suffix}.`;
  return label;
}

function ProvisionBlock({ p }: { p: Provision }) {
  return (
    <div className="py-4 border-b border-slate-100 last:border-0">
      <div className="font-bold text-slate-900 mb-2 text-sm">
        ■ {grLabel(p)}
      </div>
      {p.sub && p.sub.length > 0 ? (
        <div className="space-y-2">
          {p.sub.map((s) => (
            <p key={s.num} className="text-slate-800 leading-relaxed ml-4 text-sm">
              □ {s.text}
            </p>
          ))}
        </div>
      ) : (
        <p className="text-slate-800 leading-relaxed ml-4 text-sm">□ {p.text}</p>
      )}
    </div>
  );
}

export function LawPanel({ law }: { law: LawDetail }) {
  return (
    <div className="bg-[#f5f7fb] flex-1 overflow-y-auto py-8">
      <article className="mx-auto max-w-2xl bg-white rounded-lg p-8 shadow-sm">
        {/* Haus */}
        <header className="text-center space-y-1 mb-8 pb-6 border-b border-slate-200">
          {law.document_date && law.case_number && (
            <div className="text-sm text-slate-500">
              {law.document_date} · nr. {law.case_number}
            </div>
          )}
          <h1 className="text-2xl font-bold text-slate-900">{law.law_name}</h1>
          {law.url && (
            <a
              href={law.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-indigo-600 hover:underline block"
            >
              Ferill málsins á Alþingi
            </a>
          )}
          {law.document_date && (
            <div className="text-sm text-slate-500">
              Tók gildi {law.document_date}
            </div>
          )}
          <div className="text-xs text-slate-400 pt-1">
            <Link to={`/lagasafn/${law.kafli}`} className="hover:underline">
              {law.kafli_label}
            </Link>
          </div>
        </header>

        {/* Greinar */}
        {law.provisions.length > 0 ? (
          <div>
            {law.provisions.map((p) => (
              <ProvisionBlock key={`${p.num}-${p.suffix ?? ""}`} p={p} />
            ))}
          </div>
        ) : (
          <p className="text-slate-500 italic text-center py-8 text-sm">
            Engar greinar fundust í þessum lögum.
          </p>
        )}
      </article>
    </div>
  );
}
