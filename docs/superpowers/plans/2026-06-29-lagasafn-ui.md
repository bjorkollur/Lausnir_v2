# Lagasafn UI — Þrír flokkar og sérstök lögabirtingarsíða

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skipta yfirlitsflettinum í þrjá flokka (Lagasafn / Heimildir / Bókasafn) og birta lög með sérstakri greinasíðu í stað almennrar dómabirtingar.

**Architecture:** NavRail fær þrjár nýjar tenglar; sérstakur bakenda-endapunktur `/api/law/:id` skilar lögaskjali með `provisions` JSONB; nýtt `LawPanel` sýnir greinar í þrepaskiptu útliti (■ N. gr. → □ mgr.). `CatalogPage` (Heimildir) síar frá lagasafn- og baekur-hlutana. `LagasafnPage` sýnir 48 kafla; `/lagasafn/:n` sýnir lögalista; `/log/:id` sýnir einstakt lag.

**Tech Stack:** FastAPI (Python), React 19, TypeScript, Tailwind, React Query, React Router 7, Vitest + Testing Library.

## Global Constraints

- `frontend/` er rót allra TypeScript-skráa; keyra `npm test` úr þeirri möppu
- `uv run` er notað fyrir allar Python-skipanir
- Bakendi keyrir á `http://localhost:8077`
- `case_number` format: `"{nr}/{year}"` e.g. `"33/1944"`
- `provisions` JSONB: `[{"num": N, "suffix"?: "a", "text": "...", "sub"?: [{"num": M, "text": "..."}]}]`
- Lögaheiti í `summary`-dálki kann að hafa `[` fremst og `]N)` aftast (Alþingi-bóknúmerUN) — þessar merkingar skulu fjarlægðar í bakenda
- Bókasafn-síða: einungis stub — listi yfir logfraediritgerdir að koma; aldrei útfæra meira en gert er ráð fyrir
- Heimildir-síðan: sýna allt nema `key === "lagasafn"` og `key === "baekur"` í CatalogTree
- NavRail-tenglar í röð: Leit (`/`) → Lagasafn (`/lagasafn`) → Heimildir (`/heimildir`) → Bókasafn (`/bokasafn`)
- Öll React-prófanir nota `renderWithProviders` úr `src/test/renderWithProviders.tsx`
- Keyra **alltaf** `npm test` í `frontend/` eftir hverja verkefni til að ganga úr skugga um að ekkert hafi brotið

---

## Task 1: Bakendi — `/api/law/:doc_id` endapunktur

**Files:**
- Modify: `engine/api/app.py`

**Interfaces:**
- Consumes: `Document.provisions` (JSONB), `Document.summary`, `Document.url`, `Source.short_name`
- Produces: `GET /api/law/{doc_id}` → `{id, case_number, law_name, verdict_type, document_date, url, kafli, kafli_label, provisions}`

- [ ] **Step 1: Bæta við hjálparfalli og endapunkti í `engine/api/app.py`**

Opna `engine/api/app.py`. Finna línuna þar sem `@app.get("/api/provision")` byrjar. **Strax fyrir ofan** þá línu, bæta við:

```python
import re as _re_law

_LAW_FOOTNOTE_RE = _re_law.compile(r'^\[|\]\d+\)$')


def _clean_law_name(name: str | None) -> str | None:
    """Strip Alþingi footnote brackets: '[Lög um ...]1)' → 'Lög um ...'"""
    if not name:
        return name
    return _LAW_FOOTNOTE_RE.sub("", name.strip()).strip()


@app.get("/api/law/{doc_id}")
async def get_law(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return a lagasafn document with structured provisions for LawPanel."""
    orm = await session.get(Document, doc_id)
    if orm is None:
        raise HTTPException(status_code=404, detail="Law not found")

    src = await session.get(Source, orm.source_id)
    if src is None or not src.short_name.startswith("lagasafn_"):
        raise HTTPException(status_code=404, detail="Document is not a law")

    kafli_num = int(src.short_name.split("_")[1])  # "lagasafn_01" → 1

    return {
        "id": str(orm.id),
        "case_number": orm.case_number,
        "law_name": _clean_law_name(orm.summary),
        "verdict_type": orm.verdict_type,
        "document_date": orm.document_date.isoformat() if orm.document_date else None,
        "url": orm.url,
        "kafli": kafli_num,
        "kafli_label": src.display_name,
        "provisions": orm.provisions or [],
    }
```

