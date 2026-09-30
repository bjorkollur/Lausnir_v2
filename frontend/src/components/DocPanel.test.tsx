import { describe, it, expect, vi, beforeAll, afterAll, afterEach } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { DocPanel } from "./DocPanel";
import { server, http, HttpResponse } from "../test/msw";
import type { CitationRef, DocumentDetail } from "../api/types";
import { LARGE_DOC_THRESHOLD, SEARCHABLE_THRESHOLD } from "../lib/splitMarkdown";

// react-pdf drives pdf.js canvas rendering and a web worker — neither exists under
// jsdom, and DocPanel's own branching logic (not react-pdf's internals) is what
// this file tests, so the real viewer is swapped for a stub that proves it was
// mounted with the right URL.
vi.mock("./PdfViewer", () => ({
  PdfViewer: ({ url }: { url: string }) => <div data-testid="pdf-viewer">{url}</div>,
}));

// Typed explicitly: an untyped fixture silently drifts from the real API shape,
// and `tsc -b` is the only thing that catches it (plain `tsc --noEmit` on the
// root config compiles nothing — it is `files: []` plus project references).
const doc: DocumentDetail = {
  case_number_is_title: false,
  locked: null,
  embargo_until: null,
  has_pdf: false,
  id: "a", source: "haestirettur", source_display: "Hæstiréttur", external_id: "x", url: "https://island.is/domar/x",
  urlausn: "Hrd. 59/2025 – Dómur", court: "Hrd.", case_number: "59/2025", document_date: "2026-06-10",
  verdict_type: "Dómur", instance_tier: 3, case_type: "Einkamál",
  plaintiffs: [{ name: "A", lawyer: null }], defendants: [{ name: "B", lawyer: null }],
  keywords: ["Börn", "Barnavernd"], summary: "Reifun hér", body_text: "## Dómsorð\nTexti",
  lower_body_text: null, appeal_links: [{ relation: "appealed_to", confidence: 1, method: "resolution_link",
    document_id: "b", source: "landsrettur", urlausn: "Lrd. 1/2024 – Dómur" }],
  markdown: "## Dómsorð\nTexti",
  citations_out: [], citations_out_total: 0,
  cited_by: [], cited_by_total: 0,
  citations_unresolved_total: 0,
};

const cite = (over: Partial<CitationRef> = {}): CitationRef => ({
  document_id: "c1", urlausn: "Hrd. 10/2019 – Dómur", source: "haestirettur",
  document_date: "2019-05-05", layer: "body", passage_id: null, anchor: null,
  raw_text: "dómi Hæstaréttar í máli nr. 10/2019", confidence: 0.9,
  also_appeal: false, same_case: false, ...over,
});

/** The block CitationList renders: heading plus its own list and button. */
const listOf = (name: string) =>
  within(screen.getByRole("heading", { name }).parentElement!);

