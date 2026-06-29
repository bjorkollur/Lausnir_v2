import { Link } from "react-router-dom";
import { useSources } from "../hooks/useSources";
import { ErrorState } from "../components/states";

export default function LagasafnPage() {
  const { data, isPending, isError } = useSources();
  const lagasafnNode = data?.catalog.find((n) => n.key === "lagasafn");
  const chapters = lagasafnNode?.children ?? [];

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-bold mb-1">Lagasafn Alþingis</h1>
      <p className="text-slate-500 mb-6 text-sm">
        {lagasafnNode ? `${lagasafnNode.count} lög í ${chapters.length} köflum` : "Hleður..."}
      </p>

      {isPending ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-14 bg-slate-100 rounded-lg animate-pulse" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState error={new Error("Ekki tókst að sækja lagasafn")} />
      ) : (
        <div className="space-y-2">
          {chapters.map((ch) => {
            const n = ch.key.replace("lagasafn_", "").replace(/^0/, "");
            return (
              <Link
                key={ch.key}
                to={`/lagasafn/${n}`}
                className="flex items-center justify-between px-5 py-4 bg-white rounded-lg border border-slate-200 hover:border-indigo-300 hover:bg-indigo-50 transition-colors group"
              >
                <span className="font-medium text-slate-800 group-hover:text-indigo-700">
                  {ch.label}
                </span>
                <span className="text-slate-400 text-sm tabular-nums ml-4">
                  {ch.count}
                  <span className="ml-1 text-slate-300">›</span>
                </span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
