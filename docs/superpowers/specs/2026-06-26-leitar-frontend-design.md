# Lausnir v2 — Leitar-frontend (v1) — Hönnun

**Dagsetning:** 2026-06-26
**Staða:** Samþykkt hönnun, tilbúin í implementation-plan.

## Samhengi & tilgangur

Búið er að byggja FastAPI leitar-API ofan á ~89.400 íslenskra réttarheimilda
(`engine/api/app.py`, `engine/search/queries.py`, `engine/config/source_groups.py`).
API-ið styður: lemmatíseraða orðaleit (`fts_is`), regex-leit (pg_trgm), stigskipt
scope-tré (Dómstólar/Stjórnsýsla/Nefndir/Bækur með Dómar/Úrskurðir drill-down),
dagsetningasíur, leitar-háðar facet-tölur og skjala-detail með áfrýjunartengingum.

Það vantar **viðmót**. Þessi hönnun lýsir v1 frontend — leitar- og lesviðmóti í anda
[Fons Juris](https://fons.is) (notandi sendi ítarleg UI-spec af leitar- og skjalasíðu),
en með okkar eigin hreina stíl.

## Ákvarðanir (festar með notanda)

- **Sjónræn nálgun:** innblásið af Fons (sama uppbygging/virkni), okkar eigin hreini
  stíll — ekki Fons-litir/letur.
- **v1-umfang:** hrein lesleit + skjalasýn + catalog-vafrun. **Utan v1:** möppur,
  vistaðar leitir, glósur, „Áþekkar heimildir", Lagaskrá/inline-lagatilvísanir,
  „Vísanir í heimild", síur eftir dómurum/málflytjendum (vantar structured gögn).
- **Stakkur:** React 19 + Vite + TypeScript + Tailwind + TanStack Query +
  Radix/shadcn frumeiningar + React Router.
- **Keyrsla:** local-only, engin auth.

## Uppbygging & keyrsla

Nýtt `frontend/` í rót (við hlið `engine/`):

```
frontend/
  src/
    api/         # typed client + TS-týpur sem spegla JSON (search, facets, sources, document)
    routes/      # SearchPage (/), CatalogPage (/heimildir), DocumentPage (/domur/:id)
    components/  # NavRail, SearchBar, Toolbar, ScopeChips, FacetSidebar, ResultCard, DocPanel ...
    hooks/       # useSearch (infinite), useFacets, useDocument, useSources
    lib/         # tokens, utils (citation, highlight sanitize)
  index.html, vite.config.ts, tailwind.config.ts, package.json
```

- **Leiðir (React Router):** `/` leit · `/heimildir` catalog-vafrun · `/domur/:id` skjal.
- **API-client:** þunnur typed wrapper les `VITE_API_BASE` (sjálfgefið
  `http://localhost:8077`). CORS þegar opið fyrir localhost.
- **Þróun:** `uvicorn engine.api.app:app` á `:8077` + `npm run dev` (Vite á `:5173`).
- **Síðar (nice-to-have, ekki v1):** `npm run build` → static, mountað á FastAPI með
  `StaticFiles` fyrir eina-skipun keyrslu.
- **Týpu-öryggi:** TS-interface fyrir hvert svar (`SearchResult`, `CatalogNode`,
  `DocumentDetail`, `Facets`) í `api/types.ts`.

## Útlit & stíll

**Leitarleið `/` — fullt Fons-líkt þrískipt (desktop):**

```
┌─────────────────────────────────────────────────────────────────────┐
│ HAUS: [logo]   [ 🔍 stórt leitarbox ]        [regex⃝] [röðun▾]       │
│        [scope-chips: Dómstólar ✕ · Hæstiréttur–Dómar ✕]  [Tímabil▾]  │
├──────────┬──────────────────────────────────────┬───────────────────┤
│ NAV-RAIL │  NIÐURSTÖÐUR (flex, ~720px)          │ FACET-SIDEBAR      │
│ (slim)   │  „N niðurstöður"                     │ (~300px)           │
│  ⌂ Leit  │  ▸ ResultCard                        │ stigskipt tré      │
│  ⚖ Heim- │  ▸ ResultCard                        │ m/ lifandi tölum   │
│   ildir  │  … infinite scroll                   │                    │
│  👤(síðar)│                                      │                    │
└──────────┴──────────────────────────────────────┴───────────────────┘
```

- **Nav-rail (v1, lágmark):** logo efst; **Leit** (`/`), **Heimildir** (`/heimildir`);
  notanda-pláss neðst (óvirkt í v1). Engir dauðir hlekkir.
- **ResultCard:** urlausn (titill → `/domur/:id`) · dómstóll – tegund · dagsetning ·
  aðilar (með „Sjá meira" útþenslu) · útdráttur með `<mark>`-highlight · lykilorð-chips ·
  áfrýjunar-merki ef `has_appeal_links`.
- **Facet-sidebar:** stigskipt checkbox-tré úr `/api/facets`; tölur uppfærast við leit;
  val uppfærir scope (→ niðurstöður). Tréð: Dómstólar → Hæstiréttur/Landsréttur/
  Héraðsdómar → Dómar/Úrskurðir (+ Málskotsbeiðnir); Stjórnsýsla; Nefndir; Bækur.

**Catalog-leið `/heimildir`:** rendar catalog-tréð úr `/api/sources` (heildartölur);
hver hnútur → scoped leit á `/`.

**Skjalaleið `/domur/:id`:** efst bar (← til baka, urlausn, ytri tengill ↗); fyrir neðan
miðjusett „prent-líkt" panel: haus (dómstóll, mál nr., dags., aðilar/„gegn"),
Lykilorð-chips, Reifun (skáletruð), Meginmál (markdown → kaflar), Áfrýjunartengingar neðst.

**Design tokens (okkar eigin):** hlutlaus grunnur (slate/zinc grátóna, hvít yfirborð),
einn rólegur accent (indigo/blár) fyrir tengla + virkt val; highlight = mjúkt gult;
letur = **Inter**. Responsive: facet-sidebar verður „drawer" á mjórri skjá.

## Íhlutir (components)

| Íhlutur | Hlutverk | Háð |
|---------|----------|-----|
| `NavRail` | Slim vinstri rail (Leit, Heimildir) | React Router |
| `SearchBar` | Leitarbox + regex-rofi | URL state |
| `Toolbar` | Röðun, Tímabil-popover, regex-reitir-popover | URL state |
| `ScopeChips` | Virkt scope sýnt sem chips m/ ✕ | URL state |
| `FacetSidebar` | Stigskipt checkbox-tré m/ tölum | `useFacets` |
| `ResultsList` | Listi + infinite scroll (IntersectionObserver) | `useSearch` |
| `ResultCard` | Ein niðurstaða | — |
| `CatalogTree` | `/heimildir` tré | `useSources` |
| `DocPanel` | Skjala-detail (markdown body) | `useDocument` |
| `DocHeader` | Bar yfir skjali (til baka, ytri tengill) | — |

## Gagnaflæði & ástand

- **URL = sannleikur** fyrir leitarástand: `q, mode, scope[], date_from, date_to, sort,
  regex_fields` sem query-params → bókamerkjanlegt, deilanlegt, „til baka" virkar.
- **TanStack Query lyklar:**
  - *Leit* — infinite query á `(q, mode, scope, dates, sort, regex_fields)` → `/api/search`
    síður; infinite scroll.
  - *Facets* — sér query á `(q, mode, dates, regex_fields)` — **ekki** scope (facets hunsa
    val) → sidebar-tölur breytast bara við nýja leit/dagsetningu, ekki við hökun.
  - *Skjal* — query á `id` → `/api/document/:id`.
  - *Sources* — query fyrir `/heimildir`.
- **Val í facet-tré** → uppfærir scope í URL → leit endursækir; sidebar helst stöðug.
- **Markdown:** `body_text`/`markdown` rendað með `react-markdown` (sanitize sem leyfir
  `<mark>`), kaflar verða fyrirsagnir.

## Regex-viðmót (okkar hönnun — Fons hefur ekki regex)

- Rofi við leitarbox. Virkt → placeholder „regex mynstur…"; lítill *reitir*-popover velur
  `regex_fields` (sjálfg. `body_text`; líka `summary`, `case_number`, `parties`,
  `keywords`, `lower_body_text`).
- Ógilt mynstur → API skilar 400 → inline villuskilaboð undir boxi.
- Tímálok (statement_timeout 10s á bakenda) → vinaleg skilaboð („mynstrið er of víðtækt").
- Röðun sjálfgefið „nýjast" í regex-ham (relevance á ekki við).

## Ástönd (states)

- **Loading:** skeleton-kort í niðurstöðum + skeleton í facet.
- **Tómt:** „Engar niðurstöður" + vísbending um að víkka leit.
- **Villa:** 400 → inline; 500/net → toast + „reyna aftur".
- **Tímabil:** popover með frá/til dagsetningum (skrifar `date_from`/`date_to`).

## Prófanir

- **Einingapróf (Vitest + React Testing Library):** `ResultCard`, `FacetSidebar` (tré +
  hökun), regex-rofi, `ScopeChips`.
- **Integration:** MSW (Mock Service Worker) til að móta API-svör.
- **E2E (handvirkt):** Playwright gegn keyrandi API + uvicorn (staðfesta leit, facet-val,
  skjalasýn sjónrænt).

## API-endapunktar notaðir (til viðmiðunar)

- `GET /api/search` — `q, mode, scope[], date_from, date_to, sort, page, page_size,
  regex_fields` → `{total, page, page_size, results[]}` (urlausn, court, case_number,
  document_date, verdict_type, source, snippet, keywords, plaintiffs, defendants,
  has_appeal_links).
- `GET /api/facets` — `q, mode, date_from, date_to, regex_fields` → `{catalog[], total}`
  (scope-tré með leitar-háðum tölum).
- `GET /api/sources` — heildar catalog-tré + flatur listi + `regex_fields`.
- `GET /api/document/{id}` — full skjal + `markdown` + `appeal_links`.

## Utan umfangs (v1)

Möppur/„Bæta í möppu", vistaðar leitir, glósur, „Áþekkar heimildir" (þarf embeddings —
frestað), Lagaskrá + inline-lagatilvísanir, „Vísanir í heimild" (tilvísanagraf), síur eftir
dómurum/málflytjendum/lagatilvísun (vantar structured gögn), build-served-by-FastAPI,
auth/hýsing.
