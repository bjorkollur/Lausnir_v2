import { useParams } from "react-router-dom";
import { useLaw } from "../hooks/useLaw";
import { LawPanel } from "../components/LawPanel";
import { ErrorState } from "../components/states";
import { Breadcrumbs } from "../components/Breadcrumbs";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { cleanLawTitle } from "../lib/lawTitle";

export default function LawPage() {
  const { id } = useParams<{ id: string }>();
  const { data, isPending, isError, error, refetch } = useLaw(id ?? "");
  useDocumentTitle(data ? cleanLawTitle(data.law_name) || "Lög" : isError ? "Lög fundust ekki" : null);

  if (isPending) {
    return (
      <div role="status" className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
        <span className="sr-only">Sæki lög…</span>
        <div aria-hidden className="max-w-[44rem] animate-pulse space-y-3 xl:ml-[15rem]">
          <div className="h-3.5 w-1/3 rounded bg-surface-sunken" />
          <div className="h-7 w-2/3 rounded bg-surface-sunken" />
          <div className="h-3.5 w-1/4 rounded bg-surface-sunken" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-16 rounded bg-surface-sunken" />
          ))}
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
        <Breadcrumbs items={[{ label: "Lagasafn", to: "/lagasafn" }]} />
        <ErrorState error={error} onRetry={() => void refetch()} />
      </div>
    );
  }

  return <LawPanel law={data} />;
}
