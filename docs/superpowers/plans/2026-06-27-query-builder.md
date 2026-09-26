# Query Builder (Leitargluggi v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the binary Regex toggle with a 7-mode search dropdown (Orðaleit, Heilt orð, Hluti af orði, Byrjar á, Eitthvað af, Nálægt, Regex) backed by proper SQL for each mode.

**Architecture:** Backend gains a `_build_text_filter()` helper that translates each new mode into SQL WHERE fragments; frontend replaces `RegexToggle` with `ModeDropdown` and adds `proximity_n` to URL state. Three independent tasks: backend, frontend state/client, frontend UI.

**Tech Stack:** Python 3.13 + FastAPI + SQLAlchemy + PostgreSQL (pg_trgm, GIN FTS); React 19 + TypeScript + Tailwind + Radix UI; Vitest + React Testing Library; uv for Python.

## Global Constraints

- All 7 mode keys lowercase ASCII: `keyword`, `exact`, `prefix`, `substring`, `any`, `proximity`, `regex`
- `proximity_n`: integer 1–50, default 5; only relevant when `mode=proximity`
- Regex-backed modes (`exact`, `prefix`, `substring`, `any`, `regex`) respect `regex_fields`; `keyword` and `proximity` use FTS columns only
- Only `keyword` and `proximity` support `sort=relevance` (have FTS rank); other modes auto-switch to `newest`
- Mode labels in Icelandic (UI): Orðaleit, Heilt orð, Hluti af orði, Byrjar á, Eitthvað af, Nálægt, Regex
- Statement timeout applied to all regex-backed modes (`REGEX_TIMEOUT_MS = 10_000`)
- `_build_text_filter` is a pure function (no I/O); tested in isolation
- Run tests with: backend `uv run pytest tests/test_search_queries.py -v`, frontend `npm test -- --run`

---

## File Map

**Modified:**
- `engine/search/queries.py` — add `VALID_MODES`, `_build_text_filter()`, update `search_documents()` and `facet_counts()`
- `engine/api/app.py` — expand mode pattern, add `proximity_n` param to `/api/search` and `/api/facets`
- `frontend/src/api/types.ts` — expand `Mode` type, add `proximity_n` to `SearchParams`
- `frontend/src/lib/searchState.ts` — add `proximity_n` to `SearchState`/`DEFAULT_STATE`, expand `MODES`
- `frontend/src/api/client.ts` — pass `proximity_n` in `searchQs` and `fetchFacets`
- `frontend/src/hooks/useFacets.ts` — add `proximity_n` to query key and params
- `frontend/src/components/Toolbar.tsx` — "Reitir" visible for 5 modes, disable relevance for non-FTS modes
- `frontend/src/routes/SearchPage.tsx` — swap `RegexToggle` → `ModeDropdown`

**Created:**
- `tests/test_search_queries.py` — pytest unit tests for `_build_text_filter` and `_order_clause`
- `frontend/src/components/ModeDropdown.tsx`
- `frontend/src/components/ModeDropdown.test.tsx`

**Deleted:**
- `frontend/src/components/RegexToggle.tsx`
- `frontend/src/components/RegexToggle.test.tsx` (if exists)

---

## Task 1: Backend — `_build_text_filter` + updated modes

**Files:**
- Modify: `engine/search/queries.py`
- Modify: `engine/api/app.py`
- Create: `tests/test_search_queries.py`

**Interfaces:**
- Produces: `_build_text_filter(mode, words, fields, proximity_n) -> tuple[list[str], dict]` (pure function)
- Produces: `search_documents(..., proximity_n: int = 5)` and `facet_counts(..., proximity_n: int = 5)` (updated signatures)
- Produces: `/api/search?proximity_n=5` and `/api/facets?proximity_n=5` accepted by app.py

- [ ] **Step 1: Write failing tests**

Create `tests/test_search_queries.py`:

