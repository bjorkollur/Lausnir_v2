import { useEffect, useRef } from "react";
import { useSearch } from "../hooks/useSearch";
import { ResultCard } from "./ResultCard";
import { ResultsSkeleton, EmptyState, ErrorState } from "./states";
import { activeFilterCount, CLEAR_FILTERS, type SearchState } from "../lib/searchState";
import { formatCount } from "../lib/formatNumber";
import { MODE_LABELS } from "../lib/modes";

function plural(n: number) {
  return n === 1 ? "1 niðurstaða" : `${formatCount(n)} niðurstöður`;
}

const ACTION =
  "h-8 rounded-md border border-border bg-surface px-3 text-meta text-ink hover:border-border-strong";

/** What to try when nothing matched, from what is actually narrowing the
 *  search: the filters in force, then the mode. */
function NoResults({ state, onChange }: { state: SearchState; onChange?: (p: Partial<SearchState>) => void }) {
  const filters = activeFilterCount(state);
  const otherMode = state.mode !== "keyword" && Boolean(state.q);
  const title = state.q ? <>Engar niðurstöður fyrir „{state.q}“</> : "Engin skjöl uppfylla síurnar";

  return (
    <EmptyState title={title}>
      <ul className="list-disc space-y-1 pl-5">
        {state.q && <li>Athugaðu stafsetningu eða prófaðu almennari orð.</li>}
        {filters > 0 && <li>Síurnar þrengja leitina, prófaðu að fjarlægja einhverjar þeirra.</li>}
        {otherMode && (
          <li>
            Leitað er í hamnum „{MODE_LABELS[state.mode]}“. Orðaleit finnur öll orðin í hvaða
            beygingarmynd sem er, hvar sem er í skjalinu.
          </li>
        )}
      </ul>
      {onChange && (filters > 0 || otherMode) && (
        <div className="flex flex-wrap gap-2 pt-1">
          {filters > 0 && (
            <button type="button" className={ACTION} onClick={() => onChange(CLEAR_FILTERS)}>
              Fjarlægja allar síur
            </button>
          )}
          {otherMode && (
            <button type="button" className={ACTION} onClick={() => onChange({ mode: "keyword", sort: "relevance" })}>
              Nota orðaleit
            </button>
          )}
        </div>
      )}
    </EmptyState>
  );
}

export function ResultsList({ state, onChange }:
  { state: SearchState; onChange?: (p: Partial<SearchState>) => void }) {
  const q = useSearch(state);
  const sentinel = useRef<HTMLDivElement>(null);

  const { hasNextPage, isFetchingNextPage, fetchNextPage } = q;

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) {
        fetchNextPage();
      }
    });
    io.observe(el);
    return () => io.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  if (q.isPending) return <ResultsSkeleton />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;

  const page0 = q.data.pages[0];
  const total = page0.total;
  if (total === 0) return <NoResults state={state} onChange={onChange} />;

  const items = q.data.pages.flatMap((p) => p.results);

  return (
    <div>
      {/* A status region, so a screen reader hears the count when a search
          lands instead of nothing at all. WCAG 4.1.3 (Status Messages). */}
      <div role="status" className="pb-1 pt-4">
        <p className="text-meta text-ink-soft">{plural(total)}</p>
        {page0.relaxed && (
          <p data-testid="relaxed-notice" className="mt-1 text-meta text-ink-soft">
            {page0.strict_total > 0 ? (
              total - page0.strict_total === 0 ? (
                // M5: relaxation fired on the pre-search estimate but found no
                // additional reachable matches — don't claim "Sýni einnig 0 skjöl".
                <>
                  <strong className="font-medium text-ink">{page0.strict_total}</strong> skjöl innihalda öll leitarorðin.
                </>
              ) : (
                <>
                  <strong className="font-medium text-ink">{page0.strict_total}</strong> skjöl innihalda öll leitarorðin. Sýni einnig{" "}
                  <strong className="font-medium text-ink">{total - page0.strict_total}</strong> skjöl sem innihalda flest eða sum þeirra.
                </>
              )
            ) : (
              <>Engin skjöl innihalda öll leitarorðin. Sýni skjöl sem innihalda flest eða sum þeirra.</>
            )}
          </p>
        )}
      </div>
      {items.map((r) => (
        <ResultCard key={r.id} r={r} />
      ))}
      <div ref={sentinel} className="h-8" />
      {isFetchingNextPage && <ResultsSkeleton />}
      {!hasNextPage && items.length > 20 && (
        <p className="pb-10 text-center text-meta text-ink-faint">Allar niðurstöður birtar</p>
      )}
    </div>
  );
}
