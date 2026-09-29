import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { CitationRef, DocumentDetail, Party } from "../api/types";
import { documentPdfUrl, fetchCitations } from "../api/client";
import { splitMarkdown, LARGE_DOC_THRESHOLD, SEARCHABLE_THRESHOLD } from "../lib/splitMarkdown";
import { findMatches } from "../lib/findMatches";
import { applyHighlights } from "../lib/highlightMatches";
import { scrollToOccurrence } from "../lib/scrollToMatch";
import { isValidRegex } from "../lib/matcher";
import { LazyMarkdownSection } from "./LazyMarkdownSection";
import { DocSearchBar } from "./DocSearchBar";
import { Markdown } from "./Markdown";
import { FootnoteList } from "./FootnoteList";
import { PdfViewer } from "./PdfViewer";
import { CitationList } from "./CitationList";
import { extractFootnotes } from "../lib/footnotes";

const partyNames = (ps: Party[]) => ps.map((p) => p.name).join(", ");

/** Icelandic labels for the appeal relations under "Tengd mál". An unknown
 * relation is shown verbatim rather than dropped — the API may grow a new one
 * before this map does, and a raw name still tells the reader something. */
const RELATION_LABELS: Record<string, string> = {
  appealed_to: "Áfrýjað til",
  appealed_from: "Áfrýjað frá",
  leyfisbeidni_um: "Málskotsbeiðni um",
  leiddi_til_doms: "Leiddi til dóms",
};

type Direction = "out" | "in";
/** Matches the page size the API uses for the copy embedded in /api/document/:id. */
const CITATIONS_PAGE_SIZE = 50;
const NO_EXTRA: Record<Direction, { items: CitationRef[]; page: number }> = {
  out: { items: [], page: 1 },
  in: { items: [], page: 1 },
};

/** Shown when a document has no readable body. About a third of Skemman theses
 * are embargoed at the source, so a blank page here is expected far too often to
 * leave unexplained — without this the reader can't tell a restriction from a bug. */
