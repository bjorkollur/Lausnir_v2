# Lestur á heilli bók — design spec

## Samhengi

Eftir að lögfræðibækur voru gerðar leitanlegar (`logfraedibaekur` heimild, sjá `docs/superpowers/specs/2026-07-23-logfraedibaekur-design.md`), kom í ljós að núverandi einstaklings-skjalasíða (`/domur/{id}` → `DocumentPage.tsx` → `DocPanel.tsx`) ræður ekki við bókalengdar texta. Staðfest með beinni vafraprófun: `Afbrot og refsiábyrgð. 1` (1.396.931 stafir í `markdown`) skilar auðri síðu í **15+ sekúndur** áður en efni birtist.

**Rótarástæða**: `DocPanel.tsx` gefur öllum markdown-strengnum í eina `<ReactMarkdown>` einingu. Vafrinn (og `remark`/`rehype` þáttarinn undir hettunni) verður að þátta og byggja DOM-tré fyrir allan textann samstundis við hleðslu, óháð því hversu mikið er sýnilegt á skjánum. Netsvarið sjálft (2,8 MB JSON) kemur hratt — flöskuhálsinn er CPU-bundin þáttun/teikning á aðalþræði, ekki netið.

„Finna bók" reyndist þegar virka fullkomlega: leit afmörkuð við `Lögfræðibækur`-flokkinn (núverandi `/api/search?scope=logfraedibaekur`) skilar réttum niðurstöðum með úrdráttum, og hver niðurstaða hlekkjar á `/domur/{id}`. `BokasafnPage.tsx` er í dag bara stubbur sem vísar notanda á aðalleitina — það er í lagi, engin breyting þörf þar.

## Ákvarðanir teknar í brainstorming

1. **Lesturform**: dreginn texti (markdown), ekki upprunalega PDF-ið. Notandi vill samfellda, leitanlega framsetningu sömu og restin af kerfinu notar — ekki PDF-viewer.
2. **Skipting**: sýndarskrun (virtualized) sem lítur út eins og samfellt skjal — ekki síðuskipting með áfram/til baka hnöppum.
3. **Tækni**: **innfædd** vafratækni (`content-visibility: auto` + `IntersectionObserver`), **ekki** nýtt npm-safn (t.d. `react-virtuoso`). Ástæða: ekkert sýndarskrunar-safn er þegar uppsett í verkefninu, og innfædd lausn forðast nýja ósjálfstæði auk þess að leysa vandamálið sem JS-sýndarskrunarsöfn eiga oft í basli með (breytileg hæð málsgreina í markdown-birtu efni).

## Arkitektúr

```
DocPanel.tsx
  │
  ├─ if markdown.length <= LARGE_DOC_THRESHOLD (50 000 stafir):
  │     <ReactMarkdown>{markdown}</ReactMarkdown>     ← ÓBREYTT, núverandi hegðun
  │
  └─ else (bók):
        splitMarkdown(markdown)                        ← ný hrein fall, sker í ~500 orða einingar
              │                                            á málsgreinamörkum, ENGIN skörun
              ▼
        segments: string[]
              │
              ▼
        segments.map(s => <LazyMarkdownSection text={s} />)
              │
              ▼
        Hver eining: IntersectionObserver frestar <ReactMarkdown>
        þáttun þar til nálægt sjónsviði; content-visibility:auto
        + contain-intrinsic-size fyrir vafra-layout/paint hagræðingu
```

### Af hverju ekki endurnýta `document_chunks`?

`document_chunks` (notað fyrir chunk-aware leit) er með **50 orða skörun** milli aðliggjandi chunka — nauðsynlegt fyrir FTS-samhengi/relevance en óæskilegt fyrir samfelldan lestur (notandi myndi sjá endurtekinn texta á milli "síðna"). `splitMarkdown()` er því sjálfstætt, einfalt fall með sömu málsgreina-mörk rökfræði en núll skörun — eingöngu til birtingar, engin tenging við leitarkerfið eða gagnagrunninn.

### Ný skjöl

- **Búa til** `frontend/src/lib/splitMarkdown.ts`:
  ```ts
  export function splitMarkdown(text: string, targetWords = 500): string[]
  ```
  Skiptir á tvöföldum nýlínum (málsgreinamörk), safnar málsgreinum þar til `targetWords` er náð, byrjar nýja einingu. Engin skörun. Skilar `[text]` ef `text` er stutt (< target). Skilar `[]` fyrir tóman streng.

- **Búa til** `frontend/src/components/LazyMarkdownSection.tsx`:
  ```tsx
  export function LazyMarkdownSection({ text }: { text: string }): JSX.Element
  ```
  Notar `useRef` + `IntersectionObserver` (með `rootMargin` svo næstu 1-2 einingar undir sjónsviði byrja þáttun áður en notandi skrunar alveg að þeim — engin sýnileg hökt). Áður en sýnilegt: birtir autt `<div>` með áætlaðri lágmarkshæð (`contain-intrinsic-size`, reiknað gróflega út frá orðafjölda). Eftir að orðið sýnilegt/nálægt: birtir `<ReactMarkdown>{text}</ReactMarkdown>` í `<section style={{ contentVisibility: "auto" }}>`.

### Breyting á núverandi skjali

- **Breyta** `frontend/src/components/DocPanel.tsx`: skipta út núverandi einu `<ReactMarkdown>` línu fyrir skilyrta rökfræði (`markdown.length > 50_000` þröskuldur) sem birtir annaðhvort óbreytta núverandi hegðun eða nýja `LazyMarkdownSection`-listann. Ekkert annað í skjalinu breytist.

## Áhættur og mörk

- **Núll áhætta fyrir núverandi ~89.000 skjöl**: öll dómsúrlausn/ritgerð sem er undir 50.000 stafa þröskuldinum (nánast öll, þar sem lengsta hefðbundna úrlausn er langt undir því) fer óbreytta leiðina — engin breyting á núverandi lestrarupplifun.
- **Bakendi óbreyttur**: `/api/document/{id}` skilar sama JSON og áður (allur markdown-textinn í einu). Skiptingin gerist eingöngu í framenda, eftir að gögnin eru komin. Ákveðið vísvitandi til að halda breytingunni lítilli — ef 2,8 MB JSON-svarið sjálft reynist vandamál síðar (hægt net, símaviðmót), er það sér verkefni.
- **`content-visibility: auto` + `IntersectionObserver` er staðlað, vel stutt** í öllum núverandi vöfrum (Chrome/Edge/Firefox/Safari) — engin fallback-þörf skilgreind fyrir þessa útfærslu.

## Út fyrir umfang

- Efnisyfirlit/kaflaflakk innan bókar — ekki beðið um, ekki hluti af þessari útfærslu.
- Leit *innan* opinnar bókar (Ctrl+F í vafra dugar fyrir sýnilegan hluta — virkar ekki yfir alla bókina vegna löttar hleðslu, þekkt takmörkun, ekki leyst hér).
- Niðurhal/prentun bókarinnar sem PDF — notandi getur nálgast upprunalega PDF-ið beint á diski ef þörf er.
