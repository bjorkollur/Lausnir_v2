import { useParams } from "react-router-dom";
import { useDocument } from "../hooks/useDocument";
import { DocHeader } from "../components/DocHeader";
import { DocPanel } from "../components/DocPanel";
import { ErrorState } from "../components/states";

export default function DocumentPage() {
  const { id = "" } = useParams();
  const { data, isPending, isError, error } = useDocument(id);
  if (isPending) return (
    <div className="p-8">
      <div className="h-64 bg-slate-100 rounded animate-pulse" />
    </div>
  );
  if (isError) return <ErrorState error={error} />;
  return (
    <div className="flex h-full flex-col">
      <DocHeader doc={data} />
      <DocPanel doc={data} />
    </div>
  );
}
