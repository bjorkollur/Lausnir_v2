# Search UI Redesign + Keyword Filter + Provision-on-Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a keyword-only filter (matches the `keywords` JSONB tag column, not body text), surface provision search on the landing page (not just the in-results toolbar), and redesign the frontend with the `minimalist-ui` design skill — replacing the abandoned uncommitted redesign currently sitting in the working tree.

**Architecture:** Backend gets one new pure filter-builder function (`_build_keyword_filter`, mirrors the existing `_build_provision_filter`) plus a `keyword` parameter threaded through `search_documents` and the `/api/search` endpoint. Frontend gets a new `KeywordInput` component (structurally identical to the existing `ProvisionInput`) and both inputs render on `LandingView` as well as `Toolbar`. The visual redesign is a separate pass applied after the functional wiring is in place, so the design work restyles real, working inputs rather than placeholder markup.

**Tech Stack:** FastAPI, SQLAlchemy (raw `text()` queries), PostgreSQL JSONB, React 19, TypeScript, Tailwind CSS v4, React Router, Playwright (manual verification, no test suite exists for the frontend).

## Global Constraints

- Backend filter additions must be pure functions unit-testable without a DB session, matching the existing `_build_provision_filter` pattern in `engine/search/queries.py`.
- Every new query parameter that combines with text search must also be threaded into `_search_by_chunks`'s `extra_where`/`extra_params` — omitting this silently drops the filter when scope resolves entirely to chunked sources (`logfraediritgerdir`, `baekur`). This is a known regression class (see the `provision` filter's "Bug 2" fix already in this codebase).
- Frontend state threading for any new search parameter touches exactly four files in this order: `frontend/src/api/types.ts` (interface), `frontend/src/lib/searchState.ts` (URL ↔ state), `frontend/src/api/client.ts` (state → query string), and the component(s) that read/write it. `provision` already does this — `keyword` must mirror it exactly.
- Visual redesign uses the `minimalist-ui` skill (document-style workspace aesthetic — warm monochrome, typographic hierarchy, flat surfaces) as the primary design language. Do NOT apply `gpt-taste` or `high-end-visual-design` skill directives (GSAP motion, bento grids, hero archetypes) — this is a dense search-results tool, not a marketing landing page. `design-taste-frontend`'s own Section 13 explicitly lists "dense product UI" as out of scope for its marketing-page vocabulary — apply only its Section 11 (audit-before-touching methodology), not its block library.
- No functional regressions: every interactive control listed in each task's "Must keep working" checklist must still work after that task's changes — verified by Task 5.

---

### Task 1: Backend — keyword filter in `search_documents` + `/api/search`

**Files:**
- Modify: `engine/search/queries.py:89-116` (add `_build_keyword_filter` after `_build_provision_filter`), `engine/search/queries.py:416-430` (add `keyword` param), `engine/search/queries.py:472-526` (wire filter + chunk routing)
- Modify: `engine/api/app.py:121-145` (add `keyword` query param)
- Test: `tests/test_search_queries.py`

**Interfaces:**
- Produces: `_build_keyword_filter(keyword: str) -> tuple[str, dict]` — pure function, same shape as `_build_provision_filter`. Returns `("d.keywords::text ILIKE :keyword_pattern", {"keyword_pattern": f"%{keyword}%"})`.
- Produces: `search_documents(..., keyword: str | None = None)` — new keyword-only param, combines with `q`/`provision`/`scope`/dates via AND.
- Produces: `/api/search?keyword=...` — new query param on the existing endpoint.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_search_queries.py` (append at end of file):

```python
def test_build_keyword_filter_basic():
    from engine.search.queries import _build_keyword_filter
    frag, params = _build_keyword_filter("skaðabætur")
    assert frag == "d.keywords::text ILIKE :keyword_pattern"
    assert params == {"keyword_pattern": "%skaðabætur%"}


