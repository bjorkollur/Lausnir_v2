import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { searchDocuments } from "../api/client";
import { useSources } from "../hooks/useSources";
import { ErrorState } from "../components/states";

export default function LagasafnKafliPage() {
  const { n } = useParams<{ n: string }>();
  // "1" → "lagasafn_01", "12" → "lagasafn_12"
  const scope = `lagasafn_${n?.padStart(2, "0") ?? "01"}`;

  const { data, isPending, isError } = useQuery({
    queryKey: ["lagasafn-kafli", scope],
    queryFn: () =>
      searchDocuments({
        q: "",
        mode: "keyword",
        scope: [scope],
        sort: "oldest",
        page: 1,
        page_size: 200,
      }),
    enabled: !!n,
    staleTime: 5 * 60 * 1000,
  });

  const { data: sources } = useSources();
  const lagasafnNode = sources?.catalog.find((c) => c.key === "lagasafn");
  const chapter = lagasafnNode?.children?.find((c) => c.key === scope);

  return (
    <div className="p-6 max-w-3xl">
      <Link
        to="/lagasafn"
        className="text-sm text-slate-500 hover:text-indigo-600 mb-4 inline-block"
      >
        ← Lagasafn
      </Link>
      <h1 className="text-2xl font-bold mb-6">
        {chapter?.label ?? `Kafli ${n}`}
      </h1>

      {isPending ? (
        <div className="space-y-1">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-10 bg-slate-100 rounded animate-pulse" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState error={new Error("Ekki tókst að sækja lög")} />
      ) : (
        <div className="divide-y divide-slate-100">
          {(data?.results ?? []).map((r) => (
            <Link
              key={r.id}
              to={`/log/${r.id}`}
              className="flex items-start justify-between py-3 px-2 hover:bg-slate-50 rounded group"
            >
              <span className="text-indigo-700 group-hover:underline text-sm leading-snug">
                {/* snippet = lögaheiti þegar q="" fyrir lagasafn */}
                {r.snippet || r.urlausn}
              </span>
              <span className="text-slate-400 text-xs shrink-0 ml-4 tabular-nums pt-0.5">
                nr.&nbsp;{r.case_number}
              </span>
            </Link>
          ))}
          {data?.results.length === 0 && (
            <p className="text-slate-500 text-sm py-4">Engin lög fundust í þessum kafla.</p>
          )}
        </div>
      )}
    </div>
  );
}
