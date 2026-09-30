import { Link } from "react-router-dom";
import type { CatalogNode } from "../api/types";
import { formatCount } from "../lib/formatNumber";

/** The catalogue is a lookup table, not prose: the reader is finding one source
 *  among a hundred. It runs in columns so the whole corpus is on screen at once
 *  instead of down a single 4.000px scroll, and a group reads as a group rather
 *  than as one more blue link at a different indent. */
function Leaf({ node }: { node: CatalogNode }) {
  return (
    <Link
      to={`/?scope=${encodeURIComponent(node.key)}`}
      className="group -mx-2 flex items-baseline gap-3 rounded px-2 py-[3px] hover:bg-surface"
    >
      <span className="min-w-0 flex-1 truncate text-meta text-ink group-hover:text-accent">
        {node.label}
      </span>
      <span className="tabular shrink-0 text-micro text-ink-faint">
        {formatCount(node.count)}
      </span>
    </Link>
  );
}

function Group({ node }: { node: CatalogNode }) {
  // A source with no documents cannot narrow anything; it is a row the reader
  // has to skip. Hæstiréttur's "Úrskurðir" is the standing example: the court
  // issues none (docs/wiki/09-gildrur.md).
  const kids = (node.children ?? []).filter((c) => c.count > 0 || c.children?.length);
  return (
    <section className="mb-8">
      <div className="mb-2 flex items-baseline gap-3 border-b border-border pb-1">
        <Link
          to={`/?scope=${encodeURIComponent(node.key)}`}
          className="min-w-0 flex-1 truncate text-meta font-medium text-ink hover:text-accent"
        >
          {node.label}
        </Link>
        <span className="tabular shrink-0 text-micro text-ink-faint">
          {formatCount(node.count)}
        </span>
      </div>
      <div className="columns-1 gap-10 sm:columns-2 xl:columns-3">
        {kids.map((c) =>
          c.children?.length ? (
            <div key={c.key} className="mb-3 break-inside-avoid">
              <Leaf node={c} />
              <div className="ml-3 border-l border-border pl-2">
                {c.children
                  .filter((g) => g.count > 0)
                  .map((g) => <Leaf key={g.key} node={g} />)}
              </div>
            </div>
          ) : (
            <div key={c.key} className="break-inside-avoid">
              <Leaf node={c} />
            </div>
          ),
        )}
      </div>
    </section>
  );
}

export function CatalogTree({ nodes }: { nodes: CatalogNode[] }) {
  return (
    <div>
      {nodes.map((n) =>
        n.children?.length ? <Group key={n.key} node={n} /> : (
          <section key={n.key} className="mb-8 max-w-sm">
            <Leaf node={n} />
          </section>
        ),
      )}
    </div>
  );
}