```python
"""Unit tests for _build_text_filter and _order_clause."""
import pytest
from engine.search.queries import _build_text_filter, _order_clause


def test_exact_single_word():
    frags, params = _build_text_filter("exact", ["gæsluvarðhald"], None, 5)
    assert len(frags) == 1
    assert "pat_0" in params
    assert params["pat_0"] == r"\mgæsluvarðhald\M"
    assert "~* :pat_0" in frags[0]


def test_exact_two_words_ands_both():
    frags, params = _build_text_filter("exact", ["a", "b"], None, 5)
    assert len(frags) == 2  # AND of two conditions
    assert "pat_0" in params and "pat_1" in params
    assert params["pat_0"] == r"\ma\M"
    assert params["pat_1"] == r"\mb\M"


def test_prefix():
    frags, params = _build_text_filter("prefix", ["gæslu"], None, 5)
    assert params["pat_0"] == r"\mgæslu"


def test_substring():
    frags, params = _build_text_filter("substring", ["hald"], None, 5)
    assert params["pat_0"] == "hald"


def test_any_two_words():
    frags, params = _build_text_filter("any", ["dómur", "úrskurður"], None, 5)
    assert len(frags) == 1
    assert params["pattern"] == "(dómur|úrskurður)"


def test_proximity_two_words():
    frags, params = _build_text_filter("proximity", ["gæsluvarðhald", "rannsókn"], None, 5)
    assert len(frags) == 1
    assert "prox_q" in params
    assert "<5>" in params["prox_q"]
    # Both lemmas present
    assert "gæsluvarðhald" in params["prox_q"]
    assert "rannsókn" in params["prox_q"]


def test_proximity_custom_n():
    frags, params = _build_text_filter("proximity", ["a", "b"], None, 10)
    assert "<10>" in params["prox_q"]


def test_proximity_single_word_no_chevron():
    frags, params = _build_text_filter("proximity", ["gæsluvarðhald"], None, 5)
    assert "<" not in params["prox_q"]  # single word, no proximity operator


def test_empty_words_returns_nothing():
    frags, params = _build_text_filter("exact", [], None, 5)
    assert frags == []
    assert params == {}


def test_order_clause_proximity_allows_relevance():
    result = _order_clause("proximity", True, "relevance", "ts_rank(x,y)")
    assert "ts_rank" in result


def test_order_clause_exact_overrides_relevance_to_newest():
    result = _order_clause("exact", True, "relevance", "0::real")
    assert "document_date DESC" in result
    assert "0::real" not in result


def test_order_clause_newest():
    result = _order_clause("exact", True, "newest", "0::real")
    assert "document_date DESC" in result


def test_order_clause_oldest():
    result = _order_clause("keyword", True, "oldest", "ts_rank(x,y)")
    assert "document_date ASC" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_search_queries.py -v
```
Expected: `ImportError` or `AttributeError` — `_build_text_filter` not found yet.

- [ ] **Step 3: Add `VALID_MODES` and `_build_text_filter` to `queries.py`**

In `engine/search/queries.py`, after the `_regex_snippet` function (around line 115), add:

```python
VALID_MODES = frozenset({
    "keyword", "exact", "prefix", "substring", "any", "proximity", "regex"
})


def _build_text_filter(
    mode: str, words: list[str], fields: list[str] | None, proximity_n: int
) -> tuple[list[str], dict[str, Any]]:
    """Return (where_fragments, params) for exact/prefix/substring/any/proximity modes.

    words = [w for w in q.split() if w] — pre-split, filtered empty.
    Not called for 'keyword' or 'regex' (handled inline in search_documents).
    """
    frags: list[str] = []
    params: dict[str, Any] = {}
    if not words:
        return frags, params

    effective_fields = [f for f in (fields or DEFAULT_REGEX_FIELDS) if f in REGEX_COLUMNS]

    if mode == "proximity":
        lemma_words = [lemmatize_query(w) for w in words]
        lemma_words = [lw for lw in lemma_words if lw]
        if not lemma_words:
            return frags, params
        if len(lemma_words) == 1:
            tsq = lemma_words[0]
        else:
            tsq = f" <{proximity_n}> ".join(lemma_words)
        params["prox_q"] = tsq
        frags.append("d.fts_is @@ to_tsquery('simple', :prox_q)")
        return frags, params

    if mode == "any":
        pattern = "(" + "|".join(re.escape(w) for w in words) + ")"
        params["pattern"] = pattern
        if effective_fields:
            ors = " OR ".join(f"{REGEX_COLUMNS[f]} ~* :pattern" for f in effective_fields)
            frags.append(f"({ors})")
        return frags, params

    # exact, prefix, substring — one SQL AND-fragment per word
    templates: dict[str, str] = {
        "exact": r"\m{w}\M",
        "prefix": r"\m{w}",
        "substring": "{w}",
    }
    tmpl = templates[mode]
    for i, w in enumerate(words):
        pat = tmpl.format(w=re.escape(w))
        params[f"pat_{i}"] = pat
        if effective_fields:
            ors = " OR ".join(
                f"{REGEX_COLUMNS[f]} ~* :pat_{i}" for f in effective_fields
            )
            frags.append(f"({ors})")
    return frags, params
```

- [ ] **Step 4: Update `_order_clause` to allow relevance for proximity**

