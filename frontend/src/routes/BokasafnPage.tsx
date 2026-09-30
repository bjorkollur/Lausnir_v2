import { useSources } from "../hooks/useSources";

export default function BokasafnPage() {
  const { data } = useSources();
  const baekurNode = data?.catalog.find((n) => n.key === "baekur");
  const count = baekurNode?.count ?? 0;

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-bold mb-1">Bókasafn</h1>
      <p className="text-ink-soft mb-6 text-sm">
        {count > 0 ? `${count} lögfræðiritgerðir` : "Hleður..."}
      </p>
      <div className="bg-white rounded-lg border border-border p-6 text-ink-soft text-sm">
        Leit í lögfræðiritgerðum kemur hér. Nota má aðalleitina til að leita í þessum gögnum með því að velja <strong>Lögfræðiritgerðir</strong> í flokkavelju.
      </div>
    </div>
  );
}
