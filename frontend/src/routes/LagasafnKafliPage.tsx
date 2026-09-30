import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { searchDocuments } from "../api/client";
import { useSources } from "../hooks/useSources";
import { PageShell } from "../components/PageShell";
import { ErrorState } from "../components/states";
import { cleanLawTitle } from "../lib/lawTitle";

export default function LagasafnKafliPage() {
  const { n } = useParams<{ n: string }>();
  // "1" → "lagasafn_01", "12" → "lagasafn_12"
  const scope = `lagasafn_${n?.padStart(2, "0") ?? "01"}`;

  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ["lagasafn-kafli", scope],
    queryFn: () =>
      searchDocuments({
        q: "",
        mode: "keyword",
        scope: [scope],
        sort: "oldest",
        page: 1,
        page_size: 100,
      }),
    enabled: !!n,
    staleTime: 5 * 60 * 1000,
  });

  const { data: sources } = useSources();
  const lagasafnNode = sources?.catalog.find((c) => c.key === "lagasafn");
  const chapter = lagasafnNode?.children?.find((c) => c.key === scope);
  const title = chapter?.label ?? `Kafli ${n}`;
  const laws = data?.results ?? [];

  return (
    <PageShell
      title={title}
      subtitle={data ? `${laws.length} lög í kaflanum` : undefined}
      breadcrumbs={[{ label: "Lagasafn", to: "/lagasafn" }]}
    >
      {isPending ? (
        <div role="status" className="max-w-3xl space-y-px">
          <span className="sr-only">Sæki lög…</span>
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} aria-hidden className="h-10 animate-pulse rounded bg-surface-sunken" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : laws.length === 0 ? (
        <p className="text-body text-ink-soft">Engin lög fundust í þessum kafla.</p>
      ) : (
        <ul className="max-w-3xl">
          {laws.map((r) => (
            <li key={r.id}>
              <Link
                to={`/log/${r.id}`}
                className="group -mx-2 flex items-baseline gap-4 rounded border-b border-border px-2 py-2.5 hover:bg-surface"
              >
                <span className="min-w-0 flex-1 text-meta text-ink group-hover:text-accent">
                  {/* snippet = lögaheiti þegar q="" fyrir lagasafn */}
                  {cleanLawTitle(r.snippet) || r.urlausn}
                </span>
                <span className="tabular shrink-0 text-micro text-ink-faint">nr. {r.case_number}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </PageShell>
  );
}
