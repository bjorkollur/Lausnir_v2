import type { CatalogNode } from "../api/types";

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

/** Selecting a node drops its ancestors (too broad) and descendants (redundant). */
export function toggleScope(catalog: CatalogNode[], scope: string[], key: string): string[] {
  if (scope.includes(key)) return scope.filter((k) => k !== key);
  const ancestors = new Set(ancestorKeys(catalog, key) ?? []);
  const node = findNode(catalog, key);
  const descendants = new Set(node ? descendantKeys(node) : []);
  return [...scope.filter((k) => !ancestors.has(k) && !descendants.has(k)), key];
}
