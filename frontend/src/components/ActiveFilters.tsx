import { XIcon } from "@phosphor-icons/react";
import { activeFilterCount, CLEAR_FILTERS, type SearchState } from "../lib/searchState";
import { formatIcelandicDate } from "../lib/formatDate";

/** "1. mars 2020 – 31. desember 2021", "Frá 1. mars 2020", "Til 31. desember 2021". */
function periodLabel(from?: string, to?: string): string {
  const f = formatIcelandicDate(from);
  const t = formatIcelandicDate(to);
  if (f && t) return `${f} – ${t}`;
  return f ? `Frá ${f}` : `Til ${t}`;
}

/** Everything narrowing the current search, above the results, each one
 *  removable where it is read. Only sources used to appear here; a period, a
 *  provision or a keyword could be in force with nothing on screen saying so
 *  except a result count that seemed too small. */
export function ActiveFilters({ state, labelOf, onChange }:
  { state: SearchState; labelOf: (key: string) => string; onChange: (p: Partial<SearchState>) => void }) {
  if (activeFilterCount(state) === 0) return null;

  const chips: { key: string; label: string; remove: Partial<SearchState> }[] = [
    ...state.scope.map((k) => ({
      key: `scope:${k}`,
      label: labelOf(k),
      remove: { scope: state.scope.filter((x) => x !== k) },
    })),
  ];
  if (state.date_from || state.date_to) {
    chips.push({
      key: "period",
      label: periodLabel(state.date_from, state.date_to),
      remove: { date_from: undefined, date_to: undefined },
    });
  }
  if (state.provision) {
    chips.push({ key: "provision", label: state.provision, remove: { provision: undefined } });
  }
  if (state.keyword) {
    chips.push({ key: "keyword", label: `Lykilorð: ${state.keyword}`, remove: { keyword: undefined } });
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="sr-only">Virkar síur:</span>
      <ul className="contents">
        {chips.map((c) => (
          <li
            key={c.key}
            className="inline-flex h-7 max-w-full items-center gap-0.5 rounded-md border border-accent/40 bg-accent-soft pl-2.5 text-meta text-ink"
          >
            <span className="truncate">{c.label}</span>
            <button
              type="button"
              aria-label={`Fjarlægja ${c.label}`}
              onClick={() => onChange(c.remove)}
              className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-ink-soft hover:text-ink"
            >
              <XIcon size={12} aria-hidden />
            </button>
          </li>
        ))}
      </ul>
      {chips.length > 1 && (
        <button
          type="button"
          onClick={() => onChange(CLEAR_FILTERS)}
          className="h-7 px-2 text-meta text-ink-soft underline-offset-2 hover:text-ink hover:underline"
        >
          Hreinsa allt
        </button>
      )}
    </div>
  );
}
