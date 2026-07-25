import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { DocPanel } from "./DocPanel";
import { LARGE_DOC_THRESHOLD } from "../lib/splitMarkdown";

const doc = {
  id: "a", source: "haestirettur", source_display: "Hæstiréttur", external_id: "x", url: "https://island.is/domar/x",
  urlausn: "Hrd. 59/2025 – Dómur", court: "Hrd.", case_number: "59/2025", document_date: "2026-06-10",
  verdict_type: "Dómur", instance_tier: 3, case_type: "Einkamál",
  plaintiffs: [{ name: "A", lawyer: null }], defendants: [{ name: "B", lawyer: null }],
  keywords: ["Börn", "Barnavernd"], summary: "Reifun hér", body_text: "## Dómsorð\nTexti",
  lower_body_text: null, appeal_links: [{ relation: "appealed_to", confidence: 1, method: "resolution_link",
    document_id: "b", source: "landsrettur", urlausn: "Lrd. 1/2024 – Dómur" }],
  markdown: "## Dómsorð\nTexti",
};

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
    const placeholders = container.querySelectorAll('[aria-hidden="true"]');
    expect(placeholders.length).toBeGreaterThan(1);
  });
});
