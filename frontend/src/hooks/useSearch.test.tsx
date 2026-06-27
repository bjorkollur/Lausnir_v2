import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { server, http, HttpResponse } from "../test/msw";
import { useSearch } from "./useSearch";
import { DEFAULT_STATE } from "../lib/searchState";

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useSearch", () => {
  it("fetches the first page", async () => {
    server.use(
      http.get("http://localhost:8077/api/search", () =>
        HttpResponse.json({
          total: 2,
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
              snippet: "x",
              has_appeal_links: false,
            },
          ],
        })
      )
    );
    const { result } = renderHook(() => useSearch({ ...DEFAULT_STATE, q: "x" }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.pages[0].total).toBe(2);
  });
});
