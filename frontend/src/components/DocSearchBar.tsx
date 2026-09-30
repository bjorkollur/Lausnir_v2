import { CaretDownIcon, CaretUpIcon, MagnifyingGlassIcon } from "@phosphor-icons/react";

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
    <div className="mb-6 flex h-10 items-center gap-1.5 rounded-md border border-border bg-surface pl-3 pr-1.5">
      <MagnifyingGlassIcon size={14} aria-hidden className="shrink-0 text-ink-faint" />
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
        placeholder={disabled ? "Sæki texta til leitar…" : useRegex ? "Regex mynstur…" : "Leita í skjalinu…"}
        aria-label="Leita í skjalinu"
        className="min-w-0 flex-1 bg-transparent text-meta text-ink outline-none placeholder:text-ink-faint disabled:cursor-wait"
      />
      <button
        type="button"
        onClick={() => onUseRegexChange(!useRegex)}
        aria-pressed={useRegex}
        aria-label="Regex leit"
        title="Regex leit"
        className={`h-7 rounded px-1.5 font-mono text-micro ${
          useRegex ? "bg-cta text-cta-ink" : "text-ink-soft hover:bg-surface-sunken"
        }`}
      >
        .*
      </button>
      <span aria-live="polite" className="tabular whitespace-nowrap px-1 text-micro text-ink-soft">{status}</span>
      <button
        type="button"
        onClick={onPrev}
        disabled={matchCount === 0}
        aria-label="Fyrri niðurstaða"
        className="grid h-7 w-7 place-items-center rounded text-ink-soft hover:bg-surface-sunken disabled:text-border-strong disabled:hover:bg-transparent"
      >
        <CaretUpIcon size={13} weight="bold" aria-hidden />
      </button>
      <button
        type="button"
        onClick={onNext}
        disabled={matchCount === 0}
        aria-label="Næsta niðurstaða"
        className="grid h-7 w-7 place-items-center rounded text-ink-soft hover:bg-surface-sunken disabled:text-border-strong disabled:hover:bg-transparent"
      >
        <CaretDownIcon size={13} weight="bold" aria-hidden />
      </button>
    </div>
  );
}
