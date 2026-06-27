import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { server, http, HttpResponse } from "../test/msw";
import { ResultsList } from "./ResultsList";
import { DEFAULT_STATE } from "../lib/searchState";

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("ResultsList", () => {
  it("renders total and a card", async () => {
    server.use(
      http.get("http://localhost:8077/api/search", () =>
        HttpResponse.json({
          total: 1,
          page: 1,
          page_size: 20,
          results: [
            {
              id: "a",
              urlausn: "Hrd. 1/2020",
              source: "haestirettur",
              source_display: "Hæstiréttur",
              court: "Hrd.",
              case_number: "1/2020",
              document_date: "2020-01-01",
              verdict_type: "Dómur",
              keywords: [],
              plaintiffs: [],
              defendants: [],
              snippet: "s",
              has_appeal_links: false,
            },
          ],
        })
      )
    );
    renderWithProviders(<ResultsList state={{ ...DEFAULT_STATE, q: "x" }} />);
    await waitFor(() =>
      expect(screen.getByText(/1 niðurstaða|1 niðurstöður/)).toBeInTheDocument()
    );
    expect(screen.getByRole("link", { name: /Hrd\. 1\/2020/ })).toBeInTheDocument();
  });

  it("shows empty state when no results", async () => {
    server.use(
      http.get("http://localhost:8077/api/search", () =>
        HttpResponse.json({ total: 0, page: 1, page_size: 20, results: [] })
      )
    );
    renderWithProviders(<ResultsList state={{ ...DEFAULT_STATE, q: "zzz" }} />);
    await waitFor(() =>
      expect(screen.getByText(/Engar niðurstöður/)).toBeInTheDocument()
    );
  });
});
