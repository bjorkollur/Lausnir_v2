import { useSearchParams } from "react-router-dom";
import { FunnelSimpleIcon } from "@phosphor-icons/react";
import { useMemo, useRef, useState } from "react";
import {
  activeFilterCount, hasSearchCriteria, parseSearchState, toSearchParams, type SearchState,
} from "../lib/searchState";
import { useSources } from "../hooks/useSources";
import { SearchBar } from "../components/SearchBar";
import { ModeDropdown } from "../components/ModeDropdown";
import { FieldsPicker, SortSelect, CONTROL } from "../components/Toolbar";
import { ActiveFilters } from "../components/ActiveFilters";
import { ResultsList } from "../components/ResultsList";
import { FilterPanel } from "../components/FilterPanel";
import { LandingView } from "../components/LandingView";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useScrollMemory } from "../lib/useScrollMemory";
import type { CatalogNode, Mode } from "../api/types";

const REGEX_BACKED_MODES = new Set<Mode>(["exact", "prefix", "substring", "any", "regex"]);

function flattenLabels(nodes: CatalogNode[], out: Record<string, string> = {}): Record<string, string> {
  for (const n of nodes) {
    out[n.key] = n.label;
    if (n.children) flattenLabels(n.children, out);
  }
  return out;
}

export default function SearchPage() {
  const [sp, setSp] = useSearchParams();
  const state = parseSearchState(sp);
  const sources = useSources();
  const labels = useMemo(() => flattenLabels(sources.data?.catalog ?? []), [sources.data]);
  const labelOf = (k: string) => labels[k] ?? k;
  const regexFields = sources.data?.regex_fields ?? ["body_text"];

  const [filtersOpen, setFiltersOpen] = useState(false);
  const results = useRef<HTMLElement>(null);
  useScrollMemory(results, "results");

  const searching = hasSearchCriteria(state);
  useDocumentTitle(
    !searching ? null : state.q ? `„${state.q}“ – Leit` : "Leit",
  );

  const patch = (p: Partial<SearchState>) => setSp(toSearchParams({ ...state, ...p }));

  // A search needs a query or something narrowing the corpus; without either
  // this is the front page.
  if (!searching) {
    return (
      <LandingView
        state={state}
        catalog={sources.data?.catalog ?? []}
        total={sources.data?.total ?? 0}
        sourceCount={sources.data?.sources.length ?? 0}
        patch={patch}
      />
    );
  }

  const filterCount = activeFilterCount(state);

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border">
        <div className="mx-auto max-w-[1400px] space-y-2.5 px-4 py-3 sm:px-6">
          {/* Wraps rather than overflowing: at 390px the query takes the first
              row and mode, sort and filters share the second. */}
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <SearchBar state={state} onChange={patch} />
            <div className="flex min-w-0 flex-1 basis-full items-center gap-2 sm:basis-auto">
              <ModeDropdown state={state} onChange={patch} />
              {REGEX_BACKED_MODES.has(state.mode) && (
                <FieldsPicker state={state} regexFields={regexFields} onChange={patch} />
              )}
              <SortSelect state={state} onChange={patch} className="min-w-0" />
              <button
                type="button"
                onClick={() => setFiltersOpen(true)}
                aria-haspopup="dialog"
                className={`${CONTROL} ml-auto inline-flex shrink-0 items-center gap-1.5 lg:hidden`}
              >
                <FunnelSimpleIcon size={15} aria-hidden />
                Síur
                {filterCount > 0 && (
                  <span className="tabular grid h-5 min-w-5 place-items-center rounded-full bg-cta px-1.5 text-micro text-cta-ink">
                    {filterCount}
                  </span>
                )}
              </button>
            </div>
          </div>
          <ActiveFilters state={state} labelOf={labelOf} onChange={patch} />
        </div>
      </header>
      <div className="mx-auto flex min-h-0 w-full max-w-[1400px] flex-1 lg:px-6">
        <FilterPanel
          state={state}
          onChange={patch}
          open={filtersOpen}
          onClose={() => setFiltersOpen(false)}
        />
        <main
          ref={results}
          aria-label="Niðurstöður"
          className="min-w-0 flex-1 overflow-y-auto px-4 sm:px-6 lg:border-l lg:border-border lg:pl-8 lg:pr-0"
        >
          <h1 className="sr-only">Leitarniðurstöður</h1>
          <ResultsList state={state} onChange={patch} />
        </main>
      </div>
    </div>
  );
}