Replace the existing `_order_clause` function (lines 84–95) with:

```python
def _order_clause(mode: str, has_text: bool, sort: str, rank_expr: str) -> str:
    """Return the ORDER BY body (uses real SQL expressions, no output aliases).

    ``relevance`` only applies to keyword and proximity search (both have FTS rank).
    """
    if sort == "relevance" and not (mode in ("keyword", "proximity") and has_text):
        sort = "newest"  # relevance is meaningless without FTS rank
    if sort == "relevance":
        return f"{rank_expr} DESC, d.document_date DESC NULLS LAST, d.id"
    if sort == "oldest":
        return "d.document_date ASC NULLS LAST, d.id"
    return "d.document_date DESC NULLS LAST, d.id"
```

- [ ] **Step 5: Update `search_documents` signature and mode handling**

Replace the `if mode not in ("keyword", "regex"):` check and the text-filter block. The full updated `search_documents` signature and text-handling section:

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
) -> SearchResults:
    """Run a search and return one page of results plus the total match count."""
    if mode not in VALID_MODES:
        raise SearchError(f"Unknown mode {mode!r}")
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    offset = (page - 1) * page_size
    q = (q or "").strip()
```

Then replace the text-filter block (lines 157–181) with:

```python
    has_text = bool(q)
    rank_expr = "0::real"
    regex_pattern: str | None = None
    snip_pattern: str | None = None  # for Python-side _regex_snippet
    words = [w for w in q.split() if w] if q else []

    if has_text and mode == "keyword":
        lemmas = lemmatize_query(q)
        if lemmas:
            params["lemmas"] = lemmas
            where.append("d.fts_is @@ plainto_tsquery('simple', :lemmas)")
            rank_expr = "ts_rank(d.fts_is, plainto_tsquery('simple', :lemmas))"
        else:
            has_text = False
    elif has_text and mode == "regex":
        try:
            re.compile(q)
        except re.error as exc:
            raise SearchError(f"Invalid regex: {exc}") from exc
        fields = regex_fields or DEFAULT_REGEX_FIELDS
        unknown = [f for f in fields if f not in REGEX_COLUMNS]
        if unknown:
            raise SearchError(f"Unknown regex field(s): {unknown}")
        regex_pattern = q
        snip_pattern = q
        params["pattern"] = q
        ors = " OR ".join(f"{REGEX_COLUMNS[f]} ~* :pattern" for f in fields)
        where.append(f"({ors})")
    elif has_text:
        # exact, prefix, substring, any, proximity
        text_frags, text_params = _build_text_filter(mode, words, regex_fields, proximity_n)
        if text_frags:
            where.extend(text_frags)
            params.update(text_params)
            if mode == "proximity":
                rank_expr = "ts_rank(d.fts_is, to_tsquery('simple', :prox_q))"
            elif mode == "any":
                snip_pattern = text_params.get("pattern")
            else:
                snip_pattern = text_params.get("pat_0")  # first word for snippet
        else:
            has_text = False
```

- [ ] **Step 6: Update timeout and snippet logic in `search_documents`**

Replace the timeout block (line 187–188) with:

```python
    # Apply timeout to all regex-backed modes.
    if mode in ("regex", "exact", "prefix", "substring", "any") and has_text:
        await session.execute(text(f"SET LOCAL statement_timeout = {REGEX_TIMEOUT_MS}"))
```

Replace the snippet selection block (lines 208–222) with:

```python
    if mode == "keyword" and has_text:
        snippet_select = (
            "ts_headline('simple', coalesce(d.body_text, ''), "
            "plainto_tsquery('simple', :hl), "
            "'StartSel=<mark>,StopSel=</mark>,MaxFragments=2,MaxWords=28,"
            "MinWords=8,ShortWord=2') AS snippet"
        )
        page_params["hl"] = q
        body_head_select = "NULL AS body_head"
    elif mode == "proximity" and has_text and "prox_q" in params:
        snippet_select = (
            "ts_headline('simple', coalesce(d.body_text, ''), "
            "to_tsquery('simple', :prox_q), "
            "'StartSel=<mark>,StopSel=</mark>,MaxFragments=2,MaxWords=28,"
            "MinWords=8,ShortWord=2') AS snippet"
        )
        body_head_select = "NULL AS body_head"
    else:
        snippet_select = "NULL AS snippet"
        body_head_select = (
            f"left(d.body_text, {_REGEX_SNIPPET_SCAN}) AS body_head"
            if snip_pattern else "NULL AS body_head"
        )
