import { useSearchParams } from "react-router-dom";
import { FunnelIcon } from "@phosphor-icons/react";
import { useMemo, useState } from "react";
import { parseSearchState, toSearchParams, type SearchState } from "../lib/searchState";
import { useSources } from "../hooks/useSources";
import { SearchBar } from "../components/SearchBar";
import { ModeDropdown } from "../components/ModeDropdown";
import { Toolbar } from "../components/Toolbar";
import { ScopeChips } from "../components/ScopeChips";
import { ResultsList } from "../components/ResultsList";
import { FacetSidebar } from "../components/FacetSidebar";
import { LandingView } from "../components/LandingView";
import type { CatalogNode } from "../api/types";

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

  const [facetsOpen, setFacetsOpen] = useState(false);

  const patch = (p: Partial<SearchState>) => setSp(toSearchParams({ ...state, ...p }));

  // Show landing page when no active query or filter
  if (!state.q && !state.provision && !state.keyword && state.scope.length === 0) {
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

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border">
        <div className="mx-auto max-w-[1400px] space-y-2 px-4 py-3 sm:px-6">
          {/* Wraps rather than overflowing: at 390px the row used to push the
              mode and date controls off the right edge and squeeze the query
              field to a 40px stub. */}
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <SearchBar state={state} onChange={patch} />
            <ModeDropdown state={state} onChange={patch} />
            <Toolbar state={state} regexFields={regexFields} onChange={patch} />
            <button
              type="button"
              onClick={() => setFacetsOpen(true)}
              className="inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-meta text-ink-soft hover:border-border-strong hover:text-ink lg:hidden"
            >
              <FunnelIcon size={14} aria-hidden />
              Heimildir
            </button>
          </div>
          <ScopeChips state={state} labelOf={labelOf} onChange={patch} />
        </div>
      </header>
      <div className="mx-auto flex min-h-0 w-full max-w-[1400px] flex-1">
        <main className="min-w-0 flex-1 overflow-y-auto px-4 sm:px-6">
          <ResultsList state={state} />
        </main>
        <FacetSidebar
          state={state}
          onChange={patch}
          open={facetsOpen}
          onClose={() => setFacetsOpen(false)}
        />
      </div>
    </div>
  );
}
