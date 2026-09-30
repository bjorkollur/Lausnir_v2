import { Link } from "react-router-dom";
import { ArrowRightIcon } from "@phosphor-icons/react";
import { useSources } from "../hooks/useSources";
import { PageShell } from "../components/PageShell";
import { formatCount } from "../lib/formatNumber";

/** The page used to be a paragraph telling the reader to go and do the thing
 *  themselves in another view. It now does it: each entry is a link straight
 *  into the search, already scoped. */
const SECTIONS = [
  { key: "logfraediritgerdir", label: "Lögfræðiritgerðir", hint: "Meistara- og BA-ritgerðir úr Skemmunni" },
  { key: "logfraedibaekur", label: "Lögfræðibækur", hint: "Fræðirit og handbækur" },
];

export default function BokasafnPage() {
  const { data } = useSources();
  const baekurNode = data?.catalog.find((n) => n.key === "baekur");
  const countOf = (key: string) =>
    baekurNode?.children?.find((c) => c.key === key)?.count ?? 0;

  return (
    <PageShell
      title="Bókasafn"
      subtitle={baekurNode ? `${formatCount(baekurNode.count)} rit` : undefined}
    >
      <ul className="grid max-w-3xl gap-3 sm:grid-cols-2">
        {SECTIONS.map((s) => (
          <li key={s.key}>
            <Link
              to={`/?scope=${encodeURIComponent(s.key)}`}
              className="group flex h-full flex-col justify-between gap-6 rounded-card border border-border p-5 hover:border-border-strong hover:bg-surface"
            >
              <div>
                <span className="text-heading text-ink group-hover:text-accent">{s.label}</span>
                <p className="mt-1 text-meta text-ink-soft">{s.hint}</p>
              </div>
              <span className="inline-flex items-center gap-2 text-meta text-ink-faint">
                <span className="tabular">{formatCount(countOf(s.key))}</span>
                <ArrowRightIcon size={13} aria-hidden className="transition-transform group-hover:translate-x-0.5" />
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </PageShell>
  );
}
