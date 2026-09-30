import { Link } from "react-router-dom";
import { CaretRightIcon } from "@phosphor-icons/react";

export interface Crumb {
  label: string;
  to?: string;
}

/** Where this page sits in the hierarchy, each level a link back up. The law
 *  pages are three levels deep and used to offer only "← Lagasafn" on one of
 *  them, and nothing at all on a law. */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Brauðmolar" className="mb-3">
      <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-meta text-ink-soft">
        {items.map((c, i) => (
          <li key={`${i}-${c.label}`} className="inline-flex min-w-0 items-center gap-1.5">
            {i > 0 && <CaretRightIcon size={10} weight="bold" aria-hidden className="shrink-0 text-ink-faint" />}
            {c.to ? (
              <Link to={c.to} className="truncate underline-offset-2 hover:text-accent hover:underline">
                {c.label}
              </Link>
            ) : (
              <span aria-current="page" className="truncate text-ink">{c.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
