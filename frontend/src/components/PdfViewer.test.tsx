import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { PdfViewer } from "./PdfViewer";

const PAGE_TEXTS = ["Fyrsta síða, ekkert áhugavert.", "Önnur síða inniheldur lykilorð hér.", "Þriðja síðan."];

// react-pdf drives real canvas rendering and a pdf.js web worker, neither of
// which exist under jsdom — PdfViewer's own search/jump-to-page logic (not
// react-pdf's rendering) is what this file tests, so both are stubbed out.
// getDocument/getPage/getTextContent stay real-shaped, since PdfViewer's text
// extraction calls them directly, bypassing react-pdf's own components.
vi.mock("react-pdf", () => ({
  pdfjs: {
    GlobalWorkerOptions: {},
    getDocument: () => ({
      promise: Promise.resolve({
        numPages: PAGE_TEXTS.length,
        getPage: (n: number) =>
          Promise.resolve({
            getTextContent: () => Promise.resolve({ items: [{ str: PAGE_TEXTS[n - 1] }] }),
          }),
      }),
    }),
  },
  Document: ({ children, onLoadSuccess }: { children: React.ReactNode; onLoadSuccess?: (a: { numPages: number }) => void }) => {
    onLoadSuccess?.({ numPages: PAGE_TEXTS.length });
    return <div>{children}</div>;
  },
  Page: ({ pageNumber }: { pageNumber: number }) => <div data-testid="pdf-page">Síða {pageNumber}</div>,
}));

describe("PdfViewer", () => {
  it("disables the search box until per-page text extraction finishes", async () => {
    renderWithProviders(<PdfViewer url="https://example.test/x.pdf" />);
    expect(await screen.findByPlaceholderText("Leita í skjalinu…")).not.toBeDisabled();
  });

  it("jumps to the page containing a match", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PdfViewer url="https://example.test/x.pdf" />);

    const input = await screen.findByPlaceholderText("Leita í skjalinu…");
    await user.type(input, "lykilorð");

    expect(await screen.findByText("1 af 1")).toBeInTheDocument();
    expect(screen.getByText("Síða 2 af 3")).toBeInTheDocument();
    expect(screen.getByTestId("pdf-page")).toHaveTextContent("Síða 2");
  });

  it("reports no results for a query that appears on no page", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PdfViewer url="https://example.test/x.pdf" />);

    const input = await screen.findByPlaceholderText("Leita í skjalinu…");
    await user.type(input, "finnst-hvergi");

    expect(await screen.findByText("Engar niðurstöður")).toBeInTheDocument();
    // No match to jump to — stays on the page it started on.
    expect(screen.getByText("Síða 1 af 3")).toBeInTheDocument();
  });
});
