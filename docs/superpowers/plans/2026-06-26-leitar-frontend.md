# Leitar-frontend (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a React read-only search + document-reading UI over the existing Lausnir FastAPI search API, in the spirit of Fons Juris but with our own clean style.

**Architecture:** A standalone Vite + React SPA in `frontend/` that talks to the FastAPI server at `http://localhost:8077` (CORS already open). URL query-params are the single source of truth for search state; TanStack Query handles fetching/caching/infinite-scroll. Three routes: `/` (search), `/heimildir` (catalog browse), `/domur/:id` (document).

**Tech Stack:** React 19, Vite, TypeScript, Tailwind CSS v4, TanStack Query v5, React Router v7, Radix UI primitives, react-markdown + DOMPurify. Tests: Vitest + React Testing Library + MSW + jsdom.

## Global Constraints

- All UI text is **Icelandic**. No English in the interface.
- API base URL comes from `import.meta.env.VITE_API_BASE`, default `http://localhost:8077`. Never hard-code the host elsewhere.
- Search state lives **only** in the URL query string (`q, mode, scope, date_from, date_to, sort, regex_fields`). Components read/write it through the `searchState` helpers — never local component state for these.
- Facet counts depend on `(q, mode, date_from, date_to, regex_fields)` but **NOT** `scope` — the facet query key must exclude scope.
- Design tokens: neutral base (Tailwind `slate`/`zinc`), one accent = `indigo-600`, search highlight = `bg-yellow-200`, font = **Inter**. No Fons navy.
- Snippets and document bodies contain server-generated `<mark>` highlight tags. Render them via DOMPurify allowing **only** the `<mark>` tag — never `dangerouslySetInnerHTML` without sanitizing.
- Every task ends green (`npm run test` passes) and is committed.
- The API server must be running for MSW-free integration checks; unit/integration tests mock the API with MSW and need no live server.

## API response shapes (ground truth — used across tasks)

```ts
// GET /api/search?q&mode&scope(repeatable)&date_from&date_to&sort&page&page_size&regex_fields(repeatable)
interface SearchResponse {
  total: number; page: number; page_size: number; results: SearchResult[];
}
interface Party { name: string; lawyer: string | null; }
interface SearchResult {
  id: string; urlausn: string; source: string; source_display: string;
  court: string | null; case_number: string | null; document_date: string | null;
  verdict_type: string | null; keywords: string[]; plaintiffs: Party[]; defendants: Party[];
  snippet: string; has_appeal_links: boolean;
}
// GET /api/facets?q&mode&date_from&date_to&regex_fields  → {catalog, total}
interface CatalogNode { key: string; label: string; count: number; children?: CatalogNode[]; }
interface FacetsResponse { catalog: CatalogNode[]; total: number; }
// GET /api/sources → {catalog, sources, regex_fields, total}
interface SourceFlat { short_name: string; display_name: string; abbreviation: string | null; count: number; }
interface SourcesResponse { catalog: CatalogNode[]; sources: SourceFlat[]; regex_fields: string[]; total: number; }
// GET /api/document/:id?markdown=true
interface AppealLink { relation: string; confidence: number | null; method: string | null; document_id: string; source: string; urlausn: string; }
interface DocumentDetail {
  id: string; source: string; source_display: string; external_id: string; url: string | null;
  urlausn: string; court: string | null; case_number: string | null; document_date: string | null;
  verdict_type: string | null; instance_tier: number | null; case_type: string | null;
  plaintiffs: Party[]; defendants: Party[]; keywords: string[]; summary: string | null;
  body_text: string | null; lower_body_text: string | null; appeal_links: AppealLink[];
  markdown: string | null;
}
```

## File Structure

```
frontend/
  package.json, vite.config.ts, tsconfig.json, vitest.config.ts, index.html
  src/
    main.tsx                # entry: QueryClientProvider + RouterProvider
    index.css               # tailwind + Inter + tokens
    App.tsx                 # <Routes> for /, /heimildir, /domur/:id
    api/types.ts            # interfaces above
    api/client.ts           # typed fetch wrappers (searchDocuments, fetchFacets, fetchSources, fetchDocument)
    lib/searchState.ts      # SearchState <-> URLSearchParams
    lib/sanitize.ts         # markHtml(): DOMPurify allowing only <mark>
    hooks/useSearch.ts      # useInfiniteQuery
    hooks/useFacets.ts
    hooks/useDocument.ts
    hooks/useSources.ts
    components/NavRail.tsx
    components/SearchBar.tsx
    components/RegexToggle.tsx
    components/Toolbar.tsx          # sort select + date popover + regex-fields popover
    components/ScopeChips.tsx
    components/FacetSidebar.tsx     # renders FacetNode tree
    components/FacetNode.tsx
    components/ResultCard.tsx
    components/ResultsList.tsx      # infinite scroll
    components/CatalogTree.tsx
    components/DocHeader.tsx
    components/DocPanel.tsx
    components/states.tsx           # ResultsSkeleton, EmptyState, ErrorState
    routes/SearchPage.tsx
    routes/CatalogPage.tsx
    routes/DocumentPage.tsx
    test/setup.ts                   # jest-dom
    test/msw.ts                     # MSW server + handlers + fixtures
    test/renderWithProviders.tsx    # QueryClient + MemoryRouter wrapper
```

---

### Task 1: Scaffold project + tooling + tokens

**Files:**
- Create: `frontend/` (Vite React-TS), `frontend/vite.config.ts`, `frontend/vitest.config.ts`, `frontend/src/index.css`, `frontend/src/test/setup.ts`
- Modify: `frontend/package.json` (scripts/deps), `.gitignore` (add `frontend/node_modules`, `frontend/dist`)

**Interfaces:**
- Produces: a running dev server (`npm run dev`) and a green `npm run test`; Tailwind v4 active; Inter font; `renderWithProviders` test helper available in Task 5+.

- [ ] **Step 1: Scaffold Vite app**

Run:
```bash
cd /Volumes/RuleOfLaw/Lausnir
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
```

- [ ] **Step 2: Install runtime + dev dependencies**

```bash
npm install react-router-dom @tanstack/react-query @radix-ui/react-checkbox \
  @radix-ui/react-popover @radix-ui/react-switch @radix-ui/react-select \
  react-markdown dompurify
npm install -D tailwindcss @tailwindcss/vite vitest jsdom \
  @testing-library/react @testing-library/jest-dom @testing-library/user-event \
  msw @types/dompurify
```

- [ ] **Step 3: Configure Vite + Tailwind v4 + Vitest**

`frontend/vite.config.ts`:
```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
});
```

`frontend/vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"] },
});
```

`frontend/src/test/setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 4: Tailwind entry + tokens + Inter**

Replace `frontend/src/index.css`:
```css
@import "tailwindcss";
@import url("https://rsms.me/inter/inter.css");

:root { --accent: oklch(0.51 0.23 277); } /* indigo-600 */

@theme {
  --font-sans: "Inter", ui-sans-serif, system-ui, sans-serif;
}

