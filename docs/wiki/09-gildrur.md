# 09 — Gildrur og staða

← [Wiki-forsíða](README.md)

Þekktar gildrur, ósamræmi og ólokið verk. Staðfest 28.07.2026.

## Gildrur í gögnum og kóða

### `case_number` er EKKI einkvæmt
Notaðu `.scalars().all()`, **ekki** `scalar_one_or_none()`, þegar þú spyrð eftir málsnúmeri. Sama málsnúmer kemur fyrir í mörgum dómstólum og árum.

### `fts_is` uppfærist ekki sjálfkrafa
Ólíkt `fts` (sem er `GENERATED ALWAYS`) er `fts_is` venjulegur dálkur sem krefst `backfill_fts_is.py`. Nýtt skjal án þess finnst **ekki** í venjulegri leit. Sjá [05-leit](05-leit.md).

### `passage_hash` er ekki trigger
Ólíkt `fts`/`fts_is` er `passages`-taflan aldrei uppfærð sjálfkrafa við skrift á `documents`. `documents.passage_hash` geymir `md5()` af textalögunum eins og þau voru við síðustu `passages`-smíð; NULL eða misræmi þýðir úreltar/vantandi efnisgreinar fyrir það skjal. `scripts/backfill_passages.py` (keyrt sjálfkrafa af `update_all.py` sem `passages_refresh`-þrepið) ber saman og endursmíðar aðeins það sem er úrelt. Nýtt skjal án þessa finnst **ekki** í `keyword`/`proximity` leit — sama gildruflokkur og `fts_is`.

### `documents.fts_is` og `passages` verða að vera endurbyggð saman
`rebuild_passages()` (kallað af `backfill_passages.py`) endurnýjar `documents.fts_is` úr sömu lemmum og `passages`-röðunum, í sömu færslu — það er eina leiðin sem `fts_is` uppfærist eftir að skjal er endurunnið. `backfill_fts_is.py` fyllir aðeins `NULL`-gildi og lagar því **ekki** skjal sem átti `fts_is` fyrir en fékk nýjan `body_text` seinna (t.d. eftir endur-extraction). Þangað til `rebuild_passages()` keyrir á því skjali er `passages` p rétt en forsían `d.fts_is @@ tsq` í efnisgreinaleitinni (sjá F3, [05-leit](05-leit.md)) getur útilokað skjalið úr `keyword`/`proximity`-leit þótt efnisgreinarnar sjálfar passi.

### Eftir fulla `backfill_passages.py --all` þarf viðhald á vísum
Full endurbygging eyðir og setur inn allar 1,78 M `passages`-raðir og endurskrifar hvert `documents.fts_is` — GIN-vísarnir blása út við það (`ix_passage_fts_is` 661 → 1164 MB, `ix_doc_fts_is` 333 → 441 MB, mælt 27.09.2026). Keyrðu strax á eftir:

```sql
VACUUM (ANALYZE, VERBOSE) passages;
VACUUM (ANALYZE, VERBOSE) documents;
REINDEX INDEX CONCURRENTLY ix_passage_fts_is;   -- 1164 → 522 MB, ~1:43
REINDEX INDEX CONCURRENTLY ix_doc_fts_is;       -- 441 → 228 MB, ~0:38
```

Annars hægist verulega á leitinni þangað til það er gert — bæði vísarnir tvöfaldast í lestri (diskurinn er á USB, svo kaldir lestrar ráða) og `ANALYZE` eitt og sér lagar það ekki. Athugaðu að `VACUUM ANALYZE`/`REINDEX` lagar **ekki** `plan_cache_mode`-gildruna; þau eru sjálfstæð vandamál.

### `plan_cache_mode`: almennt plan drepur efnisgreinaleitina
asyncpg keyrir allt um prepared statements, svo PostgreSQL skiptir hverri setningu yfir í **almennt plan** (generic plan) eftir 5. keyrslu á tengingu. Fyrir efnisgreinaleitina er það hörmung: `tsquery`-ið kemur úr bindibreytu (`plainto_tsquery('simple', $1)`), svo almennt plan hefur engar upplýsingar um valkvæmni — hvert þrep er áætlað á ~1 röð og áætlarinn velur að endurskanna `ix_passage_fts_is` GIN-vísinn **einu sinni fyrir hvert frambjóðendaskjal** (2000 lykkjur × ~550 þús. TID) í stað eins GIN-skanns hash-joinaðs við `cand`. Mælt á g017 (492-spurninga gullmengið, 27.09.2026): 25 ms í keyrslum 1–5, **9.745 ms** frá keyrslu 6. Sama fyrirspurn í `psql` með bókstöflum er alltaf hröð, svo gildran sést ekki nema hún sé keyrð oftar en 5× í sömu tengingu.