- [ ] **Step 2: Endurræsa bakenda og staðfesta**

```bash
pkill -f "uvicorn engine.api.app" 2>/dev/null; sleep 1
DATABASE_URL=postgresql+asyncpg://geiri@localhost/lausnir_v2 \
  uv run uvicorn engine.api.app:app --port 8077 --log-level warning &
sleep 2

# Sækja UUID stjórnarskrárinnar
UUID=$(DATABASE_URL=postgresql+asyncpg://geiri@localhost/lausnir_v2 \
  uv run python3 -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
async def main():
    e = create_async_engine('postgresql+asyncpg://geiri@localhost/lausnir_v2')
    async with e.connect() as c:
        r = (await c.execute(text(\"\"\"
            SELECT d.id FROM documents d
            JOIN sources s ON s.id = d.source_id
            WHERE d.case_number='33/1944' AND s.short_name LIKE 'lagasafn_%'
            LIMIT 1
        \"\"\"))).fetchone()
        print(r[0])
asyncio.run(main())
" 2>/dev/null)

curl -s "http://localhost:8077/api/law/$UUID" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('law_name:', d['law_name'])
print('case_number:', d['case_number'])
print('kafli:', d['kafli'], d['kafli_label'])
print('provisions:', len(d['provisions']), 'greinar')
print('first provision:', json.dumps(d['provisions'][0], ensure_ascii=False))
"
```

Búist er við:
```
law_name: Stjórnarskrá lýðveldisins Íslands
case_number: 33/1944
kafli: 1 1. Stjórnskipunarlög o.fl.
provisions: 81 greinar
first provision: {"num": 1, "sub": [...], "text": "..."}
```

- [ ] **Step 3: Prófa footnote-hreinsun**

```bash
# Finna lag með [] í nafni
UUID2=$(DATABASE_URL=postgresql+asyncpg://geiri@localhost/lausnir_v2 \
  uv run python3 -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
async def main():
    e = create_async_engine('postgresql+asyncpg://geiri@localhost/lausnir_v2')
    async with e.connect() as c:
        r = (await c.execute(text(\"\"\"
            SELECT d.id FROM documents d
            JOIN sources s ON s.id = d.source_id
            WHERE d.summary LIKE '[%]%' AND s.short_name LIKE 'lagasafn_%'
            LIMIT 1
        \"\"\"))).fetchone()
        print(r[0])
asyncio.run(main())
" 2>/dev/null)

curl -s "http://localhost:8077/api/law/$UUID2" | python3 -c "
import json,sys; d=json.load(sys.stdin); print('law_name:', d['law_name'])
"
```

Búist er við að `law_name` byrji EKKI á `[` og endi EKKI á `]1)`.

- [ ] **Step 4: 404-próf**

```bash
curl -s -o /dev/null -w "%{http_code}" "http://localhost:8077/api/law/00000000-0000-0000-0000-000000000000"
# Á að skila 404
```

- [ ] **Step 5: Commit**

```bash
cd /Volumes/RuleOfLaw/Lausnir
git add engine/api/app.py
git commit -m "feat: add /api/law/:id endpoint with provisions and law_name cleanup"
```

---

## Task 2: Frontend — gerðir, API-client og useLaw hook

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/hooks/useLaw.ts`

**Interfaces:**
- Consumes: `GET /api/law/:id` (from Task 1)
- Produces:
  - `LawDetail` type exported from `types.ts`
  - `fetchLaw(id: string): Promise<LawDetail>` exported from `client.ts`
  - `useLaw(id: string)` hook from `hooks/useLaw.ts` — returns React Query result

- [ ] **Step 1: Bæta við gerðum í `frontend/src/api/types.ts`**

Bæta við eftirfarandi **neðst** í `types.ts`:

```typescript
// GET /api/law/:id
export interface SubProvision {
  num: number;
  text: string;
}

export interface Provision {
  num: number;
  suffix?: string;   // "a", "b" etc. — til staðar aðeins í stafliðagreinum (218. gr. a.)
  text: string;
  sub?: SubProvision[];
}

