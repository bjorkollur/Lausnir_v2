# 07 — Framendi

← [Wiki-forsíða](README.md)

`frontend/` — React 19 + TypeScript + Vite + TailwindCSS 4.

```bash
cd frontend
npm run dev        # Vite á porti 5173
npm test           # vitest run
npm run build      # tsc -b && vite build
```

API-grunnur: `import.meta.env.VITE_API_BASE ?? "http://localhost:8077"`.

## Síður (React Router 7)

| Slóð | Íhlutur | Hlutverk |
|---|---|---|
| `/` | `SearchPage` | Aðalleitin — leitarstika, facets, niðurstöður |
| `/lagasafn` | `LagasafnPage` | Yfirlit yfir 48 kafla lagasafnsins |
| `/lagasafn/:n` | `LagasafnKafliPage` | Lög innan eins kafla |
| `/log/:id` | `LawPage` | Ein lög með ákvæðum (`LawPanel`) |
| `/heimildir` | `CatalogPage` | Heimildatréð |
| `/bokasafn` | `BokasafnPage` | Bókasafnið |
| `/domur/:id` | `DocumentPage` | Eitt skjal — dómur, úrskurður eða **heil bók** |

`TopNav` (56px lárétt stika) er alltaf efst; `#efni` fyrir neðan hana er skrunsvæði appsins — glugginn sjálfur skrunar aldrei.

**Síðuheiti:** hver síða setur `<title>` með `useDocumentTitle` (`lib/useDocumentTitle.ts`), t.d. „„skaðabætur“ – Leit – Lausnir" eða heiti dóms/laga (WCAG 2.4.2). `PageShell` gerir það sjálfkrafa út frá `title`.

**Fókus við leiðaskipti:** `App` færir fókus á `#efni` (`tabIndex=-1`) þegar `pathname` breytist, nema síðan hafi sjálf sett fókus inni í efninu (forsíðan setur hann á leitarreitinn).

**Skrunminni:** `lib/useScrollMemory.ts` endurheimtir skrunstöðu innri skrunsvæða eftir sögufærslu, eins og vafrinn gerir fyrir gluggann: ný færsla (hlekkur, leit) byrjar efst, til baka/áfram skilar lesanda þangað sem hann var. Notað á `#efni`, niðurstöðulistann (`SearchPage`) og lesarann (`DocPanel`). Án þess skilaði „til baka" úr dómi lesanda á niðurstöðu 1, og tilvitnun opnuð neðarlega í dómi opnaði næsta dóm jafn neðarlega.

## Ástandsstjórnun

**Leitarástand býr í URL-inu**, ekki í React-state. `lib/searchState.ts`:

```ts
parseSearchState(sp: URLSearchParams): SearchState
toSearchParams(s: SearchState): URLSearchParams
```

Þetta gerir hverja leit deilanlega og bókamerkjanlega, og bakhnappurinn virkar rétt. Ógild gildi falla þögult í sjálfgefið (`mode` sem er ekki í `MODES` → `keyword`).

Snyrtimennska: `proximity_n` er aðeins skrifað í URL þegar það er annað en sjálfgefið 5.

**Leit eða forsíða:** `hasSearchCriteria(s)` — fyrirspurn **eða** einhver sía (`activeFilterCount`: heimildir, tímabil, lagaákvæði, lykilorð). Án hvors tveggja sýnir `/` forsíðuna. Tímabil eitt og sér telst nú leit (áður var slóð með aðeins `date_from` áfram forsíðan). `CLEAR_FILTERS` fjarlægir allar síur en heldur fyrirspurn, ham og röðun.

Hamirnir sjö, heiti þeirra og lýsingar eru á **einum stað**, `lib/modes.ts` (`ALL_MODES`, `MODE_LABELS`, `MODE_HINTS`, `FTS_MODES`, `sortForMode`, `effectiveSort`) — `LandingView`, `ModeDropdown` og `Toolbar` báru áður hvert sitt eintak.

**Gagnasókn** er í gegnum TanStack Query hooks: `useSearch`, `useFacets`, `useSources`, `useDocument`, `useLaw`.

## Íhlutir