Vörnin er `plan_cache_mode = force_custom_plan`, sett í `connect_args` í `engine/database/connection.py`. Endurplönun kostar ~0,5–7 ms á setningu, hverfandi í samanburði. Ef sú stilling er fjarlægð (eða ný `create_async_engine`-kallstöð bætist við án hennar) kemur regressjónin aftur og lítur út eins og I/O-vandamál þótt hún sé 100% CPU (`pg_stat_activity.wait_event` er NULL í öllum sýnum).

### `validation_errors` geymir tvenns konar „ekkert"
19.554 raðir hafa JSON-gildið `null` (skalar) og 25.546 hafa raunverulegan fylkjalista. SQL sem gerir ráð fyrir fylki springur:

```sql
-- ✗ ERROR: cannot extract elements from a scalar
select jsonb_array_elements(validation_errors) from documents
where validation_errors is not null;

-- ✓ verður að verja sig
... where jsonb_typeof(validation_errors) = 'array'
```

### `keyword`-leit nær aðeins yfir efstu 2000 frambjóðendaskjölin
`passage_search.PASSAGE_CANDIDATE_DOCS = 2000` takmarkar hversu mörg skjöl Stig 1 (`ts_rank` á `documents.fts_is`) skilar áfram í efnisgreinaleitina. Fyrir venjulegar fyrirspurnir er þetta ósýnilegt — færri en 2000 skjöl passa hvort eð er. En fyrir mjög algeng stök orð sem passa í fleiri en 2000 skjöl nær síðufletting umfram það aldrei niðurstöðum, þótt fleiri skjöl séu til sem innihalda orðið. Sjá [05-leit](05-leit.md).

### Regex verður að vera á berum dálki
`coalesce(body_text, '')` eyðileggur trigram-vísinn og þvingar seq-scan yfir 1,7 GB. Skjalfest í `REGEX_COLUMNS` í `queries.py`.

### `<N>` í PostgreSQL FTS er NÁKVÆMLEGA N, ekki „innan N"
Þess vegna byggir `proximity`-hamurinn tvíátta sameiningu fyrir hvert bil 1..N. Sjá [05-leit](05-leit.md).

### `lower_body_text = None` er rétt fyrir Landsrétt
Landsréttardómar sem fella ekki inn texta héraðsdóms eiga að hafa NULL þarna — það er ekki villa.

### Landsréttar-PDF: leitað að `"Bold"`, ekki `"BoldMT"`
Til að ná bæði eldri (`Times New Roman,Bold`) og nýrri (`TimesNewRomanPS-BoldMT`) leturheitum. Skáletruð afbrigði eru útilokuð með `"Ital"` athugun í `_heading_marker()`.

### `_correct_date_if_wrong()` er ekki blanket-override
Það hnekkir API-dagsetningunni **aðeins** þegar texti skjalsins sjálfs sýnir aðra dagsetningu.

### Þriðjungur ritgerða hefur engan texta — og það er rétt
1.484 af 4.311 `logfraediritgerdir` hafa `body_text IS NULL`. Af þeim eru **1.480 læstar** (`raw_api_data->>'locked' = 'true'`) hjá Skemman, oft með `embargo_until` dagsetningu. Þetta er aðgangsstefna útgefandans, ekki bilun í innflutningi — ekki „laga" með endurinnflutningi.

Aðeins **4** eru ólæstar en textalausar; þær eru raunverulegar eyður:
`a889d477`, `8f01a48c`, `6c8bb094`, `05de5a01`.

Athugið að `locked` er geymt sem strengurinn `"true"`/`"false"` í `raw_api_data`, ekki JSON-boolean.

### Héraðsdómur birtir ekki alla dóma
~30% „no-candidate" hlutfall í `hrd_herd` tengingum er væntanlegt, ekki bilun.

