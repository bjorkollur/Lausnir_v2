import { Link } from "react-router-dom";
import type { CatalogNode } from "../api/types";

function Node({ node, depth }: { node: CatalogNode; depth: number }) {
  return (
    <div>
      <div className="flex items-center gap-2 py-1" style={{ paddingLeft: depth * 16 }}>
        <Link to={`/?scope=${encodeURIComponent(node.key)}`} className="text-accent hover:underline">{node.label}</Link>
        <span className="text-xs text-ink-faint tabular-nums">{node.count.toLocaleString("is-IS")}</span>
      </div>
      {node.children?.map((c) => <Node key={c.key} node={c} depth={depth + 1} />)}
    </div>
  );
}

export function CatalogTree({ nodes }: { nodes: CatalogNode[] }) {
  return <div>{nodes.map((n) => <Node key={n.key} node={n} depth={0} />)}</div>;
}