| Íhlutur | Hlutverk |
|---|---|
| `SearchBar` | Leitarreitur (`role="search"`) með áföstum leitarhnappi og hreinsihnappi |
| `ModeDropdown` | Val á leitarham + `proximity_n` reitur + „?"-sprettigluggi sem lýsir hömunum (falinn undir `sm`) |
| `Toolbar` | `SortSelect` (sýnir „Nýjast fyrst" þegar engin fyrirspurn er til að raða eftir vægi — `effectiveSort` í `lib/modes.ts`), `FieldsPicker` (regex-reitir) og sameiginlegi `CONTROL`-klasinn |
| `ActiveFilters` | Allar virkar síur fyrir ofan niðurstöðurnar — heimildir, tímabil, lagaákvæði, lykilorð — hver fjarlægjanleg, og „Hreinsa allt" |
| `FilterPanel` / `FacetNode` | Síuspjaldið vinstra megin: tímabil, lagaákvæði, lykilorð og heimildatréð. Undir `lg` rennur það inn sem modal-gluggi (`role="dialog"`, fókusgildra, Escape lokar, „Sýna N niðurstöður") |
| `ResultsList` / `ResultCard` | Niðurstöður með útdráttum og `anchor` (efnisgreinar-heimilisfang, t.d. „12. mgr.") þegar leitin skilar einni; sjá „Slökuð leit" hér að neðan. Fjöldinn er `role="status"`. Tómt ástand nefnir leitina og býður „Fjarlægja allar síur" / „Nota orðaleit"; villuástand býður „Reyna aftur" |
| `DocPanel` | Skjalabirting (dómar **og** bækur), þ.m.t. „Tengd mál" og „Tilvitnanir". `PdfViewer` er hlaðið lötu (`React.lazy`) — pdf.js er ~420 kB og aðeins PDF-sýnin þarf það |
| `DocHeader` | Klístraður haus lesarans; örin fer **til baka í sögunni** (leit, tilvitnun, lög), og á forsíðu aðeins ef skjalið var opnað beint |
| `DocOutline` / `DocOutlineCompact` | Efnisyfirlit: dálkur á `xl`, felliflipi („Efnisyfirlit (n)") undir `xl` |
| `CitationList` | Ein átt tilvitnana („Vitnar í" eða „Vitnað í þennan dóm") með „Sýna fleiri (n)" síðuflettingu |
| `LawPanel` | Lagabirting: brauðmolar, heiti án breytingamerkja (`lib/lawTitle.ts`), „Lög nr. X · Tók gildi [dags.]", greinayfirlit á `xl`, og á hverri grein hlekkurinn „Tilvísanir" → `/?provision=72. gr. laga nr. 33/1944` |
| `Breadcrumbs` | Brauðmolar (`nav aria-label="Brauðmolar"`), notaðir á kafla- og lagasíðum; núverandi síða er `h1`, ekki síðasti moli |
| `CatalogTree` / `SourceTree` | Heimildatré (Heimildir-síðan / ýtarleg leit á forsíðu) |
| `states.tsx` | Hleðslu- (`role="status"`), villu- (með „Reyna aftur") og tómleikaástand |
| `LandingView` | Forsíðan: einn reitur og „Leita", og „Ýtarleg leit" fellanleg (opin/lokuð geymt í `localStorage`). **Allt forsíðuformið er drög þar til það er sent** — áður skrifaði hver stilling sig í slóðina strax, svo að haka við heimild breytti síðunni í niðurstöður og henti leitarorðinu |

## Slökuð leit — tilkynning og merki

Bakendarök í [05-leit](05-leit.md); þetta er eingöngu birtingin. `types.ts` ber `SearchResponse.strict_total`/`.relaxed` og `SearchResult.match_tier`.

`ResultsList` sýnir eina tilkynningarlínu fyrir ofan listann þegar `relaxed` er satt:
- `strict_total > 0`: „**{strict_total}** skjöl innihalda öll leitarorðin. Sýni einnig **{total − strict_total}** skjöl sem innihalda flest eða sum þeirra."
- `strict_total == 0`: „Engin skjöl innihalda öll leitarorðin. Sýni skjöl sem innihalda flest eða sum þeirra."

`ResultCard` sýnir lítið merki við hlið `anchor`-merkisins þegar `match_tier` er 1 eða 2: „flest orðin" (þrep 1) eða „sum orðin" (þrep 2). Ekkert merki fyrir þrep 0 (og aldrei fyrir hina hamina, þar sem `match_tier` er alltaf 0).

## Tilvitnanir — „Tengd mál" og „Tilvitnanir" í `DocPanel`

Bakendagögnin eru í [02-gagnagrunnur](02-gagnagrunnur.md) (`citations`/`cites`) og [06-api](06-api.md) (`/api/document/{id}`-svæðin, `/api/document/{id}/citations`); þetta er eingöngu birtingin.

**„Tengd mál"** sýnir áfrýjunar-/málskotstengsl (`doc.appeal_links`, `relation <> 'cites'`) með íslenskum merkingum:

| `relation` | Merking |
|---|---|
| `appealed_to` | „Áfrýjað til" |
| `appealed_from` | „Áfrýjað frá" |
| `leyfisbeidni_um` | „Málskotsbeiðni um" |
| `leiddi_til_doms` | „Leiddi til dóms" |

Óþekkt `relation`-heiti birtist óbreytt (`RELATION_LABELS[l.relation] ?? l.relation`).

**„Tilvitnanir"** birtist þegar `citations_out_total > 0 || cited_by_total > 0 || citations_unresolved_total > 0`, með tveimur `CitationList`-listum:
- „Vitnar í (n)" — `doc.citations_out`.
- „Vitnað í þennan dóm (n)" — `doc.cited_by`.

Hver færsla er `urlausn` sem hlekkur á `/domur/{id}`, og undir henni `raw_text` í smáletri. Merki:
- `also_appeal` → „(í áfrýjunarkeðju)".
- `same_case` → „(sama mál)".

**Engin tvítekning:** skjal sem er bæði í `appeal_links` og í `citations_out`/`cited_by` með `also_appeal: true` er **aðeins** sýnt undir „Tilvitnanir" — `relatedLinks` í `DocPanel.tsx` síar það úr „Tengd mál" (`shownAsCitation`-mengið). Þannig lítur eitt raunverulegt tengsl ekki út eins og tvö.

„Sýna fleiri" (í `CitationList`) sækir næstu síðu með `GET /api/document/{id}/citations?direction=…&page=…` og bætir við listann; hnappurinn hverfur þegar `items.length >= total`.

Neðst, ef `citations_unresolved_total > 0`: „1 tilvitnun fannst ekki í safninu" eða „N tilvitnanir fundust ekki í safninu" — tilvitnanir sem fundust í texta en leystust ekki á neitt skjal í safninu (`unresolved`/`ambiguous`/`pre_coverage`), sagt berum orðum svo ófullkominn listi líti ekki út eins og heill.

**Leitarlisti:** `ResultCard` sýnir „vitnað í N sinnum" (eintölu „vitnað í 1 sinni") þegar `r.cited_by_count > 0`, ekkert þegar `0`.

## Langur lestur — `DocPanel`

Á við bækur **og ritgerðir** (og langa dóma). Ekkert af þessu er bundið við eina heimild — allt er þröskuldadrifið, svo ný heimild með löngum texta fær sömu meðferð sjálfkrafa.

### 1. Tveir aðskildir þröskuldar

```ts
LARGE_DOC_THRESHOLD  = 50_000   // löt birting  (frammistaða)
SEARCHABLE_THRESHOLD =  8_000   // leitarreitur (notagildi)
```

Þeir eru viljandi aðskildir: 22.000 stafa ritgerð er alltof löng til að renna augum yfir en hvergi nærri því að þurfa bútaða birtingu. Að binda hvort tveggja við sama þröskuld skildi 235 ritgerðir eftir án leitar.

| Lengd | Birting | Leitarreitur |
|---|---|---|
| < 8k | eitt `<ReactMarkdown>` | ✗ |
| 8k–50k | eitt `<ReactMarkdown>` | ✓ |
| > 50k | `LazyMarkdownSection` bútar | ✓ |

### 2. Löt birting (lazy rendering)

Án hennar tók 15+ sekúndur með auðri síðu að opna bók.

Yfir `LARGE_DOC_THRESHOLD`: `splitMarkdown()` klippir í ~500-orða búta á málsgreinaskilum (**engin skörun** — lesandi má aldrei sjá sama texta tvisvar), og hver bútur er `LazyMarkdownSection`. Þetta er hrein orðatalning án tillits til efnisgreinaskipulags — óskylt bakenda-`passages`-töflunni, sem er byggð fyrir tilvitnun/leit, ekki lata birtingu.

`LazyMarkdownSection` notar innbyggt `IntersectionObserver` (rootMargin 600px) + CSS `content-visibility: auto`. **Engin ný pakkaháð** — meðvitað val í stað virtualization-bókasafns.

`visible` er *afleitt* gildi (`intersected || forceVisible`), ekki sitt eigið state — þannig lendir raunverulegt efni í DOM-inu í sömu render-umferð og `forceVisible` flettist, sem leitin þarf.

### 3. Leit í skjali

Birtist fyrir skjöl yfir `SEARCHABLE_THRESHOLD`.

| Skrá | Hlutverk |
|---|---|
| `lib/matcher.ts` | Sameiginlegur „matcher"-smiður — venjuleg eða regex-leit á **einum stað** |
| `lib/findMatches.ts` | Finnur allar samsvaranir yfir búta — leitar í **hráum texta**, svo ófundnir/óbirtir bútar finnast líka |
| `lib/highlightMatches.ts` | Merking með innbyggða **CSS Custom Highlight API** (`CSS.highlights`) — engin DOM-breyting, engin árekstur við React |
| `lib/scrollToMatch.ts` | Skrollar á **nákvæma samsvörun**, ekki miðju bútsins |
| `components/DocSearchBar.tsx` | Reitur, „N af M" teljari, ↑/↓, Enter / Shift+Enter, `.*` regex-hnappur |

**Ein leið fyrir bæði tilvik:** fyrir lötu skjölin er leitað í bútunum (samsvörun getur leynst í óbirtum hluta); fyrir hin er allt skjalið einn „bútur", því það er hvort eð er allt í DOM-inu. Þannig er `findMatches`/`scrollToOccurrence` sami kóði í báðum tilvikum — aðeins gámurinn er annar (`#doc-segment-N` eða `bodyRef`).

Regex-hamur: `.*` hnappur (`aria-pressed`). Ógilt mynstur sýnir *„Ógilt regex mynstur"* í stað *„Engar niðurstöður"*.

Tvær villur sem fundust **aðeins við beina vafraprófun**, ekki í einingaprófum:
- Skrollað var á miðju heils búts — of ónákvæmt í skönnuðum bókum þar sem bútur getur spannað margar síður án málsgreinaskila
- `window.scrollBy` var notað, en síðan skrollar ekki gluggann heldur innri `overflow-y-auto` díf

### 4. Haus fyrir titla í stað málsnúmera

`case_number_is_title` (úr `SourceConfig`, birt í `/api/document`) stýrir hausnum:

| | Dómur/úrskurður | Ritgerð/bók |
|---|---|---|
| `case_number` | „Mál nr. 59/2025" | titillinn beint |
| `document_date` | full dagsetning | aðeins ártal |
| `defendants` | „gegn" + nöfn | sleppt (dálkurinn geymir höfunda, ekki gagnaðila) |

### 5. Markdown-mállýska — `Markdown.tsx`

Öll markdown-birting fer í gegnum einn hjúp, `components/Markdown.tsx`, sem virkjar **`remark-gfm`**. Án hans birtast tvennt rangt:

- **Töflur** — `pdf_parser._table_to_markdown()` býr þær til og ~2.461 skjal inniheldur eina; án GFM birtast þær sem hrátt `|---|` pípurusl
- **Neðanmálsgreinar** — `[^1]` tilvísanir og `[^1]:` skýringar

Merkimiðar eru staðfærðir (`footnoteLabel`, `footnoteBackLabel`) — sjálfgefið er enskt „Footnotes“.

### 6. Neðanmálsgreinar og lötu bútarnir

Hver lazy-bútur er þáttaður sem **sjálfstætt** markdown-skjal. `[^12]` tilvísun í bút 3 sem á skýringu í bút 40 verður því aldrei leyst og birtist sem bókstaflegur texti.

`splitMarkdown()` leysir þetta: skýringarnar eru dregnar út úr skjalinu og **festar aftur við þann bút sem vísar í þær**. Það gerir hvern bút sjálfbæran — og setur um leið hverja skýringu nálægt sínum texta, líkt og á prentaðri síðu.

Skýring sem enginn vísar í er sett í lista undir „Neðanmálsgreinar án tilvísunar“ — GFM hendir annars ónotaðri skýringu þegjandi, sem eyðir textanum.

### 7. Þegar enginn texti er til

Um þriðjungur ritgerða á Skemman er **læstur** hjá útgefanda (`locked: true`, stundum með `embargo_until`). Áður birtist auð síða og notandi gat ekki greint aðgangstakmörkun frá bilun. Nú birtist skýring með tengli á útgefanda.

⚠️ Merkið verður að vera `doc.body_text`, **ekki** birti textinn — API-ið býr alltaf til stutta lýsigagna-`markdown` (titil, dagsetningu, slóð), svo `content` er aldrei tómt.

## Próf

28 prófskrár, 156 próf, Vitest + Testing Library + MSW.

Reglur sem gilda í öllum prófum:
- Leita eftir hlutverki/texta (`getByRole`, `getByText`), **ekki** `data-testid`
- `renderWithProviders` fyrir íhluti sem þurfa Router/QueryClient
- `src/test/setup.ts` stubbar `IntersectionObserver` sem no-op og `scrollIntoView`. Próf sem þarf að kveikja á athuguninni setur sinn eigin staðbundna override og skilar hinum global aftur á eftir.
