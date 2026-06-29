import { useSources } from "../hooks/useSources";
import { CatalogTree } from "../components/CatalogTree";
import { ErrorState } from "../components/states";

// Heimildir sýnir einungis dóma og úrskurði; lagasafn og bókasafn hafa eigin síður
const EXCLUDED_KEYS = new Set(["lagasafn", "baekur"]);

export default function CatalogPage() {
  const { data, isPending, isError, error } = useSources();
  const filtered = data?.catalog.filter((n) => !EXCLUDED_KEYS.has(n.key)) ?? [];
  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-bold mb-4">Heimildir</h1>
      {isPending ? <div className="h-64 bg-slate-100 rounded animate-pulse" />
        : isError ? <ErrorState error={error} />
        : <CatalogTree nodes={filtered} />}
    </div>
  );
}
