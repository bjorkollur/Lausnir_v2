import { useState } from "react";
import * as Checkbox from "@radix-ui/react-checkbox";
import { CaretDownIcon, CaretRightIcon, CheckIcon } from "@phosphor-icons/react";
import type { CatalogNode } from "../api/types";

/** A source in the facet tree.
 *
 *  Nodes with no hits for the current query are hidden unless they are
 *  selected: a sidebar where half the rows read 0 costs the reader a scan of
 *  every row to find the ones that can actually narrow the search. A selected
 *  node stays visible at 0 so the reason for an empty result set is on screen
 *  and can be switched off again. */
export function FacetNode({ node, selected, depth, onToggle }:
  { node: CatalogNode; selected: Set<string>; depth: number; onToggle: (key: string) => void }) {
  const [open, setOpen] = useState(depth <= 1);
  const kids = (node.children ?? []).filter(
    (c) => c.count > 0 || selected.has(c.key) || hasSelectedDescendant(c, selected),
  );
  const hasKids = kids.length > 0;
  const isSelected = selected.has(node.key);

  return (
    <div>
      <div
        className={`flex items-center gap-2 rounded py-1 pr-2 ${isSelected ? "bg-accent-soft" : ""}`}
        style={{ paddingLeft: depth * 14 + 4 }}
      >
        {hasKids ? (
          <button
            aria-label={open ? "fella saman" : "opna"}
            onClick={() => setOpen(!open)}
            className="grid w-4 place-items-center text-ink-faint hover:text-ink"
          >
            {open ? <CaretDownIcon size={11} weight="bold" /> : <CaretRightIcon size={11} weight="bold" />}
          </button>
        ) : (
          <span className="w-4" />
        )}
        <Checkbox.Root
          aria-label={node.label}
          checked={isSelected}
          onCheckedChange={() => onToggle(node.key)}
          className="grid h-[15px] w-[15px] place-items-center rounded-[3px] border border-border-strong data-[state=checked]:border-accent data-[state=checked]:bg-accent"
        >
          <Checkbox.Indicator className="text-cta-ink">
            <CheckIcon size={10} weight="bold" />
          </Checkbox.Indicator>
        </Checkbox.Root>
        <span className="flex-1 truncate text-meta text-ink">{node.label}</span>
        <span className="tabular text-micro text-ink-faint">
          {node.count.toLocaleString("is-IS")}
        </span>
      </div>
      {hasKids && open && kids.map((c) => (
        <FacetNode key={c.key} node={c} selected={selected} depth={depth + 1} onToggle={onToggle} />
      ))}
    </div>
  );
}

function hasSelectedDescendant(node: CatalogNode, selected: Set<string>): boolean {
  return (node.children ?? []).some(
    (c) => selected.has(c.key) || hasSelectedDescendant(c, selected),
  );
}
