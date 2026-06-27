import { useFacets } from "../hooks/useFacets";
import { FacetNode } from "./FacetNode";
import type { SearchState } from "../lib/searchState";

export function FacetSidebar({ state, onChange }:
  { state: SearchState; onChange: (p: Partial<SearchState>) => void }) {
  const { data, isPending } = useFacets(state);
  const selected = new Set(state.scope);
  const toggle = (key: string) =>
    onChange({ scope: selected.has(key) ? state.scope.filter((k) => k !== key) : [...state.scope, key] });

  return (
    <aside className="w-[300px] shrink-0 border-l border-slate-200 p-4 overflow-y-auto">
      {isPending && <div className="h-40 bg-slate-100 rounded animate-pulse" />}
      {data?.catalog.map((node) => (
        <FacetNode key={node.key} node={node} selected={selected} depth={0} onToggle={toggle} />
      ))}
    </aside>
  );
}
