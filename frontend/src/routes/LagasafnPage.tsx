import { Link } from "react-router-dom";
import { useSources } from "../hooks/useSources";
import { PageShell } from "../components/PageShell";
import { ErrorState } from "../components/states";
import { formatCount } from "../lib/formatNumber";

export default function LagasafnPage() {
  const { data, isPending, isError } = useSources();
  const lagasafnNode = data?.catalog.find((n) => n.key === "lagasafn");
  const chapters = lagasafnNode?.children ?? [];

  return (
    <PageShell
      title="Lagasafn Alþingis"
      subtitle={
        lagasafnNode
          ? `${formatCount(lagasafnNode.count)} lög í ${chapters.length} köflum`
          : undefined
      }
    >
      {isPending ? (
        <div className="grid gap-x-10 gap-y-px sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="h-9 animate-pulse rounded bg-surface-sunken" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState error={new Error("Ekki tókst að sækja lagasafn")} />
      ) : (
        // 48 chapters in three columns rather than 48 cards down a 3.200px
        // scroll. A chapter is a link with a count; it does not need a card.
        <ul className="grid gap-x-10 sm:grid-cols-2 xl:grid-cols-3">
          {chapters.map((ch) => {
            const n = ch.key.replace("lagasafn_", "").replace(/^0/, "");
            return (
              <li key={ch.key}>
                <Link
                  to={`/lagasafn/${n}`}
                  className="group -mx-2 flex items-baseline gap-3 rounded border-b border-border px-2 py-2 hover:bg-surface"
                >
                  <span className="min-w-0 flex-1 truncate text-meta text-ink group-hover:text-accent">
                    {ch.label}
                  </span>
                  <span className="tabular shrink-0 text-micro text-ink-faint">
                    {formatCount(ch.count)}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </PageShell>
  );
}
