import { useLocation, useNavigate } from "react-router-dom";
import { ArrowLeftIcon, ArrowSquareOutIcon } from "@phosphor-icons/react";
import type { DocumentDetail } from "../api/types";

/** Sticks under the main nav so the reader always knows which document they are
 *  in and can get back out. Shares the page container, so its left edge lines up
 *  with the reading column instead of starting 24px in.
 *
 *  Back means back. The arrow used to link to "/", the empty front page, so a
 *  reader who opened a judgment from result 40 of a filtered search lost the
 *  query, the filters and their place in the list. It now steps back through
 *  history, which also covers arriving from a citation or a law, and only
 *  falls back to the front page when this document was opened directly. */
export function DocHeader({ doc }: { doc: DocumentDetail }) {
  const navigate = useNavigate();
  const location = useLocation();
  const hasHistory = location.key !== "default";

  return (
    <div className="sticky top-0 z-20 border-b border-border bg-surface">
      <div className="mx-auto flex h-12 max-w-[1400px] items-center gap-3 px-4 sm:px-6">
        <button
          type="button"
          onClick={() => (hasHistory ? navigate(-1) : navigate("/"))}
          aria-label={hasHistory ? "Til baka" : "Til baka í leit"}
          title={hasHistory ? "Til baka" : "Til baka í leit"}
          className="-ml-1.5 grid h-8 w-8 shrink-0 place-items-center rounded text-ink-soft hover:bg-surface-sunken hover:text-ink"
        >
          <ArrowLeftIcon size={16} aria-hidden />
        </button>
        <span className="truncate text-meta font-medium text-ink">{doc.urlausn}</span>
        {doc.url && (
          <a
            href={doc.url}
            target="_blank"
            rel="noreferrer"
            className="ml-auto inline-flex shrink-0 items-center gap-1.5 text-meta text-ink-soft hover:text-accent"
          >
            Frumrit
            <span className="sr-only">(opnast í nýjum flipa)</span>
            <ArrowSquareOutIcon size={13} aria-hidden />
          </a>
        )}
      </div>
    </div>
  );
}
