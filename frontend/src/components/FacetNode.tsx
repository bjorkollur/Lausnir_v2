import { useState } from "react";
import * as Checkbox from "@radix-ui/react-checkbox";
import type { CatalogNode } from "../api/types";

export function FacetNode({ node, selected, depth, onToggle }:
  { node: CatalogNode; selected: Set<string>; depth: number; onToggle: (key: string) => void }) {
  const [open, setOpen] = useState(depth <= 1);
  const hasKids = !!node.children?.length;
  return (
    <div>
      <div className="flex items-center gap-2 py-1" style={{ paddingLeft: depth * 14 }}>
        {hasKids ? (
          <button aria-label={open ? "fella saman" : "opna"} onClick={() => setOpen(!open)} className="w-4 text-[var(--ink-faint)]">
            {open ? "▾" : "▸"}
          </button>
        ) : <span className="w-4" />}
        <Checkbox.Root
          aria-label={node.label}
          checked={selected.has(node.key)}
          onCheckedChange={() => onToggle(node.key)}
          className="w-4 h-4 border border-[var(--border-strong)] rounded data-[state=checked]:bg-[var(--accent)] data-[state=checked]:border-[var(--accent)] grid place-items-center">
          <Checkbox.Indicator className="text-white text-[10px]">✓</Checkbox.Indicator>
        </Checkbox.Root>
        <span className="flex-1 text-sm text-[var(--ink)]">{node.label}</span>
        <span className="text-xs text-[var(--ink-faint)] tabular-nums">{node.count.toLocaleString("is-IS")}</span>
      </div>
      {hasKids && open && node.children!.map((c) => (
        <FacetNode key={c.key} node={c} selected={selected} depth={depth + 1} onToggle={onToggle} />
      ))}
    </div>
  );
}
