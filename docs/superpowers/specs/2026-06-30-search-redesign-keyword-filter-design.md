# Search UI redesign + keyword filter + provision-on-landing — Design

## Context

Provision search (lagaákvæði) shipped as a `ProvisionInput` field in `Toolbar.tsx`, only visible once a search is already underway (the `Toolbar` only renders inside the results page header, not on `LandingView`). Separately, a partial visual redesign (navy/serif "Lausnir" wordmark, minimalist styling) was left uncommitted in the working tree across `LandingView.tsx`, `ResultCard.tsx`, `ScopeChips.tsx`, `ModeDropdown.tsx`, `SourceTree.tsx`, `SearchBar.tsx`, `index.css`.

This round of work:
1. Discards the uncommitted redesign and re-does it with a deliberately chosen design skill (`minimalist-ui`) instead of an ad-hoc style.
2. Surfaces provision search on the landing page, not just the in-results toolbar.
3. Adds a new keyword-only filter: search restricted to the `keywords` JSONB tag column (the lykilorð tags already rendered under each result card), independent of body-text search modes.

## Out of scope

- Changing the underlying provision-extraction or FTS pipeline (already shipped, working).
- New backend indexes — current scale (~90k docs) is filtered by scope/date before any text/tag match runs, same as existing regex-mode keyword search.
- `gpt-taste` / `high-end-visual-design` skill directives (GSAP motion, bento grids, landing-page archetypes) — wrong fit for a dense results-list research tool.

## Architecture

Three independent slices, built together because they touch the same files:

### A. Visual redesign

Discard via `git checkout -- frontend/src/components/LandingView.tsx frontend/src/components/ModeDropdown.tsx frontend/src/components/ResultCard.tsx frontend/src/components/ScopeChips.tsx frontend/src/components/SearchBar.tsx frontend/src/components/SourceTree.tsx frontend/src/index.css` to return to the last-committed baseline, then rebuild using the `minimalist-ui` skill (document-style workspace aesthetic: muted monochrome, typographic hierarchy, flat surfaces, no heavy shadows/gradients/pill buttons) combined with `design-taste-frontend`'s audit-first redesign methodology (read existing structure, infer brief, ship without restating defaults). Scope of restyle: `LandingView`, `SearchBar`, `Toolbar`, `ResultCard`, `ScopeChips`, `SourceTree`, `ModeDropdown`, `index.css`. `ResultsList.tsx` and `FacetSidebar.tsx` are in scope only if visual inconsistency would otherwise result (e.g. font/color tokens) — no functional changes to either.

### B. Provision search on landing page

`ProvisionInput` (defined at the bottom of `Toolbar.tsx`) is rendered from `LandingView.tsx` as well as `Toolbar.tsx`, wired the same way: `<ProvisionInput value={state.provision ?? ""} onChange={(v) => patch({ provision: v || undefined })} />`. No new component — reuse as-is. `ProvisionInput` is currently a private (non-exported) function in `Toolbar.tsx`; it must be exported for `LandingView` to import it.

### C. Keyword-only filter

New `KeywordInput` component, structurally identical to `ProvisionInput` (local draft state + `useEffect` sync + clear button + submit-on-change-only), placed next to it in both `Toolbar` and `LandingView`. Filters strictly on the `keywords` JSONB array — not body text, not summary.

**Backend** (`engine/search/queries.py`):
- New `keyword: str | None = None` parameter on `search_documents`.
- WHERE fragment: `d.keywords::text ILIKE :keyword_pattern` with `keyword_pattern = f"%{keyword}%"`, added to the `where` list independent of `mode` — same placement pattern as the existing provision filter block (added once, before `has_text` branching).
- Must also be threaded into `_search_by_chunks`'s `extra_where`/`extra_params`, mirroring how the provision filter was fixed there (Bug 2 in the provision-search work) — otherwise a keyword filter combined with a chunked-scope (`logfraediritgerdir`/`baekur`) text search would be silently dropped.
- No new parser needed (no grammar to parse, unlike provision) — straight substring containment.

**API** (`engine/api/app.py`): new `keyword: str | None = Query(None)` on `/api/search`, passed through to `search_documents`.

**Frontend**: `keyword?: string` added to `SearchState` (`searchState.ts`), `SearchParams` (`types.ts`), URL param read/write in `parseSearchState`/`toSearchParams`, and `searchQs()` in `client.ts` — each exactly mirroring how `provision` was threaded through.

`SearchPage.tsx`'s landing-page gate becomes:
```ts
if (!state.q && !state.provision && !state.keyword && state.scope.length === 0) {
```

## Data flow

```
LandingView / Toolbar
  ProvisionInput  → state.provision → URL ?provision= → API ?provision= → cited_provisions JSONB containment
  KeywordInput    → state.keyword   → URL ?keyword=    → API ?keyword=   → keywords::text ILIKE
  SearchBar       → state.q         → URL ?q=          → API ?q=        → fts_is / regex / chunk search (mode-dependent)
```

All three filters are independent and combine with AND — a search can carry `q` + `provision` + `keyword` + `scope` + dates simultaneously, same as `provision` already combines with `q` and `scope` today.

## Error handling

- Empty/whitespace-only `keyword` after `.trim()` is treated as absent (same as `ProvisionInput`'s `onChange={(v) => onChange(v || undefined)}` pattern) — no empty-string filter ever reaches the API.
- No new failure modes server-side: `ILIKE` on a JSONB-cast-to-text column cannot throw for any input (unlike regex, which can fail to compile) — no try/except needed around the new WHERE fragment.

## Testing

- Backend: extend `tests/test_search_queries.py` (or a new `tests/test_keyword_search.py`) with cases — keyword filter alone, keyword + q combined, keyword + chunked scope (verifies the `_search_by_chunks` wiring isn't silently dropped, same regression class as the provision Bug 2 fix), keyword filtering nothing (0 results, no error) for a tag that doesn't exist.
- Frontend: no existing test harness beyond manual/Playwright verification (confirmed by current repo state — no `*.test.tsx` files). Verify via Playwright: landing page shows both `ProvisionInput` and `KeywordInput`; typing a known keyword and submitting reaches the results page with the URL carrying `?keyword=`; combining `q` + `keyword` narrows results correctly; redesigned components render without console errors at both desktop width and the existing breakpoints already exercised by `flex-wrap` in `Toolbar`.
- Visual: screenshot landing page and results page post-redesign, compare against `minimalist-ui` principles (no gradients, no heavy shadows, no pill buttons on large containers) as a self-check before calling the redesign done.

## Execution

User asked for this work to run through agents. Plan: write the implementation plan, then execute via `subagent-driven-development` — one implementer per task (backend keyword filter, frontend keyword/provision plumbing, visual redesign dispatched as a design-skill-driven task), task review after each, Playwright verification pass at the end, fix any errors found before calling it done.
