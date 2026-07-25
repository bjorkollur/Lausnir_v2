import type { SearchState } from "../lib/searchState";

export function ScopeChips({ state, labelOf, onChange }:
  { state: SearchState; labelOf: (key: string) => string; onChange: (p: Partial<SearchState>) => void }) {
  if (state.scope.length === 0) return null;
  const remove = (k: string) => onChange({ scope: state.scope.filter((x) => x !== k) });
  return (
    <div className="flex flex-wrap gap-2">
      {state.scope.map((k) => (
        <span key={k} className="inline-flex items-center gap-1.5 rounded-md border border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--ink)] text-sm font-medium px-3 py-1">
          {labelOf(k)}
          <button
            aria-label={`fjarlægja ${labelOf(k)}`}
            onClick={() => remove(k)}
            className="text-[var(--ink-soft)] hover:text-[var(--ink)] text-xs leading-none transition-colors"
          >
            ✕
          </button>
        </span>
      ))}
      <button
        onClick={() => onChange({ scope: [] })}
        className="text-sm text-[var(--ink-soft)] border border-[var(--border)] rounded-md px-3 py-1 hover:border-[var(--border-strong)] hover:text-[var(--ink)] transition-colors"
      >
        Hreinsa {state.scope.length}
      </button>
    </div>
  );
}