export interface LawDetail {
  id: string;
  case_number: string | null;
  law_name: string | null;
  verdict_type: string | null;
  document_date: string | null;
  url: string | null;
  kafli: number;
  kafli_label: string;
  provisions: Provision[];
}
```

- [ ] **Step 2: Bæta við `fetchLaw` í `frontend/src/api/client.ts`**

Bæta við **eftir** `fetchDocument`-línuna:

```typescript
export const fetchLaw = (id: string) => getJson<LawDetail>(`/api/law/${id}`);
```

Einnig bæta `LawDetail` við import-línuna efst:

```typescript
import type { SearchParams, SearchResponse, FacetsResponse, SourcesResponse, DocumentDetail, LawDetail } from "./types";
```

- [ ] **Step 3: Búa til `frontend/src/hooks/useLaw.ts`**

```typescript
import { useQuery } from "@tanstack/react-query";
import { fetchLaw } from "../api/client";

export function useLaw(id: string) {
  return useQuery({
    queryKey: ["law", id],
    queryFn: () => fetchLaw(id),
    enabled: !!id,
    staleTime: 5 * 60 * 1000,
  });
}
```

- [ ] **Step 4: Keyra prófanir**

```bash
cd /Volumes/RuleOfLaw/Lausnir/frontend
npm test
```

Búist er við: öll fyrirlæg próf standast (ekkert brotnar).

- [ ] **Step 5: Commit**

```bash
cd /Volumes/RuleOfLaw/Lausnir
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/hooks/useLaw.ts
git commit -m "feat: add LawDetail types, fetchLaw client, and useLaw hook"
```

---

## Task 3: NavRail, leiðir og Heimildir-síuun + BókasafnPage

**Files:**
- Modify: `frontend/src/components/NavRail.tsx`
- Modify: `frontend/src/components/NavRail.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/routes/CatalogPage.tsx`
- Create: `frontend/src/routes/BokasafnPage.tsx`

**Interfaces:**
- Consumes: `useSources()` (existing), `CatalogTree` (existing)
- Produces:
  - NavRail með fjórum tenglum: `/` (Leit), `/lagasafn` (Lagasafn), `/heimildir` (Heimildir), `/bokasafn` (Bókasafn)
  - Routes: `/lagasafn`, `/lagasafn/:n`, `/log/:id`, `/bokasafn`
  - CatalogPage sýnir einungis domstolar/stjornsysla/nefndir (ekki lagasafn eða baekur)

- [ ] **Step 1: Uppfæra `frontend/src/components/NavRail.test.tsx`**

Skrifa test fyrst (TDD). Eyða öllu innihaldi skráarinnar og skrifa:

```typescript
import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { NavRail } from "./NavRail";

describe("NavRail", () => {
  it("shows all four nav links in order", () => {
    renderWithProviders(<NavRail />);
    expect(screen.getByRole("link", { name: /Leit/i })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /Lagasafn/i })).toHaveAttribute("href", "/lagasafn");
    expect(screen.getByRole("link", { name: /Heimildir/i })).toHaveAttribute("href", "/heimildir");
    expect(screen.getByRole("link", { name: /Bókasafn/i })).toHaveAttribute("href", "/bokasafn");
  });
});
```

- [ ] **Step 2: Keyra próf til að staðfesta að þeir bresti**

```bash
cd /Volumes/RuleOfLaw/Lausnir/frontend
npm test -- --reporter=verbose 2>&1 | grep -A5 "NavRail"
```

Búist er við: FAIL — `/lagasafn`, `/bokasafn` tenglar ekki til staðar.

- [ ] **Step 3: Uppfæra `frontend/src/components/NavRail.tsx`**

```typescript
import { NavLink } from "react-router-dom";

const item = "flex flex-col items-center gap-1 py-3 text-xs text-slate-500 hover:text-indigo-600";
const active = "text-indigo-600";

function cls(isActive: boolean) {
  return `${item} ${isActive ? active : ""}`;
}