html, body, #root { height: 100%; }
body { @apply bg-white text-slate-900 antialiased; font-family: var(--font-sans); }
mark { @apply bg-yellow-200 text-slate-900 rounded-sm px-0.5; }
```

Add the env default — `frontend/.env`:
```
VITE_API_BASE=http://localhost:8077
```

- [ ] **Step 5: Add scripts and a placeholder smoke test**

In `frontend/package.json` ensure scripts:
```json
"scripts": { "dev": "vite", "build": "tsc -b && vite build", "preview": "vite preview", "test": "vitest run", "test:watch": "vitest" }
```

`frontend/src/smoke.test.ts`:
```ts
import { describe, it, expect } from "vitest";
describe("tooling", () => { it("runs", () => { expect(1 + 1).toBe(2); }); });
```

- [ ] **Step 6: Verify dev server + tests**

Run: `npm run test` → Expected: 1 passed.
Run: `npm run dev` then open `http://localhost:5173` → Expected: Vite default page loads. Stop the server.

- [ ] **Step 7: Commit**

```bash
cd /Volumes/RuleOfLaw/Lausnir
echo "frontend/node_modules/" >> .gitignore
echo "frontend/dist/" >> .gitignore
git add frontend/.gitignore .gitignore
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts \
  frontend/vitest.config.ts frontend/tsconfig*.json frontend/index.html \
  frontend/src/index.css frontend/src/test/setup.ts frontend/src/smoke.test.ts frontend/.env
git commit -m "chore(frontend): scaffold Vite + React + Tailwind v4 + Vitest"
```

---

### Task 2: API types + typed client

**Files:**
- Create: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/api/client.test.ts`, `frontend/src/test/msw.ts`

**Interfaces:**
- Produces: `searchDocuments(params): Promise<SearchResponse>`, `fetchFacets(params): Promise<FacetsResponse>`, `fetchSources(): Promise<SourcesResponse>`, `fetchDocument(id): Promise<DocumentDetail>`. Also `ApiError` (with `.status`). All types from "API response shapes" above live in `types.ts`.

- [ ] **Step 1: Write types**

`frontend/src/api/types.ts` — copy the full block from "API response shapes (ground truth)" above (all `interface` declarations, each `export`ed). Also add:
```ts
export type Mode = "keyword" | "regex";
export type Sort = "relevance" | "newest" | "oldest";
export interface SearchParams {
  q: string; mode: Mode; scope: string[];
  date_from?: string; date_to?: string; sort: Sort;
  page?: number; page_size?: number; regex_fields?: string[];
}
```

- [ ] **Step 2: Write the failing client test**

`frontend/src/api/client.test.ts`:
```ts
import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { server, http, HttpResponse } from "../test/msw";
import { searchDocuments, fetchDocument, ApiError } from "./client";

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
```

`frontend/src/test/msw.ts`:
```ts
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
export const server = setupServer();
export { http, HttpResponse };
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm run test -- client` → Expected: FAIL ("Cannot find module './client'").

- [ ] **Step 4: Implement the client**

`frontend/src/api/client.ts`:
```ts
import type { SearchParams, SearchResponse, FacetsResponse, SourcesResponse, DocumentDetail } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8077";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = "ApiError"; }
}

