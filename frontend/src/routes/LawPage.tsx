import { useParams, Link } from "react-router-dom";
import { useLaw } from "../hooks/useLaw";
import { LawPanel } from "../components/LawPanel";
import { ErrorState } from "../components/states";

export default function LawPage() {
  const { id } = useParams<{ id: string }>();
  const { data, isPending, isError, error } = useLaw(id ?? "");

  if (isPending) {
    return (
      <div className="bg-canvas flex-1 p-8">
        <div className="mx-auto max-w-2xl bg-white rounded-lg p-8 shadow-sm space-y-4 animate-pulse">
          <div className="h-4 bg-surface-sunken rounded w-1/3 mx-auto" />
          <div className="h-8 bg-surface-sunken rounded w-2/3 mx-auto" />
          <div className="h-4 bg-surface-sunken rounded w-1/2 mx-auto" />
          <div className="h-px bg-border my-4" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-16 bg-canvas rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-8">
        <Link to="/lagasafn" className="text-sm text-ink-soft hover:text-accent mb-4 inline-block">
          ← Lagasafn
        </Link>
        <ErrorState error={error} />
      </div>
    );
  }

  if (!data) return null;

  return <LawPanel law={data} />;
}