def test_build_keyword_filter_uses_named_param():
    from engine.search.queries import _build_keyword_filter
    frag, params = _build_keyword_filter("forsjá")
    assert ":keyword_pattern" in frag
    assert "keyword_pattern" in params
    assert params["keyword_pattern"] == "%forsjá%"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Volumes/RuleOfLaw/Lausnir && uv run pytest tests/test_search_queries.py -k keyword_filter -v`
Expected: FAIL with `ImportError: cannot import name '_build_keyword_filter'`

- [ ] **Step 3: Add `_build_keyword_filter`**

In `engine/search/queries.py`, insert immediately after the `_build_provision_filter` function (after the line `{"prov_filter": json.dumps([obj])},` and the closing `)` — i.e. right before the blank line that precedes `from sqlalchemy import text`):

```python
def _build_keyword_filter(keyword: str) -> tuple[str, dict]:
    """Build a WHERE fragment matching the keywords JSONB tag column only.

    Case-insensitive substring match — `keywords::text` casts the JSONB array
    to its text representation (e.g. '["forsjá", "skaðabætur"]') and ILIKE
    matches anywhere in it. Same mechanism the existing regex-mode "Lykilorð"
    field already uses (REGEX_COLUMNS["keywords"]), just exposed without
    requiring the user to switch into regex mode.

    Returns (sql_fragment, params_dict) where sql_fragment uses :keyword_pattern.
    """
    return (
        "d.keywords::text ILIKE :keyword_pattern",
        {"keyword_pattern": f"%{keyword}%"},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Volumes/RuleOfLaw/Lausnir && uv run pytest tests/test_search_queries.py -k keyword_filter -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Add `keyword` parameter to `search_documents`**

In `engine/search/queries.py`, find the `search_documents` signature:

```python
async def search_documents(
    session: AsyncSession,
    *,
    q: str = "",
    mode: str = "keyword",
    scope: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    regex_fields: list[str] | None = None,
    proximity_n: int = 5,
    provision: str | None = None,
) -> SearchResults:
```

Change the last parameter line to add `keyword`:

```python
async def search_documents(
    session: AsyncSession,
    *,
    q: str = "",
    mode: str = "keyword",
    scope: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    regex_fields: list[str] | None = None,
    proximity_n: int = 5,
    provision: str | None = None,
    keyword: str | None = None,
) -> SearchResults:
```

- [ ] **Step 6: Wire the keyword filter into the WHERE clause**

Find the provision filter block (ends with the `where.append(prov_frag)` / `params.update(prov_params)` pair inside the bare-law fallback, followed by the blank line and `has_text = bool(q)`):

```python
            else:
                prov_frag, prov_params = _build_provision_filter(bare_law.group(1), None, None, None)
                where.append(prov_frag)
                params.update(prov_params)

    has_text = bool(q)
```

Insert a new block between the provision block and `has_text = bool(q)`:

```python
            else:
                prov_frag, prov_params = _build_provision_filter(bare_law.group(1), None, None, None)
                where.append(prov_frag)
                params.update(prov_params)

    # Keyword filter (independent of text mode) — matches the keywords JSONB
    # tag column only, never body text.
    if keyword and keyword.strip():
        kw_frag, kw_params = _build_keyword_filter(keyword.strip())
        where.append(kw_frag)
        params.update(kw_params)

    has_text = bool(q)
```

- [ ] **Step 7: Wire the keyword filter into chunk-routing (`_search_by_chunks`)**

Find this block (the chunk-routing path for keyword-mode search over chunked scopes):

```python
                # Bug 2 fix: build the provision filter and pass it into the chunk search
                # so it is not silently dropped when routing through _search_by_chunks.
                prov_extra_where: list[str] = []
                prov_extra_params: dict = {}
                if provision:
                    _parsed = parse_provision_query(provision)
                    if _parsed:
                        _law, _gr, _sfx, _mgr = _parsed
                        _prov_frag, _prov_prm = _build_provision_filter(_law, _gr, _sfx, _mgr)
                        prov_extra_where.append(_prov_frag)
                        prov_extra_params.update(_prov_prm)
                    else:
                        _bare = re.search(r'\b(\d+/\d{4})\b', provision)
                        if _bare:
                            _prov_frag, _prov_prm = _build_provision_filter(
                                _bare.group(1), None, None, None
                            )
                            prov_extra_where.append(_prov_frag)
                            prov_extra_params.update(_prov_prm)
                return await _search_by_chunks(
                    session,
                    lemmas=lemmas,
                    scope_filter=scope_filter,
                    date_from=date_from,
                    date_to=date_to,
                    sort=sort,
                    page=page,
                    page_size=page_size,
                    extra_where=prov_extra_where or None,
                    extra_params=prov_extra_params or None,
                )
```

Replace it with (renames `prov_extra_*` to `chunk_extra_*` since the accumulator now carries more than just the provision filter, and adds the keyword filter into the same accumulator):

```python
                # Build extra WHERE fragments (provision + keyword) and pass them into
                # the chunk search so neither is silently dropped when routing through
                # _search_by_chunks (same regression class as the provision "Bug 2" fix).
                chunk_extra_where: list[str] = []
                chunk_extra_params: dict = {}
                if provision:
                    _parsed = parse_provision_query(provision)
                    if _parsed:
                        _law, _gr, _sfx, _mgr = _parsed
                        _prov_frag, _prov_prm = _build_provision_filter(_law, _gr, _sfx, _mgr)
                        chunk_extra_where.append(_prov_frag)
                        chunk_extra_params.update(_prov_prm)
                    else:
                        _bare = re.search(r'\b(\d+/\d{4})\b', provision)
                        if _bare:
                            _prov_frag, _prov_prm = _build_provision_filter(
                                _bare.group(1), None, None, None
                            )
                            chunk_extra_where.append(_prov_frag)
                            chunk_extra_params.update(_prov_prm)
                if keyword and keyword.strip():
                    _kw_frag, _kw_prm = _build_keyword_filter(keyword.strip())
                    chunk_extra_where.append(_kw_frag)
                    chunk_extra_params.update(_kw_prm)
                return await _search_by_chunks(
                    session,
                    lemmas=lemmas,
                    scope_filter=scope_filter,
                    date_from=date_from,
                    date_to=date_to,
                    sort=sort,
                    page=page,
                    page_size=page_size,
                    extra_where=chunk_extra_where or None,
                    extra_params=chunk_extra_params or None,
                )
```

- [ ] **Step 8: Add `keyword` to the `/api/search` endpoint**

In `engine/api/app.py`, find:

```python
@app.get("/api/search")
async def search(
    q: str = Query("", description="Search text or regex pattern"),
    mode: str = Query("keyword", pattern="^(keyword|exact|prefix|substring|any|proximity|regex)$"),
    scope: list[str] | None = Query(None, description="Group labels, source short_names, or 'all'"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    sort: str = Query("relevance", pattern="^(relevance|newest|oldest)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=100),
    regex_fields: list[str] | None = Query(None, description="Fields for regex mode"),
    proximity_n: int = Query(5, ge=1, le=50),
    provision: str | None = Query(None, description="Provision reference, e.g. '218. gr. 19/1940'"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        res = await search_documents(
            session, q=q, mode=mode, scope=scope,
            date_from=date_from, date_to=date_to, sort=sort,
            page=page, page_size=page_size, regex_fields=regex_fields,
            proximity_n=proximity_n, provision=provision,
        )
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"total": res.total, "page": res.page, "page_size": res.page_size, "results": res.results}
```

Replace with:

```python
@app.get("/api/search")
async def search(
    q: str = Query("", description="Search text or regex pattern"),
    mode: str = Query("keyword", pattern="^(keyword|exact|prefix|substring|any|proximity|regex)$"),
    scope: list[str] | None = Query(None, description="Group labels, source short_names, or 'all'"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    sort: str = Query("relevance", pattern="^(relevance|newest|oldest)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=100),
    regex_fields: list[str] | None = Query(None, description="Fields for regex mode"),
    proximity_n: int = Query(5, ge=1, le=50),
    provision: str | None = Query(None, description="Provision reference, e.g. '218. gr. 19/1940'"),
    keyword: str | None = Query(None, description="Filter by keywords/tags column only, substring match"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        res = await search_documents(
            session, q=q, mode=mode, scope=scope,
            date_from=date_from, date_to=date_to, sort=sort,
            page=page, page_size=page_size, regex_fields=regex_fields,
            proximity_n=proximity_n, provision=provision, keyword=keyword,
        )
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"total": res.total, "page": res.page, "page_size": res.page_size, "results": res.results}
```

- [ ] **Step 9: Run the full backend test suite**

Run: `cd /Volumes/RuleOfLaw/Lausnir && uv run pytest tests/test_search_queries.py tests/test_provision_search.py -v`
Expected: all PASS (existing tests + 2 new ones), no regressions.

- [ ] **Step 10: Manual smoke test against the running API**

Run (requires the API server running per the project's existing `--env-file .env` startup):
```bash
curl -s "http://localhost:8077/api/search?keyword=for%20sj%C3%A1&page_size=3" | python3 -m json.tool
```
Expected: valid JSON response with `total` and `results` keys, no 500 error. (Exact `total` count depends on live data — any non-error response with the right shape confirms the wiring works.)

- [ ] **Step 11: Commit**

```bash
git add engine/search/queries.py engine/api/app.py tests/test_search_queries.py
git commit -m "feat: add keyword-only filter on the keywords JSONB tag column"
```

---

### Task 2: Frontend — discard uncommitted redesign, thread `keyword`, surface provision search on landing

**Files:**
- Discard (git checkout to HEAD): `frontend/src/components/LandingView.tsx`, `frontend/src/components/ModeDropdown.tsx`, `frontend/src/components/ResultCard.tsx`, `frontend/src/components/ScopeChips.tsx`, `frontend/src/components/SearchBar.tsx`, `frontend/src/components/SourceTree.tsx`, `frontend/src/index.css`
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/lib/searchState.ts`, `frontend/src/components/Toolbar.tsx`, `frontend/src/components/LandingView.tsx`, `frontend/src/routes/SearchPage.tsx`

**Interfaces:**
- Consumes: `engine/api/app.py`'s `/api/search?keyword=` param from Task 1.
- Produces: `SearchState.keyword?: string` — consumed by Task 3/4 (redesign must preserve this field's UI, not remove it).
- Produces: `export function KeywordInput({ value, onChange }: { value: string; onChange: (v: string) => void })` in `frontend/src/components/Toolbar.tsx` — importable by `LandingView.tsx`.
- Produces: `export function ProvisionInput(...)` (newly exported, was private) in the same file, importable by `LandingView.tsx`.

- [ ] **Step 1: Discard the uncommitted partial redesign**

These seven files currently have uncommitted changes from an abandoned redesign attempt. Discard them to return to the last-committed baseline before making functional changes — this avoids functional edits landing on top of half-finished styling that Task 3/4 will replace anyway.

```bash
cd /Volumes/RuleOfLaw/Lausnir
git checkout -- frontend/src/components/LandingView.tsx frontend/src/components/ModeDropdown.tsx frontend/src/components/ResultCard.tsx frontend/src/components/ScopeChips.tsx frontend/src/components/SearchBar.tsx frontend/src/components/SourceTree.tsx frontend/src/index.css
git status --short frontend/
```
Expected: `git status --short frontend/` shows no `M` lines for those seven files.

- [ ] **Step 2: Add `keyword` to `SearchParams` (types.ts)**

In `frontend/src/api/types.ts`, find:

```typescript
export interface SearchParams {
  q: string; mode: Mode; scope: string[];
  date_from?: string; date_to?: string; sort: Sort;
  page?: number; page_size?: number; regex_fields?: string[];
  proximity_n?: number; provision?: string;
}
```

Replace with:

```typescript
export interface SearchParams {
  q: string; mode: Mode; scope: string[];
  date_from?: string; date_to?: string; sort: Sort;
  page?: number; page_size?: number; regex_fields?: string[];
  proximity_n?: number; provision?: string; keyword?: string;
}
```

- [ ] **Step 3: Add `keyword` to `searchQs` (client.ts)**

In `frontend/src/api/client.ts`, find:

```typescript
  if (p.provision) qs.set("provision", p.provision);
  return qs;
}
```

Replace with:

```typescript
  if (p.provision) qs.set("provision", p.provision);
  if (p.keyword) qs.set("keyword", p.keyword);
  return qs;
}
```

- [ ] **Step 4: Add `keyword` to `SearchState` (searchState.ts)**

In `frontend/src/lib/searchState.ts`, find:

```typescript
export interface SearchState {
  q: string;
  mode: Mode;
  scope: string[];
  date_from?: string;
  date_to?: string;
  sort: Sort;
  regex_fields: string[];
  proximity_n: number;
  provision?: string;
}
```

Replace with:

```typescript
export interface SearchState {
  q: string;
  mode: Mode;
  scope: string[];
  date_from?: string;
  date_to?: string;
  sort: Sort;
  regex_fields: string[];
  proximity_n: number;
  provision?: string;
  keyword?: string;
}
```

Find `parseSearchState`'s return object:

```typescript
    proximity_n,
    provision: sp.get("provision") ?? undefined,
  };
}
```

Replace with:

```typescript
    proximity_n,
    provision: sp.get("provision") ?? undefined,
    keyword: sp.get("keyword") ?? undefined,
  };
}
```

Find `toSearchParams`:

```typescript
  if (s.provision) sp.set("provision", s.provision);
  for (const x of s.scope) sp.append("scope", x);
