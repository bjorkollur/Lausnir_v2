import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { CitationList } from "./CitationList";
import type { CitationRef } from "../api/types";

const cite = (over: Partial<CitationRef> = {}): CitationRef => ({
  document_id: "c1",
  urlausn: "Hrd. 10/2019 – Dómur",
  source: "haestirettur",
  document_date: "2019-05-05",
  layer: "body",
  passage_id: null,
  anchor: null,
  raw_text: "dómi Hæstaréttar í máli nr. 10/2019",
  confidence: 0.9,
  also_appeal: false,
  same_case: false,
  ...over,
});

describe("CitationList", () => {
  it("shows the count in the title and links each entry to its document", () => {
    renderWithProviders(<CitationList title="Vitnar í" items={[cite()]} total={1} />);
    expect(screen.getByRole("heading", { name: "Vitnar í (1)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Hrd\. 10\/2019/ })).toHaveAttribute(
      "href",
      "/domur/c1",
    );
    expect(screen.getByText("dómi Hæstaréttar í máli nr. 10/2019")).toBeInTheDocument();
  });

  it("marks an appeal-chain citation and a same-case citation", () => {
    renderWithProviders(
      <CitationList
        title="Vitnar í"
        items={[
          cite({ document_id: "c1", also_appeal: true }),
          cite({ document_id: "c2", urlausn: "Lrd. 5/2021 – Dómur", same_case: true }),
        ]}
        total={2}
      />,
    );
    expect(screen.getByText("(í áfrýjunarkeðju)")).toBeInTheDocument();
    expect(screen.getByText("(sama mál)")).toBeInTheDocument();
  });

  it("offers no 'Sýna fleiri' when every entry is already shown", () => {
    const onMore = vi.fn();
    renderWithProviders(
      <CitationList title="Vitnar í" items={[cite()]} total={1} onMore={onMore} />,
    );
    expect(screen.queryByRole("button", { name: /^Sýna fleiri/ })).not.toBeInTheDocument();
  });

  it("offers 'Sýna fleiri' when the total exceeds what is shown, and calls onMore", async () => {
    const user = userEvent.setup();
    const onMore = vi.fn();
    renderWithProviders(
      <CitationList title="Vitnar í" items={[cite()]} total={4} onMore={onMore} />,
    );
    await user.click(screen.getByRole("button", { name: /^Sýna fleiri/ }));
    expect(onMore).toHaveBeenCalledTimes(1);
  });

  it("hides the button when the caller cannot load more", () => {
    renderWithProviders(<CitationList title="Vitnar í" items={[cite()]} total={4} />);
    expect(screen.queryByRole("button", { name: /^Sýna fleiri/ })).not.toBeInTheDocument();
  });
});
