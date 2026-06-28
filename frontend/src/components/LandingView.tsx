import { useState, type FormEvent } from "react";
import type { CatalogNode } from "../api/types";
import type { SearchState } from "../lib/searchState";

const GROUP_ICONS: Record<string, string> = {
  domstolar: "⚖️",
  stjornsysla: "🏛️",
  nefndir: "📋",
  baekur: "📚",
};

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

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (localQ.trim()) patch({ q: localQ.trim() });
  };

  const toggleGroup = (key: string) => {
    const next = state.scope.includes(key)
      ? state.scope.filter((k) => k !== key)
      : [...state.scope, key];
    patch({ scope: next });
  };

  return (
    <div className="flex flex-col items-center justify-center h-full gap-10 px-6 pb-12">
      {/* Branding */}
      <div className="text-center">
        <h1 className="text-5xl font-bold text-indigo-600 tracking-tight">Lausnir</h1>
        <p className="text-slate-500 mt-2 text-lg">Íslenskar réttarheimildir</p>
      </div>

      {/* Search bar */}
      <form onSubmit={submit} className="w-full max-w-2xl flex gap-2">
        <input
          autoFocus
          value={localQ}
          onChange={(e) => setLocalQ(e.target.value)}
          placeholder="Leita í réttarheimildum…"
          aria-label="Leitarbox"
          className="flex-1 h-14 rounded-full border-2 border-slate-200 px-6 text-lg outline-none focus:border-indigo-500 transition-colors"
        />
        <button
          type="submit"
          disabled={!localQ.trim()}
          className="h-14 px-8 bg-indigo-600 text-white rounded-full font-medium hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Leita
        </button>
      </form>

      {/* Scope cards */}
      {catalog.length > 0 && (
        <div className="w-full max-w-2xl">
          <p className="text-xs text-slate-400 uppercase tracking-wide mb-3">
            Þrengja leit að heimildum (valfrjálst)
          </p>
          <div className="grid grid-cols-2 gap-3">
            {catalog.map((group) => {
              const selected = state.scope.includes(group.key);
              return (
                <button
                  key={group.key}
                  type="button"
                  onClick={() => toggleGroup(group.key)}
                  className={`p-4 rounded-xl border-2 text-left transition-all ${
                    selected
                      ? "border-indigo-500 bg-indigo-50 shadow-sm"
                      : "border-slate-200 hover:border-slate-300 bg-white hover:shadow-sm"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-xl">{GROUP_ICONS[group.key] ?? "📄"}</span>
                    <span className={`font-medium ${selected ? "text-indigo-700" : "text-slate-800"}`}>
                      {group.label}
                    </span>
                    {selected && (
                      <span className="ml-auto text-indigo-500 text-sm">✓</span>
                    )}
                  </div>
                  <div className="text-sm text-slate-400 mt-1 ml-7">
                    {group.count.toLocaleString("is-IS")} skjöl
                  </div>
                </button>
              );
            })}
          </div>
          {state.scope.length > 0 && (
            <button
              type="button"
              onClick={() => patch({ scope: [] })}
              className="mt-2 text-xs text-slate-400 hover:text-slate-600 underline"
            >
              Hreinsa val
            </button>
          )}
        </div>
      )}

      {/* Stats */}
      <p className="text-xs text-slate-400">
        {total.toLocaleString("is-IS")} skjöl · {sourceCount} heimildir
      </p>
    </div>
  );
}