```

Replace with:

```typescript
  if (s.provision) sp.set("provision", s.provision);
  if (s.keyword) sp.set("keyword", s.keyword);
  for (const x of s.scope) sp.append("scope", x);
```

- [ ] **Step 5: Export `ProvisionInput` and add `KeywordInput` in Toolbar.tsx**

In `frontend/src/components/Toolbar.tsx`, find:

```typescript
function ProvisionInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
```

Replace with:

```typescript
export function ProvisionInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
```

At the end of the file, after the closing `}` of `ProvisionInput`, append:

```typescript

// ── KeywordInput ──────────────────────────────────────────────────────────────

export function KeywordInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  const submit = (e: FormEvent) => { e.preventDefault(); onChange(draft.trim()); };
  const clear = () => { setDraft(""); onChange(""); };

  return (
    <form onSubmit={submit} className="flex items-center gap-1">
      <div className="relative flex items-center">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Lykilorð…"
          aria-label="Leita eftir lykilorði"
          className={`text-sm border rounded-sm px-3 py-1.5 w-44 outline-none transition-colors ${
            value ? "border-indigo-400 bg-indigo-50" : "border-slate-300 bg-white hover:border-slate-400"
          } focus:border-[#0a246a]`}
        />
        {draft && (
          <button
            type="button"
            onClick={clear}
            aria-label="Hreinsa lykilorð"
            className="absolute right-1.5 text-slate-400 hover:text-slate-600 text-xs leading-none"
          >
            ✕
          </button>
        )}
      </div>
      {draft !== value && (
        <button
          type="submit"
          className="text-xs text-indigo-600 hover:text-indigo-800 px-1"
        >
          Leita
        </button>
      )}
    </form>
  );
}
```

In the same file, find the `Toolbar` component's JSX where `ProvisionInput` is rendered:

```typescript
      <ProvisionInput value={state.provision ?? ""} onChange={(v) => onChange({ provision: v || undefined })} />
