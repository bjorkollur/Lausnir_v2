import { useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { findMatches } from "../lib/findMatches";
import { applyHighlights } from "../lib/highlightMatches";
import { isValidRegex } from "../lib/matcher";
import { DocSearchBar } from "./DocSearchBar";

// Loaded from our own build output, never a CDN — the app is meant to run local-only.
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

const PAGE_WIDTH = 640; // fits the article's max-w-2xl (672px) column with a small margin

/** Renders the original PDF page-by-page — exact headers, footers and footnotes,
 * for documents where markdown reconstruction can't be trusted (see DocPanel).
 *
 * Search runs against pdf.js's own per-page text layer, independent of our
 * body_text/markdown extraction — the two can disagree (that's the whole reason
 * this viewer exists), and a search box here must find what's actually on the
 * page, not what our pipeline made of it. */
export function PdfViewer({ url }: { url: string }) {
  const [numPages, setNumPages] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [error, setError] = useState(false);

  // null = extraction still running. One sequential pass over getTextContent()
  // per page — no rendering, so this is far cheaper than the canvas paint the
  // Document/Page components do, and finishes well before the user is likely
  // to have typed a query.
  const [pageTexts, setPageTexts] = useState<string[] | null>(null);
  const [query, setQuery] = useState("");
  const [useRegex, setUseRegex] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const pageContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setPageTexts(null);
    (async () => {
      const pdf = await pdfjs.getDocument(url).promise;
      const texts: string[] = [];
      for (let i = 1; i <= pdf.numPages; i++) {
        if (cancelled) return;
        const page = await pdf.getPage(i);
        const content = await page.getTextContent();
        texts.push(content.items.map((it) => ("str" in it ? it.str : "")).join(" "));
      }
      if (!cancelled) setPageTexts(texts);
    })().catch(() => {
      if (!cancelled) setPageTexts([]);
    });
    return () => {
      cancelled = true;
    };
  }, [url]);

  const regexValid = !useRegex || query.trim().length === 0 || isValidRegex(query);
  const matches = useMemo(
    () => (pageTexts ? findMatches(pageTexts, query, useRegex) : []),
    [pageTexts, query, useRegex],
  );

  useEffect(() => {
    setActiveIndex(0);
  }, [query, useRegex]);

  // A "segment" here is one PDF page — jump the viewer straight to it.
  useEffect(() => {
    const page = matches[activeIndex]?.segmentIndex;
    if (page !== undefined) setPageNumber(page + 1);
  }, [activeIndex, matches]);

  const goToNext = () =>
    matches.length > 0 && setActiveIndex((i) => (i + 1) % matches.length);
  const goToPrev = () =>
    matches.length > 0 && setActiveIndex((i) => (i - 1 + matches.length) % matches.length);

  // Same no-DOM-mutation approach as DocPanel's markdown body: the text layer
  // pdf.js renders is real text nodes, so the CSS Custom Highlight API applies
  // here exactly as it does there. MutationObserver catches both a query edit
  // (rerun on the current page) and a page turn (rerun once the new page's
  // text layer lands in the DOM).
  useEffect(() => {
    const el = pageContainerRef.current;
    if (!el) return;
    applyHighlights(el, query, useRegex);
    const mo = new MutationObserver(() => applyHighlights(el, query, useRegex));
    mo.observe(el, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, [query, useRegex, pageNumber]);

  if (error) {
    return (
      <p className="text-sm text-ink-soft">Ekki tókst að sækja PDF-skjalið.</p>
    );
  }

  return (
    <div className="flex flex-col items-center">
      <div className="w-full max-w-md">
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
          disabled={pageTexts === null}
        />
      </div>
      <div className="mb-4 flex items-center gap-3 text-sm">
        <button
          type="button"
          onClick={() => setPageNumber((p) => Math.max(1, p - 1))}
          disabled={pageNumber <= 1}
          aria-label="Fyrri síða"
          className="rounded px-2 py-1 text-ink-soft hover:bg-border disabled:opacity-30"
        >
          ←
        </button>
        <span className="whitespace-nowrap text-ink-soft">
          Síða {pageNumber} af {numPages ?? "…"}
        </span>
        <button
          type="button"
          onClick={() => setPageNumber((p) => (numPages ? Math.min(numPages, p + 1) : p))}
          disabled={numPages === null || pageNumber >= numPages}
          aria-label="Næsta síða"
          className="rounded px-2 py-1 text-ink-soft hover:bg-border disabled:opacity-30"
        >
          →
        </button>
      </div>
      <Document
        file={url}
        onLoadSuccess={({ numPages: n }) => setNumPages(n)}
        onLoadError={() => setError(true)}
        loading={<p className="text-sm text-ink-soft">Sæki PDF…</p>}
      >
        <div ref={pageContainerRef}>
          <Page pageNumber={pageNumber} width={PAGE_WIDTH} className="shadow-sm" />
        </div>
      </Document>
    </div>
  );
}