async function getJson<T>(path: string, qs?: URLSearchParams): Promise<T> {
  const url = `${BASE}${path}${qs && [...qs].length ? `?${qs}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

function searchQs(p: SearchParams): URLSearchParams {
  const qs = new URLSearchParams();
  if (p.q) qs.set("q", p.q);
  qs.set("mode", p.mode);
  qs.set("sort", p.sort);
  for (const s of p.scope) qs.append("scope", s);
  if (p.date_from) qs.set("date_from", p.date_from);
  if (p.date_to) qs.set("date_to", p.date_to);
  if (p.page) qs.set("page", String(p.page));
  if (p.page_size) qs.set("page_size", String(p.page_size));
  for (const f of p.regex_fields ?? []) qs.append("regex_fields", f);
  return qs;
}

export const searchDocuments = (p: SearchParams) => getJson<SearchResponse>("/api/search", searchQs(p));

export function fetchFacets(p: Omit<SearchParams, "scope" | "sort" | "page" | "page_size">): Promise<FacetsResponse> {
  const qs = new URLSearchParams();
  if (p.q) qs.set("q", p.q);
  qs.set("mode", p.mode);
  if (p.date_from) qs.set("date_from", p.date_from);
  if (p.date_to) qs.set("date_to", p.date_to);
  for (const f of p.regex_fields ?? []) qs.append("regex_fields", f);
  return getJson<FacetsResponse>("/api/facets", qs);
}

export const fetchSources = () => getJson<SourcesResponse>("/api/sources");
export const fetchDocument = (id: string) => getJson<DocumentDetail>(`/api/document/${id}`);
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- client` → Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api frontend/src/test/msw.ts
git commit -m "feat(frontend): typed API client + response types"
```

---

### Task 3: URL search-state encode/decode

**Files:**
- Create: `frontend/src/lib/searchState.ts`, `frontend/src/lib/searchState.test.ts`

**Interfaces:**
- Produces: `parseSearchState(sp: URLSearchParams): SearchState`, `toSearchParams(s: SearchState): URLSearchParams`, `DEFAULT_STATE`. `SearchState = { q: string; mode: Mode; scope: string[]; date_from?: string; date_to?: string; sort: Sort; regex_fields: string[] }`. Defaults: mode `keyword`, sort `relevance`, scope `[]` (= whole DB), regex_fields `[]`.

- [ ] **Step 1: Write the failing test**

`frontend/src/lib/searchState.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { parseSearchState, toSearchParams, DEFAULT_STATE } from "./searchState";

describe("searchState", () => {
  it("defaults when params empty", () => {
    const s = parseSearchState(new URLSearchParams(""));
    expect(s).toEqual(DEFAULT_STATE);
  });
  it("round-trips multi-value scope and regex_fields", () => {
    const sp = new URLSearchParams("q=x&mode=regex&sort=newest&scope=domstolar&scope=nefndir&regex_fields=body_text&date_from=2020-01-01");
    const s = parseSearchState(sp);
    expect(s.scope).toEqual(["domstolar", "nefndir"]);
    expect(s.mode).toBe("regex");
    expect(s.date_from).toBe("2020-01-01");
    expect(toSearchParams(s).toString()).toBe(
      new URLSearchParams("q=x&mode=regex&sort=newest&date_from=2020-01-01&scope=domstolar&scope=nefndir&regex_fields=body_text").toString()
    );
  });
  it("ignores invalid mode/sort", () => {
    const s = parseSearchState(new URLSearchParams("mode=bogus&sort=bogus"));
    expect(s.mode).toBe("keyword");
    expect(s.sort).toBe("relevance");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- searchState` → Expected: FAIL ("Cannot find module './searchState'").

- [ ] **Step 3: Implement**

`frontend/src/lib/searchState.ts`:
```ts
import type { Mode, Sort } from "../api/types";

export interface SearchState {
  q: string; mode: Mode; scope: string[];
  date_from?: string; date_to?: string; sort: Sort; regex_fields: string[];
}

export const DEFAULT_STATE: SearchState = {
  q: "", mode: "keyword", scope: [], sort: "relevance", regex_fields: [],
};

const MODES: Mode[] = ["keyword", "regex"];
const SORTS: Sort[] = ["relevance", "newest", "oldest"];

export function parseSearchState(sp: URLSearchParams): SearchState {
  const mode = sp.get("mode"); const sort = sp.get("sort");
  return {
    q: sp.get("q") ?? "",
    mode: MODES.includes(mode as Mode) ? (mode as Mode) : "keyword",
    sort: SORTS.includes(sort as Sort) ? (sort as Sort) : "relevance",
    scope: sp.getAll("scope"),
    regex_fields: sp.getAll("regex_fields"),
    date_from: sp.get("date_from") ?? undefined,
    date_to: sp.get("date_to") ?? undefined,
  };
}

export function toSearchParams(s: SearchState): URLSearchParams {
  const sp = new URLSearchParams();
  if (s.q) sp.set("q", s.q);
  sp.set("mode", s.mode);
  sp.set("sort", s.sort);
  if (s.date_from) sp.set("date_from", s.date_from);
  if (s.date_to) sp.set("date_to", s.date_to);
  for (const x of s.scope) sp.append("scope", x);
  for (const f of s.regex_fields) sp.append("regex_fields", f);
  return sp;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- searchState` → Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/searchState.ts frontend/src/lib/searchState.test.ts
git commit -m "feat(frontend): URL search-state encode/decode"
```

---

### Task 4: TanStack Query hooks

**Files:**
- Create: `frontend/src/hooks/useSearch.ts`, `useFacets.ts`, `useDocument.ts`, `useSources.ts`, `frontend/src/hooks/useSearch.test.tsx`, `frontend/src/test/renderWithProviders.tsx`

**Interfaces:**
- Consumes: client functions (Task 2), `SearchState` (Task 3).
- Produces: `useSearch(state: SearchState)` → `useInfiniteQuery` returning pages of `SearchResponse`; `useFacets(state)` → `FacetsResponse`; `useDocument(id)` → `DocumentDetail`; `useSources()` → `SourcesResponse`. `renderWithProviders(ui, { route })` test helper wrapping in `QueryClientProvider` + `MemoryRouter`.

- [ ] **Step 1: Write the test-providers helper**

`frontend/src/test/renderWithProviders.tsx`:
```tsx
import { ReactElement } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

export function renderWithProviders(ui: ReactElement, route = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 2: Write the failing hook test**

`frontend/src/hooks/useSearch.test.tsx`:
```tsx
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
    server.use(http.get("http://localhost:8077/api/search", () =>
      HttpResponse.json({ total: 2, page: 1, page_size: 20,
        results: [{ id: "a", urlausn: "Hrd. 1/2020", source: "haestirettur", source_display: "Hæstiréttur",
          court: "Hrd.", case_number: "1/2020", document_date: "2020-01-01", verdict_type: "Dómur",
          keywords: [], plaintiffs: [], defendants: [], snippet: "x", has_appeal_links: false }] })));
    const { result } = renderHook(() => useSearch({ ...DEFAULT_STATE, q: "x" }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.pages[0].total).toBe(2);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm run test -- useSearch` → Expected: FAIL ("Cannot find module './useSearch'").

- [ ] **Step 4: Implement the hooks**

`frontend/src/hooks/useSearch.ts`:
```ts
import { useInfiniteQuery } from "@tanstack/react-query";
import { searchDocuments } from "../api/client";
import type { SearchState } from "../lib/searchState";

const PAGE_SIZE = 20;

export function useSearch(s: SearchState) {
  return useInfiniteQuery({
    queryKey: ["search", s],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      searchDocuments({ ...s, page: pageParam, page_size: PAGE_SIZE }),
    getNextPageParam: (last) =>
      last.page * last.page_size < last.total ? last.page + 1 : undefined,
  });
}
```

`frontend/src/hooks/useFacets.ts`:
```ts
import { useQuery } from "@tanstack/react-query";
import { fetchFacets } from "../api/client";
import type { SearchState } from "../lib/searchState";

export function useFacets(s: SearchState) {
  // NB: scope/sort excluded from the key — facets ignore the source selection.
  return useQuery({
    queryKey: ["facets", s.q, s.mode, s.date_from, s.date_to, s.regex_fields],
    queryFn: () => fetchFacets({ q: s.q, mode: s.mode, date_from: s.date_from, date_to: s.date_to, regex_fields: s.regex_fields }),
  });
}
```

`frontend/src/hooks/useDocument.ts`:
```ts
import { useQuery } from "@tanstack/react-query";
import { fetchDocument } from "../api/client";

export function useDocument(id: string) {
  return useQuery({ queryKey: ["document", id], queryFn: () => fetchDocument(id), enabled: !!id });
}
```

`frontend/src/hooks/useSources.ts`:
```ts
import { useQuery } from "@tanstack/react-query";
import { fetchSources } from "../api/client";

export function useSources() {
  return useQuery({ queryKey: ["sources"], queryFn: fetchSources, staleTime: 5 * 60 * 1000 });
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- useSearch` → Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks frontend/src/test/renderWithProviders.tsx
git commit -m "feat(frontend): TanStack Query hooks (search/facets/document/sources)"
```

---

### Task 5: App shell — routing, providers, NavRail, layout

**Files:**
- Create: `frontend/src/App.tsx`, `frontend/src/components/NavRail.tsx`, `frontend/src/components/NavRail.test.tsx`
- Modify: `frontend/src/main.tsx`
- Create stubs: `frontend/src/routes/SearchPage.tsx`, `CatalogPage.tsx`, `DocumentPage.tsx` (each returns a heading; fleshed out later)

**Interfaces:**
- Consumes: nothing new.
- Produces: routes `/`, `/heimildir`, `/domur/:id` mounted; `NavRail` with links to `/` ("Leit") and `/heimildir` ("Heimildir"); app wrapped in `QueryClientProvider` + `BrowserRouter`.

- [ ] **Step 1: Write the failing NavRail test**

`frontend/src/components/NavRail.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { NavRail } from "./NavRail";

describe("NavRail", () => {
  it("shows the two v1 links", () => {
    renderWithProviders(<NavRail />);
    expect(screen.getByRole("link", { name: /Leit/i })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /Heimildir/i })).toHaveAttribute("href", "/heimildir");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- NavRail` → Expected: FAIL ("Cannot find module './NavRail'").

- [ ] **Step 3: Implement NavRail + stubs + App + main**

`frontend/src/components/NavRail.tsx`:
```tsx
import { NavLink } from "react-router-dom";

const item = "flex flex-col items-center gap-1 py-3 text-xs text-slate-500 hover:text-indigo-600";
const active = "text-indigo-600";

export function NavRail() {
  return (
    <nav className="w-20 shrink-0 border-r border-slate-200 bg-white flex flex-col items-center py-3">
      <div className="mb-4 font-bold text-indigo-600">L</div>
      <NavLink to="/" className={({ isActive }) => `${item} ${isActive ? active : ""}`} end>
        <span aria-hidden>⌂</span>Leit
      </NavLink>
      <NavLink to="/heimildir" className={({ isActive }) => `${item} ${isActive ? active : ""}`}>
        <span aria-hidden>⚖</span>Heimildir
      </NavLink>
      <div className="mt-auto text-xs text-slate-300">👤</div>
    </nav>
  );
}
```

`frontend/src/routes/SearchPage.tsx`, `CatalogPage.tsx`, `DocumentPage.tsx` (stubs):
```tsx
export default function SearchPage() { return <h1 className="p-6 text-xl">Leit</h1>; }
```
```tsx
export default function CatalogPage() { return <h1 className="p-6 text-xl">Heimildir</h1>; }
```
```tsx
export default function DocumentPage() { return <h1 className="p-6 text-xl">Skjal</h1>; }
```

`frontend/src/App.tsx`:
```tsx
import { Routes, Route } from "react-router-dom";
import { NavRail } from "./components/NavRail";
import SearchPage from "./routes/SearchPage";
import CatalogPage from "./routes/CatalogPage";
import DocumentPage from "./routes/DocumentPage";

export default function App() {
  return (
    <div className="flex h-full">
      <NavRail />
      <div className="flex-1 min-w-0 overflow-auto">
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/heimildir" element={<CatalogPage />} />
          <Route path="/domur/:id" element={<DocumentPage />} />
        </Routes>
      </div>
    </div>
  );
}
```

`frontend/src/main.tsx`:
```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

const qc = new QueryClient();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter><App /></BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
);
```

- [ ] **Step 4: Run test + dev server**

Run: `npm run test -- NavRail` → Expected: PASS.
Run: `npm run dev`, open `/`, `/heimildir`, `/domur/x` → Expected: NavRail + the right stub heading on each. Stop server.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/main.tsx frontend/src/components/NavRail.tsx \
  frontend/src/components/NavRail.test.tsx frontend/src/routes
git commit -m "feat(frontend): app shell, routing, NavRail"
```

---

### Task 6: Search controls — SearchBar, RegexToggle, ScopeChips, Toolbar

**Files:**
- Create: `frontend/src/components/SearchBar.tsx`, `RegexToggle.tsx`, `ScopeChips.tsx`, `Toolbar.tsx`, `frontend/src/components/SearchBar.test.tsx`, `ScopeChips.test.tsx`

**Interfaces:**
- Consumes: `SearchState` + a callback to patch it. Define the patch contract here: each control takes `state: SearchState` and `onChange(patch: Partial<SearchState>): void`.
- Produces: `SearchBar`, `RegexToggle`, `ScopeChips`, `Toolbar` components. `LABELS` map for scope keys → Icelandic labels is built from `useSources()` in SearchPage and passed to `ScopeChips` as `labelOf(key) => string`.

- [ ] **Step 1: Write failing tests**

`frontend/src/components/SearchBar.test.tsx`:
```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SearchBar } from "./SearchBar";
import { DEFAULT_STATE } from "../lib/searchState";

describe("SearchBar", () => {
  it("submits typed query on Enter", async () => {
    const onChange = vi.fn();
    render(<SearchBar state={DEFAULT_STATE} onChange={onChange} />);
    await userEvent.type(screen.getByRole("searchbox"), "gæsla{Enter}");
    expect(onChange).toHaveBeenCalledWith({ q: "gæsla" });
  });
  it("shows regex placeholder in regex mode", () => {
    render(<SearchBar state={{ ...DEFAULT_STATE, mode: "regex" }} onChange={vi.fn()} />);
    expect(screen.getByRole("searchbox")).toHaveAttribute("placeholder", expect.stringMatching(/regex/i));
  });
});
```

`frontend/src/components/ScopeChips.test.tsx`:
```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ScopeChips } from "./ScopeChips";
import { DEFAULT_STATE } from "../lib/searchState";

describe("ScopeChips", () => {
  it("renders a chip per scope and removes on click", async () => {
    const onChange = vi.fn();
    render(<ScopeChips state={{ ...DEFAULT_STATE, scope: ["domstolar", "nefndir"] }}
      labelOf={(k) => k.toUpperCase()} onChange={onChange} />);
    expect(screen.getByText("DOMSTOLAR")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /fjarlægja DOMSTOLAR/i }));
    expect(onChange).toHaveBeenCalledWith({ scope: ["nefndir"] });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- SearchBar ScopeChips` → Expected: FAIL (modules missing).

- [ ] **Step 3: Implement the controls**

`frontend/src/components/SearchBar.tsx`:
```tsx
import { useState, useEffect, FormEvent } from "react";
import type { SearchState } from "../lib/searchState";

export function SearchBar({ state, onChange }:
  { state: SearchState; onChange: (p: Partial<SearchState>) => void }) {
  const [text, setText] = useState(state.q);
  useEffect(() => setText(state.q), [state.q]);
  const submit = (e: FormEvent) => { e.preventDefault(); onChange({ q: text.trim() }); };
  return (
    <form onSubmit={submit} className="flex-1">
      <input
        role="searchbox"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={state.mode === "regex" ? "regex mynstur…" : "Leita…"}
        className="w-full h-12 rounded-full border border-slate-300 px-5 text-base outline-none focus:border-indigo-500"
      />
    </form>
  );
}
```

`frontend/src/components/RegexToggle.tsx`:
```tsx
import * as Switch from "@radix-ui/react-switch";
import type { SearchState } from "../lib/searchState";

export function RegexToggle({ state, onChange }:
  { state: SearchState; onChange: (p: Partial<SearchState>) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-slate-600">
      <Switch.Root
        checked={state.mode === "regex"}
        onCheckedChange={(on) =>
          onChange({ mode: on ? "regex" : "keyword", sort: on ? "newest" : "relevance" })}
        className="w-10 h-6 rounded-full bg-slate-300 data-[state=checked]:bg-indigo-600 relative">
        <Switch.Thumb className="block w-5 h-5 bg-white rounded-full translate-x-0.5 data-[state=checked]:translate-x-[18px] transition-transform" />
      </Switch.Root>
      Regex
    </label>
  );
}
```

`frontend/src/components/ScopeChips.tsx`:
```tsx
import type { SearchState } from "../lib/searchState";

export function ScopeChips({ state, labelOf, onChange }:
  { state: SearchState; labelOf: (key: string) => string; onChange: (p: Partial<SearchState>) => void }) {
  if (state.scope.length === 0) return null;
  const remove = (k: string) => onChange({ scope: state.scope.filter((x) => x !== k) });
  return (
    <div className="flex flex-wrap gap-2">
      {state.scope.map((k) => (
        <span key={k} className="inline-flex items-center gap-1 rounded-full bg-indigo-600 text-white text-sm font-medium px-3 py-1">
          {labelOf(k)}
          <button aria-label={`fjarlægja ${labelOf(k)}`} onClick={() => remove(k)} className="ml-1">✕</button>
        </span>
      ))}
      <button onClick={() => onChange({ scope: [] })} className="text-sm text-slate-500 rounded-full px-3 py-1 bg-slate-200">
        Hreinsa {state.scope.length}
      </button>
    </div>
  );
}
```

`frontend/src/components/Toolbar.tsx`:
```tsx
import * as Popover from "@radix-ui/react-popover";
import type { SearchState, } from "../lib/searchState";
import type { Sort } from "../api/types";

const REGEX_FIELD_LABELS: Record<string, string> = {
  body_text: "Meginmál", summary: "Reifun", case_number: "Málsnúmer",
  parties: "Aðilar", keywords: "Lykilorð", lower_body_text: "Neðri dómur",
};

export function Toolbar({ state, regexFields, onChange }:
  { state: SearchState; regexFields: string[]; onChange: (p: Partial<SearchState>) => void }) {
  return (
    <div className="flex items-center gap-3">
      <select
        aria-label="Röðun"
        value={state.sort}
        onChange={(e) => onChange({ sort: e.target.value as Sort })}
        className="text-sm border border-slate-300 rounded-full px-3 py-1.5">
        <option value="relevance">Relevans</option>
        <option value="newest">Nýjast fyrst</option>
        <option value="oldest">Elst fyrst</option>
      </select>

      <Popover.Root>
        <Popover.Trigger className="text-sm border border-slate-300 rounded-full px-3 py-1.5">Tímabil</Popover.Trigger>
        <Popover.Portal>
          <Popover.Content className="bg-white border border-slate-200 rounded-lg p-3 shadow-md flex flex-col gap-2">
            <label className="text-sm">Frá <input type="date" value={state.date_from ?? ""}
              onChange={(e) => onChange({ date_from: e.target.value || undefined })} className="border rounded px-2 py-1" /></label>
            <label className="text-sm">Til <input type="date" value={state.date_to ?? ""}
              onChange={(e) => onChange({ date_to: e.target.value || undefined })} className="border rounded px-2 py-1" /></label>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      {state.mode === "regex" && (
        <Popover.Root>
          <Popover.Trigger className="text-sm border border-slate-300 rounded-full px-3 py-1.5">Reitir</Popover.Trigger>
          <Popover.Portal>
            <Popover.Content className="bg-white border border-slate-200 rounded-lg p-3 shadow-md flex flex-col gap-1">
              {regexFields.map((f) => {
                const on = (state.regex_fields.length ? state.regex_fields : ["body_text"]).includes(f);
                return (
                  <label key={f} className="text-sm flex items-center gap-2">
                    <input type="checkbox" checked={on} onChange={(e) => {
                      const base = state.regex_fields.length ? state.regex_fields : ["body_text"];
                      const next = e.target.checked ? [...new Set([...base, f])] : base.filter((x) => x !== f);
                      onChange({ regex_fields: next });
                    }} />
                    {REGEX_FIELD_LABELS[f] ?? f}
                  </label>
                );
              })}
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- SearchBar ScopeChips` → Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SearchBar.tsx frontend/src/components/RegexToggle.tsx \
  frontend/src/components/ScopeChips.tsx frontend/src/components/Toolbar.tsx \
  frontend/src/components/SearchBar.test.tsx frontend/src/components/ScopeChips.test.tsx
git commit -m "feat(frontend): search controls (bar, regex toggle, scope chips, toolbar)"
```

---

### Task 7: ResultCard

**Files:**
- Create: `frontend/src/components/ResultCard.tsx`, `frontend/src/lib/sanitize.ts`, `frontend/src/components/ResultCard.test.tsx`

**Interfaces:**
- Consumes: `SearchResult` (Task 2).
- Produces: `ResultCard({ r: SearchResult })`; `markHtml(html: string): { __html: string }` in `sanitize.ts` (DOMPurify allowing only `<mark>`).

- [ ] **Step 1: Write the failing test**

`frontend/src/components/ResultCard.test.tsx`:
```tsx
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- ResultCard` → Expected: FAIL.

- [ ] **Step 3: Implement sanitize + card**

`frontend/src/lib/sanitize.ts`:
```ts
import DOMPurify from "dompurify";
export function markHtml(html: string): { __html: string } {
  return { __html: DOMPurify.sanitize(html ?? "", { ALLOWED_TAGS: ["mark"], ALLOWED_ATTR: [] }) };
}
```

`frontend/src/components/ResultCard.tsx`:
```tsx
import { useState } from "react";
import { Link } from "react-router-dom";
import type { SearchResult, Party } from "../api/types";
import { markHtml } from "../lib/sanitize";

const partyLine = (ps: Party[]) => ps.map((p) => p.name).join(", ");

export function ResultCard({ r }: { r: SearchResult }) {
  const [open, setOpen] = useState(false);
  const parties = [...r.plaintiffs, ...r.defendants];
  return (
    <article className="py-4 border-b border-slate-100">
      <div className="flex items-baseline gap-2 flex-wrap">
        <Link to={`/domur/${r.id}`} className="text-indigo-700 font-semibold text-[17px] hover:underline">
          {r.urlausn}
        </Link>
        {r.has_appeal_links && <span className="text-xs text-slate-500" title="Hefur áfrýjunartengingar">⛓ tengt</span>}
      </div>
      <div className="text-sm text-slate-500">{r.source_display}{r.document_date ? ` · ${r.document_date}` : ""}</div>
      {parties.length > 0 && (
        <p className="text-sm text-slate-700 mt-1">
          {open ? partyLine(parties) : partyLine(parties).slice(0, 120)}
          {partyLine(parties).length > 120 && (
            <button onClick={() => setOpen(!open)} className="ml-1 text-indigo-600">
              {open ? "Sjá minna" : "Sjá meira"}
            </button>
          )}
        </p>
      )}
      <p className="text-sm text-slate-800 mt-2 leading-relaxed" dangerouslySetInnerHTML={markHtml(r.snippet)} />
      {r.keywords.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {r.keywords.map((k) => (
            <span key={k} className="text-sm text-slate-600 bg-slate-100 border border-slate-200 rounded-full px-3 py-0.5">{k}</span>
          ))}
        </div>
      )}
    </article>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- ResultCard` → Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ResultCard.tsx frontend/src/lib/sanitize.ts frontend/src/components/ResultCard.test.tsx
git commit -m "feat(frontend): ResultCard + sanitized highlight"
```

---

### Task 8: ResultsList with infinite scroll + states

**Files:**
- Create: `frontend/src/components/ResultsList.tsx`, `frontend/src/components/states.tsx`, `frontend/src/components/ResultsList.test.tsx`

**Interfaces:**
- Consumes: `useSearch` (Task 4), `ResultCard` (Task 7).
- Produces: `ResultsList({ state })`; `states.tsx` exports `ResultsSkeleton`, `EmptyState`, `ErrorState({ error })`.

- [ ] **Step 1: Write states.tsx**

`frontend/src/components/states.tsx`:
```tsx
import { ApiError } from "../api/client";

export function ResultsSkeleton() {
  return <div className="py-4 space-y-4" aria-label="Hleð…">
    {Array.from({ length: 5 }).map((_, i) => (
      <div key={i} className="h-20 rounded bg-slate-100 animate-pulse" />
    ))}</div>;
}
export function EmptyState() {
  return <p className="py-10 text-center text-slate-500">Engar niðurstöður. Prófaðu að víkka leitina.</p>;
}
export function ErrorState({ error }: { error: unknown }) {
  const msg = error instanceof ApiError && error.status === 400
    ? error.message
    : "Eitthvað fór úrskeiðis. Reyndu aftur.";
  return <p className="py-10 text-center text-red-600">{msg}</p>;
}
```

- [ ] **Step 2: Write the failing ResultsList test**

`frontend/src/components/ResultsList.test.tsx`:
```tsx
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
    server.use(http.get("http://localhost:8077/api/search", () =>
      HttpResponse.json({ total: 1, page: 1, page_size: 20, results: [{
        id: "a", urlausn: "Hrd. 1/2020", source: "haestirettur", source_display: "Hæstiréttur",
        court: "Hrd.", case_number: "1/2020", document_date: "2020-01-01", verdict_type: "Dómur",
        keywords: [], plaintiffs: [], defendants: [], snippet: "s", has_appeal_links: false }] })));
    renderWithProviders(<ResultsList state={{ ...DEFAULT_STATE, q: "x" }} />);
    await waitFor(() => expect(screen.getByText(/1 niðurstaða|1 niðurstöður/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /Hrd\. 1\/2020/ })).toBeInTheDocument();
  });

  it("shows empty state when no results", async () => {
    server.use(http.get("http://localhost:8077/api/search", () =>
      HttpResponse.json({ total: 0, page: 1, page_size: 20, results: [] })));
    renderWithProviders(<ResultsList state={{ ...DEFAULT_STATE, q: "zzz" }} />);
    await waitFor(() => expect(screen.getByText(/Engar niðurstöður/)).toBeInTheDocument());
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm run test -- ResultsList` → Expected: FAIL.

- [ ] **Step 4: Implement ResultsList**

`frontend/src/components/ResultsList.tsx`:
```tsx
import { useEffect, useRef } from "react";
import { useSearch } from "../hooks/useSearch";
import { ResultCard } from "./ResultCard";
import { ResultsSkeleton, EmptyState, ErrorState } from "./states";
import type { SearchState } from "../lib/searchState";

function plural(n: number) { return n === 1 ? "1 niðurstaða" : `${n.toLocaleString("is-IS")} niðurstöður`; }

export function ResultsList({ state }: { state: SearchState }) {
  const q = useSearch(state);
  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && q.hasNextPage && !q.isFetchingNextPage) q.fetchNextPage();
    });
    io.observe(el);
    return () => io.disconnect();
  }, [q.hasNextPage, q.isFetchingNextPage, q]);

  if (q.isPending) return <ResultsSkeleton />;
  if (q.isError) return <ErrorState error={q.error} />;
  const total = q.data.pages[0].total;
  if (total === 0) return <EmptyState />;
  const items = q.data.pages.flatMap((p) => p.results);

  return (
    <div>
      <p className="text-sm text-slate-500 py-2">{plural(total)}</p>
      {items.map((r) => <ResultCard key={r.id} r={r} />)}
      <div ref={sentinel} className="h-8" />
      {q.isFetchingNextPage && <ResultsSkeleton />}
    </div>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- ResultsList` → Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ResultsList.tsx frontend/src/components/states.tsx frontend/src/components/ResultsList.test.tsx
git commit -m "feat(frontend): ResultsList with infinite scroll + states"
```

---

### Task 9: FacetSidebar (recursive tree)

**Files:**
- Create: `frontend/src/components/FacetNode.tsx`, `frontend/src/components/FacetSidebar.tsx`, `frontend/src/components/FacetNode.test.tsx`

**Interfaces:**
- Consumes: `useFacets` (Task 4), `CatalogNode` (Task 2), `SearchState`.
- Produces: `FacetSidebar({ state, onChange })`; `FacetNode({ node, selected, depth, onToggle })`. Toggling a node key adds/removes it from `state.scope`.

- [ ] **Step 1: Write the failing FacetNode test**

`frontend/src/components/FacetNode.test.tsx`:
```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FacetNode } from "./FacetNode";

const node = { key: "domstolar", label: "Dómstólar", count: 4874, children: [
  { key: "haestirettur", label: "Hæstiréttur", count: 2157, children: [
    { key: "haestirettur_domar", label: "Dómar", count: 712 }] }] };

describe("FacetNode", () => {
  it("shows label + count and toggles on checkbox", async () => {
    const onToggle = vi.fn();
    render(<FacetNode node={node} selected={new Set()} depth={0} onToggle={onToggle} />);
    expect(screen.getByText("Dómstólar")).toBeInTheDocument();
    expect(screen.getByText("4.874")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /Dómstólar/ }));
    expect(onToggle).toHaveBeenCalledWith("domstolar");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- FacetNode` → Expected: FAIL.

- [ ] **Step 3: Implement FacetNode + FacetSidebar**

`frontend/src/components/FacetNode.tsx`:
```tsx
import { useState } from "react";
import * as Checkbox from "@radix-ui/react-checkbox";
import type { CatalogNode } from "../api/types";

export function FacetNode({ node, selected, depth, onToggle }:
  { node: CatalogNode; selected: Set<string>; depth: number; onToggle: (key: string) => void }) {
  const [open, setOpen] = useState(depth === 0);
  const hasKids = !!node.children?.length;
  return (
    <div>
      <div className="flex items-center gap-2 py-1" style={{ paddingLeft: depth * 14 }}>
        {hasKids ? (
          <button aria-label={open ? "fella saman" : "opna"} onClick={() => setOpen(!open)} className="w-4 text-slate-400">
            {open ? "▾" : "▸"}
          </button>
        ) : <span className="w-4" />}
        <Checkbox.Root
          aria-label={node.label}
          checked={selected.has(node.key)}
          onCheckedChange={() => onToggle(node.key)}
          className="w-4 h-4 border border-slate-300 rounded data-[state=checked]:bg-indigo-600 data-[state=checked]:border-indigo-600 grid place-items-center">
          <Checkbox.Indicator className="text-white text-[10px]">✓</Checkbox.Indicator>
        </Checkbox.Root>
        <span className="flex-1 text-sm text-slate-700">{node.label}</span>
        <span className="text-xs text-slate-400 tabular-nums">{node.count.toLocaleString("is-IS")}</span>
      </div>
      {hasKids && open && node.children!.map((c) => (
        <FacetNode key={c.key} node={c} selected={selected} depth={depth + 1} onToggle={onToggle} />
      ))}
    </div>
  );
}
```

`frontend/src/components/FacetSidebar.tsx`:
```tsx
import { useFacets } from "../hooks/useFacets";
import { FacetNode } from "./FacetNode";
import type { SearchState } from "../lib/searchState";

export function FacetSidebar({ state, onChange }:
  { state: SearchState; onChange: (p: Partial<SearchState>) => void }) {
  const { data, isPending } = useFacets(state);
  const selected = new Set(state.scope);
  const toggle = (key: string) =>
    onChange({ scope: selected.has(key) ? state.scope.filter((k) => k !== key) : [...state.scope, key] });

  return (
    <aside className="w-[300px] shrink-0 border-l border-slate-200 p-4 overflow-y-auto">
      {isPending && <div className="h-40 bg-slate-100 rounded animate-pulse" />}
      {data?.catalog.map((node) => (
        <FacetNode key={node.key} node={node} selected={selected} depth={0} onToggle={toggle} />
      ))}
    </aside>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- FacetNode` → Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FacetNode.tsx frontend/src/components/FacetSidebar.tsx frontend/src/components/FacetNode.test.tsx
git commit -m "feat(frontend): recursive facet sidebar"
```

---

### Task 10: SearchPage wiring (URL state + layout)

**Files:**
- Modify: `frontend/src/routes/SearchPage.tsx`
- Create: `frontend/src/routes/SearchPage.test.tsx`

**Interfaces:**
- Consumes: all controls (Task 6), `ResultsList` (Task 8), `FacetSidebar` (Task 9), `useSources` (Task 4), `parseSearchState`/`toSearchParams` (Task 3).
- Produces: the full search page. Builds `labelOf` from `useSources()` catalog (flatten key→label) for `ScopeChips`.

- [ ] **Step 1: Write the failing page test**

`frontend/src/routes/SearchPage.test.tsx`:
```tsx
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- SearchPage` → Expected: FAIL (stub has no results).

- [ ] **Step 3: Implement SearchPage**

`frontend/src/routes/SearchPage.tsx`:
```tsx
import { useSearchParams } from "react-router-dom";
import { useMemo } from "react";
import { parseSearchState, toSearchParams, type SearchState } from "../lib/searchState";
import { useSources } from "../hooks/useSources";
import { SearchBar } from "../components/SearchBar";
import { RegexToggle } from "../components/RegexToggle";
import { Toolbar } from "../components/Toolbar";
import { ScopeChips } from "../components/ScopeChips";
import { ResultsList } from "../components/ResultsList";
import { FacetSidebar } from "../components/FacetSidebar";
import type { CatalogNode } from "../api/types";

function flattenLabels(nodes: CatalogNode[], out: Record<string, string> = {}) {
  for (const n of nodes) { out[n.key] = n.label; if (n.children) flattenLabels(n.children, out); }
  return out;
}

export default function SearchPage() {
  const [sp, setSp] = useSearchParams();
  const state = parseSearchState(sp);
  const sources = useSources();
  const labels = useMemo(() => flattenLabels(sources.data?.catalog ?? []), [sources.data]);
  const labelOf = (k: string) => labels[k] ?? k;
  const regexFields = sources.data?.regex_fields ?? ["body_text"];

  const patch = (p: Partial<SearchState>) => setSp(toSearchParams({ ...state, ...p }));

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-slate-200 px-6 py-3 space-y-2">
        <div className="flex items-center gap-4">
          <SearchBar state={state} onChange={patch} />
          <RegexToggle state={state} onChange={patch} />
          <Toolbar state={state} regexFields={regexFields} onChange={patch} />
        </div>
        <ScopeChips state={state} labelOf={labelOf} onChange={patch} />
      </header>
      <div className="flex flex-1 min-h-0">
        <main className="flex-1 min-w-0 overflow-y-auto px-6"><ResultsList state={state} /></main>
        <FacetSidebar state={state} onChange={patch} />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test + dev server**

Run: `npm run test -- SearchPage` → Expected: PASS.
Run (with API up): `npm run dev`, open `/?q=gæsluvarðhald` → Expected: real results, facet counts, chips appear when you check a facet, URL updates. Stop server.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/SearchPage.tsx frontend/src/routes/SearchPage.test.tsx
git commit -m "feat(frontend): wire SearchPage (URL state + layout)"
```

---

### Task 11: DocumentPage

**Files:**
- Create: `frontend/src/components/DocHeader.tsx`, `frontend/src/components/DocPanel.tsx`, `frontend/src/components/DocPanel.test.tsx`
- Modify: `frontend/src/routes/DocumentPage.tsx`

**Interfaces:**
- Consumes: `useDocument` (Task 4), `DocumentDetail`/`AppealLink` (Task 2), `markHtml` (Task 7).
- Produces: `DocHeader({ doc })`, `DocPanel({ doc })`, and the wired `DocumentPage` reading `:id` from the route.

- [ ] **Step 1: Write the failing DocPanel test**

`frontend/src/components/DocPanel.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { DocPanel } from "./DocPanel";

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- DocPanel` → Expected: FAIL.

- [ ] **Step 3: Implement DocHeader, DocPanel, DocumentPage**

`frontend/src/components/DocHeader.tsx`:
```tsx
import { Link } from "react-router-dom";
import type { DocumentDetail } from "../api/types";

export function DocHeader({ doc }: { doc: DocumentDetail }) {
  return (
    <div className="flex items-center gap-3 border-b border-slate-200 px-6 py-3">
      <Link to="/" aria-label="Til baka" className="text-slate-500">←</Link>
      <div className="font-semibold">{doc.urlausn}</div>
      <span className="text-sm text-slate-500">{doc.source_display}{doc.verdict_type ? ` – ${doc.verdict_type}` : ""}</span>
      {doc.url && <a href={doc.url} target="_blank" rel="noreferrer" className="text-indigo-600 text-sm" title="Opna frumrit">↗</a>}
    </div>
  );
}
```

`frontend/src/components/DocPanel.tsx`:
```tsx
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import type { DocumentDetail, Party } from "../api/types";

const parties = (ps: Party[]) => ps.map((p) => p.name).join(", ");

export function DocPanel({ doc }: { doc: DocumentDetail }) {
  return (
    <div className="bg-[#f5f7fb] flex-1 overflow-y-auto py-8">
      <article className="mx-auto max-w-2xl bg-white rounded-lg p-8 shadow-sm">
        <header className="text-center space-y-1 mb-6">
          <h1 className="text-2xl font-bold uppercase">{doc.source_display}</h1>
          {doc.case_number && <div className="font-semibold">Mál nr. {doc.case_number}</div>}
          {doc.document_date && <div className="text-slate-600">{doc.document_date}</div>}
          {doc.plaintiffs.length > 0 && <div className="font-semibold pt-2">{parties(doc.plaintiffs)}</div>}
          {doc.defendants.length > 0 && <><div className="text-slate-600">gegn</div>
            <div className="font-semibold">{parties(doc.defendants)}</div></>}
        </header>

        {doc.keywords.length > 0 && (
          <section className="mb-6">
            <h2 className="font-bold mb-2">Lykilorð</h2>
            <div className="flex flex-wrap gap-1.5">
              {doc.keywords.map((k) => (
                <span key={k} className="text-sm text-slate-600 bg-slate-100 rounded-full px-3 py-0.5">{k}</span>
              ))}
            </div>
          </section>
        )}

        {doc.summary && (
          <section className="mb-6">
            <h2 className="font-bold mb-2">Reifun</h2>
            <p className="italic text-slate-800 leading-relaxed">{doc.summary}</p>
          </section>
        )}

        <section className="prose prose-slate max-w-none prose-headings:font-bold prose-headings:text-base">
          <ReactMarkdown>{doc.markdown ?? doc.body_text ?? ""}</ReactMarkdown>
        </section>

        {doc.appeal_links.length > 0 && (
          <section className="mt-8 border-t border-slate-200 pt-4">
            <h2 className="font-bold mb-2">Tengd mál</h2>
            <ul className="space-y-1">
              {doc.appeal_links.map((l) => (
                <li key={l.document_id} className="text-sm">
                  <span className="text-slate-500">{l.relation === "appealed_to" ? "Áfrýjað frá: " : "Áfrýjað til: "}</span>
                  <Link to={`/domur/${l.document_id}`} className="text-indigo-700 hover:underline">{l.urlausn}</Link>
                </li>
              ))}
            </ul>
          </section>
        )}
      </article>
    </div>
  );
}
```

`frontend/src/routes/DocumentPage.tsx`:
```tsx
import { useParams } from "react-router-dom";
import { useDocument } from "../hooks/useDocument";
import { DocHeader } from "../components/DocHeader";
import { DocPanel } from "../components/DocPanel";
import { ErrorState } from "../components/states";

export default function DocumentPage() {
  const { id = "" } = useParams();
  const { data, isPending, isError, error } = useDocument(id);
  if (isPending) return <div className="p-8"><div className="h-64 bg-slate-100 rounded animate-pulse" /></div>;
  if (isError) return <ErrorState error={error} />;
  return (
    <div className="flex h-full flex-col">
      <DocHeader doc={data} />
      <DocPanel doc={data} />
    </div>
  );
}
```

Add the Tailwind typography plugin for `prose`:
```bash
cd frontend && npm install -D @tailwindcss/typography
```
Then in `src/index.css` after the tailwind import add: `@plugin "@tailwindcss/typography";`

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- DocPanel` → Expected: PASS.

- [ ] **Step 5: Verify against API**

Run (API up): `npm run dev`; from a search, click a result → Expected: document renders with header, keywords, reifun, body sections, appeal links. Stop server.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/DocHeader.tsx frontend/src/components/DocPanel.tsx \
  frontend/src/components/DocPanel.test.tsx frontend/src/routes/DocumentPage.tsx \
  frontend/src/index.css frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): DocumentPage (header + panel + markdown + appeal links)"
```

---

### Task 12: CatalogPage (/heimildir)

**Files:**
- Create: `frontend/src/components/CatalogTree.tsx`, `frontend/src/components/CatalogTree.test.tsx`
- Modify: `frontend/src/routes/CatalogPage.tsx`

**Interfaces:**
- Consumes: `useSources` (Task 4), `CatalogNode` (Task 2).
- Produces: `CatalogTree({ nodes })` rendering each node as a link to `/?scope=<key>`; wired `CatalogPage`.

- [ ] **Step 1: Write the failing test**

`frontend/src/components/CatalogTree.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { CatalogTree } from "./CatalogTree";

const nodes = [{ key: "domstolar", label: "Dómstólar", count: 44155, children: [
  { key: "haestirettur", label: "Hæstiréttur", count: 13466 }] }];

describe("CatalogTree", () => {
  it("links each node to a scoped search", () => {
    renderWithProviders(<CatalogTree nodes={nodes} />);
    expect(screen.getByRole("link", { name: /Dómstólar/ })).toHaveAttribute("href", "/?scope=domstolar");
    expect(screen.getByRole("link", { name: /Hæstiréttur/ })).toHaveAttribute("href", "/?scope=haestirettur");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- CatalogTree` → Expected: FAIL.

- [ ] **Step 3: Implement CatalogTree + CatalogPage**

`frontend/src/components/CatalogTree.tsx`:
```tsx
import { Link } from "react-router-dom";
import type { CatalogNode } from "../api/types";

function Node({ node, depth }: { node: CatalogNode; depth: number }) {
  return (
    <div>
      <div className="flex items-center gap-2 py-1" style={{ paddingLeft: depth * 16 }}>
        <Link to={`/?scope=${encodeURIComponent(node.key)}`} className="text-indigo-700 hover:underline">{node.label}</Link>
        <span className="text-xs text-slate-400 tabular-nums">{node.count.toLocaleString("is-IS")}</span>
      </div>
      {node.children?.map((c) => <Node key={c.key} node={c} depth={depth + 1} />)}
    </div>
  );
}

export function CatalogTree({ nodes }: { nodes: CatalogNode[] }) {
  return <div>{nodes.map((n) => <Node key={n.key} node={n} depth={0} />)}</div>;
}
```

`frontend/src/routes/CatalogPage.tsx`:
```tsx
import { useSources } from "../hooks/useSources";
import { CatalogTree } from "../components/CatalogTree";

export default function CatalogPage() {
  const { data, isPending } = useSources();
  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-bold mb-4">Heimildir</h1>
      {isPending ? <div className="h-64 bg-slate-100 rounded animate-pulse" />
        : <CatalogTree nodes={data!.catalog} />}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- CatalogTree` → Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CatalogTree.tsx frontend/src/components/CatalogTree.test.tsx frontend/src/routes/CatalogPage.tsx
git commit -m "feat(frontend): catalog browse page (/heimildir)"
```

---

### Task 13: Full-suite check + manual E2E verification

**Files:** none (verification only).

- [ ] **Step 1: Run the whole suite**

Run: `cd frontend && npm run test` → Expected: all tests pass.
Run: `npm run build` → Expected: type-check + build succeed, no TS errors.

- [ ] **Step 2: Manual E2E against the live API**

Start API: `cd /Volumes/RuleOfLaw/Lausnir && DATABASE_URL="postgresql+asyncpg://geiri@localhost/lausnir_v2" uv run uvicorn engine.api.app:app --port 8077` (separate terminal).
Start frontend: `cd frontend && npm run dev`.
Walk through (Playwright MCP or browser):
1. `/` → type "gæsluvarðhald" → results render, facet counts populate.
2. Check "Hæstiréttur – Dómar" in sidebar → chip appears, URL gets `scope=haestirettur_domar`, results narrow to Hæstiréttur Dómar only.
3. Toggle Regex → placeholder changes; search `sératkvæð` → matches; invalid `[` → inline error.
4. Set Tímabil from 2020-01-01 → results + facet counts update.
5. Click a result → document page: header, keywords, reifun, body sections, appeal links; back arrow returns.
6. Nav to `/heimildir` → tree with counts; click a node → scoped search.
Expected: every step behaves as designed.

- [ ] **Step 3: Final commit (if any tweaks)**

```bash
git add -A && git commit -m "chore(frontend): v1 verification fixes" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:** search page (Tasks 6–10), facet sidebar w/ live counts (Task 9, useFacets excludes scope), result cards w/ parties+keywords+highlight (Task 7), infinite scroll (Task 8), document view w/ markdown+appeal links (Task 11), catalog browse (Task 12), nav-rail (Task 5), regex UI + fields popover (Task 6 Toolbar/RegexToggle), date range (Task 6 Toolbar), states (Task 8 states.tsx), URL-as-truth (Task 3 + Task 10), tokens/Inter (Task 1), tests across tasks (Task 13 suite). All spec sections map to a task.

**Placeholder scan:** no TBD/TODO; every code step has full code; commands have expected output.

**Type consistency:** `SearchState`, `SearchParams`, `CatalogNode`, `SearchResult`, `DocumentDetail`, `Party`, `AppealLink` used consistently across tasks; `onChange(patch: Partial<SearchState>)` is the uniform control contract; `markHtml` defined in Task 7 before its reuse in Task 11; `useFacets` key excludes scope per Global Constraints.

**Out of scope confirmed:** no folders/notes/similar-docs/law-register tasks — matches the spec's "Utan umfangs".