```

Replace with (adds `KeywordInput` right after it):

```typescript
      <ProvisionInput value={state.provision ?? ""} onChange={(v) => onChange({ provision: v || undefined })} />

      <KeywordInput value={state.keyword ?? ""} onChange={(v) => onChange({ keyword: v || undefined })} />
```

- [ ] **Step 6: Render `ProvisionInput` and `KeywordInput` on `LandingView`**

In `frontend/src/components/LandingView.tsx`, add the import at the top of the file, alongside the existing `SourceTree` import:

```typescript
import { SourceTree } from "./SourceTree";
import { ProvisionInput, KeywordInput } from "./Toolbar";
```

Find the simple search form block:

```typescript
        {/* Simple search bar */}
        <form onSubmit={handleSubmit} className="w-full flex gap-2">
          <input
            ref={searchInputRef}
            autoFocus
            value={localQ}
            onChange={(e) => setLocalQ(e.target.value)}
            placeholder={
              state.mode === "regex" ? "regex mynstur…" : "Leita í réttarheimildum…"
            }
            aria-label="Leitarbox"
            className="flex-1 h-14 rounded-full border-2 border-slate-200 px-6 text-lg outline-none focus:border-indigo-500 transition-colors"
          />
          <button
            type="submit"
            disabled={!localQ.trim()}
            className="h-14 px-8 bg-indigo-600 text-white rounded-full font-semibold hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Leita
          </button>
        </form>

        {/* Stats footer */}
