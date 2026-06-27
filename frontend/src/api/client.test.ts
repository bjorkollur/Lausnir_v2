import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { server, http, HttpResponse } from "../test/msw";
import { searchDocuments, ApiError } from "./client";

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("searchDocuments", () => {
  it("builds query string and parses the response", async () => {
    let seen = "";
    server.use(http.get("http://localhost:8077/api/search", ({ request }) => {
      seen = new URL(request.url).search;
      return HttpResponse.json({ total: 1, page: 1, page_size: 20, results: [] });
    }));
    const res = await searchDocuments({ q: "test", mode: "keyword", scope: ["domstolar"], sort: "relevance" });
    expect(res.total).toBe(1);
    expect(seen).toContain("q=test");
    expect(seen).toContain("scope=domstolar");
    expect(seen).toContain("mode=keyword");
  });

  it("throws ApiError with status on 400", async () => {
    server.use(http.get("http://localhost:8077/api/search", () =>
      HttpResponse.json({ detail: "Invalid regex" }, { status: 400 })));
    await expect(searchDocuments({ q: "[", mode: "regex", scope: [], sort: "newest" }))
      .rejects.toMatchObject({ status: 400, message: "Invalid regex" } satisfies Partial<ApiError>);
  });
});
