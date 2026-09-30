import { useParams } from "react-router-dom";
import { useDocument } from "../hooks/useDocument";
import { DocHeader } from "../components/DocHeader";
import { DocPanel } from "../components/DocPanel";
import { ErrorState } from "../components/states";
import { useDocumentTitle } from "../lib/useDocumentTitle";

export default function DocumentPage() {
  const { id = "" } = useParams();
  const { data, isPending, isError, error, refetch } = useDocument(id);
  useDocumentTitle(
    data ? (data.case_number_is_title ? data.case_number : data.urlausn) : isError ? "Skjal fannst ekki" : null,
  );
  if (isPending) return (
    <div role="status" className="mx-auto max-w-[1400px] px-4 py-10 sm:px-6">
      <span className="sr-only">Sæki skjal…</span>
      <div aria-hidden className="max-w-[44rem] animate-pulse space-y-3 xl:ml-[15rem]">
        <div className="h-7 w-2/3 rounded bg-surface-sunken" />
        <div className="h-3.5 w-1/4 rounded bg-surface-sunken" />
        <div className="h-3.5 w-full rounded bg-surface-sunken pt-6" />
        <div className="h-3.5 w-11/12 rounded bg-surface-sunken" />
        <div className="h-3.5 w-4/5 rounded bg-surface-sunken" />
      </div>
    </div>
  );
  if (isError) return (
    <div className="mx-auto max-w-[1400px] px-4 sm:px-6">
      <ErrorState error={error} onRetry={() => void refetch()} />
    </div>
  );
  return (
    <div className="flex h-full flex-col">
      <DocHeader doc={data} />
      <DocPanel doc={data} />
    </div>
  );
}
