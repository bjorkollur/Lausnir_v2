import { useEffect, useRef } from "react";
import { useSearch } from "../hooks/useSearch";
import { ResultCard } from "./ResultCard";
import { ResultsSkeleton, EmptyState, ErrorState } from "./states";
import type { SearchState } from "../lib/searchState";
import { formatCount } from "../lib/formatNumber";

function plural(n: number) {
  return n === 1 ? "1 niðurstaða" : `${formatCount(n)} niðurstöður`;
}

export function ResultsList({ state }: { state: SearchState }) {
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
  if (q.isError) return <ErrorState error={q.error} />;

  const page0 = q.data.pages[0];
  const total = page0.total;
  if (total === 0) return <EmptyState />;

  const items = q.data.pages.flatMap((p) => p.results);

  return (
    <div>
      {page0.relaxed && (
        <p data-testid="relaxed-notice" className="text-sm text-[var(--ink-soft)] mb-2">
          {page0.strict_total > 0 ? (
            total - page0.strict_total === 0 ? (
              // M5: relaxation fired on the pre-search estimate but found no
              // additional reachable matches — don't claim "Sýni einnig 0 skjöl".
              <>
                <strong>{page0.strict_total}</strong> skjöl innihalda öll leitarorðin.
              </>
            ) : (
              <>
                <strong>{page0.strict_total}</strong> skjöl innihalda öll leitarorðin. Sýni einnig{" "}
                <strong>{total - page0.strict_total}</strong> skjöl sem innihalda flest eða sum þeirra.
              </>
            )
          ) : (
            <>Engin skjöl innihalda öll leitarorðin. Sýni skjöl sem innihalda flest eða sum þeirra.</>
          )}
        </p>
      )}
      <p className="text-sm text-[var(--ink-soft)] py-2">{plural(total)}</p>
      {items.map((r) => (
        <ResultCard key={r.id} r={r} />
      ))}
      <div ref={sentinel} className="h-8" />
      {q.isFetchingNextPage && <ResultsSkeleton />}
    </div>
  );
}
