import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { ResultCard } from "./ResultCard";

const r = {
  id: "abc", urlausn: "Hrd. 48/2022 – Dómur", source: "haestirettur", source_display: "Hæstiréttur",
  court: "Hrd.", case_number: "48/2022", document_date: "2023-03-29", verdict_type: "Dómur",
  keywords: ["Gæsluvarðhald"], plaintiffs: [{ name: "Ríkið", lawyer: null }], defendants: [{ name: "A", lawyer: null }],
  snippet: "texti <mark>gæsluvarðhald</mark> meira", has_appeal_links: true,
};

describe("ResultCard", () => {
  it("links to the document and shows highlighted snippet + keyword", () => {
    renderWithProviders(<ResultCard r={r} />);
    expect(screen.getByRole("link", { name: /Hrd\. 48\/2022/ })).toHaveAttribute("href", "/domur/abc");
    expect(screen.getByText("Gæsluvarðhald")).toBeInTheDocument();
    expect(document.querySelector("mark")?.textContent).toBe("gæsluvarðhald");
  });
});
