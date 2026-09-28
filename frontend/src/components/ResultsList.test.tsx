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
          strict_total: 1,
          relaxed: false,
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
              match_tier: 0,
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
        HttpResponse.json({ total: 0, page: 1, page_size: 20, strict_total: 0, relaxed: false, results: [] })
      )
    );
    renderWithProviders(<ResultsList state={{ ...DEFAULT_STATE, q: "zzz" }} />);
    await waitFor(() =>
      expect(screen.getByText(/Engar niðurstöður/)).toBeInTheDocument()
    );
  });

  const resultStub = {
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
    match_tier: 1,
  };

  it("shows the relaxed notice with strict/relaxed counts when relaxed and strict_total > 0", async () => {
    server.use(
      http.get("http://localhost:8077/api/search", () =>
        HttpResponse.json({
          total: 9,
          page: 1,
          page_size: 20,
          strict_total: 2,
          relaxed: true,
          results: [resultStub],
        })
      )
    );
    renderWithProviders(<ResultsList state={{ ...DEFAULT_STATE, q: "x" }} />);
    const notice = await screen.findByTestId("relaxed-notice");
    expect(notice).toHaveTextContent("2 skjöl innihalda öll leitarorðin");
    expect(notice).toHaveTextContent("7 skjöl");
  });

  it("shows the relaxed notice for zero strict matches", async () => {
    server.use(
      http.get("http://localhost:8077/api/search", () =>
        HttpResponse.json({
          total: 5,
          page: 1,
          page_size: 20,
          strict_total: 0,
          relaxed: true,
          results: [resultStub],
        })
      )
    );
    renderWithProviders(<ResultsList state={{ ...DEFAULT_STATE, q: "x" }} />);
    const notice = await screen.findByTestId("relaxed-notice");
    expect(notice).toHaveTextContent("Engin skjöl innihalda öll leitarorðin");
  });

  it("shows only the first sentence when relaxed finds no additional matches", async () => {
    server.use(
      http.get("http://localhost:8077/api/search", () =>
        HttpResponse.json({
          total: 2,
          page: 1,
          page_size: 20,
          strict_total: 2,
          relaxed: true,
          results: [{ ...resultStub, match_tier: 0 }],
        })
      )
    );
    renderWithProviders(<ResultsList state={{ ...DEFAULT_STATE, q: "x" }} />);
    const notice = await screen.findByTestId("relaxed-notice");
    expect(notice).toHaveTextContent("2 skjöl innihalda öll leitarorðin.");
    expect(notice.textContent).not.toMatch(/Sýni einnig/);
  });

  it("shows no relaxed notice when relaxed is false", async () => {
    server.use(
      http.get("http://localhost:8077/api/search", () =>
        HttpResponse.json({
          total: 1,
          page: 1,
          page_size: 20,
          strict_total: 1,
          relaxed: false,
          results: [{ ...resultStub, match_tier: 0 }],
        })
      )
    );
    renderWithProviders(<ResultsList state={{ ...DEFAULT_STATE, q: "x" }} />);
    await waitFor(() =>
      expect(screen.getByRole("link", { name: /Hrd\. 1\/2020/ })).toBeInTheDocument()
    );
    expect(screen.queryByTestId("relaxed-notice")).toBeNull();
  });
});
