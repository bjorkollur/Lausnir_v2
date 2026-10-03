import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { server, http, HttpResponse } from "../test/msw";
import CatalogPage from "./CatalogPage";

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("CatalogPage", () => {
  it("lists rulings and decisions only, not laws, books or journal articles", async () => {
    server.use(
      http.get("http://localhost:8077/api/sources", () => HttpResponse.json({
        catalog: [
          { key: "domstolar", label: "Dómstólar", count: 10, children: [] },
          { key: "lagasafn", label: "Lagasafn", count: 5 },
          { key: "baekur", label: "Bækur", count: 3 },
          { key: "timarit", label: "Tímarit og fræðigreinar", count: 7 },
        ],
        sources: [], regex_fields: [], total: 25,
      })),
    );
    renderWithProviders(<CatalogPage />);
    await waitFor(() => expect(screen.getByText("Dómstólar")).toBeInTheDocument());
    expect(screen.queryByText("Tímarit og fræðigreinar")).not.toBeInTheDocument();
    expect(screen.queryByText("Bækur")).not.toBeInTheDocument();
    expect(screen.getByText("10 skjöl")).toBeInTheDocument();
  });
});
