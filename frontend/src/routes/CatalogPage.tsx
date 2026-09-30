import { useSources } from "../hooks/useSources";
import { CatalogTree } from "../components/CatalogTree";
import { PageShell } from "../components/PageShell";
import { ErrorState } from "../components/states";
import { formatCount } from "../lib/formatNumber";

// Heimildir sýnir einungis dóma og úrskurði; lagasafn og bókasafn hafa eigin síður
const EXCLUDED_KEYS = new Set(["lagasafn", "baekur"]);

export default function CatalogPage() {
  const { data, isPending, isError, error } = useSources();
  const filtered = data?.catalog.filter((n) => !EXCLUDED_KEYS.has(n.key)) ?? [];
  const total = filtered.reduce((sum, n) => sum + n.count, 0);

  return (
    <PageShell
      title="Heimildir"
      subtitle={isPending ? undefined : `${formatCount(total)} skjöl`}
    >
      {isPending ? (
        <div className="h-64 animate-pulse rounded bg-surface-sunken" />
      ) : isError ? (
        <ErrorState error={error} />
      ) : (
        <CatalogTree nodes={filtered} />
      )}
    </PageShell>
  );
}