describe("DocPanel", () => {
  it("renders keywords, reifun, body heading and appeal link", () => {
    renderWithProviders(<DocPanel doc={doc} />);
    expect(screen.getByText("Börn")).toBeInTheDocument();
    expect(screen.getByText("Reifun hér")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Dómsorð" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Lrd\. 1\/2024/ })).toHaveAttribute("href", "/domur/b");
  });
});

function words(n: number, wordsPerParagraph = 50): string {
  const paragraphs: string[] = [];
  for (let i = 0; i < n; i += wordsPerParagraph) {
    const count = Math.min(wordsPerParagraph, n - i);
    const para = Array.from({ length: count }, (_, j) => `word${i + j}`).join(" ");
    paragraphs.push(para);
  }
  return paragraphs.join("\n\n");
}

describe("DocPanel with a large (book-length) document", () => {
  it("renders lazy placeholder sections instead of one direct ReactMarkdown call", () => {
    const bigMarkdown = "## Upphafskafli\n\n" + words(Math.ceil(LARGE_DOC_THRESHOLD / 5));
    const bigDoc = { ...doc, markdown: bigMarkdown, body_text: bigMarkdown };
    const { container } = renderWithProviders(<DocPanel doc={bigDoc} />);

    // The project-wide no-op IntersectionObserver stub (src/test/setup.ts) never
    // fires, so every section stays an aria-hidden placeholder — proving the large
    // document takes the lazy path instead of rendering everything immediately.
    expect(screen.queryByRole("heading", { name: "Upphafskafli" })).not.toBeInTheDocument();
    const placeholders = container.querySelectorAll('[id^="doc-segment-"][aria-hidden="true"]');
    expect(placeholders.length).toBeGreaterThan(1);
  });
});

describe("DocPanel search", () => {
  it("does not render a search bar for short documents", () => {
    renderWithProviders(<DocPanel doc={doc} />);
    expect(screen.queryByLabelText("Leita í skjalinu")).not.toBeInTheDocument();
  });

  it("renders a search bar for book-length documents", () => {
    const bigMarkdown = "## Upphafskafli\n\n" + words(Math.ceil(LARGE_DOC_THRESHOLD / 5));
    const bigDoc = { ...doc, markdown: bigMarkdown, body_text: bigMarkdown };
    renderWithProviders(<DocPanel doc={bigDoc} />);
    expect(screen.getByLabelText("Leita í skjalinu")).toBeInTheDocument();
  });

  it("jumps to and renders a match hidden inside a later, not-yet-visible lazy section", async () => {
    const user = userEvent.setup();
    const totalWords = Math.ceil(LARGE_DOC_THRESHOLD / 5);
    const half = Math.floor(totalWords / 2);
    const bigMarkdown =
      "## Upphafskafli\n\n" +
      words(half) +
      "\n\nleitarordeitt er fundið hér\n\n" +
      words(totalWords - half, 50);
    const bigDoc = { ...doc, markdown: bigMarkdown, body_text: bigMarkdown };
    renderWithProviders(<DocPanel doc={bigDoc} />);

    // Buried past the first ~500-word segment — stays an unrendered placeholder
    // until the search jump forces it visible.
    expect(screen.queryByText(/leitarordeitt/)).not.toBeInTheDocument();

    const input = screen.getByLabelText("Leita í skjalinu");
    await user.type(input, "leitarordeitt");

    expect(await screen.findByText(/leitarordeitt/)).toBeInTheDocument();
    expect(screen.getByText("1 af 1")).toBeInTheDocument();
  });

  it("offers search on a mid-length document without switching to lazy rendering", () => {
    // Between the two thresholds: long enough to be worth searching, short enough
    // that chunked rendering would be pointless overhead.
    const midMarkdown = "## Kafli\n\n" + words(Math.ceil(SEARCHABLE_THRESHOLD / 5) + 400);
    expect(midMarkdown.length).toBeGreaterThan(SEARCHABLE_THRESHOLD);
    expect(midMarkdown.length).toBeLessThan(LARGE_DOC_THRESHOLD);

    const midDoc = { ...doc, markdown: midMarkdown, body_text: midMarkdown };
    const { container } = renderWithProviders(<DocPanel doc={midDoc} />);

    expect(screen.getByLabelText("Leita í skjalinu")).toBeInTheDocument();
    // Rendered eagerly — the heading is present and there are no lazy placeholders.
    expect(screen.getByRole("heading", { name: "Kafli" })).toBeInTheDocument();
    expect(container.querySelectorAll('[id^="doc-segment-"][aria-hidden="true"]')).toHaveLength(0);
  });

  it("counts matches in a mid-length, eagerly-rendered document", async () => {
    const user = userEvent.setup();
    const midMarkdown =
      "## Kafli\n\n" + words(Math.ceil(SEARCHABLE_THRESHOLD / 5)) + "\n\nsérstaktleitarord hér\n\n";
    const midDoc = { ...doc, markdown: midMarkdown, body_text: midMarkdown };
    renderWithProviders(<DocPanel doc={midDoc} />);

    await user.type(screen.getByLabelText("Leita í skjalinu"), "sérstaktleitarord");
    expect(await screen.findByText("1 af 1")).toBeInTheDocument();
  });

  it("shows no results for a query that doesn't appear in the text", async () => {
    const user = userEvent.setup();
    const bigMarkdown = "## Upphafskafli\n\n" + words(Math.ceil(LARGE_DOC_THRESHOLD / 5));
    const bigDoc = { ...doc, markdown: bigMarkdown, body_text: bigMarkdown };
    renderWithProviders(<DocPanel doc={bigDoc} />);

    const input = screen.getByLabelText("Leita í skjalinu");
    await user.type(input, "hvergitil");

    expect(await screen.findByText("Engar niðurstöður")).toBeInTheDocument();
  });
});

describe("DocPanel for theses and books (case_number_is_title)", () => {
  const thesis: DocumentDetail = {
    ...doc,
    source: "logfraediritgerdir",
    source_display: "Lögfræðiritgerðir (Skemman)",
    case_number_is_title: true,
    case_number: "NATO og netárásir",
    document_date: "2026-06-22",
    plaintiffs: [{ name: "Snæbjört Pálsdóttir", lawyer: null }],
    defendants: [],
    verdict_type: "Ritgerð",
  };

  it("shows the title plainly instead of labelling it a case number", () => {
    renderWithProviders(<DocPanel doc={thesis} />);
    expect(screen.getByText("NATO og netárásir")).toBeInTheDocument();
    expect(screen.queryByText(/Mál nr\./)).not.toBeInTheDocument();
  });

  it("leads with the title as the heading, not the source repository", () => {
    renderWithProviders(<DocPanel doc={thesis} />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("NATO og netárásir");
    // The repository still appears, but demoted out of the heading.
    expect(heading).not.toHaveTextContent("Skemman");
    expect(screen.getByText("Lögfræðiritgerðir (Skemman)")).toBeInTheDocument();
  });

  it("keeps the court as the heading on a court ruling", () => {
    renderWithProviders(<DocPanel doc={doc} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Hæstiréttur");
  });

  it("shows only the year, not a raw ISO date", () => {
    renderWithProviders(<DocPanel doc={thesis} />);
    expect(screen.getByText("2026")).toBeInTheDocument();
    expect(screen.queryByText("2026-06-22")).not.toBeInTheDocument();
  });

  it("never renders 'gegn' between authors", () => {
    const twoAuthors = {
      ...thesis,
      defendants: [{ name: "Annar Höfundur", lawyer: null }],
    };
    renderWithProviders(<DocPanel doc={twoAuthors} />);
    expect(screen.queryByText("gegn")).not.toBeInTheDocument();
  });

  it("still labels a real case number on a court ruling", () => {
    // Court and case number share one heading line. They used to be stacked,
    // with the court as a small-caps label above the number.
    renderWithProviders(<DocPanel doc={doc} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Hæstiréttur, mál nr. 59/2025",
    );
    expect(screen.getByText("gegn")).toBeInTheDocument();
  });
});

describe("DocPanel when the body text is unavailable", () => {
  const empty: DocumentDetail = {
    ...doc,
    source: "logfraediritgerdir",
    case_number_is_title: true,
    body_text: null,
    lower_body_text: null,
    // The API renders a metadata stub into `markdown` even when there is no body
    // text, so this must be non-null here or the fixture wouldn't reproduce the
    // real case — an earlier version keyed the notice off the rendered content and
    // passed this test while showing nothing at all in the browser.
    markdown:
      "[https://skemman.is/handle/1946/54186](https://skemman.is/handle/1946/54186)\n\n" +
      "**Lögfræðiritgerðir (Skemman) – Ritgerð – NATO og netárásir**\n\n**22. júní 2026**",
    summary: null,
    url: "https://skemman.is/handle/1946/54186",
  };

  it("explains an embargo instead of showing a blank page", () => {
    renderWithProviders(<DocPanel doc={{ ...empty, locked: true, embargo_until: "01.01.2035" }} />);
    expect(screen.getByText("Textinn er ekki aðgengilegur")).toBeInTheDocument();
    expect(screen.getByText(/01\.01\.2035/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Skoða hjá útgefanda/ })).toHaveAttribute(
      "href",
      "https://skemman.is/handle/1946/54186",
    );
  });

  it("omits the embargo date when the source didn't report one", () => {
    renderWithProviders(<DocPanel doc={{ ...empty, locked: true, embargo_until: null }} />);
    expect(screen.getByText(/læst hjá útgefanda\./)).toBeInTheDocument();
  });

  it("reports a plain fetch failure when the document is not locked", () => {
    renderWithProviders(<DocPanel doc={{ ...empty, locked: false }} />);
    expect(screen.getByText("Enginn texti fylgir þessu skjali")).toBeInTheDocument();
  });

  it("shows no notice when the document does have text", () => {
    renderWithProviders(<DocPanel doc={doc} />);
    expect(screen.queryByText("Textinn er ekki aðgengilegur")).not.toBeInTheDocument();
    expect(screen.queryByText("Enginn texti fylgir þessu skjali")).not.toBeInTheDocument();
  });
});

describe("DocPanel footnotes", () => {
  const withNotes = (bodyText: string): DocumentDetail => ({
    ...doc,
    markdown: bodyText,
    body_text: bodyText,
  });

  it("gathers all notes into one list at the end, even when split across sections", () => {
    // Long enough to be rendered as several independently-parsed lazy sections —
    // the case where remark-gfm cannot resolve a reference against a definition
    // that lives in a different section.
    const big =
      `${words(Math.ceil(LARGE_DOC_THRESHOLD / 5))} fyrri[^1]\n\n` +
      `${words(600)} seinni[^2]\n\n` +
      `[^1]: Fyrsta skýring.\n[^2]: Önnur skýring.`;
    renderWithProviders(<DocPanel doc={withNotes(big)} />);

    const list = screen.getByRole("heading", { name: "Neðanmálsgreinar" })
      .parentElement!;
    expect(list).toHaveTextContent("Fyrsta skýring.");
    expect(list).toHaveTextContent("Önnur skýring.");
  });

  it("never leaves raw footnote markup visible", () => {
    const text = `${words(600)} vísun[^3]\n\n[^3]: Skýring.`;
    const { container } = renderWithProviders(<DocPanel doc={withNotes(text)} />);
    expect(container.textContent).not.toMatch(/\[\^\d+\]/);
  });

  it("links a reference to its note and the note back to the reference", () => {
    const text = `Texti[^5] meira\n\n[^5]: Skýring.`;
    const { container } = renderWithProviders(<DocPanel doc={withNotes(text)} />);
    const ref = container.querySelector('a[href="#nmg-5"]');
    expect(ref).not.toBeNull();
    // The note must exist to jump to...
    expect(container.querySelector('#nmg-5')).not.toBeNull();
    // ...and the back-link must have a real target, not a dangling anchor.
    expect(ref).toHaveAttribute('id', 'nmgv-5');
    expect(container.querySelector('a[href="#nmgv-5"]')).not.toBeNull();
  });

  it("renders no footnote section for a document without notes", () => {
    renderWithProviders(<DocPanel doc={doc} />);
    expect(screen.queryByRole("heading", { name: "Neðanmálsgreinar" })).not.toBeInTheDocument();
  });
});

describe("DocPanel PDF view", () => {
  it("shows no Texti/PDF toggle when no PDF is stored", () => {
    renderWithProviders(<DocPanel doc={doc} />);
    expect(screen.queryByRole("button", { name: "PDF" })).not.toBeInTheDocument();
  });

  it("defaults to Texti and switches to the PDF viewer on click", async () => {
    const user = userEvent.setup();
    const withPdf = { ...doc, id: "doc-42", has_pdf: true };
    renderWithProviders(<DocPanel doc={withPdf} />);

    expect(screen.getByRole("heading", { name: "Dómsorð" })).toBeInTheDocument();
    expect(screen.queryByTestId("pdf-viewer")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "PDF" }));

    expect(screen.queryByRole("heading", { name: "Dómsorð" })).not.toBeInTheDocument();
    // The viewer is lazy-loaded, so it arrives a tick after the click.
    expect(await screen.findByTestId("pdf-viewer")).toHaveTextContent("/api/document/doc-42/pdf");

    await user.click(screen.getByRole("button", { name: "Texti" }));
    expect(screen.getByRole("heading", { name: "Dómsorð" })).toBeInTheDocument();
  });
});

