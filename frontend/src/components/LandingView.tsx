import { useState, useRef, type FormEvent } from "react";
import type { CatalogNode } from "../api/types";
import type { Mode, Sort } from "../api/types";
import type { SearchState } from "../lib/searchState";
import { SourceTree } from "./SourceTree";

// ── Mode labels ───────────────────────────────────────────────────────────────

const MODE_LABELS: Record<Mode, string> = {
  keyword: "Orðaleit",
  exact: "Heilt orð",
  prefix: "Byrjar á",
  substring: "Hluti af orði",
  any: "Eitthvað af",
  proximity: "Nálægt",
  regex: "Regex",
};

const ALL_MODES: Mode[] = [
  "keyword",
  "exact",
  "prefix",
  "substring",
  "any",
  "proximity",
  "regex",
];

// Modes that support ts_rank (can use sort=relevance)
const FTS_MODES = new Set<Mode>(["keyword", "proximity"]);

// ── LandingView ───────────────────────────────────────────────────────────────

export function LandingView({
  state,
  catalog,
  total,
  sourceCount,
  patch,
}: {
  state: SearchState;
  catalog: CatalogNode[];
  total: number;
  sourceCount: number;
  patch: (p: Partial<SearchState>) => void;
}) {
  const [localQ, setLocalQ] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (localQ.trim()) {
      patch({ q: localQ.trim() });
    }
  };

  const handleModeChange = (mode: Mode) => {
    const sort: Sort =
      !FTS_MODES.has(mode) && state.sort === "relevance" ? "newest" : state.sort;
    patch({ mode, sort });
  };

  const handleScopeChange = (scope: string[]) => {
    patch({ scope });
  };

  return (
    <div className="min-h-full flex flex-col items-center px-6 pb-16">
      {/* ── Simple search section ───────────────────────────────────────────── */}
      <div className="flex flex-col items-center gap-8 py-16 w-full max-w-2xl">
        {/* Branding */}
        <div className="text-center">
          <h1 className="text-5xl font-bold text-indigo-600 tracking-tight">
            Lausnir
          </h1>
          <p className="text-slate-500 mt-2 text-lg">Íslenskar réttarheimildir</p>
        </div>

        {/* Simple search bar */}
        <form onSubmit={handleSubmit} className="w-full flex gap-2">
          <input
            ref={searchInputRef}
            autoFocus
            value={localQ}
            onChange={(e) => setLocalQ(e.target.value)}
            placeholder={
              state.mode === "regex" ? "regex mynstur…" : "Leita í réttarheimildum…"
            }
            aria-label="Leitarbox"
            className="flex-1 h-14 rounded-full border-2 border-slate-200 px-6 text-lg outline-none focus:border-indigo-500 transition-colors"
          />
          <button
            type="submit"
            disabled={!localQ.trim()}
            className="h-14 px-8 bg-indigo-600 text-white rounded-full font-semibold hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Leita
          </button>
        </form>

        {/* Stats footer */}
        <p className="text-xs text-slate-400">
          {total.toLocaleString("is-IS")} skjöl · {sourceCount} heimildir
        </p>
      </div>

      {/* ── Divider ────────────────────────────────────────────────────────── */}
      <div className="w-full max-w-2xl flex items-center gap-4 mb-10">
        <div className="flex-1 border-t border-slate-200" />
        <span className="text-sm text-slate-400 font-medium">Ýtarleg leit</span>
        <div className="flex-1 border-t border-slate-200" />
      </div>

      {/* ── Advanced search section ─────────────────────────────────────────── */}
      <div className="w-full max-w-2xl space-y-8">
        {/* Mode selection */}
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">
            Leitarstilling
          </h2>
          <div className="flex flex-wrap gap-2">
            {ALL_MODES.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => handleModeChange(m)}
                className={`px-4 py-1.5 rounded-full text-sm font-medium border transition-colors ${
                  state.mode === m
                    ? "bg-indigo-600 text-white border-indigo-600"
                    : "bg-white text-slate-700 border-slate-300 hover:border-indigo-400 hover:text-indigo-600"
                }`}
              >
                {MODE_LABELS[m]}
              </button>
            ))}
          </div>

          {/* Proximity distance picker */}
          {state.mode === "proximity" && (
            <div className="mt-3 flex items-center gap-2 text-sm text-slate-600">
              <span>Innan</span>
              <input
                type="number"
                min={1}
                max={50}
                value={state.proximity_n}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  if (Number.isFinite(n) && n >= 1 && n <= 50) {
                    patch({ proximity_n: n });
                  }
                }}
                className="w-16 border border-slate-300 rounded px-2 py-0.5 text-center"
              />
              <span>orða</span>
            </div>
          )}
        </section>

        {/* Date range */}
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">
            Tímabil
          </h2>
          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <span className="w-8 text-right text-slate-500">Frá</span>
              <input
                type="date"
                value={state.date_from ?? ""}
                onChange={(e) =>
                  patch({ date_from: e.target.value || undefined })
                }
                className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm outline-none focus:border-indigo-500 transition-colors"
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <span className="w-8 text-right text-slate-500">Til</span>
              <input
                type="date"
                value={state.date_to ?? ""}
                onChange={(e) =>
                  patch({ date_to: e.target.value || undefined })
                }
                className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm outline-none focus:border-indigo-500 transition-colors"
              />
            </label>
            {(state.date_from || state.date_to) && (
              <button
                type="button"
                onClick={() => patch({ date_from: undefined, date_to: undefined })}
                className="text-xs text-slate-400 hover:text-slate-600 underline"
              >
                Hreinsa tímabil
              </button>
            )}
          </div>
        </section>

        {/* Source tree */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
              Heimildir
            </h2>
            {state.scope.length > 0 && (
              <span className="text-xs text-indigo-600 font-medium">
                {state.scope.length} valin
              </span>
            )}
          </div>
          <SourceTree
            catalog={catalog}
            scope={state.scope}
            onScopeChange={handleScopeChange}
          />
        </section>

        {/* Advanced search submit button */}
        <form onSubmit={handleSubmit}>
          <button
            type="submit"
            disabled={!localQ.trim()}
            className="w-full h-12 bg-indigo-600 text-white rounded-xl font-semibold hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Leita með ýtarlegri leit
          </button>
          {!localQ.trim() && (
            <p className="text-center text-xs text-slate-400 mt-2">
              Sláðu inn leitarorð í reitinn að ofan
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
