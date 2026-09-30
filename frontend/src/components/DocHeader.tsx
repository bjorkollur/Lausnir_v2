import { Link } from "react-router-dom";
import { ArrowLeftIcon, ArrowSquareOutIcon } from "@phosphor-icons/react";
import type { DocumentDetail } from "../api/types";

/** Sticks under the main nav so the reader always knows which document they are
 *  in and can get back out. Shares the page container, so its left edge lines up
 *  with the reading column instead of starting 24px in. */
export function DocHeader({ doc }: { doc: DocumentDetail }) {
  return (
    <div className="sticky top-0 z-20 border-b border-border bg-surface">
      <div className="mx-auto flex h-12 max-w-[1400px] items-center gap-3 px-6">
        <Link
          to="/"
          aria-label="Til baka í leit"
          className="grid h-7 w-7 shrink-0 place-items-center rounded text-ink-soft hover:bg-surface-sunken hover:text-ink"
        >
          <ArrowLeftIcon size={15} />
        </Link>
        <span className="truncate text-meta font-medium text-ink">{doc.urlausn}</span>
        {doc.url && (
          <a
            href={doc.url}
            target="_blank"
            rel="noreferrer"
            className="ml-auto inline-flex shrink-0 items-center gap-1.5 text-meta text-ink-soft hover:text-accent"
          >
            Frumrit
            <ArrowSquareOutIcon size={13} aria-hidden />
          </a>
        )}
      </div>
    </div>
  );
}
