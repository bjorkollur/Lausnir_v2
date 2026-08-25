export function DocSearchBar({
  query,
  onQueryChange,
  useRegex,
  onUseRegexChange,
  regexValid,
  matchCount,
  activeIndex,
  onNext,
  onPrev,
  disabled = false,
}: {
  query: string;
  onQueryChange: (q: string) => void;
  useRegex: boolean;
  onUseRegexChange: (v: boolean) => void;
  regexValid: boolean;
  matchCount: number;
  activeIndex: number;
  onNext: () => void;
  onPrev: () => void;
  /** True while the searchable text isn't ready yet (PDF text extraction) —
   * blocks typing instead of showing a misleading "no results". */
  disabled?: boolean;
}) {
  const trimmed = query.trim();
  const status = disabled
    ? null
    : !regexValid
      ? "Ógilt regex mynstur"
      : trimmed.length === 0 || (!useRegex && trimmed.length < 2)
        ? null
        : matchCount === 0
          ? "Engar niðurstöður"
          : `${activeIndex + 1} af ${matchCount}`;

  return (
    <div className="mb-6 flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <input
        type="text"
        value={query}
        disabled={disabled}
        onChange={(e) => onQueryChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          e.preventDefault();
          if (e.shiftKey) onPrev();
          else onNext();
        }}
        placeholder={disabled ? "Sæki texta til leitar…" : useRegex ? "Regex mynstur..." : "Leita í skjalinu..."}
        aria-label="Leita í skjalinu"
        className="flex-1 bg-transparent text-sm outline-none placeholder:text-slate-400 disabled:cursor-wait"
      />
      <button
        type="button"
        onClick={() => onUseRegexChange(!useRegex)}
        aria-pressed={useRegex}
        aria-label="Regex leit"
        title="Regex leit"
        className={`rounded px-1.5 py-1 font-mono text-xs ${
          useRegex ? "bg-slate-700 text-white" : "text-slate-500 hover:bg-slate-200"
        }`}
      >
        .*
      </button>
      <span className="whitespace-nowrap text-sm text-slate-500">{status}</span>
      <button
        type="button"
        onClick={onPrev}
        disabled={matchCount === 0}
        aria-label="Fyrri niðurstaða"
        className="rounded px-2 py-1 text-slate-600 hover:bg-slate-200 disabled:opacity-30"
      >
        ↑
      </button>
      <button
        type="button"
        onClick={onNext}
        disabled={matchCount === 0}
        aria-label="Næsta niðurstaða"
        className="rounded px-2 py-1 text-slate-600 hover:bg-slate-200 disabled:opacity-30"
      >
        ↓
      </button>
    </div>
  );
}