```

Replace with (adds a row for the two structured filters directly below the main search form):

```typescript
        {/* Simple search bar */}
        <form onSubmit={handleSubmit} className="w-full flex gap-2">
          <input
            ref={searchInputRef}
            autoFocus
            value={localQ}
            onChange={(e) => setLocalQ(e.target.value)}
            placeholder={
              state.mode === "regex" ? "regex mynstur…" : "Leita í réttarheimildum…"
            }
            aria-label="Leitarbox"
            className="flex-1 h-14 rounded-full border-2 border-slate-200 px-6 text-lg outline-none focus:border-indigo-500 transition-colors"
          />
          <button
            type="submit"
            disabled={!localQ.trim()}
            className="h-14 px-8 bg-indigo-600 text-white rounded-full font-semibold hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Leita
          </button>
        </form>

        {/* Structured filters: provision reference + keyword tag */}
        <div className="flex flex-wrap items-center gap-2">
          <ProvisionInput
            value={state.provision ?? ""}
            onChange={(v) => patch({ provision: v || undefined })}
          />
          <KeywordInput
            value={state.keyword ?? ""}
            onChange={(v) => patch({ keyword: v || undefined })}
          />
        </div>

        {/* Stats footer */}
```

- [ ] **Step 7: Update `SearchPage`'s landing-page gate**

In `frontend/src/routes/SearchPage.tsx`, find:

```typescript
  // Show landing page when no active query or filter
  if (!state.q && !state.provision && state.scope.length === 0) {
```

Replace with:

```typescript
  // Show landing page when no active query or filter
  if (!state.q && !state.provision && !state.keyword && state.scope.length === 0) {
```

- [ ] **Step 8: Type-check and build**

Run: `cd /Volumes/RuleOfLaw/Lausnir/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 9: Manual verification with the dev server**

Start both servers (if not already running):
```bash
cd /Volumes/RuleOfLaw/Lausnir && DATABASE_URL=postgresql+asyncpg://geiri@localhost/lausnir_v2 uv run uvicorn engine.api.app:app --host 0.0.0.0 --port 8077 --reload &
cd /Volumes/RuleOfLaw/Lausnir/frontend && npx vite --host 0.0.0.0 --port 5173 &
```
Open `http://localhost:5173/` and confirm:
- The landing page shows both a "Lagaákvæði…" field and a "Lykilorð…" field next to the main search box.
- Typing into the "Lykilorð…" field and submitting navigates to the results page with `?keyword=...` in the URL.
- The results list is non-empty for a keyword known to exist (check via `curl http://localhost:8077/api/sources` or any existing result's visible tag chips for a real keyword to test with).

- [ ] **Step 10: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/lib/searchState.ts frontend/src/components/Toolbar.tsx frontend/src/components/LandingView.tsx frontend/src/routes/SearchPage.tsx frontend/src/components/ModeDropdown.tsx frontend/src/components/ResultCard.tsx frontend/src/components/ScopeChips.tsx frontend/src/components/SearchBar.tsx frontend/src/components/SourceTree.tsx frontend/src/index.css
git commit -m "feat: keyword-tag filter end-to-end, provision search on landing page; discard abandoned redesign"
```

---

### Task 3: Visual redesign — design tokens + landing page

**Files:**
- Modify: `frontend/src/index.css`, `frontend/src/components/LandingView.tsx`

**Interfaces:**
- Consumes: `SearchState.provision`, `SearchState.keyword`, `ProvisionInput`, `KeywordInput` from Task 2 — these fields and components must continue to exist and function identically; only their visual presentation changes.
- Produces: CSS custom properties / Tailwind `@theme` tokens in `index.css` (font family, accent color) that Task 4 reuses for visual consistency across the rest of the app.

This is a design task, not a mechanical transcription — the exact Tailwind classes are not prescribed here. What follows are the binding constraints the output must satisfy, extracted directly from the `minimalist-ui` skill (`.agents/skills/minimalist-ui/SKILL.md`).

**Apply (minimalist-ui, Sections 2–4):**
- No "Inter", "Roboto", or "Open Sans" as the primary typeface (the current `index.css` imports Inter — replace it).
- No Tailwind heavy shadows (`shadow-md`/`shadow-lg`/`shadow-xl`).
- No `rounded-full` pill shapes on large containers or primary buttons (the current "Leita" button and mode-tab buttons use `rounded-full` — replace with the skill's `4px`–`6px` radius guidance).
- No gradients, neon colors, or glassmorphism.
- Body text never pure black (`#000000`) — use off-black/charcoal per the skill's palette.
- Borders: `1px solid #EAEAEA` (or the skill's equivalent neutral) for structural dividers.
- Primary CTA: solid dark background, white text, no box-shadow, subtle hover shift — not the current `bg-indigo-600`/`rounded-full` button.

**Do NOT apply** (out of scope for a search utility, not a marketing page):
- Bento grid layouts (Section 5) — there is no feature-grid content here.
- Scroll-entry / staggered-reveal animations (Section 7) — `LandingView` is a single-screen form, not a scrolling marketing page.
- Hero background imagery / ambient gradient blobs (Section 6) — keep the canvas clean per the skill's own "Canvas / Background: Pure White or Warm Bone" guidance, skip the optional decorative imagery.
- `design-taste-frontend`'s block library (Section 12) — that skill's own Section 13 excludes dense product UI from its vocabulary; only its Section 11 audit methodology applies here (read the existing structure before changing it, preserve the `Lausnir` wordmark as a brand element rather than discarding it, preserve all aria-labels and accessibility attributes already present).

**Must keep working (verified in Task 5) — do not remove, rename, or break:**
- Main query input (`aria-label="Leitarbox"`), autoFocus behavior, submit on Enter and via the "Leita" button, disabled state when empty.
- `ProvisionInput` and `KeywordInput` (from Task 2) — both fields, their clear (✕) buttons, and their "Leita" submit-on-change buttons.
- Mode tab buttons for all 7 modes (`Orðaleit`, `Heilt orð`, `Byrjar á`, `Hluti af orði`, `Eitthvað af`, `Nálægt`, `Regex`), including the proximity `N orða` numeric input shown only in proximity mode.
- Date range inputs (`date_from`/`date_to`) and their "Hreinsa tímabil" clear button.
- `SourceTree` integration point (component itself is Task 4's responsibility, but `LandingView` must keep passing `catalog`, `scope`, `onScopeChange` to it unchanged).
- Stats footer (`{total} skjöl · {sourceCount} heimildir`).
- The advanced-search submit button and its disabled-state helper text.

- [ ] **Step 1: Update design tokens in `index.css`**

Replace the Inter font import and `--accent` token with a typeface and palette satisfying the constraints above. Keep the file structure (`@import "tailwindcss"`, `@plugin "@tailwindcss/typography"`, `@theme` block, `html, body, #root`, `body`, `mark` rules) — only change the font source, `--font-sans` value, `--accent` value, and add any new tokens (e.g. a serif token for the wordmark) needed by `LandingView`.

- [ ] **Step 2: Redesign `LandingView.tsx`**

Restyle the component per the constraints above. Every prop, every `patch(...)` call, and every piece of state (`localQ`, `handleSubmit`, `handleModeChange`, `handleScopeChange`) must remain functionally identical — only JSX structure and Tailwind classes change.

- [ ] **Step 3: Type-check**

Run: `cd /Volumes/RuleOfLaw/Lausnir/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Visual self-check against the constraint list**

Start the dev server (if not already running) and load `http://localhost:5173/`. Confirm against the "Apply" bullet list above: no Inter font, no heavy shadows, no `rounded-full` on the main CTA, no gradients. Confirm against "Must keep working": click through every control listed and verify it still does what it did before.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/index.css frontend/src/components/LandingView.tsx
git commit -m "redesign: landing page + design tokens (minimalist-ui)"
```

---

### Task 4: Visual redesign — dense UI components (results page, toolbar, scope chips, source tree, mode dropdown)

**Files:**
- Modify: `frontend/src/components/SearchBar.tsx`, `frontend/src/components/Toolbar.tsx`, `frontend/src/components/ResultCard.tsx`, `frontend/src/components/ScopeChips.tsx`, `frontend/src/components/SourceTree.tsx`, `frontend/src/components/ModeDropdown.tsx`

**Interfaces:**
- Consumes: design tokens (`--font-sans`, `--accent`, etc.) established in Task 3's `index.css` — reuse them, do not introduce a second competing palette.
- Consumes: `ProvisionInput`, `KeywordInput` exports from `Toolbar.tsx` (Task 2) — restyle their containing `Toolbar`, but the inputs' own JSX/logic was already finalized in Task 2; only wrap/arrange them differently if needed for layout, do not duplicate their implementation.

This results page (list of up to 100 results per page, facet sidebar, multi-row toolbar) is dense product UI, not a landing page. Apply only the token-level rules from `minimalist-ui` (typography, color, borders, spacing) — do not apply landing-page-specific patterns.

**Apply:**
- Same typography and color tokens as Task 3 (consistency across the app — a user should not see two different visual languages between the landing page and results page).
- Borders: `1px solid` neutral gray, consistent radius scale (no mixing `rounded-full` pills with `rounded-sm` rectangles arbitrarily — pick one scale and apply it to all chips/badges/buttons in this set of components).
- Off-black body text, muted gray secondary text, per the same palette as Task 3.

**Do NOT apply:**
- Per-row hover-lift shadows or scroll-entry animations on `ResultCard` — with up to 100 results per page, per-card motion is noise, not polish.
- Bento grids on `SourceTree` — it is a hierarchical checkbox tree, not a feature grid; keep its expand/collapse and two-column overflow behavior (`children.length > 8` grid) functionally intact.
- Any change to `ResultCard`'s `dangerouslySetInnerHTML={markHtml(r.snippet)}` usage or the `<mark>` highlight styling logic beyond cosmetic color — the highlighting mechanism itself is out of scope.

**Must keep working (verified in Task 5):**
- `SearchBar`: text input, mode-aware placeholder (`regex mynstur…` vs `Leita…`), submit on Enter.
- `Toolbar`: sort `<select>` (`aria-label="Röðun"`), the relevance option's `disabled={!FTS_MODES.has(state.mode)}` guard, Tímabil popover (date inputs), `ProvisionInput`, `KeywordInput`, Reitir popover (regex field checkboxes) — shown only when `REGEX_BACKED_MODES.has(state.mode)`.
- `ModeDropdown`: mode `<select>` (`aria-label="Leitarstilling"`), proximity `N orða` input shown only in proximity mode.
- `ResultCard`: link to `/domur/:id`, `has_appeal_links` indicator, source/date line, party-list truncation toggle ("Sjá meira"/"Sjá minna"), snippet `<mark>` highlighting, keyword tag list.
- `ScopeChips`: per-scope-token chip with its `aria-label="fjarlægja ${label}"` remove button, "Hreinsa N" clear-all button, returns `null` when `state.scope.length === 0`.
- `SourceTree`: expand/collapse toggle (`aria-label` "Fella saman"/"Opna"), checkbox `role="checkbox"` + `aria-checked` + `aria-label={label}` on every node, ancestor/descendant pruning logic when toggling a node (untouched — this is `toggle()` in the component body, not styling), the `children.length > 8` two-column grid layout, "Hreinsa val" clear button.

- [ ] **Step 1: Redesign `SearchBar.tsx`, `ModeDropdown.tsx`**

Apply token-level restyling. Preserve every prop and behavior listed above.

- [ ] **Step 2: Redesign `Toolbar.tsx`**

Restyle the sort select, Tímabil popover, and Reitir popover. `ProvisionInput`/`KeywordInput` JSX was finalized in Task 2 — adjust only their wrapping layout if needed for visual rhythm with the rest of the toolbar, not their internal markup or logic.

- [ ] **Step 3: Redesign `ResultCard.tsx`, `ScopeChips.tsx`, `SourceTree.tsx`**

Apply token-level restyling per the constraints above.

- [ ] **Step 4: Type-check**

Run: `cd /Volumes/RuleOfLaw/Lausnir/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Visual self-check against the constraint list**

Load a results page (e.g. `http://localhost:5173/?q=skaðabætur`) and confirm: consistent tokens with the landing page, no per-card animation, `SourceTree` two-column overflow still works when a group has more than 8 children, every "Must keep working" control still functions.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SearchBar.tsx frontend/src/components/Toolbar.tsx frontend/src/components/ResultCard.tsx frontend/src/components/ScopeChips.tsx frontend/src/components/SourceTree.tsx frontend/src/components/ModeDropdown.tsx
git commit -m "redesign: results page + toolbar + facet components (minimalist-ui)"
```

---

### Task 5: End-to-end verification

**Files:** none (verification only — produces fixes in the files above if errors are found)

**Interfaces:**
- Consumes: the complete, redesigned app from Tasks 1–4.

- [ ] **Step 1: Backend test suite**

Run: `cd /Volumes/RuleOfLaw/Lausnir && uv run pytest tests/ -v`
Expected: all tests pass, including the Task 1 additions.

- [ ] **Step 2: Frontend type-check**

Run: `cd /Volumes/RuleOfLaw/Lausnir/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Playwright pass — landing page**

With both dev servers running (`uvicorn` on `:8077`, `vite` on `:5173`), use Playwright to:
1. Navigate to `http://localhost:5173/`.
2. Take a screenshot, confirm visually: no Inter font, no `rounded-full` pills on primary buttons, no heavy shadows, no gradients (per Task 3's constraint list).
3. Check the browser console for errors (`browser_console_messages`) — must be empty of errors (warnings acceptable).
4. Type a known provision reference (e.g. `218. gr. 19/1940`) into the "Lagaákvæði…" field, submit, confirm the URL carries `?provision=` and the results page renders (not the landing page).
5. Navigate back to `/`, type a known keyword tag into the "Lykilorð…" field, submit, confirm the URL carries `?keyword=` and results render.
6. Navigate back to `/`, combine a text query (`q`) with a keyword filter in the same search, confirm both params appear in the URL and results are narrower than either filter alone (or equally empty if no overlap — confirm via the `total` count shown on the results page, not via re-deriving expected counts).

- [ ] **Step 4: Playwright pass — results page interactions**

1. From a results page, open the Tímabil popover, set a date range, confirm results update.
2. Open the Reitir popover (switch to `regex` mode first via `ModeDropdown` if needed), toggle a field checkbox, confirm it updates `regex_fields` in the URL.
3. Click a `ScopeChips` remove button, confirm the chip disappears and the URL updates.
4. In the `SourceTree` (landing page), expand a group with more than 8 children, confirm the two-column layout renders, toggle a child checkbox, confirm `scope` updates.
5. On a `ResultCard` with a long party list, click "Sjá meira", confirm it expands; click again, confirm it collapses back.

- [ ] **Step 5: Fix any errors found**

If any check in Steps 3–4 fails, identify which Task's file is responsible, fix it directly, re-run `npx tsc --noEmit` and the specific failing Playwright check, and repeat until all checks pass. Commit fixes with a message describing what was broken and why (e.g. `fix: restore keyword filter clear button after redesign regression`).

- [ ] **Step 6: Final commit (only if Step 5 produced fixes not yet committed)**

```bash
git add -A
git status --short  # confirm only expected files are staged
git commit -m "fix: address verification findings from end-to-end pass"
```