### `npx tsc --noEmit` í `frontend/` athugar EKKERT
Rót-`tsconfig.json` er `{"files": [], "references": [...]}`. Án `-b` þýðir það **núll skrár**:

```bash
npx tsc --noEmit          # ✗ þögult, athugar 0 skrár — alltaf „hreint"
npx tsc -b                # ✓ raunveruleg athugun (það sem npm run build keyrir)
npx tsc -b --force        # ✓ hunsar tsbuildinfo skyndiminnið
```

Staðfest 28.07.2026 með `--listFiles`: `--noEmit` snerti enga skrá úr `src/`. Þetta faldi bæði fyrirliggjandi villu í `SourceTree.tsx` (sem braut `npm run build`) og rangar prófsniðmátsgerðir. **Notaðu alltaf `tsc -b`.**

### Ósniðmátuð prófsniðmát reka frá API-inu
`const doc = {...}` án `: DocumentDetail` þýðir að TypeScript ber það aldrei saman við raunverulegt svarform. Sniðmátið í `DocPanel.test.tsx` er nú sniðmátað viljandi — nýtt svæði í `DocumentDetail` brýtur þá prófin strax í stað þess að læðast fram hjá.

### Auður lestur er ekki alltaf tómt `content`
`content = doc.markdown ?? doc.body_text ?? ""`. API-ið býr til lýsigagna-`markdown` (titil, dagsetningu, slóð) jafnvel fyrir skjöl **án nokkurs texta**, svo `content` er aldrei tómt. Til að greina „enginn texti" verður að skoða `doc.body_text` beint. Þetta olli raunverulegri villu sem einingapróf náði ekki (sniðmátið hafði `markdown: null`, sem gerist aldrei í raun) — vafraprófun fann hana.

### JSX strengir vinna ekki `\n`
`text="## Titill\n\nMál"` í JSX gefur bókstaflegt bakstrik-n (char 92,110), ekki línuskil (char 10). Verður að vera `text={"## Titill\n\nMál"}`. Þetta olli röngum prófum sem voru „lagfærð" með því að slaka á fullyrðingum áður en rót fannst.

## Ósamræmi í skjölum

| Atriði | Vandi |
|---|---|
| `sources_catalogue.md` | Segir **~86.614 skjöl / 59 heimildir**. Raunin: **91.152 / 111**. Uppfært síðast júní 2026. |
| `CLAUDE.md` → `scripts/supervised.sh` | Skriptan er **ekki til á diski**. Dauð tilvísun í `## Running` kaflanum. |
| `CLAUDE.md:143` → `Renderer.rebuild_all()` | Fallið er **ekki til** neins staðar í kóðanum. Raunveruleg leið: `scripts/backfill_render_all.py`. |
| `CLAUDE.md:205` → `scripts/backfill_render.py` | Rangt skráarnafn — heitir `backfill_render_all.py`. |
| `CLAUDE.md:175,220` → `scripts/migrate_v1.py` | Ekki til lengur. |
| `engine/collectors/` | Mappan er til en **tóm**. Söfnunarrökfræði býr í `scripts/import_*.py`. (CLAUDE.md nefnir hana ekki.) |
| `CLAUDE.md` skema | Nefnir hvorki `case_type`, `provisions`, `cited_provisions`, `isbn`, `publisher`, `fts_is`, `verdict_filename` né `passages`/`document_links` töflurnar. |
| `CLAUDE.md` → `SourceConfig.pdf_path(external_id)` | Rangt fyrir heradsdomstolar/haestirettur/landsrettur/endurupptokudomur: `import_*.py` og `backfill_heradsdomstolar_detail.py` kalla alltaf `pdf_path(vf)` þar sem `vf` er `documents.verdict_filename` (t.d. `HerdRvk_E-4047-2018_D_27-11-2020.pdf`, ekki `g-9b7fbb27-....pdf`) — fallback á `external_id` bara ef `verdict_filename` er NULL. `engine/api/app.py:217` og `engine/search/queries.py:710` nota samt `external_id` beint fyrir `has_pdf`/`/api/document/{id}/pdf` hjá þessum heimildum, sem þýðir að sú leið finnur skrána sjaldnast — óskoðað hvort þetta er virkur galli í dag. Uppgötvað 27.09.2026 við `scripts/migrate_pdfstring_to_disk.py`. |