```

Replace the result-processing snippet line (currently `if mode == "regex" and regex_pattern:`) with:

```python
            if snip_pattern:
                snippet = _regex_snippet(r.get("body_head"), r.get("summary"), snip_pattern)
            else:
                snippet = r.get("snippet") or ""
```

- [ ] **Step 7: Update `facet_counts` signature and mode handling**

Add `proximity_n: int = 5` parameter and replace the text-filter block in `facet_counts` (lines 294–310):

```python
async def facet_counts(
    session: AsyncSession,
    *,
    q: str = "",
    mode: str = "keyword",
    date_from: date | None = None,
    date_to: date | None = None,
    regex_fields: list[str] | None = None,
    proximity_n: int = 5,
) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
```

Replace the text-filter block inside `facet_counts` (the `if q and mode == "keyword":` block) with:

```python
    words = [w for w in q.split() if w] if q else []

    if q and mode == "keyword":
        lemmas = lemmatize_query(q)
        if lemmas:
            params["lemmas"] = lemmas
            where.append("d.fts_is @@ plainto_tsquery('simple', :lemmas)")
    elif q and mode == "regex":
        try:
            re.compile(q)
        except re.error as exc:
            raise SearchError(f"Invalid regex: {exc}") from exc
        fields = regex_fields or DEFAULT_REGEX_FIELDS
        unknown = [f for f in fields if f not in REGEX_COLUMNS]
        if unknown:
            raise SearchError(f"Unknown regex field(s): {unknown}")
        params["pattern"] = q
        ors = " OR ".join(f"{REGEX_COLUMNS[f]} ~* :pattern" for f in fields)
        where.append(f"({ors})")
        await session.execute(text(f"SET LOCAL statement_timeout = {REGEX_TIMEOUT_MS}"))
    elif q:
        # exact, prefix, substring, any, proximity
        text_frags, text_params = _build_text_filter(mode, words, regex_fields, proximity_n)
        where.extend(text_frags)
        params.update(text_params)
        if mode in ("exact", "prefix", "substring", "any"):
            await session.execute(text(f"SET LOCAL statement_timeout = {REGEX_TIMEOUT_MS}"))
