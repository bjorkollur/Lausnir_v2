import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { server, http, HttpResponse } from "../test/msw";
import SearchPage from "./SearchPage";

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const sources = { catalog: [], sources: [], regex_fields: ["body_text"], total: 0 };
const facets = { catalog: [{ key: "domstolar", label: "Dómstólar", count: 1 }], total: 1 };

describe("SearchPage", () => {
  it("renders results and facet sidebar from URL query", async () => {
    server.use(
      http.get("http://localhost:8077/api/sources", () => HttpResponse.json(sources)),
      http.get("http://localhost:8077/api/facets", () => HttpResponse.json(facets)),
      http.get("http://localhost:8077/api/search", () =>
        HttpResponse.json({ total: 1, page: 1, page_size: 20, results: [{
          id: "a", urlausn: "Hrd. 1/2020", source: "haestirettur", source_display: "Hæstiréttur",
          court: "Hrd.", case_number: "1/2020", document_date: "2020-01-01", verdict_type: "Dómur",
          keywords: [], plaintiffs: [], defendants: [], snippet: "s", has_appeal_links: false }] })),
    );
    renderWithProviders(<SearchPage />, "/?q=test");
    await waitFor(() => expect(screen.getByRole("link", { name: /Hrd\. 1\/2020/ })).toBeInTheDocument());
    expect(screen.getByText("Dómstólar")).toBeInTheDocument();
  });
});
