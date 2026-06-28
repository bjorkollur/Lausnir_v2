import { useState } from "react";
import type { CatalogNode } from "../api/types";

// ── helpers ──────────────────────────────────────────────────────────────────

function ancestorKeys(
  nodes: CatalogNode[],
  target: string,
  path: string[] = [],
): string[] | null {
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

// ── SourceTree ────────────────────────────────────────────────────────────────

interface SourceTreeProps {
  catalog: CatalogNode[];
  scope: string[];
  onScopeChange: (scope: string[]) => void;
}

export function SourceTree({ catalog, scope, onScopeChange }: SourceTreeProps) {
  const selected = new Set(scope);

  const toggle = (key: string) => {
    if (selected.has(key)) {
      onScopeChange(scope.filter((k) => k !== key));
    } else {
      const ancestors = new Set(ancestorKeys(catalog, key) ?? []);
      const node = findNode(catalog, key);
      const descendants = new Set(node ? descendantKeys(node) : []);
      const pruned = scope.filter((k) => !ancestors.has(k) && !descendants.has(k));
      onScopeChange([...pruned, key]);
    }
  };

  if (catalog.length === 0) {
    return <div className="h-10 bg-slate-100 rounded animate-pulse" />;
  }

  return (
    <div className="space-y-1">
      {catalog.map((group) => (
        <GroupNode
          key={group.key}
          node={group}
          selected={selected}
          onToggle={toggle}
        />
      ))}
      {scope.length > 0 && (
        <button
          type="button"
          onClick={() => onScopeChange([])}
          className="mt-2 text-xs text-slate-400 hover:text-slate-600 underline"
        >
          Hreinsa val
        </button>
      )}
    </div>
  );
}

// ── GroupNode (depth-0 group, collapsible) ────────────────────────────────────

function GroupNode({
  node,
  selected,
  onToggle,
}: {
  node: CatalogNode;
  selected: Set<string>;
  onToggle: (key: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const isChecked = selected.has(node.key);
  // depth-1 children only (not depth-2 verdict subtypes)
  const children = (node.children ?? []).filter(
    (c) => !isLeafVerdict(c),
  );
  const hasChildren = children.length > 0;

  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden">
      {/* Group header */}
      <div
        className={`flex items-center gap-2 px-3 py-2.5 ${
          open ? "bg-slate-50" : "bg-white hover:bg-slate-50"
        } transition-colors`}
      >
        {/* Expand toggle */}
        {hasChildren ? (
          <button
            type="button"
            aria-label={open ? "Fella saman" : "Opna"}
            onClick={() => setOpen(!open)}
            className="w-4 text-xs text-slate-400 hover:text-slate-600 flex-shrink-0"
          >
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span className="w-4 flex-shrink-0" />
        )}

        {/* Checkbox */}
        <Checkbox
          checked={isChecked}
          onToggle={() => onToggle(node.key)}
          label={node.label}
        />

        {/* Label + count */}
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="flex-1 flex items-center justify-between text-left min-w-0"
        >
          <span
            className={`text-sm font-medium truncate ${
              isChecked ? "text-indigo-700" : "text-slate-800"
            }`}
          >
            {node.label}
          </span>
          <span className="text-xs text-slate-400 tabular-nums ml-2 flex-shrink-0">
            {node.count.toLocaleString("is-IS")}
          </span>
        </button>
      </div>

      {/* Children list */}
      {hasChildren && open && (
        <div
          className={`border-t border-slate-100 ${
            children.length > 8 ? "grid grid-cols-2 gap-x-2" : ""
          } px-2 py-1`}
        >
          {children.map((child) => (
            <ChildNode
              key={child.key}
              node={child}
              selected={selected}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── ChildNode (depth-1 individual source) ────────────────────────────────────

function ChildNode({
  node,
  selected,
  onToggle,
}: {
  node: CatalogNode;
  selected: Set<string>;
  onToggle: (key: string) => void;
}) {
  const isChecked = selected.has(node.key);

  return (
    <div className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-slate-50 transition-colors">
      <span className="w-4 flex-shrink-0" />
      <Checkbox
        checked={isChecked}
        onToggle={() => onToggle(node.key)}
        label={node.label}
      />
      <span
        className={`flex-1 text-sm truncate ${
          isChecked ? "text-indigo-700 font-medium" : "text-slate-700"
        }`}
      >
        {node.label}
      </span>
      <span className="text-xs text-slate-400 tabular-nums flex-shrink-0">
        {node.count.toLocaleString("is-IS")}
      </span>
    </div>
  );
}

// ── Checkbox (no Radix — keep it simple and dependency-free here) ─────────────

function Checkbox({
  checked,
  onToggle,
  label,
}: {
  checked: boolean;
  onToggle: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      onClick={onToggle}
      className={`w-4 h-4 flex-shrink-0 border rounded flex items-center justify-center transition-colors ${
        checked
          ? "bg-indigo-600 border-indigo-600"
          : "border-slate-300 bg-white hover:border-indigo-400"
      }`}
    >
      {checked && (
        <svg
          viewBox="0 0 10 8"
          fill="none"
          className="w-2.5 h-2 text-white"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M1 4l2.5 2.5L9 1" />
        </svg>
      )}
    </button>
  );
}

// ── helpers ───────────────────────────────────────────────────────────────────

/**
 * Returns true for depth-2 nodes that are verdict subtypes
 * (e.g. haestirettur_domar, haestirettur_urskurdir).
 * These are identified by having a parent key as prefix + "_".
 * We never show them in the tree.
 */
function isLeafVerdict(node: CatalogNode): boolean {
  // Verdict-subtype nodes don't have their own children in the catalog
  // and their keys look like "{parent}_{type}" — but we can't know the parent here.
  // Instead, we check: does this node have children? If yes it's a real group.
  // Verdict subtypes are leaves (no children). But so are small single-source groups.
  // The spec says: show depth-0 and depth-1 only. GroupNode receives depth-0 children
  // which are depth-1 nodes. We should NOT filter them out here — they ARE what we want.
  // So isLeafVerdict is always false at this call site (depth-1 children of the group).
  // This function is kept as a no-op placeholder for future use.
  return false;
}