```

- [ ] **Step 8: Update `app.py` mode pattern and add `proximity_n`**

In `engine/api/app.py`, update the `/api/search` endpoint:

```python
@app.get("/api/search")
async def search(
    q: str = Query("", description="Search text or regex pattern"),
    mode: str = Query("keyword", pattern="^(keyword|exact|prefix|substring|any|proximity|regex)$"),
    scope: list[str] | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    sort: str = Query("relevance", pattern="^(relevance|newest|oldest)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=100),
    regex_fields: list[str] | None = Query(None),
    proximity_n: int = Query(5, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        res = await search_documents(
            session, q=q, mode=mode, scope=scope,
            date_from=date_from, date_to=date_to, sort=sort,
            page=page, page_size=page_size, regex_fields=regex_fields,
            proximity_n=proximity_n,
        )
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"total": res.total, "page": res.page, "page_size": res.page_size, "results": res.results}
```

Update the `/api/facets` endpoint:

```python
@app.get("/api/facets")
async def facets(
    q: str = Query(""),
    mode: str = Query("keyword", pattern="^(keyword|exact|prefix|substring|any|proximity|regex)$"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    regex_fields: list[str] | None = Query(None),
    proximity_n: int = Query(5, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        by_source, by_source_vt = await facet_counts(
            session, q=q, mode=mode, date_from=date_from, date_to=date_to,
            regex_fields=regex_fields, proximity_n=proximity_n,
        )
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    tree = [_annotate(cat, by_source, by_source_vt) for cat in catalog()]
    return {"catalog": tree, "total": sum(cat["count"] for cat in tree)}
```

- [ ] **Step 9: Run tests — all must pass**

```bash
uv run pytest tests/test_search_queries.py -v
```
Expected: 13 tests passing.

- [ ] **Step 10: Smoke-test with curl**

```bash
# Restart API if running: kill existing uvicorn and restart with DATABASE_URL
DATABASE_URL="postgresql+asyncpg://geiri@localhost/lausnir_v2" uv run uvicorn engine.api.app:app --port 8077 --log-level warning &

# exact mode — should match word boundaries only
curl -s "http://localhost:8077/api/search?q=gæsluvarðhald&mode=exact&page=1" | python3 -c "import sys,json; d=json.load(sys.stdin); print('exact total:', d['total'])"

# proximity mode — gæsluvarðhald within 5 words of rannsókn  
curl -s "http://localhost:8077/api/search?q=gæsluvarðhald+rannsókn&mode=proximity&proximity_n=5&page=1" | python3 -c "import sys,json; d=json.load(sys.stdin); print('proximity total:', d['total'])"

# any mode — dómur OR úrskurður
curl -s "http://localhost:8077/api/search?q=dómur+úrskurður&mode=any&page=1" | python3 -c "import sys,json; d=json.load(sys.stdin); print('any total:', d['total'])"
```
Expected: exact total ~ similar to keyword; proximity gives subset; any gives large number.

- [ ] **Step 11: Commit**

```bash
git add engine/search/queries.py engine/api/app.py tests/test_search_queries.py
git commit -m "feat: add exact/prefix/substring/any/proximity search modes to backend"
```

---

## Task 2: Frontend state, types, client, hooks

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/lib/searchState.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/hooks/useFacets.ts`
- Modify: `frontend/src/lib/searchState.test.ts`

**Interfaces:**
- Consumes: Task 1's backend (mode strings, proximity_n param)
- Produces: `Mode` type with 7 values; `SearchState` with `proximity_n: number`; `SearchParams` with `proximity_n?: number`; `fetchFacets` passes `proximity_n`

- [ ] **Step 1: Update failing test for searchState**

In `frontend/src/lib/searchState.test.ts`, add these tests:

```typescript
  it("proximity_n round-trips through URL params", () => {
    const sp = new URLSearchParams("q=x&mode=proximity&sort=relevance&proximity_n=10");
    const s = parseSearchState(sp);
    expect(s.proximity_n).toBe(10);
    expect(s.mode).toBe("proximity");
    const out = toSearchParams(s);
    expect(out.get("proximity_n")).toBe("10");
  });

  it("proximity_n defaults to 5 when absent", () => {
    const s = parseSearchState(new URLSearchParams("mode=proximity"));
    expect(s.proximity_n).toBe(5);
  });

  it("proximity_n omitted from URL when default (5)", () => {
    const s = parseSearchState(new URLSearchParams("mode=proximity"));
    const out = toSearchParams(s);
    expect(out.has("proximity_n")).toBe(false);
  });

  it("proximity_n clamps invalid values to default", () => {
    const s = parseSearchState(new URLSearchParams("proximity_n=999"));
    expect(s.proximity_n).toBe(5);
  });

  it("all new modes are valid", () => {
    for (const m of ["exact", "prefix", "substring", "any", "proximity"]) {
      const s = parseSearchState(new URLSearchParams(`mode=${m}`));
      expect(s.mode).toBe(m);
    }
  });
```

- [ ] **Step 2: Run tests — new ones must fail**

```bash
cd frontend && npm test -- --run
```
Expected: 5 new tests fail (proximity_n property missing, modes not valid).

- [ ] **Step 3: Update `types.ts`**

Replace:
```typescript
export type Mode = "keyword" | "regex";
```
With:
```typescript
export type Mode = "keyword" | "exact" | "prefix" | "substring" | "any" | "proximity" | "regex";
```

Add `proximity_n` to `SearchParams`:
```typescript
export interface SearchParams {
  q: string; mode: Mode; scope: string[];
  date_from?: string; date_to?: string; sort: Sort;
  page?: number; page_size?: number; regex_fields?: string[];
  proximity_n?: number;
}
```

- [ ] **Step 4: Update `searchState.ts`**

Replace the entire file contents with:

```typescript
import type { Mode, Sort } from "../api/types";

export interface SearchState {
  q: string;
  mode: Mode;
  scope: string[];
  date_from?: string;
  date_to?: string;
  sort: Sort;
  regex_fields: string[];
  proximity_n: number;
}

export const DEFAULT_STATE: SearchState = {
  q: "",
  mode: "keyword",
  scope: [],
  sort: "relevance",
  regex_fields: [],
  proximity_n: 5,
};

const MODES: Mode[] = [
  "keyword", "exact", "prefix", "substring", "any", "proximity", "regex",
];
const SORTS: Sort[] = ["relevance", "newest", "oldest"];

export function parseSearchState(sp: URLSearchParams): SearchState {
  const mode = sp.get("mode");
  const sort = sp.get("sort");
  const rawN = parseInt(sp.get("proximity_n") ?? "5", 10);
  const proximity_n = Number.isFinite(rawN) && rawN >= 1 && rawN <= 50 ? rawN : 5;
  return {
    q: sp.get("q") ?? "",
    mode: MODES.includes(mode as Mode) ? (mode as Mode) : "keyword",
    sort: SORTS.includes(sort as Sort) ? (sort as Sort) : "relevance",
    scope: sp.getAll("scope"),
    regex_fields: sp.getAll("regex_fields"),
    date_from: sp.get("date_from") ?? undefined,
    date_to: sp.get("date_to") ?? undefined,
    proximity_n,
  };
}

export function toSearchParams(s: SearchState): URLSearchParams {
  const sp = new URLSearchParams();
  if (s.q) sp.set("q", s.q);
  sp.set("mode", s.mode);
  sp.set("sort", s.sort);
  if (s.date_from) sp.set("date_from", s.date_from);
  if (s.date_to) sp.set("date_to", s.date_to);
  // Only persist proximity_n when non-default to keep URLs clean
  if (s.mode === "proximity" && s.proximity_n !== 5) {
    sp.set("proximity_n", String(s.proximity_n));
  }
  for (const x of s.scope) sp.append("scope", x);
  for (const f of s.regex_fields) sp.append("regex_fields", f);
  return sp;
}
```

- [ ] **Step 5: Update `client.ts`**

In `searchQs`, add after the `regex_fields` loop:
```typescript
  if (p.proximity_n !== undefined && p.proximity_n !== 5) {
    qs.set("proximity_n", String(p.proximity_n));
  }
```

Update `fetchFacets` to pass `proximity_n`:
```typescript
export function fetchFacets(
  p: Omit<SearchParams, "scope" | "sort" | "page" | "page_size">
): Promise<FacetsResponse> {
  const qs = new URLSearchParams();
  if (p.q) qs.set("q", p.q);
  qs.set("mode", p.mode);
  if (p.date_from) qs.set("date_from", p.date_from);
  if (p.date_to) qs.set("date_to", p.date_to);
  for (const f of p.regex_fields ?? []) qs.append("regex_fields", f);
  if (p.proximity_n !== undefined && p.proximity_n !== 5) {
    qs.set("proximity_n", String(p.proximity_n));
  }
  return getJson<FacetsResponse>("/api/facets", qs);
}
```

- [ ] **Step 6: Update `useFacets.ts`**

Replace the entire file:

```typescript
import { useQuery } from "@tanstack/react-query";
import { fetchFacets } from "../api/client";
import type { SearchState } from "../lib/searchState";

export function useFacets(s: SearchState) {
  // NB: scope/sort excluded from the key — facets ignore source selection.
  return useQuery({
    queryKey: [
      "facets", s.q, s.mode, s.date_from, s.date_to,
      s.regex_fields, s.proximity_n,
    ],
    queryFn: () =>
      fetchFacets({
        q: s.q,
        mode: s.mode,
        date_from: s.date_from,
        date_to: s.date_to,
        regex_fields: s.regex_fields,
        proximity_n: s.proximity_n,
      }),
  });
}
```

- [ ] **Step 7: Run all frontend tests — all 18 + 5 new must pass**

```bash
cd frontend && npm test -- --run
```
Expected: 23 tests passing (18 original + 5 new).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/lib/searchState.ts \
        frontend/src/api/client.ts frontend/src/hooks/useFacets.ts \
        frontend/src/lib/searchState.test.ts
git commit -m "feat: expand Mode type and add proximity_n to search state and client"
```

---

## Task 3: ModeDropdown UI + Toolbar + SearchPage wiring

**Files:**
- Create: `frontend/src/components/ModeDropdown.tsx`
- Create: `frontend/src/components/ModeDropdown.test.tsx`
- Modify: `frontend/src/components/Toolbar.tsx`
- Modify: `frontend/src/routes/SearchPage.tsx`
- Delete: `frontend/src/components/RegexToggle.tsx`

**Interfaces:**
- Consumes: `SearchState` with `proximity_n: number` (Task 2); `Mode` with 7 values (Task 2)
- Produces: `<ModeDropdown state={state} onChange={patch} />` replaces `<RegexToggle>`

- [ ] **Step 1: Write failing tests for ModeDropdown**

Create `frontend/src/components/ModeDropdown.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ModeDropdown } from "./ModeDropdown";
import type { SearchState } from "../lib/searchState";
import { DEFAULT_STATE } from "../lib/searchState";

const make = (overrides?: Partial<SearchState>): SearchState => ({
  ...DEFAULT_STATE,
  ...overrides,
});

describe("ModeDropdown", () => {
  it("renders all 7 mode options", () => {
    render(<ModeDropdown state={make()} onChange={() => {}} />);
    const select = screen.getByRole("combobox", { name: /leitarstilling/i });
    const options = Array.from((select as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toEqual([
      "keyword", "exact", "prefix", "substring", "any", "proximity", "regex",
    ]);
  });

  it("shows proximity N input only when mode=proximity", () => {
    const { rerender } = render(<ModeDropdown state={make()} onChange={() => {}} />);
    expect(screen.queryByRole("spinbutton")).toBeNull();

    rerender(<ModeDropdown state={make({ mode: "proximity" })} onChange={() => {}} />);
    expect(screen.getByRole("spinbutton")).toBeTruthy();
  });

  it("proximity N input shows current value", () => {
    render(
      <ModeDropdown state={make({ mode: "proximity", proximity_n: 10 })} onChange={() => {}} />
    );
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    expect(input.value).toBe("10");
  });

  it("changing mode calls onChange with new mode", () => {
    const onChange = vi.fn();
    render(<ModeDropdown state={make()} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: /leitarstilling/i }), {
      target: { value: "exact" },
    });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ mode: "exact" }));
  });

  it("switching from keyword to exact auto-switches sort to newest", () => {
    const onChange = vi.fn();
    render(<ModeDropdown state={make({ mode: "keyword", sort: "relevance" })} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: /leitarstilling/i }), {
      target: { value: "exact" },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ mode: "exact", sort: "newest" })
    );
  });

  it("switching to proximity keeps relevance sort", () => {
    const onChange = vi.fn();
    render(<ModeDropdown state={make({ mode: "keyword", sort: "relevance" })} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: /leitarstilling/i }), {
      target: { value: "proximity" },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ mode: "proximity", sort: "relevance" })
    );
  });

  it("changing proximity_n calls onChange with new value", () => {
    const onChange = vi.fn();
    render(
      <ModeDropdown state={make({ mode: "proximity", proximity_n: 5 })} onChange={onChange} />
    );
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "12" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ proximity_n: 12 }));
  });
});
```

- [ ] **Step 2: Run tests — ModeDropdown tests must fail**

```bash
cd frontend && npm test -- --run
```
Expected: 6 new tests fail (ModeDropdown not found).

- [ ] **Step 3: Create `ModeDropdown.tsx`**

Create `frontend/src/components/ModeDropdown.tsx`:

```tsx
import type { SearchState } from "../lib/searchState";
import type { Mode, Sort } from "../api/types";