describe("DocPanel citations", () => {
  beforeAll(() => server.listen());
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());

  const cited: DocumentDetail = {
    ...doc,
    citations_out: [cite()],
    citations_out_total: 1,
    cited_by: [cite({ document_id: "c2", urlausn: "Lrd. 5/2021 – Dómur", same_case: true,
                      raw_text: "úrskurði Landsréttar í máli nr. 5/2021" })],
    cited_by_total: 3,
  };

  it("renders both citation lists with their totals and the raw citing sentence", () => {
    renderWithProviders(<DocPanel doc={cited} />);
    expect(screen.getByRole("heading", { name: "Tilvitnanir" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Vitnar í (1)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Vitnað í þennan dóm (3)" })).toBeInTheDocument();
    expect(screen.getByText("dómi Hæstaréttar í máli nr. 10/2019")).toBeInTheDocument();
    expect(listOf("Vitnar í (1)").getByRole("link", { name: /Hrd\. 10\/2019/ }))
      .toHaveAttribute("href", "/domur/c1");
    expect(screen.getByText("(sama mál)")).toBeInTheDocument();
  });

  it("offers 'Sýna fleiri' only for the list with more entries than are shown", () => {
    renderWithProviders(<DocPanel doc={cited} />);
    expect(listOf("Vitnar í (1)").queryByRole("button", { name: /^Sýna fleiri/ })).toBeNull();
    expect(listOf("Vitnað í þennan dóm (3)").getByRole("button", { name: /^Sýna fleiri/ }))
      .toBeInTheDocument();
  });

  it("appends the next page when 'Sýna fleiri' is clicked", async () => {
    const user = userEvent.setup();
    let asked: URL | null = null;
    server.use(
      http.get("http://localhost:8077/api/document/a/citations", ({ request }) => {
        asked = new URL(request.url);
        return HttpResponse.json({
          direction: "in", total: 3, page: 2, page_size: 50,
          items: [cite({ document_id: "c3", urlausn: "Hérd. Rvk. E-9/2018 – Dómur",
                         raw_text: "úrskurði héraðsdóms í máli nr. E-9/2018" })],
        });
      }),
    );
    renderWithProviders(<DocPanel doc={cited} />);

    await user.click(
      listOf("Vitnað í þennan dóm (3)").getByRole("button", { name: /^Sýna fleiri/ }),
    );

    expect(await screen.findByRole("link", { name: /E-9\/2018/ })).toHaveAttribute(
      "href", "/domur/c3",
    );
    expect(asked!.searchParams.get("direction")).toBe("in");
    expect(asked!.searchParams.get("page")).toBe("2");
  });

  it("reports unresolved citations in the plural and the singular", () => {
    const { unmount } = renderWithProviders(
      <DocPanel doc={{ ...cited, citations_unresolved_total: 2 }} />,
    );
    expect(screen.getByText("2 tilvitnanir fundust ekki í safninu")).toBeInTheDocument();
    unmount();

    renderWithProviders(<DocPanel doc={{ ...cited, citations_unresolved_total: 1 }} />);
    expect(screen.getByText("1 tilvitnun fannst ekki í safninu")).toBeInTheDocument();
  });

  it("renders no citation section at all when there is nothing to show", () => {
    renderWithProviders(<DocPanel doc={doc} />);
    expect(screen.queryByRole("heading", { name: "Tilvitnanir" })).not.toBeInTheDocument();
  });

  it("labels every known appeal relation, and shows an unknown one verbatim", () => {
    renderWithProviders(<DocPanel doc={{ ...doc, appeal_links: [
      { relation: "leyfisbeidni_um", confidence: 1, method: "m", document_id: "b1",
        source: "haestirettur", urlausn: "Hrd. málsk. 2024-1" },
      { relation: "leiddi_til_doms", confidence: 1, method: "m", document_id: "b2",
        source: "haestirettur", urlausn: "Hrd. 2/2024 – Dómur" },
      { relation: "appealed_from", confidence: 1, method: "m", document_id: "b3",
        source: "landsrettur", urlausn: "Lrd. 3/2023 – Dómur" },
      { relation: "eitthvad_annad", confidence: 1, method: "m", document_id: "b4",
        source: "landsrettur", urlausn: "Lrd. 4/2023 – Dómur" },
    ] }} />);
    expect(screen.getByText("Málskotsbeiðni um:")).toBeInTheDocument();
    expect(screen.getByText("Leiddi til dóms:")).toBeInTheDocument();
    expect(screen.getByText("Áfrýjað frá:")).toBeInTheDocument();
    expect(screen.getByText("eitthvad_annad:")).toBeInTheDocument();
  });

  it("shows an appeal-chain citation once — in Tilvitnanir, not in Tengd mál", () => {
    const { container } = renderWithProviders(<DocPanel doc={{
      ...doc,
      // "b" is doc's appeal link (Lrd. 1/2024) and is also cited in the body.
      citations_out: [cite({ document_id: "b", urlausn: "Lrd. 1/2024 – Dómur",
                             also_appeal: true })],
      citations_out_total: 1,
    }} />);

    expect(container.querySelectorAll('a[href="/domur/b"]')).toHaveLength(1);
    expect(listOf("Vitnar í (1)").getByText("(í áfrýjunarkeðju)")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Tengd mál" })).not.toBeInTheDocument();
  });

  it("still lists appeal links that are not among the citations", () => {
    renderWithProviders(<DocPanel doc={{ ...doc, citations_out: [cite()], citations_out_total: 1 }} />);
    expect(screen.getByRole("heading", { name: "Tengd mál" })).toBeInTheDocument();
    expect(screen.getByText("Áfrýjað til:")).toBeInTheDocument();
  });
});