export function NavRail() {
  return (
    <nav className="w-20 shrink-0 border-r border-slate-200 bg-white flex flex-col items-center py-3">
      <div className="mb-4 font-bold text-indigo-600 text-lg">L</div>
      <NavLink to="/" className={({ isActive }) => cls(isActive)} end>
        <span aria-hidden>⌕</span>Leit
      </NavLink>
      <NavLink to="/lagasafn" className={({ isActive }) => cls(isActive)}>
        <span aria-hidden>📜</span>Lagasafn
      </NavLink>
      <NavLink to="/heimildir" className={({ isActive }) => cls(isActive)}>
        <span aria-hidden>⚖</span>Heimildir
      </NavLink>
      <NavLink to="/bokasafn" className={({ isActive }) => cls(isActive)}>
        <span aria-hidden>📚</span>Bókasafn
      </NavLink>
      <div className="mt-auto text-xs text-slate-300">👤</div>
    </nav>
  );
}
```

- [ ] **Step 4: Keyra próf til að staðfesta að þau standast**

```bash
cd /Volumes/RuleOfLaw/Lausnir/frontend
npm test -- --reporter=verbose 2>&1 | grep -A5 "NavRail"
```

Búist er við: PASS.

- [ ] **Step 5: Uppfæra `frontend/src/routes/CatalogPage.tsx`**

```typescript
import { useSources } from "../hooks/useSources";
import { CatalogTree } from "../components/CatalogTree";
import { ErrorState } from "../components/states";

// Heimildir sýnir einungis dóma og úrskurði; lagasafn og bókasafn hafa eigin síður
const EXCLUDED_KEYS = new Set(["lagasafn", "baekur"]);

export default function CatalogPage() {
  const { data, isPending, isError, error } = useSources();
  const filtered = data?.catalog.filter((n) => !EXCLUDED_KEYS.has(n.key)) ?? [];
  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-bold mb-4">Heimildir</h1>
      {isPending ? <div className="h-64 bg-slate-100 rounded animate-pulse" />
        : isError ? <ErrorState error={error} />
        : <CatalogTree nodes={filtered} />}
    </div>
  );
}
```

- [ ] **Step 6: Búa til `frontend/src/routes/BokasafnPage.tsx`**

```typescript
import { useSources } from "../hooks/useSources";