const MODE_LABELS: Record<Mode, string> = {
  keyword: "Orðaleit",
  exact: "Heilt orð",
  prefix: "Byrjar á",
  substring: "Hluti af orði",
  any: "Eitthvað af",
  proximity: "Nálægt",
  regex: "Regex",
};

const ALL_MODES: Mode[] = [
  "keyword", "exact", "prefix", "substring", "any", "proximity", "regex",
];

// Modes that support ts_rank (can use sort=relevance)
const FTS_MODES = new Set<Mode>(["keyword", "proximity"]);

export function ModeDropdown({
  state,
  onChange,
}: {
  state: SearchState;
  onChange: (p: Partial<SearchState>) => void;
}) {
  function handleModeChange(mode: Mode) {
    const updates: Partial<SearchState> = { mode };
    // Auto-switch away from relevance when FTS rank isn't available
    if (!FTS_MODES.has(mode) && state.sort === "relevance") {
      (updates as { sort: Sort }).sort = "newest";
    }
    onChange(updates);
  }

  return (
    <div className="flex items-center gap-2">
      <select
        aria-label="Leitarstilling"
        value={state.mode}
        onChange={(e) => handleModeChange(e.target.value as Mode)}
        className="text-sm border border-slate-300 rounded-full px-3 py-1.5 bg-white"
      >
        {ALL_MODES.map((m) => (
          <option key={m} value={m}>
            {MODE_LABELS[m]}
          </option>
        ))}
      </select>

      {state.mode === "proximity" && (
        <label className="flex items-center gap-1 text-sm text-slate-600">
          innan
          <input
            type="number"
            min={1}
            max={50}
            value={state.proximity_n}
            onChange={(e) => {
              const n = parseInt(e.target.value, 10);
              if (Number.isFinite(n) && n >= 1 && n <= 50) onChange({ proximity_n: n });
            }}
            className="w-14 border border-slate-300 rounded px-2 py-0.5 text-center text-sm"
          />
          orða
        </label>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Update `Toolbar.tsx`**

The "Reitir" button should appear for regex-backed modes, and "Bestar niðurstöður" should be disabled when mode has no FTS rank. Replace the file:

```tsx
import * as Popover from "@radix-ui/react-popover";
import type { SearchState } from "../lib/searchState";
import type { Mode, Sort } from "../api/types";

const REGEX_FIELD_LABELS: Record<string, string> = {
  body_text: "Meginmál", summary: "Reifun", case_number: "Málsnúmer",
  parties: "Aðilar", keywords: "Lykilorð", lower_body_text: "Neðri dómur",
};

// Modes that use regex_fields (show Reitir button)
const REGEX_BACKED_MODES = new Set<Mode>(["exact", "prefix", "substring", "any", "regex"]);
// Modes where relevance sort is meaningful
const FTS_MODES = new Set<Mode>(["keyword", "proximity"]);

export function Toolbar({ state, regexFields, onChange }:
  { state: SearchState; regexFields: string[]; onChange: (p: Partial<SearchState>) => void }) {
  return (
    <div className="flex items-center gap-3">
      <select
        aria-label="Röðun"
        value={state.sort}
        onChange={(e) => onChange({ sort: e.target.value as Sort })}
        className="text-sm border border-slate-300 rounded-full px-3 py-1.5">
        <option value="relevance" disabled={!FTS_MODES.has(state.mode)}>
          Bestar niðurstöður
        </option>
        <option value="newest">Nýjast fyrst</option>
        <option value="oldest">Elst fyrst</option>
      </select>

      <Popover.Root>
        <Popover.Trigger className="text-sm border border-slate-300 rounded-full px-3 py-1.5">
          Tímabil
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content className="bg-white border border-slate-200 rounded-lg p-3 shadow-md flex flex-col gap-2">
            <label className="text-sm">Frá <input type="date" value={state.date_from ?? ""}
              onChange={(e) => onChange({ date_from: e.target.value || undefined })}
              className="border rounded px-2 py-1" /></label>
            <label className="text-sm">Til <input type="date" value={state.date_to ?? ""}
              onChange={(e) => onChange({ date_to: e.target.value || undefined })}
              className="border rounded px-2 py-1" /></label>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      {REGEX_BACKED_MODES.has(state.mode) && (
        <Popover.Root>
          <Popover.Trigger className="text-sm border border-slate-300 rounded-full px-3 py-1.5">
            Reitir
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content className="bg-white border border-slate-200 rounded-lg p-3 shadow-md flex flex-col gap-1">
              {regexFields.map((f) => {
                const base = state.regex_fields.length ? state.regex_fields : ["body_text"];
                const on = base.includes(f);
                return (
                  <label key={f} className="text-sm flex items-center gap-2">
                    <input type="checkbox" checked={on} onChange={(e) => {
                      const next = e.target.checked
                        ? [...new Set([...base, f])]
                        : base.filter((x) => x !== f);
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

- [ ] **Step 5: Update `SearchPage.tsx` — swap RegexToggle for ModeDropdown**

In `frontend/src/routes/SearchPage.tsx`, replace:
```tsx
import { RegexToggle } from "../components/RegexToggle";
```
With:
```tsx
import { ModeDropdown } from "../components/ModeDropdown";
```

Replace `<RegexToggle state={state} onChange={patch} />` with:
```tsx
<ModeDropdown state={state} onChange={patch} />
```

- [ ] **Step 6: Delete RegexToggle files**

```bash
rm frontend/src/components/RegexToggle.tsx
# Check if test file exists before deleting
ls frontend/src/components/RegexToggle.test.tsx 2>/dev/null && rm frontend/src/components/RegexToggle.test.tsx || true
```

- [ ] **Step 7: Run all tests — all must pass**

```bash
cd frontend && npm test -- --run
```
Expected: all tests pass (previous 23 + 6 new ModeDropdown tests = 29 total; minus any RegexToggle tests that were removed).

- [ ] **Step 8: Manual smoke test**

Open `http://localhost:5173` in browser. Verify:
- Dropdown shows 7 modes where the Regex toggle was
- Selecting "Nálægt" shows "innan [5] orða" input
- Selecting "Heilt orð", "Hluti af orði", "Byrjar á", "Eitthvað af", or "Regex" shows "Reitir" button
- Selecting "Orðaleit" or "Nálægt" hides "Reitir" button
- "Bestar niðurstöður" is greyed out when mode is not Orðaleit or Nálægt
- Searching "gæsluvarðhald" with mode "Heilt orð" gives results
- Searching "gæsluvarðhald rannsókn" with mode "Nálægt" (5 orð) gives subset of keyword results

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/ModeDropdown.tsx \
        frontend/src/components/ModeDropdown.test.tsx \
        frontend/src/components/Toolbar.tsx \
        frontend/src/routes/SearchPage.tsx
git rm frontend/src/components/RegexToggle.tsx
# git rm RegexToggle.test.tsx only if it existed
git commit -m "feat: replace RegexToggle with 7-mode ModeDropdown, add proximity N input"
```