function UnavailableNotice({ doc }: { doc: DocumentDetail }) {
  return (
    <section className="rounded-md border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
      {doc.locked ? (
        <>
          <p className="font-semibold text-slate-700">Textinn er ekki aðgengilegur</p>
          <p className="mt-1">
            Skjalið er læst hjá útgefanda
            {doc.embargo_until ? ` til ${doc.embargo_until}` : ""}. Aðeins lýsigögn
            (titill, höfundur, útdráttur) eru aðgengileg hér.
          </p>
        </>
      ) : (
        <>
          <p className="font-semibold text-slate-700">Enginn texti fylgir þessu skjali</p>
          <p className="mt-1">Ekki tókst að sækja texta skjalsins frá útgefanda.</p>
        </>
      )}
      {doc.url && (
        <p className="mt-2">
          <a
            href={doc.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-indigo-700 hover:underline"
          >
            Skoða hjá útgefanda ↗
          </a>
        </p>
      )}
    </section>
  );
}

export function DocPanel({ doc }: { doc: DocumentDetail }) {
  const raw = doc.markdown ?? doc.body_text ?? "";
  // Footnotes are lifted out before splitting so they can be gathered in one
  // list at the end of the document instead of trailing each lazy section.
  const { body: content, notes } = useMemo(() => extractFootnotes(raw), [raw]);
  const isLargeDoc = content.length > LARGE_DOC_THRESHOLD;
  const isSearchable = content.length > SEARCHABLE_THRESHOLD;

  // Lazily-rendered documents must be searched segment-by-segment, since a match
  // may sit in a section that isn't mounted yet. Everything else is fully in the
  // DOM already, so one "segment" spanning the whole document is enough — that
  // keeps a single matching/scrolling code path for both cases.
  const segments = useMemo(() => {
    if (isLargeDoc) return splitMarkdown(content);
    return isSearchable ? [content] : [];
  }, [content, isLargeDoc, isSearchable]);

  const [viewMode, setViewMode] = useState<"texti" | "pdf">("texti");
  const [query, setQuery] = useState("");
  const [useRegex, setUseRegex] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [forcedSegments, setForcedSegments] = useState<Set<number>>(new Set());
  const bodyRef = useRef<HTMLElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // The document response carries the first page of both citation directions;
  // "Sýna fleiri" fetches the next one and appends it here. Keyed on doc.id so
  // navigating to another document starts from its own first page again.
  const [extra, setExtra] = useState(NO_EXTRA);
  useEffect(() => setExtra(NO_EXTRA), [doc.id]);

  const citationsOut = [...doc.citations_out, ...extra.out.items];
  const citedBy = [...doc.cited_by, ...extra.in.items];

  const loadMoreCitations = async (dir: Direction) => {
    const page = extra[dir].page + 1;
    try {
      const res = await fetchCitations(doc.id, dir, page, CITATIONS_PAGE_SIZE);
      setExtra((prev) => ({
        ...prev,
        [dir]: { items: [...prev[dir].items, ...res.items], page },
      }));
    } catch {
      // A failed page leaves the list as it was, with the button still there to
      // retry; a half-read citation list is not worth an error banner.
    }
  };

  // An appeal-chain citation is shown once, in "Tilvitnanir" with the
  // „(í áfrýjunarkeðju)“ badge (spec §8.2) — repeating it under "Tengd mál"
  // would make one relationship look like two.
  const shownAsCitation = new Set(
    [...citationsOut, ...citedBy].filter((c) => c.also_appeal).map((c) => c.document_id),
  );
  const relatedLinks = doc.appeal_links.filter((l) => !shownAsCitation.has(l.document_id));
  const hasCitations =
    doc.citations_out_total > 0 || doc.cited_by_total > 0 || doc.citations_unresolved_total > 0;

  const regexValid = !useRegex || query.trim().length === 0 || isValidRegex(query);
  const matches = useMemo(
    () => findMatches(segments, query, useRegex),
    [segments, query, useRegex],
  );

  useEffect(() => {
    setActiveIndex(0);
  }, [query, useRegex, segments]);

  useEffect(() => {
    const segIdx = matches[activeIndex]?.segmentIndex;
    if (segIdx === undefined) return;
    setForcedSegments((prev) => (prev.has(segIdx) ? prev : new Set(prev).add(segIdx)));
  }, [activeIndex, matches]);

  // Separate effect: runs after the render where forcedSegments actually
  // included segIdx, so the section's real content (not the placeholder) is
  // guaranteed to be in the DOM by the time this queries it.
  useEffect(() => {
    const segIdx = matches[activeIndex]?.segmentIndex;
    if (segIdx === undefined || !forcedSegments.has(segIdx)) return;
    // Non-lazy documents have no per-segment element; the whole body is one
    // container and the per-segment occurrence index is already the global one.
    const container = document.getElementById(`doc-segment-${segIdx}`) ?? bodyRef.current;
    const scrollContainer = scrollContainerRef.current;
    if (!container || !scrollContainer) return;
    const occurrenceInSegment = matches
      .slice(0, activeIndex)
      .filter((m) => m.segmentIndex === segIdx).length;
    scrollToOccurrence(container, query, occurrenceInSegment, scrollContainer, useRegex);
  }, [forcedSegments, activeIndex, matches, query, useRegex]);

  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    applyHighlights(el, query, useRegex);
    const mo = new MutationObserver(() => applyHighlights(el, query, useRegex));
    mo.observe(el, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, [query, useRegex, forcedSegments]);

  const goToNext = () =>
    matches.length > 0 && setActiveIndex((i) => (i + 1) % matches.length);
  const goToPrev = () =>
    matches.length > 0 && setActiveIndex((i) => (i - 1 + matches.length) % matches.length);

  return (
    <div ref={scrollContainerRef} className="bg-[#f5f7fb] flex-1 overflow-y-auto py-8">
      <article className="mx-auto max-w-2xl bg-white rounded-lg p-8 shadow-sm">
        <header className="text-center space-y-1 mb-6">
          {doc.case_number_is_title ? (
            // A thesis or book is identified by its own title, not by the
            // repository it was collected from — so the title leads and the
            // source drops to a muted label above it.
            <>
              <div className="text-sm uppercase tracking-wide text-slate-500">
                {doc.source_display}
              </div>
              {doc.case_number && (
                // No uppercase: titles run long and full caps hurts readability.
                <h1 className="text-2xl font-bold">{doc.case_number}</h1>
              )}
            </>
          ) : (
            <>
              <h1 className="text-2xl font-bold uppercase">{doc.source_display}</h1>
              {doc.case_number && (
                <div className="font-semibold">Mál nr. {doc.case_number}</div>
              )}
            </>
          )}
          {doc.document_date && (
            <div className="text-slate-600">
              {doc.case_number_is_title
                ? doc.document_date.slice(0, 4)
                : doc.document_date}
            </div>
          )}
          {doc.plaintiffs.length > 0 && (
            <div className="font-semibold pt-2">{partyNames(doc.plaintiffs)}</div>
          )}
          {/* "gegn" only makes sense between opposing parties — for theses and
              books the same column holds authors, who are not adversaries. */}
          {doc.defendants.length > 0 && !doc.case_number_is_title && (
            <>
              <div className="text-slate-600">gegn</div>
              <div className="font-semibold">{partyNames(doc.defendants)}</div>
            </>
          )}
        </header>

        {doc.keywords.length > 0 && (
          <section className="mb-6">
            <h2 className="font-bold mb-2">Lykilorð</h2>
            <div className="flex flex-wrap gap-1.5">
              {doc.keywords.map((k) => (
                <span
                  key={k}
                  className="text-sm text-slate-600 bg-slate-100 rounded-full px-3 py-0.5"
                >
                  {k}
                </span>
              ))}
            </div>
          </section>
        )}

        {doc.summary && (
          <section className="mb-6">
            <h2 className="font-bold mb-2">Reifun</h2>
            <p className="italic text-slate-800 leading-relaxed">{doc.summary}</p>
          </section>
        )}

        {doc.has_pdf && (
          // Markdown reconstruction can lose headers, footers and footnote
          // placement on long documents — "PDF" falls back to the original
          // page image instead of trying to fix that in markdown.
          <div className="mb-6 flex gap-1 rounded-md border border-slate-200 bg-slate-50 p-1 text-sm">
            {(["texti", "pdf"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setViewMode(mode)}
                aria-pressed={viewMode === mode}
                className={`flex-1 rounded px-3 py-1 ${
                  viewMode === mode ? "bg-white shadow-sm font-semibold" : "text-slate-500 hover:bg-slate-100"
                }`}
              >
                {mode === "texti" ? "Texti" : "PDF"}
              </button>
            ))}
          </div>
        )}

        {viewMode === "pdf" ? (
          <PdfViewer url={documentPdfUrl(doc.id)} />
        ) : (
          <>
            {isSearchable && (
              <DocSearchBar
                query={query}
                onQueryChange={setQuery}
                useRegex={useRegex}
                onUseRegexChange={setUseRegex}
                regexValid={regexValid}
                matchCount={matches.length}
                activeIndex={activeIndex}
                onNext={goToNext}
                onPrev={goToPrev}
              />
            )}

            <section
              ref={bodyRef}
              className="prose prose-slate max-w-none prose-headings:font-bold prose-headings:text-base"
            >
              {isLargeDoc ? (
                segments.map((segment, i) => (
                  <LazyMarkdownSection
                    key={i}
                    id={`doc-segment-${i}`}
                    text={segment}
                    forceVisible={forcedSegments.has(i)}
                  />
                ))
              ) : (
                <Markdown>{content}</Markdown>
              )}
            </section>

            {/* Keyed off the real body, not `content`: the API still renders a short
                metadata stub into `markdown` (title, date, source link) for documents
                that have no text at all, so `content` is never empty here. */}
            <FootnoteList notes={notes} />

            {!doc.body_text && !doc.lower_body_text && <UnavailableNotice doc={doc} />}
          </>
        )}

        {relatedLinks.length > 0 && (
          <section className="mt-8 border-t border-slate-200 pt-4">
            <h2 className="font-bold mb-2">Tengd mál</h2>
            <ul className="space-y-1">
              {relatedLinks.map((l) => (
                <li key={l.document_id} className="text-sm">
                  <span className="text-slate-500">
                    {RELATION_LABELS[l.relation] ?? l.relation}:{" "}
                  </span>
                  <Link
                    to={`/domur/${l.document_id}`}
                    className="text-indigo-700 hover:underline"
                  >
                    {l.urlausn}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        {hasCitations && (
          <section className="mt-8 border-t border-slate-200 pt-4">
            <h2 className="font-bold mb-2">Tilvitnanir</h2>
            {doc.citations_out_total > 0 && (
              <CitationList
                title="Vitnar í"
                items={citationsOut}
                total={doc.citations_out_total}
                onMore={() => loadMoreCitations("out")}
              />
            )}
            {doc.cited_by_total > 0 && (
              <CitationList
                title="Vitnað í þennan dóm"
                items={citedBy}
                total={doc.cited_by_total}
                onMore={() => loadMoreCitations("in")}
              />
            )}
            {doc.citations_unresolved_total > 0 && (
              // Citations the resolver could not match to a document in the
              // corpus: said plainly, so an incomplete list doesn't read as a
              // complete one.
              <p className="mt-2 text-xs text-slate-500">
                {doc.citations_unresolved_total === 1
                  ? "1 tilvitnun fannst ekki í safninu"
                  : `${doc.citations_unresolved_total} tilvitnanir fundust ekki í safninu`}
              </p>
            )}
          </section>
        )}
      </article>
    </div>
  );
}
