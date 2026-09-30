import { useState } from "react";
import { CaretDownIcon, CaretRightIcon, CheckIcon } from "@phosphor-icons/react";
import type { CatalogNode } from "../api/types";
import { toggleScope } from "../lib/scopeTree";
import { formatCount } from "../lib/formatNumber";

// ── SourceTree ────────────────────────────────────────────────────────────────

interface SourceTreeProps {
  catalog: CatalogNode[];
  scope: string[];
  onScopeChange: (scope: string[]) => void;
}

export function SourceTree({ catalog, scope, onScopeChange }: SourceTreeProps) {
  const selected = new Set(scope);

  const toggle = (key: string) => onScopeChange(toggleScope(catalog, scope, key));

  if (catalog.length === 0) {
    return <div className="h-10 bg-[var(--canvas)] border border-[var(--border)] rounded-md animate-pulse" />;
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
          className="mt-2 text-xs text-[var(--ink-faint)] hover:text-[var(--ink)] underline underline-offset-2 transition-colors"
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
  const children = node.children ?? [];
  const hasChildren = children.length > 0;

  return (
    <div className="border border-[var(--border)] rounded-md overflow-hidden">
      {/* Group header */}
      <div
        className={`flex items-center gap-2 px-3 py-2.5 ${
          open ? "bg-[var(--canvas)]" : "bg-[var(--surface)] hover:bg-[var(--canvas)]"
        } transition-colors`}
      >
        {/* Expand toggle */}
        {hasChildren ? (
          <button
            type="button"
            aria-label={`${open ? "Fella saman" : "Opna"} ${node.label}`}
            aria-expanded={open}
            onClick={() => setOpen(!open)}
            className="grid w-4 shrink-0 place-items-center text-ink-faint hover:text-ink"
          >
            {open ? <CaretDownIcon size={11} weight="bold" aria-hidden /> : <CaretRightIcon size={11} weight="bold" aria-hidden />}
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

        {/* Label + count. Clicking the name selects the group, as a label does. */}
        <button
          type="button"
          tabIndex={-1}
          aria-hidden
          onClick={() => onToggle(node.key)}
          className="flex-1 flex items-center justify-between text-left min-w-0"
        >
          <span
            className={`text-sm font-medium truncate ${
              isChecked ? "text-[var(--accent)]" : "text-[var(--ink)]"
            }`}
          >
            {node.label}
          </span>
          <span className="text-xs text-[var(--ink-faint)] tabular-nums ml-2 flex-shrink-0">
            {formatCount(node.count)}
          </span>
        </button>
      </div>

      {/* Children list */}
      {hasChildren && open && (
        <div
          className={`border-t border-[var(--border)] ${
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
    // A label, so the whole row is the click target and not only the 16px box.
    <label className="flex cursor-pointer items-center gap-2 px-2 py-1.5 rounded-md hover:bg-[var(--canvas)] transition-colors">
      <span className="w-4 flex-shrink-0" />
      <Checkbox
        checked={isChecked}
        onToggle={() => onToggle(node.key)}
        label={node.label}
      />
      <span
        className={`flex-1 text-sm truncate ${
          isChecked ? "text-[var(--accent)] font-medium" : "text-[var(--ink-soft)]"
        }`}
      >
        {node.label}
      </span>
      <span className="text-xs text-[var(--ink-faint)] tabular-nums flex-shrink-0">
        {formatCount(node.count)}
      </span>
    </label>
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
      className={`w-4 h-4 flex-shrink-0 border rounded-sm flex items-center justify-center transition-colors ${
        checked
          ? "bg-[var(--accent)] border-[var(--accent)]"
          : "border-[var(--border-strong)] bg-[var(--surface)] hover:border-[var(--accent)]"
      }`}
    >
      {checked && <CheckIcon size={10} weight="bold" aria-hidden className="text-cta-ink" />}
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
