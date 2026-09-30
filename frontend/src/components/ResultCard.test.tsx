import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { ResultCard } from "./ResultCard";
import type { SearchResult } from "../api/types";

const r: SearchResult = {
  id: "abc", urlausn: "Hrd. 48/2022 – Dómur", source: "haestirettur", source_display: "Hæstiréttur",
  court: "Hrd.", case_number: "48/2022", document_date: "2023-03-29", verdict_type: "Dómur",
  keywords: ["Gæsluvarðhald"], plaintiffs: [{ name: "Ríkið", lawyer: null }], defendants: [{ name: "A", lawyer: null }],
  snippet: "texti <mark>gæsluvarðhald</mark> meira", has_appeal_links: true, cited_by_count: 0,
  passage_id: null, anchor: null, section_kind: null, layer: null, match_count: null,
  match_tier: 0,
};

describe("ResultCard", () => {
  it("links to the document and highlights the match in the snippet", () => {
    renderWithProviders(<ResultCard r={r} />);
    expect(screen.getByRole("link", { name: /Hrd\. 48\/2022/ })).toHaveAttribute("href", "/domur/abc");
    expect(document.querySelector("mark")?.textContent).toBe("gæsluvarðhald");
  });

  it("leaves keywords off the result row", () => {
    // Keywords live on the document page. In a list they added a second row of
    // chips in the same treatment as the passage anchor, which made two
    // unrelated kinds of information look alike.
    renderWithProviders(<ResultCard r={r} />);
    expect(screen.queryByText("Gæsluvarðhald")).toBeNull();
  });

  it("shows the passage anchor when present", () => {
    renderWithProviders(<ResultCard r={{ ...r, anchor: "4.–7. mgr.", section_kind: "nidurstada", layer: "body", passage_id: "p1", match_count: 3 }} />);
    expect(screen.getByText("4.–7. mgr.")).toBeInTheDocument();
  });

  it("renders no anchor chip when anchor is null", () => {
    renderWithProviders(<ResultCard r={{ ...r, anchor: null, section_kind: null, layer: null, passage_id: null, match_count: null }} />);
    expect(screen.queryByTestId("passage-anchor")).toBeNull();
  });

  it("shows a match-tier chip for tier 1 (flest orðin)", () => {
    renderWithProviders(<ResultCard r={{ ...r, match_tier: 1 }} />);
    expect(screen.getByTestId("match-tier")).toHaveTextContent("flest orðin");
  });

  it("shows a match-tier chip for tier 2 (sum orðin)", () => {
    renderWithProviders(<ResultCard r={{ ...r, match_tier: 2 }} />);
    expect(screen.getByTestId("match-tier")).toHaveTextContent("sum orðin");
  });

  it("renders no match-tier chip for tier 0", () => {
    renderWithProviders(<ResultCard r={{ ...r, match_tier: 0 }} />);
    expect(screen.queryByTestId("match-tier")).toBeNull();
  });

  it("shows how often the case has been cited", () => {
    renderWithProviders(<ResultCard r={{ ...r, cited_by_count: 12 }} />);
    expect(screen.getByText("vitnað í 12 sinnum")).toBeInTheDocument();
  });

  it("uses the singular for a single citation", () => {
    renderWithProviders(<ResultCard r={{ ...r, cited_by_count: 1 }} />);
    expect(screen.getByText("vitnað í 1 sinni")).toBeInTheDocument();
  });

  it("says nothing when the case has never been cited", () => {
    renderWithProviders(<ResultCard r={{ ...r, cited_by_count: 0 }} />);
    expect(screen.queryByText(/vitnað í/)).toBeNull();
  });

  it("names a book by its title, with the year, not by its urlausn", () => {
    renderWithProviders(
      <ResultCard r={{ ...r, urlausn: "Bók. Kauparéttur 1. janúar 2005 – Bók", case_number: "Kauparéttur",
        document_date: "2005-01-01", case_number_is_title: true, source_display: "Lögfræðibækur" }} />,
    );
    expect(screen.getByRole("link", { name: "Kauparéttur" })).toBeInTheDocument();
    expect(screen.getByText("2005")).toBeInTheDocument();
    expect(screen.queryByText(/1\. janúar 2005/)).toBeNull();
  });

  it("drops markdown heading marks from the snippet", () => {
    renderWithProviders(<ResultCard r={{ ...r, snippet: "## 5.1. Inngangur Það hefur" }} />);
    expect(screen.getByText("5.1. Inngangur Það hefur")).toBeInTheDocument();
  });
});
