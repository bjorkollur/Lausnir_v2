import { useFacets } from "../hooks/useFacets";
import { XIcon } from "@phosphor-icons/react";
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

export function FacetSidebar({ state, onChange, open = false, onClose }: {
  state: SearchState;
  onChange: (p: Partial<SearchState>) => void;
  /** Below lg the panel slides in; above it, it is always the static column. */
  open?: boolean;
  onClose?: () => void;
}) {
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
    <>
      {/* One tree, not two. Below lg it slides in over the results instead of
          standing beside them, which at 390px left the results column 190px
          wide and every result wrapping to two words a line. */}
      {open && (
        <button
          type="button"
          aria-label="Loka heimildasíu"
          onClick={onClose}
          className="fixed inset-0 z-40 bg-ink/20 lg:hidden"
        />
      )}
      <aside
        className={`fixed inset-y-0 right-0 z-50 w-[85vw] max-w-[320px] overflow-y-auto border-l border-border bg-surface px-4 py-5 transition-transform lg:static lg:z-auto lg:w-[280px] lg:max-w-none lg:shrink-0 lg:translate-x-0 lg:bg-transparent lg:transition-none ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-micro font-medium uppercase tracking-[0.1em] text-ink-faint">
            Heimildir
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Loka"
            className="text-ink-soft hover:text-ink lg:hidden"
          >
            <XIcon size={15} />
          </button>
        </div>
      {isPending && <div className="h-40 animate-pulse rounded bg-border" />}
      {data?.catalog
        .filter((node) => node.count > 0 || selected.has(node.key))
        .map((node) => (
          <FacetNode key={node.key} node={node} selected={selected} depth={0} onToggle={toggle} />
        ))}
      </aside>
    </>
  );
}