## Tvíteknar renderer-skrár — önnur er dauður kóði

Tvær skrár heita `renderer.py` og innihalda báðar RENDER-lags rökfræði:

| Skrá | Línur | Staða |
|---|---|---|
| `engine/processors/renderer.py` | 548 | ✅ **Í notkun** — flutt inn af ~20 skriptum og `api/app.py` |
| `engine/database/renderer.py` | 197 | ⚠️ **Dauður kóði** — engin skrá í verkefninu flytur hann inn |

Staðfest 28.07.2026 með `grep -rn "database.renderer"` yfir allan kóðabasann: núll niðurstöður. Sennilega leif frá endurskipulagningu. Athugaðu áður en þú eyðir, en hann tekur ekki þátt í neinni keyrslu í dag.

## Verkfæri sem voru metin og hafnað fyrir PDF-fótnótur

### `firecrawl/anydoc` — metið 11.08.2026
Rust-bókasafn, engin ML-módel, engir API-lyklar, **23× hraðara** en okkar þáttari (1,2 s á móti 28 s fyrir 682 síður). Prófað á 4 ritgerðum og 2 bókum.

**Skilar núll neðanmálsgreinum úr PDF** — í öllum sex skjölunum. Ástæðan er kerfisleg: anydoc styður neðanmálsgreinar fyrir **skrifstofuskjöl** (DOCX, ODT, EPUB) þar sem þær eru merkingarleg bygging í skránni. Í PDF eru þær eingöngu sjónrænar og það les þær sem venjulegan texta:

```
1Alþt. 2011-2012, A-deild, þskj. 328 – 290 mál. 2Samanber lög nr. 61/2012 ...
```

`anydoc --help` staðfestir að **engar PDF-stillingar eru til** — aðeins `-o`, `-f`, `-h`, `-V`. Ekkert til að stilla. Skjalfest takmörkun: skannaðar PDF-skrár þarfnast OCR sem það gerir ekki.

**Þar sem það er betra:** raunveruleg töflugreining (475 töflulínur í safnriti þar sem við skilum 0 — sumar ósviknar, aðrar ranglega greindar titilsíður) og hraði. **Vert að muna ef DOCX/EPUB-heimild bætist við** — þar væri það líklega besti kosturinn.

⚠️ Talning ein og sér var villandi við matið: fyrsta samanburðurinn sýndi anydoc með fleiri fyrirsagnir og töflur, en skoðun úttaksins leiddi í ljós að „töflurnar“ voru ranglega greint tveggja dálka umbrot með meginmáli klemmdu í reiti. **Skoðaðu úttakið, ekki bara tölurnar.**

## `pdfString` fjarlægt úr `raw_api_data` — 27.09.2026

Fjórar heimildir (heradsdomstolar, haestirettur, landsrettur, endurupptokudomur) geymdu PDF-skjalið **tvisvar**: einu sinni sem skrá á diski (`Lausnir_Data/raw/{short_name}/{verdict_filename}.pdf`, sjá athugasemdina í töflunni hér að ofan um `pdf_path`) og aftur sem base64-streng í `raw_api_data->>'pdfString'`. Það tvítekna afrit var ~56 GB af ~76 GB `documents`-töflunni (heradsdomstolar eitt og sér 53 GB / 24.282 skjöl).

`scripts/migrate_pdfstring_to_disk.py` fjarlægði tvítekninguna, en aðeins eftir að hafa sannreynt sha256 samsvörun milli JSON-strengsins og skráarinnar á diski fyrir hverja línu. Niðurstaðan er ein af:

- **Diskskrá vantaði** → skrifuð atomically úr JSON-bætunum fyrst, svo `pdfString` fjarlægt.
- **Diskskrá er til og er byte-eins** → `pdfString` fjarlægt.
- **Diskskrá er til en er ólík** → **ekkert breytt**, línan skráð og skilin eftir með `pdfString` óhreyft.

Í staðinn fyrir `pdfString` er nú:

```json
{"pdf_sha256": "<sha256 hex>", "pdf_path": "raw/heradsdomstolar/HerdRvk_E-4047-2018_D_27-11-2020.pdf"}
```

