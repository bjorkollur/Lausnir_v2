import { Link } from "react-router-dom";
import { ArrowSquareOutIcon, MagnifyingGlassIcon } from "@phosphor-icons/react";
import type { LawDetail, Provision } from "../api/types";
import { Breadcrumbs } from "./Breadcrumbs";
import { formatIcelandicDate } from "../lib/formatDate";
import { cleanLawTitle } from "../lib/lawTitle";

function grLabel(p: Provision): string {
  let label = `${p.num}. gr.`;
  if (p.suffix) label += ` ${p.suffix}.`;
  return label;
}

const articleId = (p: Provision) => `gr-${p.num}${p.suffix ?? ""}`;

/** The reference the provision filter parses: "72. gr. laga nr. 33/1944", or
 *  "5. gr. 32/2016" for an instrument that is not a statute. */
function provisionRef(p: Provision, law: LawDetail): string {
  const act = law.verdict_type === "Lög" ? `laga nr. ${law.case_number}` : law.case_number;
  return `${grLabel(p)} ${act}`;
}

function jumpTo(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block: "start" });
  el.querySelector<HTMLElement>("h2")?.focus({ preventScroll: true });
}

/** The official lagasafn opens every málsgrein with a small open square, and
 *  lawyers count paragraphs by it ("2. mgr."). Drawn rather than typed: the
 *  serif face has no □ and the fallback glyph sat at the wrong size. */
function ParagraphMark() {
  return (
    <span
      aria-hidden
      className="mr-2 inline-block size-[0.5em] -translate-y-[0.08em] border border-ink-faint align-middle"
    />
  );
}

function ProvisionBlock({ p, law }: { p: Provision; law: LawDetail }) {
  const paragraphs = p.sub && p.sub.length > 0 ? p.sub.map((s) => s.text) : [p.text];
  const label = grLabel(p);
  return (
    <section id={articleId(p)} className="scroll-mt-6 border-b border-border py-5 last:border-0">
      <div className="mb-2 flex items-baseline gap-3">
        <h2 tabIndex={-1} className="text-meta font-semibold text-ink">
          {label}
        </h2>
        {/* Every article links to the rulings that cite it: the question a
            reader of a statute most often has next. */}
        {law.case_number && (
          <Link
            to={`/?provision=${encodeURIComponent(provisionRef(p, law))}`}
            aria-label={`Mál sem vísa í ${label}`}
            className="ml-auto inline-flex shrink-0 items-center gap-1 text-micro text-ink-faint hover:text-accent"
          >
            <MagnifyingGlassIcon size={12} aria-hidden />
            Tilvísanir
          </Link>
        )}
      </div>
      <div className="space-y-2">
        {paragraphs.map((t, i) => (
          <p key={i} className="prose-document text-ink">
            <ParagraphMark />
            {t}
          </p>
        ))}
      </div>
    </section>
  );
}

/** The article numbers as a jump grid beside the text. A statute can run to
 *  several hundred articles, and the page offered no way to reach 218. gr. but
 *  to scroll for it. */
function ArticleIndex({ provisions }: { provisions: Provision[] }) {
  if (provisions.length < 5) return <div className="hidden xl:block" />;
  return (
    <nav aria-label="Greinar" className="hidden xl:block">
      <div className="sticky top-6 max-h-[calc(100dvh-8rem)] overflow-y-auto pr-2">
        <h2 className="mb-3 text-micro font-medium uppercase tracking-[0.1em] text-ink-faint">Greinar</h2>
        <ul className="grid grid-cols-4 gap-0.5">
          {provisions.map((p) => (
            <li key={articleId(p)}>
              <button
                type="button"
                onClick={() => jumpTo(articleId(p))}
                aria-label={grLabel(p)}
                className="tabular h-7 w-full rounded text-center text-meta text-ink-soft hover:bg-surface-sunken hover:text-ink"
              >
                {p.num}
                {p.suffix ?? ""}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}

/** A law reads like the rest of the reader: left-aligned serif heading, the
 *  text in the document face at a measured width, the index beside it. It used
 *  to be a centred card in a different type system, dated in ISO twice over
 *  ("1944-06-17 · nr. 33/1944" and "Tók gildi 1944-06-17"). */
export function LawPanel({ law }: { law: LawDetail }) {
  const title = cleanLawTitle(law.law_name) || "Lög";
  const inForce = formatIcelandicDate(law.document_date);
  const reference = law.case_number ? `${law.verdict_type ?? "Lög"} nr. ${law.case_number}` : null;

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
      <div className="grid grid-cols-1 gap-x-12 xl:grid-cols-[12rem_minmax(0,1fr)]">
        <ArticleIndex provisions={law.provisions} />

        <article className="min-w-0 max-w-[44rem]">
          <header className="border-b border-border pb-6">
            <Breadcrumbs
              items={[
                { label: "Lagasafn", to: "/lagasafn" },
                { label: law.kafli_label, to: `/lagasafn/${law.kafli}` },
              ]}
            />
            <h1 className="font-serif text-title text-ink">{title}</h1>
            <p className="mt-1 text-meta text-ink-soft">
              {[reference, inForce && `Tók gildi ${inForce}`].filter(Boolean).join(" · ")}
            </p>
            {law.url && (
              <a
                href={law.url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-3 inline-flex items-center gap-1.5 text-meta text-accent hover:underline"
              >
                Á vef Alþingis
                <span className="sr-only">(opnast í nýjum flipa)</span>
                <ArrowSquareOutIcon size={13} aria-hidden />
              </a>
            )}
          </header>

          {law.provisions.length > 0 ? (
            law.provisions.map((p) => (
              <ProvisionBlock key={articleId(p)} p={p} law={law} />
            ))
          ) : (
            <p className="py-8 text-body text-ink-soft">Engar greinar fundust í þessum lögum.</p>
          )}
        </article>
      </div>
    </div>
  );
}
