import { useFacets } from "../hooks/useFacets";
import { FacetNode } from "./FacetNode";
import type { CatalogNode } from "../api/types";
import type { SearchState } from "../lib/searchState";

function ancestorKeys(nodes: CatalogNode[], target: string, path: string[] = []): string[] | null {
  for (const n of nodes) {
    if (n.key === target) return path;
    const found = ancestorKeys(n.children ?? [], target, [...path, n.key]);
    if (found) return found;
  }
  return null;
}

function descendantKeys(node: CatalogNode): string[] {
  return (node.children ?? []).flatMap((c) => [c.key, ...descendantKeys(c)]);
}

function findNode(nodes: CatalogNode[], key: string): CatalogNode | null {
  for (const n of nodes) {
    if (n.key === key) return n;
    const found = findNode(n.children ?? [], key);
    if (found) return found;
  }
  return null;
}

export function FacetSidebar({ state, onChange }:
  { state: SearchState; onChange: (p: Partial<SearchState>) => void }) {
  const { data, isPending } = useFacets(state);
  const selected = new Set(state.scope);
  const catalog = data?.catalog ?? [];

  const toggle = (key: string) => {
    if (selected.has(key)) {
      onChange({ scope: state.scope.filter((k) => k !== key) });
    } else {
      // Selecting a node: remove its ancestors (too broad) and descendants (redundant)
      const ancestors = new Set(ancestorKeys(catalog, key) ?? []);
      const node = findNode(catalog, key);
      const descendants = new Set(node ? descendantKeys(node) : []);
      const pruned = state.scope.filter((k) => !ancestors.has(k) && !descendants.has(k));
      onChange({ scope: [...pruned, key] });
    }
  };

  return (
    <aside className="w-[280px] shrink-0 overflow-y-auto border-l border-border px-4 py-5">
      <h2 className="mb-3 text-micro font-medium uppercase tracking-[0.1em] text-ink-faint">
        Heimildir
      </h2>
      {isPending && <div className="h-40 animate-pulse rounded bg-border" />}
      {data?.catalog
        .filter((node) => node.count > 0 || selected.has(node.key))
        .map((node) => (
          <FacetNode key={node.key} node={node} selected={selected} depth={0} onToggle={toggle} />
        ))}
    </aside>
  );
}
