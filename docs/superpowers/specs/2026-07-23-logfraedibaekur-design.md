# Lögfræðibækur — dropfolder ingestion design

## Samhengi

Lausnir hefur nú þegar eina "non-verdict" heimild: `logfraediritgerdir` (lögfræðiritgerðir af Skemman), sem er full chunk-indexuð (4.311 skjöl, 159.489 `document_chunks` með `fts_is`). Notandi vill sambærilegt fyrir **lögfræðibækur** — en ólíkt öllum öðrum heimildum í kerfinu (sem eru scraper/API-skriptur), er þetta fyrsta heimildin sem byggir á **staðbundnu skráardroppi**: notandi setur PDF í möppu, kerfið vinnur það og gerir heildartexta leitanlegan.

Engin ytri API er til sem gefur titil/höfund/ár fyrir handahófskennda PDF-bók — það þarf að álykta þessi gögn úr sjálfu skjalinu.

## Ákvarðanir teknar í brainstorming

1. **Keyrsla**: handvirk skrifta (`uv run python scripts/import_baekur.py`), ekki bakgrunnsvöktun (watchdog/daemon). Notandi keyrir hana sjálfur eftir að hafa sett bók/bækur í möppuna.
2. **Metadata-uppfletting**: þriggja þrepa keðja —
   - ISBN-mynstur leitað á fyrstu 5 síðum → flett upp á OpenLibrary (opið JSON API, staðfest virkt)
   - Ef ekkert ISBN eða OpenLibrary skilar engu: titill úr skráarheiti, höfundur með regex á fyrstu 2 síðum
   - Ef regex finnur ekkert: senda fyrstu 2 síður á Claude API til að draga út titil/höfund
   - `leitir.is` (íslenska bókasafnskerfið) var íhugað sem uppspretta en er JS-undirstaðað (Primo) — frestað, ekki hluti af þessari útfærslu
3. **external_id**: hreinsað ISBN ef fannst, annars skráarheiti-slug (notandi valdi einfaldleika fram yfir SHA256-hash, þrátt fyrir smávægilega árekstrarhættu ef tvær skrár heita eins)
4. **Eftir vinnslu**: upprunalega PDF-ið er flutt í `Lausnir_Data/raw/logfraedibaekur/{external_id}.pdf` — sama mynstur og allar aðrar heimildir á RAW-laginu. Dropfolder tæmist við hverja keyrslu.

## Arkitektúr

Fylgir nákvæmlega núverandi mynstri kerfisins — ekkert nýtt undirkerfi, bara ein ný heimild:

```
Lausnir_Data/dropfolder/*.pdf
        │
        ▼
scripts/import_baekur.py
        │
        ├─ 1. Metadata-keðja (ISBN → OpenLibrary → skráarheiti+regex → Claude API)
        ├─ 2. Textaútdráttur: parse_pdf() → docling_ocr_pdf() fallback (ef tómt)
        ├─ 3. chunk_document() → document_chunks (fts_is, sama og logfraediritgerdir)
        ├─ 4. write_markdown() (Renderer, sama og aðrar heimildir)
        └─ 5. Flytja PDF í Lausnir_Data/raw/logfraedibaekur/{external_id}.pdf
```

### Ný `SourceConfig` (`engine/config/sources.py`)

```python
SourceConfig(
    short_name="logfraedibaekur",
    display_name="Lögfræðibækur",
    abbreviation="Bók.",
    instance_tier=1,            # á ekki við bækur — sama og logfraediritgerdir
    has_lower_court=False,
    parse_parties="none",       # höfundur → plaintiffs[0].name handvirkt í import
    verdict_type_default="Bók",
    verdict_types_allowed=["Bók"],
    case_number_prefix="",      # titill fer í case_number
    pdf_crop=None,
    case_number_is_title=True,  # titill, ekki málsnúmer — sama mynstur og logfraediritgerdir
)
```

### Breytingar á núverandi kerfi

- `engine/config/source_groups.py`: bæta `"logfraedibaekur"` við `_BAEKUR_SOURCES` (þegar til, inniheldur bara `logfraediritgerdir`)
- `engine/search/queries.py`: bæta `"logfraedibaekur"` við `CHUNKED_SCOPE_KEYS`
- Engin breyting þarf á leitarlógík, renderer, eða validator — allt endurnýtt eins og er

### Metadata-keðja í smáatriðum

```
1. Lesa fyrstu 5 síður PDF (parse_pdf eða docling ef þarf)
2. Regex: r'(?:ISBN[:\s-]*)?(97[89][-\s]?\d[\d-]{10,16}\d|\d{9}[\dXx])'
   → hreinsa bandstrik, athuga lengd (10 eða 13 tölustafir)
3. Ef ISBN fannst:
     GET https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data
     → ef svar inniheldur "ISBN:{isbn}" lykil: nota title, authors[0].name, publish_date
     → annars: fall í gegn að skrefi 4
4. title = slugify(skráarheiti án endingar)
   Regex á fyrstu 2 síðum fyrir höfund: "eftir <Nafn>", "Höfundur:? <Nafn>",
   eða stakur stór stafur fremst á eigin línu sem líkist mannsnafni
5. Ef höfundur fannst ekki með regex:
     Senda texta fyrstu 2 síðna á Claude API (Haiku, ódýrt) með einfaldri
     bón um JSON {"title": ..., "author": ...}
6. external_id = ISBN (hreinsað) ef fannst úr skrefi 2/3, annars slugify(skráarheiti)
```

### Textaútdráttur og OCR

Endurnýtir núverandi `parse_pdf()` → `docling_ocr_pdf()` fallback-mynstur (notað af `enf`, `landsdomar`, `fjarskiptastofa` í dag): ef `parse_pdf()` skilar tómum texta, keyra OCR.

**Þekkt áhættuatriði:** `docling_ocr_pdf()` er með fast 300 sekúndna `timeout` í `engine/processors/pdf_parser.py`, stillt fyrir stakar úrskurðir (fáar síður). Heil bók með hundruðum síðna gæti farið fram úr þessu. Útfærsluplanið þarf annaðhvort að:
- hækka timeout sérstaklega fyrir `logfraedibaekur` (t.d. miða við síðufjölda × N sekúndur), eða
- keyra OCR í síðu-blokkum og sameina úttak

Þetta er ákvörðun sem tekin verður í útfærsluplaninu, ekki hér.

### Chunking og leit

Endurnýtir `chunk_document()` óbreytt — sama 500 orða/50 orða skörun og `logfraediritgerdir` notar. Ekkert nýtt hér.

## Út fyrir umfang (ekki hluti af þessari útfærslu)

- `leitir.is` ISBN-uppfletting (frestað — JS-undirstaðað Primo-kerfi, þarf frekari rannsókn á innra API)
- Sjálfvirk möppuvöktun (watchdog/launchd) — handvirk keyrsla dugar
- SHA256-undirstaðuð auðkenni — notandi valdi einfaldara skráarheiti-slug