`pdf_path` er afstætt við `DATA_DIR` (ekki `RAW_DIR`), svo það er stöðugt óháð því hvar diskurinn er tengdur. `pdf_sha256` er sha256 af skránni sjálfri (staðfest jöfn JSON-bætunum fyrir flutning) og má nota til að greina spillingu í framtíðinni án þess að lesa allt skjalið aftur í JSON.

**Þetta var eina skiptið sem `raw_api_data` var breytt eftir að hafa verið skrifað** — RAW-lagið er annars ósnertanlegt (sjá CLAUDE.md). Breytingin var stýrð, sannreynd byte-fyrir-byte áður en nokkru var eytt úr JSON, og skráð hér til að hún sé ekki misskilin sem brot á RAW-reglunni síðar.

**Taflan minnkar ekki á diski af sjálfu sér.** `UPDATE ... raw_api_data = ...` skilur eftir „dead tuples" — Postgres endurnýtir það pláss innan `documents` en gefur það ekki aftur til stýrikerfisins. Til að taflan taki raunverulega minna pláss á diskinum þarf `VACUUM FULL documents;` (eða samsvarandi, t.d. `pg_repack`) — sem `migrate_pdfstring_to_disk.py` **keyrir ekki sjálft** (læsir töflunni og þarf laust pláss í kringum stærð eftirlifandi töflu, u.þ.b. 20 GB). Sú ákvörðun er hjá þeim sem stýrir keyrslunni, ekki hluti af þessari skriptu.

## Innviðir

### Engin gagnagrunnsafritun
Meðvituð ákvörðun (sjá [08-þróun](08-throun.md)) — endurheimt með endurinnflutningi.

### Import-loggar eru hverfulir
`/tmp/lausnir_update/*.log` hreinsast við endurræsingu vélar.

## Ólokið verk

### 1. PDF-útdráttur fyrir bækur
**Staða:** rannsókn kláruð, engin innleiðing.

Bækur eru tvenns konar og þurfa ólíkar pípur:
- **Native-text** (engin heilsíðumynd) — texti er réttur, vantar aðeins markdown-strúktúr → Docling í venjulegum ham
- **Skönnuð** (heilsíðumynd á hverri síðu) — textalagið er skemmt (rómverskar tölur, efnisyfirlit) → Docling `--force-ocr`

Greiningaraðferð: `page.images` þekja í pdfplumber, ~5 síður í sýni. Af 3 bókum í dag eru 2 native-text og 1 skönnuð.

Full úttekt: `docs/logfraedibaekur-pdf-extraction-investigation.md`.

### 2. ~~Bókahaus í `DocPanel.tsx`~~ — LOKIÐ 28.07.2026
Leyst með `case_number_is_title` svæðinu í `/api/document`. Á við bæði bækur og ritgerðir. Sjá [07-framendi](07-framendi.md).

### 3. Merkingarleit (embeddings)
`embedding vector(3072)` dálkurinn er til og `pgvector` uppsett, en 0 skjöl eru fyllt og engin vektorleit er útfærð. Kerfisvítt verk (öll 91.152 skjölin), ekki bókasértækt.

### 4. Smærri atriði úr READINESS_PLAYBOOK
- Ákveða hvort `mcp-postgres` MCP-þjónninn eigi að hafa read-only aðgang í stað fulls read/write
- Íhuga varanlegri geymslu import-logga

## Öryggisákvarðanir sem standa

Þrjár þriðju aðila OCR-greinar voru metnar og **hafnað**, óháð því hvaða útdráttartól verður fyrir valinu:

| Módel | Ástæða |
|---|---|
| `baidu/Unlimited-OCR` | Krefst NVIDIA CUDA (hardkóðað `.cuda()`), enginn GPU á þessari vél |
| `sabafallah/Unlimited-OCR-Universal` | Óstaðfestur einstaklingsútgefandi, krefst `trust_remote_code=True` (keyrsla á handahófskenndum kóða) |
| `AutomatosX/AX-Unlimited-OCR-3B-MoE-MLX-MXFP8` | Óstaðfestur útgefandi, mælir með óendurskoðuðum þýddum C++ keyrsluham frá sama aðila |
