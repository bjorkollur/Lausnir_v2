import type { SearchState } from "../lib/searchState";

export function ScopeChips({ state, labelOf, onChange }:
  { state: SearchState; labelOf: (key: string) => string; onChange: (p: Partial<SearchState>) => void }) {
  if (state.scope.length === 0) return null;
  const remove = (k: string) => onChange({ scope: state.scope.filter((x) => x !== k) });
  return (
    <div className="flex flex-wrap gap-2">
      {state.scope.map((k) => (
        <span key={k} className="inline-flex items-center gap-1 rounded-full bg-indigo-600 text-white text-sm font-medium px-3 py-1">
          {labelOf(k)}
          <button aria-label={`fjarlægja ${labelOf(k)}`} onClick={() => remove(k)} className="ml-1">✕</button>
        </span>
      ))}
      <button onClick={() => onChange({ scope: [] })} className="text-sm text-slate-500 rounded-full px-3 py-1 bg-slate-200">
        Hreinsa {state.scope.length}
      </button>
    </div>
  );
}
