import { useEffect, useRef } from "react";
import { useSearch } from "../hooks/useSearch";
import { ResultCard } from "./ResultCard";
import { ResultsSkeleton, EmptyState, ErrorState } from "./states";
import type { SearchState } from "../lib/searchState";

function plural(n: number) {
  return n === 1 ? "1 niðurstaða" : `${n.toLocaleString("is-IS")} niðurstöður`;
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

  const total = q.data.pages[0].total;
  if (total === 0) return <EmptyState />;

  const items = q.data.pages.flatMap((p) => p.results);

  return (
    <div>
      <p className="text-sm text-slate-500 py-2">{plural(total)}</p>
      {items.map((r) => (
        <ResultCard key={r.id} r={r} />
      ))}
      <div ref={sentinel} className="h-8" />
      {q.isFetchingNextPage && <ResultsSkeleton />}
    </div>
  );
}
