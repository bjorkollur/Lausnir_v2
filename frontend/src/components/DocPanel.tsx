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
import { DocOutline, type OutlineEntry } from "./DocOutline";
import { formatIcelandicDate } from "../lib/formatDate";
import { stripDocumentPreamble } from "../lib/stripPreamble";
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
    <section className="rounded-md border border-border bg-canvas p-4 text-sm text-ink-soft">
      {doc.locked ? (
        <>
          <p className="font-semibold text-ink">Textinn er ekki aðgengilegur</p>
          <p className="mt-1">
            Skjalið er læst hjá útgefanda
            {doc.embargo_until ? ` til ${doc.embargo_until}` : ""}. Aðeins lýsigögn
            (titill, höfundur, útdráttur) eru aðgengileg hér.
          </p>
        </>
      ) : (
        <>
          <p className="font-semibold text-ink">Enginn texti fylgir þessu skjali</p>
          <p className="mt-1">Ekki tókst að sækja texta skjalsins frá útgefanda.</p>
        </>
      )}
      {doc.url && (
        <p className="mt-2">
          <a
            href={doc.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent hover:underline"
          >
            Skoða hjá útgefanda ↗
          </a>
        </p>
      )}
    </section>
  );
}

export function DocPanel({ doc }: { doc: DocumentDetail }) {
  // The generated markdown repeats the court, date, parties, keywords and
  // reifun that the header above already shows, with the source URL as its
  // first line. Those come off before anything is rendered.
  const raw = doc.markdown ? stripDocumentPreamble(doc.markdown) : (doc.body_text ?? "");
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

  // Outline jumps use the same two-step as the search scroller: force the
  // segment, then scroll once the real content has replaced the placeholder.
  const [pendingHeading, setPendingHeading] = useState<OutlineEntry | null>(null);
  const [activeHeading, setActiveHeading] = useState<string | null>(null);

  const jumpToHeading = (entry: OutlineEntry) => {
    setActiveHeading(entry.text);
    setForcedSegments((prev) => (prev.has(entry.segmentIndex) ? prev : new Set(prev).add(entry.segmentIndex)));
    setPendingHeading(entry);
  };

  useEffect(() => {
    if (!pendingHeading) return;
    if (isLargeDoc && !forcedSegments.has(pendingHeading.segmentIndex)) return;
    const root = bodyRef.current;
    if (!root) return;
    const target = Array.from(root.querySelectorAll("h1, h2, h3")).find(
      (h) => h.textContent?.trim() === pendingHeading.text,
    );
    target?.scrollIntoView({ block: "start", behavior: "auto" });
    setPendingHeading(null);
  }, [pendingHeading, forcedSegments, isLargeDoc]);

  const goToNext = () =>
    matches.length > 0 && setActiveIndex((i) => (i + 1) % matches.length);
  const goToPrev = () =>
    matches.length > 0 && setActiveIndex((i) => (i - 1 + matches.length) % matches.length);

  const dateLabel = doc.case_number_is_title
    ? doc.document_date?.slice(0, 4)
    : formatIcelandicDate(doc.document_date);

  // One heading line that names the document: court plus case number. The court
  // used to sit above it as a small-caps kicker, which is decoration where the
  // heading can simply carry the whole identity.
  const heading = doc.case_number_is_title
    ? doc.case_number
    : [doc.source_display, doc.case_number && `mál nr. ${doc.case_number}`]
        .filter(Boolean)
        .join(", ");

  return (
    <div ref={scrollContainerRef} className="flex-1 scroll-pt-16 overflow-y-auto bg-canvas">
      <div className="mx-auto grid max-w-[1400px] grid-cols-1 gap-x-12 gap-y-10 px-6 py-10 lg:grid-cols-[minmax(0,1fr)_17rem] xl:grid-cols-[12rem_minmax(0,1fr)_17rem]">
        <DocOutline segments={segments} activeText={activeHeading} onJump={jumpToHeading} />

        <article className="min-w-0 max-w-[44rem]">
          {/* Left-aligned, in reading order: what it is, then which case, then
              when. The old header centred six stacked lines, which reads as a
              title page rather than as the head of a document. */}
          <header className="border-b border-border pb-6">
            {/* One h1 that names the document: the court in small caps above,
                the case number carrying the size. Naming the parties by role
                was tried and reverted — "Sóknaraðili / Varnaraðili" is only
                right for kærumál, while an appealed case has an áfrýjandi and a
                stefndi. "gegn" is correct for both. */}
            {/* A thesis is identified by its own title, so the repository it was
                collected from stays outside the heading. A ruling is identified
                by court plus case number, so the court belongs inside it. */}
            {doc.case_number_is_title && (
              <p className="text-micro font-medium uppercase tracking-[0.12em] text-ink-faint">
                {doc.source_display}
              </p>
            )}
            <h1 className={`font-serif text-title text-ink ${doc.case_number_is_title ? "mt-2" : ""}`}>
              {heading}
            </h1>
            <p className="mt-1 text-meta text-ink-soft">
              {[dateLabel, doc.case_number_is_title ? null : doc.verdict_type]
                .filter(Boolean)
                .join(" · ")}
            </p>

            {doc.plaintiffs.length > 0 && (
              <div className="mt-5 space-y-1 text-meta">
                <p className="text-ink">{partyNames(doc.plaintiffs)}</p>
                {/* "gegn" only makes sense between opposing parties — for theses
                    and books the same column holds authors, who are not
                    adversaries. */}
                {doc.defendants.length > 0 && !doc.case_number_is_title && (
                  <>
                    <p className="text-ink-faint">gegn</p>
                    <p className="text-ink">{partyNames(doc.defendants)}</p>
                  </>
                )}
              </div>
            )}

            {doc.keywords.length > 0 && (
              <ul className="mt-5 flex flex-wrap gap-1.5">
                {doc.keywords.map((k) => (
                  <li
                    key={k}
                    className="rounded border border-border px-2 py-0.5 text-micro text-ink-soft"
                  >
                    {k}
                  </li>
                ))}
              </ul>
            )}
          </header>

          {doc.summary && (
            <section className="border-b border-border py-6">
              <h2 className="mb-2 text-micro font-medium uppercase tracking-[0.12em] text-ink-faint">
                Reifun
              </h2>
              <p className="font-serif text-[1.0625rem] leading-relaxed text-ink-soft">
                {doc.summary}
              </p>
            </section>
          )}

          {doc.has_pdf && (
            // Markdown reconstruction can lose headers, footers and footnote
            // placement on long documents — "PDF" falls back to the original
            // page image instead of trying to fix that in markdown.
            <div className="mt-6 inline-flex gap-1 rounded border border-border p-0.5 text-meta">
              {(["texti", "pdf"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setViewMode(mode)}
                  aria-pressed={viewMode === mode}
                  className={`rounded px-3 py-1 ${
                    viewMode === mode
                      ? "bg-cta text-cta-ink"
                      : "text-ink-soft hover:bg-surface-sunken hover:text-ink"
                  }`}
                >
                  {mode === "texti" ? "Texti" : "PDF"}
                </button>
              ))}
            </div>
          )}

          {viewMode === "pdf" ? (
            <div className="mt-6">
              <PdfViewer url={documentPdfUrl(doc.id)} />
            </div>
          ) : (
            <>
              {isSearchable && (
                <div className="mt-6">
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
                </div>
              )}

              <section
                ref={bodyRef}
                className="prose-document prose mt-8 max-w-none prose-headings:font-sans prose-headings:text-heading prose-headings:font-semibold prose-headings:text-ink prose-p:text-ink prose-strong:text-ink prose-a:text-accent"
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

              {!doc.body_text && !doc.lower_body_text && (
                <div className="mt-6">
                  <UnavailableNotice doc={doc} />
                </div>
              )}
            </>
          )}
        </article>

        {/* Related cases and citations used to sit at the very bottom, past a
            judgment that can run to 40.000 characters. They are navigation, so
            they belong beside the text. */}
        {(relatedLinks.length > 0 || hasCitations) && (
          <aside className="min-w-0 lg:border-l lg:border-border lg:pl-8">
            <div className="lg:sticky lg:top-24 lg:max-h-[calc(100dvh-8rem)] lg:overflow-y-auto">
              {relatedLinks.length > 0 && (
                <section className="mb-8">
                  <h2 className="mb-3 text-micro font-medium uppercase tracking-[0.12em] text-ink-faint">
                    Tengd mál
                  </h2>
                  <ul className="space-y-2">
                    {relatedLinks.map((l) => (
                      <li key={l.document_id} className="text-meta">
                        <span className="text-ink-faint">
                          {RELATION_LABELS[l.relation] ?? l.relation}:
                        </span>
                        <br />
                        <Link
                          to={`/domur/${l.document_id}`}
                          className="text-accent hover:underline underline-offset-2"
                        >
                          {l.urlausn}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {hasCitations && (
                <section>
                  <h2 className="mb-3 text-micro font-medium uppercase tracking-[0.12em] text-ink-faint">
                    Tilvitnanir
                  </h2>
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
                    <p className="mt-2 text-micro text-ink-faint">
                      {doc.citations_unresolved_total === 1
                        ? "1 tilvitnun fannst ekki í safninu"
                        : `${doc.citations_unresolved_total} tilvitnanir fundust ekki í safninu`}
                    </p>
                  )}
                </section>
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
