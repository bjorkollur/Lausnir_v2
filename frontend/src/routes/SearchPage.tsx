import { useSearchParams } from "react-router-dom";
import { useMemo } from "react";
import { parseSearchState, toSearchParams, type SearchState } from "../lib/searchState";
import { useSources } from "../hooks/useSources";
import { SearchBar } from "../components/SearchBar";
import { RegexToggle } from "../components/RegexToggle";
import { Toolbar } from "../components/Toolbar";
import { ScopeChips } from "../components/ScopeChips";
import { ResultsList } from "../components/ResultsList";
import { FacetSidebar } from "../components/FacetSidebar";
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

  const patch = (p: Partial<SearchState>) => setSp(toSearchParams({ ...state, ...p }));

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-slate-200 px-6 py-3 space-y-2">
        <div className="flex items-center gap-4">
          <SearchBar state={state} onChange={patch} />
          <RegexToggle state={state} onChange={patch} />
          <Toolbar state={state} regexFields={regexFields} onChange={patch} />
        </div>
        <ScopeChips state={state} labelOf={labelOf} onChange={patch} />
      </header>
      <div className="flex flex-1 min-h-0">
        <main className="flex-1 min-w-0 overflow-y-auto px-6">
          <ResultsList state={state} />
        </main>
        <FacetSidebar state={state} onChange={patch} />
      </div>
    </div>
  );
}