export default function BokasafnPage() {
  const { data } = useSources();
  const baekurNode = data?.catalog.find((n) => n.key === "baekur");
  const count = baekurNode?.count ?? 0;

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-bold mb-1">Bókasafn</h1>
      <p className="text-slate-500 mb-6 text-sm">
        {count > 0 ? `${count} lögfræðiritgerðir` : "Hleður..."}
      </p>
      <div className="bg-white rounded-lg border border-slate-200 p-6 text-slate-500 text-sm">
        Leit í lögfræðiritgerðum kemur hér. Nota má aðalleitina til að leita í þessum gögnum með því að velja <strong>Lögfræðiritgerðir</strong> í flokkavelju.
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Uppfæra `frontend/src/App.tsx`**

```typescript
import { Routes, Route } from "react-router-dom";
import { NavRail } from "./components/NavRail";
import SearchPage from "./routes/SearchPage";
import CatalogPage from "./routes/CatalogPage";
import DocumentPage from "./routes/DocumentPage";
import LagasafnPage from "./routes/LagasafnPage";
import LagasafnKafliPage from "./routes/LagasafnKafliPage";
import LawPage from "./routes/LawPage";
import BokasafnPage from "./routes/BokasafnPage";

export default function App() {
  return (
    <div className="flex h-full">
      <NavRail />
      <div className="flex-1 min-w-0 overflow-auto">
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/lagasafn" element={<LagasafnPage />} />
          <Route path="/lagasafn/:n" element={<LagasafnKafliPage />} />
          <Route path="/log/:id" element={<LawPage />} />
          <Route path="/heimildir" element={<CatalogPage />} />
          <Route path="/bokasafn" element={<BokasafnPage />} />
          <Route path="/domur/:id" element={<DocumentPage />} />
        </Routes>
      </div>
    </div>
  );
}
```

- [ ] **Step 8: Keyra öll próf**

```bash
cd /Volumes/RuleOfLaw/Lausnir/frontend
npm test
```

Búist er við: öll próf standast.

- [ ] **Step 9: Commit**

```bash
cd /Volumes/RuleOfLaw/Lausnir
git add \
  frontend/src/components/NavRail.tsx \
  frontend/src/components/NavRail.test.tsx \
  frontend/src/App.tsx \
  frontend/src/routes/CatalogPage.tsx \
  frontend/src/routes/BokasafnPage.tsx
git commit -m "feat: three-section nav (Lagasafn/Heimildir/Bókasafn) + Heimildir filter"
```

---

## Task 4: LagasafnPage — kaflayfirlit og lögalisti

**Files:**
- Create: `frontend/src/routes/LagasafnPage.tsx`
- Create: `frontend/src/routes/LagasafnKafliPage.tsx`

**Interfaces:**
- Consumes: `useSources()` (fetches `/api/sources`), `searchDocuments()` (fetches `/api/search`)
- Produces:
  - `/lagasafn` → 48 kaflar sem smellanlegar raðir með lögafjölda
  - `/lagasafn/:n` → lögalisti í kafla N, hvert lag smellanleg tengill að `/log/:id`

- [ ] **Step 1: Búa til `frontend/src/routes/LagasafnPage.tsx`**

```typescript
import { Link } from "react-router-dom";
import { useSources } from "../hooks/useSources";
import { ErrorState } from "../components/states";

export default function LagasafnPage() {
  const { data, isPending, isError } = useSources();
  const lagasafnNode = data?.catalog.find((n) => n.key === "lagasafn");
  const chapters = lagasafnNode?.children ?? [];

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-2xl font-bold mb-1">Lagasafn Alþingis</h1>
      <p className="text-slate-500 mb-6 text-sm">
        {lagasafnNode ? `${lagasafnNode.count} lög í ${chapters.length} köflum` : "Hleður..."}
      </p>

      {isPending ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-14 bg-slate-100 rounded-lg animate-pulse" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState error={new Error("Ekki tókst að sækja lagasafn")} />
      ) : (
        <div className="space-y-2">
          {chapters.map((ch) => {
            const n = ch.key.replace("lagasafn_", "").replace(/^0/, "");
            return (
              <Link
                key={ch.key}
                to={`/lagasafn/${n}`}
                className="flex items-center justify-between px-5 py-4 bg-white rounded-lg border border-slate-200 hover:border-indigo-300 hover:bg-indigo-50 transition-colors group"
              >
                <span className="font-medium text-slate-800 group-hover:text-indigo-700">
                  {ch.label}
                </span>
                <span className="text-slate-400 text-sm tabular-nums ml-4">
                  {ch.count}
                  <span className="ml-1 text-slate-300">›</span>
                </span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

Athugið: `ch.key` er t.d. `"lagasafn_01"`, `n` verður `"1"` (með `.replace(/^0/, "")`) og URL verður `/lagasafn/1`. Hér á neðan þarf `LagasafnKafliPage` að búa `"lagasafn_01"` til baka með `.padStart(2, "0")`.

- [ ] **Step 2: Búa til `frontend/src/routes/LagasafnKafliPage.tsx`**

```typescript
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { searchDocuments } from "../api/client";
import { useSources } from "../hooks/useSources";
import { ErrorState } from "../components/states";

export default function LagasafnKafliPage() {
  const { n } = useParams<{ n: string }>();
  // "1" → "lagasafn_01", "12" → "lagasafn_12"
  const scope = `lagasafn_${n?.padStart(2, "0") ?? "01"}`;

  const { data, isPending, isError } = useQuery({
    queryKey: ["lagasafn-kafli", scope],
    queryFn: () =>
      searchDocuments({
        q: "",
        mode: "keyword",
        scope: [scope],
        sort: "oldest",
        page: 1,
        page_size: 200,
      }),
    enabled: !!n,
    staleTime: 5 * 60 * 1000,
  });

  const { data: sources } = useSources();
  const lagasafnNode = sources?.catalog.find((c) => c.key === "lagasafn");
  const chapter = lagasafnNode?.children?.find((c) => c.key === scope);

  return (
    <div className="p-6 max-w-3xl">
      <Link
        to="/lagasafn"
        className="text-sm text-slate-500 hover:text-indigo-600 mb-4 inline-block"
      >
        ← Lagasafn
      </Link>
      <h1 className="text-2xl font-bold mb-6">
        {chapter?.label ?? `Kafli ${n}`}
      </h1>

      {isPending ? (
        <div className="space-y-1">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-10 bg-slate-100 rounded animate-pulse" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState error={new Error("Ekki tókst að sækja lög")} />
      ) : (
        <div className="divide-y divide-slate-100">
          {(data?.results ?? []).map((r) => (
            <Link
              key={r.id}
              to={`/log/${r.id}`}
              className="flex items-start justify-between py-3 px-2 hover:bg-slate-50 rounded group"
            >
              <span className="text-indigo-700 group-hover:underline text-sm leading-snug">
                {/* snippet = lögaheiti þegar q="" fyrir lagasafn */}
                {r.snippet || r.urlausn}
              </span>
              <span className="text-slate-400 text-xs shrink-0 ml-4 tabular-nums pt-0.5">
                nr.&nbsp;{r.case_number}
              </span>
            </Link>
          ))}
          {data?.results.length === 0 && (
            <p className="text-slate-500 text-sm py-4">Engin lög fundust í þessum kafla.</p>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Keyra öll próf**

```bash
cd /Volumes/RuleOfLaw/Lausnir/frontend
npm test
```

Búist er við: öll próf standast.

- [ ] **Step 4: Handprófun í vafra**

Ræsa vefþjón: `cd /Volumes/RuleOfLaw/Lausnir/frontend && npm run dev`

Opna `http://localhost:5173`:
1. Smella á **Lagasafn** í NavRail → 48 kaflar birtast sem raðir
2. Smella á kafla 1 (Stjórnskipunarlög) → lögalisti birtist, stjórnarskráin sést
3. Smella á næsta kafla (t.d. 5 — Dómstólar og réttarfar) → annar lögalisti

- [ ] **Step 5: Commit**

```bash
cd /Volumes/RuleOfLaw/Lausnir
git add \
  frontend/src/routes/LagasafnPage.tsx \
  frontend/src/routes/LagasafnKafliPage.tsx
git commit -m "feat: LagasafnPage chapter browser and LagasafnKafliPage law list"
```

---

## Task 5: LawPanel og LawPage — sérstök lögabirtingarsíða

**Files:**
- Create: `frontend/src/components/LawPanel.tsx`
- Create: `frontend/src/routes/LawPage.tsx`

**Interfaces:**
- Consumes: `useLaw(id)` hook (Task 2), `LawDetail` + `Provision` + `SubProvision` types (Task 2)
- Produces:
  - `LawPanel` component — sýnir lögahaus + greinar í þrepaskiptu útliti
  - `/log/:id` route — sækir lag og sýnir í LawPanel

- [ ] **Step 1: Búa til `frontend/src/components/LawPanel.tsx`**

```typescript
import { Link } from "react-router-dom";
import type { LawDetail, Provision } from "../api/types";

function grLabel(p: Provision): string {
  let label = `${p.num}. gr.`;
  if (p.suffix) label += ` ${p.suffix}.`;
  return label;
}

function ProvisionBlock({ p }: { p: Provision }) {
  return (
    <div className="py-4 border-b border-slate-100 last:border-0">
      <div className="font-bold text-slate-900 mb-2 text-sm">
        ■ {grLabel(p)}
      </div>
      {p.sub && p.sub.length > 0 ? (
        <div className="space-y-2">
          {p.sub.map((s) => (
            <p key={s.num} className="text-slate-800 leading-relaxed ml-4 text-sm">
              □ {s.text}
            </p>
          ))}
        </div>
      ) : (
        <p className="text-slate-800 leading-relaxed ml-4 text-sm">□ {p.text}</p>
      )}
    </div>
  );
}

export function LawPanel({ law }: { law: LawDetail }) {
  return (
    <div className="bg-[#f5f7fb] flex-1 overflow-y-auto py-8">
      <article className="mx-auto max-w-2xl bg-white rounded-lg p-8 shadow-sm">
        {/* Haus */}
        <header className="text-center space-y-1 mb-8 pb-6 border-b border-slate-200">
          {law.document_date && law.case_number && (
            <div className="text-sm text-slate-500">
              {law.document_date} · nr. {law.case_number}
            </div>
          )}
          <h1 className="text-2xl font-bold text-slate-900">{law.law_name}</h1>
          {law.url && (
            <a
              href={law.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-indigo-600 hover:underline block"
            >
              Ferill málsins á Alþingi
            </a>
          )}
          {law.document_date && (
            <div className="text-sm text-slate-500">
              Tók gildi {law.document_date}
            </div>
          )}
          <div className="text-xs text-slate-400 pt-1">
            <Link to={`/lagasafn/${law.kafli}`} className="hover:underline">
              {law.kafli_label}
            </Link>
          </div>
        </header>

        {/* Greinar */}
        {law.provisions.length > 0 ? (
          <div>
            {law.provisions.map((p) => (
              <ProvisionBlock key={`${p.num}-${p.suffix ?? ""}`} p={p} />
            ))}
          </div>
        ) : (
          <p className="text-slate-500 italic text-center py-8 text-sm">
            Engar greinar fundust í þessum lögum.
          </p>
        )}
      </article>
    </div>
  );
}
```

- [ ] **Step 2: Búa til `frontend/src/routes/LawPage.tsx`**

```typescript
import { useParams, Link } from "react-router-dom";
import { useLaw } from "../hooks/useLaw";
import { LawPanel } from "../components/LawPanel";
import { ErrorState } from "../components/states";

export default function LawPage() {
  const { id } = useParams<{ id: string }>();
  const { data, isPending, isError, error } = useLaw(id ?? "");

  if (isPending) {
    return (
      <div className="bg-[#f5f7fb] flex-1 p-8">
        <div className="mx-auto max-w-2xl bg-white rounded-lg p-8 shadow-sm space-y-4 animate-pulse">
          <div className="h-4 bg-slate-100 rounded w-1/3 mx-auto" />
          <div className="h-8 bg-slate-100 rounded w-2/3 mx-auto" />
          <div className="h-4 bg-slate-100 rounded w-1/2 mx-auto" />
          <div className="h-px bg-slate-200 my-4" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-16 bg-slate-50 rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-8">
        <Link to="/lagasafn" className="text-sm text-slate-500 hover:text-indigo-600 mb-4 inline-block">
          ← Lagasafn
        </Link>
        <ErrorState error={error} />
      </div>
    );
  }

  if (!data) return null;

  return <LawPanel law={data} />;
}
```

- [ ] **Step 3: Keyra öll próf**

```bash
cd /Volumes/RuleOfLaw/Lausnir/frontend
npm test
```

Búist er við: öll próf standast.

- [ ] **Step 4: Handprófun — opna stjórnarskrá**

1. Fara á `http://localhost:5173/lagasafn`
2. Smella á kafla 1 → lögalisti birtist
3. Smella á **Stjórnarskrá lýðveldisins Íslands**
4. Staðfesta:
   - Haus: "1944-06-17 · nr. 33/1944", "Stjórnarskrá lýðveldisins Íslands"
   - Tengill "Ferill málsins á Alþingi" opnar `althingi.is`
   - Greinar: "■ 1. gr." → "□ Ísland er lýðveldi..."
   - Greinar: "■ 2. gr." → "□ Alþingi og forseti..."
   - Meira en 80 greinar sýnilegar

- [ ] **Step 5: Handprófun — lag með stafliðagreinar**

Opna hegningarlög (`case_number = 19/1940`):
1. Leita að því í kafla 18 (Refsilög) eða nota aðalleit
2. Staðfesta að **218. gr.** birtist eðlilega
3. Staðfesta að **218. gr. a.** birtist sem sér provision (með `suffix = "a"`)
4. Staðfesta að **218. gr. b.** og **218. gr. c.** birtist á eftir

- [ ] **Step 6: Commit**

```bash
cd /Volumes/RuleOfLaw/Lausnir
git add \
  frontend/src/components/LawPanel.tsx \
  frontend/src/routes/LawPage.tsx
git commit -m "feat: LawPanel and LawPage — structured law display with provisions"
```

---

## Samantekt verkefna og skráa

| Skrá | Breyting |
|------|----------|
| `engine/api/app.py` | + `GET /api/law/{doc_id}`, `_clean_law_name()` |
| `frontend/src/api/types.ts` | + `SubProvision`, `Provision`, `LawDetail` |
| `frontend/src/api/client.ts` | + `fetchLaw(id)` |
| `frontend/src/hooks/useLaw.ts` | Ný skrá — `useLaw(id)` hook |
| `frontend/src/components/NavRail.tsx` | 4 tenglar: Leit / Lagasafn / Heimildir / Bókasafn |
| `frontend/src/components/NavRail.test.tsx` | Uppfært test |
| `frontend/src/App.tsx` | + `/lagasafn`, `/lagasafn/:n`, `/log/:id`, `/bokasafn` routes |
| `frontend/src/routes/CatalogPage.tsx` | Sía út `lagasafn` og `baekur` hnúta |
| `frontend/src/routes/BokasafnPage.tsx` | Ný stub-síða |
| `frontend/src/routes/LagasafnPage.tsx` | Ný kaflayfirlit-síða |
| `frontend/src/routes/LagasafnKafliPage.tsx` | Ný lögalista-síða |
| `frontend/src/components/LawPanel.tsx` | Nýtt provision-renderer |
| `frontend/src/routes/LawPage.tsx` | Ný `/log/:id` síða |
