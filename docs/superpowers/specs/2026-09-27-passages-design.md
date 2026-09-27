# Efnisgreinar (`passages`) sem leitareining — design spec

**Dagsetning:** 2026-09-27
**Staða:** Samþykkt hönnun, tilbúin í implementation-plan.
**Bakgrunnur:** `docs/2026-09-27-mat-a-adferdafraedi-og-llm-leit.md`, kaflar 2, 5 og 7.

## Samhengi

Öll orðaleit í dag (nema fyrir bækur og ritgerðir) miðar við heilt skjal: `documents.fts_is` yfir allt meginmál, `ts_rank` yfir allt meginmál, `ts_headline` yfir allt meginmál. Miðgildisdómur er ~10.000 stafir, tíundi hver yfir 47.000 og hundraðasti hver yfir 234.000. Afleiðingarnar eru lengdarskekkt röðun, ónothæfir útdrættir og engin leið til að vitna í málsgrein, sem er hvernig íslenskir lögfræðingar vitna í dóma.

Uppbygging skjalanna er þegar til í `body_text`: Landsréttardómar eru nánast allir með `##`-fyrirsagnir og númeraðar málsgreinar, héraðsdómar með fyrirsagnir í 97 % tilvika, Hæstiréttur með númeraðar málsgreinar í um helmingi nýrri dóma. Umboðsmaður, yfirskattanefnd og úrskurðarnefnd umhverfis- og auðlindamála eru hins vegar 100 % flatur texti án málsgreinaskila. Dómsmálsgreinar eru stuttar (miðgildi 10–14 orð, 90. hundraðshluti ~50 orð).

`document_chunks` (fastir 500 orða bútar með skörun, aðeins fyrir 3.068 bækur og ritgerðir) leysir þetta að hluta fyrir eina tegund heimilda og með sérreglu (`_scope_is_chunked` krefst `all()`) sem lætur blandað svið detta þögult í verri leiðina.

## Ákvarðanir teknar í brainstorming

1. **Efnisgreinar eru aðalvísir orðaleitar** (`keyword`, `proximity`) fyrir öll svið. Skjalið heldur `fts_is` fyrir facets, `keyword`-síuna, regex-hamina fimm og sem forsíu. `document_chunks` fellur.
2. **Stærð:** markmið 250 orð, hámark 400. Númeruð málsgrein er aldrei klofin nema hún ein fari yfir hámarkið.
3. **Engin skörun.** Samhengi er sótt með nágrönnum (`ordinal ± 1`), ekki með tvítöldum texta.
4. **Alembic tekið í notkun:** grunnlína núna, `passages` fyrsta alvöru migration. Lokar opnu TODO úr `READINESS_PLAYBOOK.md`.
5. **Gullsett fyrst.** 50 spurningar samdar úr reifunum Hæstaréttar og Landsréttar, yfirfarnar af notanda. Bæði gamla og nýja leiðin mældar á sama setti áður en skipt er.
6. **Ekki í þessu spec:** embeddings og hybrid-leit, tilvitnanir dóms í dóm, MCP-þjónn, `pdfString`-flutningur. Hvert fær sitt spec. `passages` er hönnuð svo `embedding halfvec(N)` bætist við sem einn dálkur síðar.

## Gagnalíkan

### Ný tafla `passages`

| Dálkur | Tegund | Hlutverk |
|---|---|---|
| `id` | uuid PK | |
| `document_id` | uuid FK → `documents.id` ON DELETE CASCADE, NOT NULL | |
| `ordinal` | integer NOT NULL | röð innan skjals, 0-byggð, samfelld yfir bæði lög |
| `layer` | text NOT NULL | `'summary'`, `'body'` eða `'lower_body'` |
| `section_path` | text NULL | texti næstu `##`-fyrirsagnar fyrir ofan, án `#`-merkja og lokatvípunkts |
| `section_kind` | text NOT NULL | `reifun` / `malsmedferd` / `malsatvik` / `malsastaedur` / `nidurstada` / `domsord` / `annad` |
| `para_from` | smallint NULL | fyrsta númeraða málsgrein í bútnum |
| `para_to` | smallint NULL | síðasta númeraða málsgrein í bútnum |
| `char_start` | integer NOT NULL | hnit í upprunadálknum (`summary`, `body_text` eða `lower_body_text` eftir `layer`) |
| `char_end` | integer NOT NULL | `source[char_start:char_end] == text` gildir alltaf |
| `text` | text NOT NULL | |
| `word_count` | smallint NOT NULL | |
| `fts_is` | tsvector NOT NULL | `to_tsvector('simple', lemmatize_text(text))` |

Skorður og vísar:
- `UNIQUE (document_id, ordinal)` — `uq_passage_doc_ordinal`
- `ix_passage_doc` btree `(document_id, ordinal)`
- `ix_passage_fts_is` GIN `(fts_is)`
- `ix_passage_section_kind` btree `(section_kind)` — fyrir `section_kind`-síuna

Enginn `embedding`-dálkur strax. Hann bætist við sem `halfvec(N)` með HNSW þegar módel hefur verið valið á gullsettinu (sérstakt spec).

### Nýr dálkur á `documents`

`passage_hash text NULL` — `md5(coalesce(summary,'') || E'\x1f' || coalesce(body_text,'') || E'\x1f' || coalesce(lower_body_text,''))` eins og það var þegar efnisgreinarnar voru byggðar. Skjal er **úrelt** þegar `passage_hash IS DISTINCT FROM md5(...)`. Skjal án texta fær samt hash (engar efnisgreinar, en ekki úrelt).

### Fellt

`document_chunks` taflan og `DocumentChunk` ORM-klasinn, í sérstakri migration eftir að skiptin hafa verið staðfest með gullsettinu.

### Tilvitnunarmerkið er reiknað, ekki geymt

`anchor` í API-svari er reiknað úr dálkunum með einu falli, `passage_anchor(para_from, para_to, section_path, ordinal)`:

| Aðstæður | `anchor` |
|---|---|
| `layer == 'summary'` | `"Reifun"` |
| `para_from == para_to` | `"4. mgr."` |
| `para_from < para_to` | `"4.–7. mgr."` |
| engar málsgreinar, `section_path` til | `"Niðurstaða"` |
| hvorugt | `"hluti 12"` (ordinal + 1) |

Þetta er RENDER-lag: alltaf afleiðanlegt úr NORM-dálkunum, aldrei geymt.

## Bútarinn — `engine/processors/segmenter.py`

Kemur í stað `engine/processors/chunker.py`. Hreint fall, engin DB, engin lemmun.

```python
@dataclass(frozen=True)
class Passage:
    ordinal: int          # sett af kallanda þegar lögin tvö eru sameinuð
    section_path: str | None
    section_kind: str
    para_from: int | None
    para_to: int | None
    char_start: int
    char_end: int
    text: str
    word_count: int

def segment(text: str, *, target_words: int = 250, max_words: int = 400) -> list[Passage]
```

### Reglur, í þessari röð

1. **Blokkir.** Textinn er skipt á auðum línum (`\n[ \t\r]*\n+`, svo `\r\n` og línur með bilum teljast líka skil) í blokkir. Hnit hverrar blokkar í upprunatextanum eru varðveitt (aldrei unnið með `strip()`-uðum afritum án þess að leiðrétta hnit). Hver blokk er flokkuð:
   - *fyrirsögn*: `^#{1,6}\s+`
   - *númeruð málsgrein*: `^\d{1,3}\.\s+[A-ZÁÉÍÓÚÝÞÆÖ]` (sama regla og `_NEW_PARA` í `pdf_parser.py`; útilokar ártöl og dagsetningar)
   - *annað*
2. **Fyrirsögn** lokar bútnum sem er í gangi (ef hann er ekki tómur), setur `section_path` og `section_kind` fyrir það sem á eftir kemur, og er sjálf **fremst í næsta bút** svo tilvitnunin sýni fyrirsögnina með textanum.
3. **Söfnun.** Blokkir bætast í bút þar til `word_count >= target_words`; þá er bútnum lokað. Blokk sem myndi færa bútinn yfir `max_words` lokar bútnum á undan sér fyrst, nema búturinn sé tómur.
4. **Yfirstærð.** Ein blokk yfir `max_words` (reglan hjá umboðsmanni og yfirskattanefnd, þar sem allt skjalið er ein blokk) er klofin á setningaskilum (`(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÝÞÆÖ„"])`) í hluta sem hver er undir `max_words`. Setning sem ein er yfir `max_words` er klofin á orðabili. Allir hlutar erfa `section_path`, `section_kind` og málsgreinabil blokkarinnar.
5. **Málsgreinabil.** `para_from`/`para_to` eru lægsta og hæsta númer númeraðra málsgreina í bútnum. Bútur án númeraðrar málsgreinar hefur `NULL` í báðum.
6. **Engin skörun.** Bútar eru samliggjandi og ná ekki yfir hvor annan. Bil milli búta (`\n\n`) tilheyrir engum bút.
7. **Stutt skjal.** Sömu reglur gilda. Skjal undir `target_words` án fyrirsagna er einn bútur; með fyrirsögnum verða bútarnir jafnmargir og kaflarnir, þótt þeir séu stuttir. Það er viljandi: „Úrskurðarorð“ sem eigin 20 orða bútur er rétta tilvitnunareiningin.
8. **Tómt.** Tómur eða hvítur texti skilar `[]`.

### `section_kind`-flokkun

Fall `classify_section(heading: str | None) -> str`, byggt á fyrirsögninni einni:

| `section_kind` | Mynstur (hástafaóháð, þolir bil milli stafa og ð/d-rugling eins og renderer) |
|---|---|
| `domsord` | Dómsorð, Úrskurðarorð, Ályktunarorð, Ályktarorð — endurnýtir `_VERDICT_SECTION_PATTERNS` úr `renderer.py` |
| `nidurstada` | Niðurstaða, Niðurstöður, Forsendur og niðurstaða, Álit (umboðsmaður) |
| `malsatvik` | Málsatvik, Málavextir, Atvik máls |
| `malsastaedur` | Málsástæður, Lagarök, Röksemdir aðila, Sjónarmið (kæranda/aðila) |
| `malsmedferd` | Málsmeðferð, Dómkröfur, Kröfur aðila, Kæruefni, Kæra |
| `annad` | allt annað, og `None` |

Rómversk töluforskeyti („IV. Niðurstaða“) er strípað áður en borið er saman. Í nefndarúrskurðum er „Niðurstaða“ hin eiginlega niðurstaða en í dómum rökstuðningskafli á undan Dómsorði; báðir fá `nidurstada`, greinarmunurinn er birtingaratriði sem renderer sér um áfram.

Mynstrin sem `renderer.py` á fyrir Dómsorð og Úrskurðarorð eru flutt í `engine/processors/sections.py` og flutt inn þaðan af bæði renderer og segmenter, svo flokkunin sé á einum stað.

### Þrjú lög

Kallandinn (`passage_index.rebuild_passages`) byggir efnisgreinar í þessari röð og lætur `ordinal` halda áfram yfir lögin:

1. `summary` → ein efnisgrein, `layer='summary'`, `section_kind='reifun'`, ordinal 0 (sleppt ef reifun vantar). Reifun yfir `max_words` fer samt gegnum `segment()` og verður að fleiri bútum. Ástæðan er að `documents.fts_is` inniheldur reifunina en `passages` annars ekki; reifunartreff mega ekki glatast við skiptin, og reifunin er þar að auki rétta einingin fyrir „headnote“-embedding síðar.
2. `body_text` → `segment()`, `layer='body'`.
3. `lower_body_text` → `segment()`, `layer='lower_body'`.

Hnit vísa alltaf í dálkinn sem `layer` segir til um.

Lagasafn (`lagasafn_*`, 915 skjöl) fær efnisgreinar eins og öll önnur skjöl: `summary` (heiti laganna) verður `reifun`-efnisgrein og `body_text` (`## N. gr.`-fyrirsagnir) er segmenterað eins og hvert annað skipulagt skjal. **(Breytt 2026-09-27 eftir lokayfirferð: að undanskilja lagasafn skildi lögin eftir talin með í `documents.fts_is` en óaðgengileg í orðaleit — `search_documents(scope=["lagasafn"])` skilaði `total` en engum röðum. Sjá F1 í fixwave-skýrslunni.)** Ákvæðið er eftir sem áður eiginlega einingin fyrir `/api/provision`; efnisgreinar bæta orðaleit ofan á það, þær breyta ekki því flæði.

### Undanskilið

Ekkert skjal er undanskilið `passages`-smíð lengur (sjá breytinguna hér að ofan).

## Leit — `engine/search/queries.py`

### Efnisgreinaleiðin

`keyword` og `proximity` fara um `passages` fyrir öll svið. Form fyrirspurnarinnar:

```sql
WITH q AS (SELECT plainto_tsquery('simple', :lemmas) AS tsq),   -- to_tsquery fyrir proximity
hits AS (
    SELECT p.document_id,
           max(ts_rank(p.fts_is, q.tsq))                                   AS best_rank,
           count(*)                                                         AS match_count,
           (array_agg(p.id ORDER BY ts_rank(p.fts_is, q.tsq) DESC, p.ordinal))[1] AS best_passage_id
    FROM passages p
    JOIN documents d ON d.id = p.document_id, q
    WHERE d.fts_is @@ q.tsq              -- forsía: skjalavísirinn
      AND p.fts_is @@ q.tsq
      AND <svið / dagsetningar / provision / keyword / section_kind>
    GROUP BY p.document_id
)
SELECT count(*) FROM hits;                                       -- total
SELECT ... FROM hits JOIN documents d ... JOIN passages bp ON bp.id = hits.best_passage_id
ORDER BY best_rank DESC, match_count DESC, d.document_date DESC NULLS LAST, d.id
LIMIT :limit OFFSET :offset;
```

- Forsían `d.fts_is @@ tsq` er örugg því texti efnisgreinar er hlutmengi þess sem `fts_is` skjalsins var byggt úr (reifunin er líka efnisgrein, sjá „Reifun“ hér að neðan).
- Röðun `relevance`: `best_rank DESC, match_count DESC, document_date DESC`. `newest`/`oldest`: dagsetning, `best_rank` sem jafnteflisregla.
- Útdráttur: `ts_headline('simple', bp.text, tsq, 'MaxFragments=1, MaxWords=35, MinWords=12, ShortWord=2, StartSel=<mark>, StopSel=</mark>')` yfir bestu efnisgreinina eina.
- Ný valfrjáls sía `section_kind` (endurtekið viðfang, t.d. `section_kind=nidurstada&section_kind=domsord`) þrengir `p.section_kind IN (...)`.
- `ts_rank` á móti `ts_rank_cd`: ákveðið á gullsettinu, ekki fyrirfram. Útfærslan tekur röðunarsegðina sem einn fasta svo skiptin séu ein lína.

### Reifun

Reifunin er efnisgrein með `layer='summary'` (sjá „Þrjú lög“). Forsían `d.fts_is @@ tsq` og efnisgreinavísirinn ná því yfir sama texta og ekkert treff sem gamla leiðin fann glatast.

### Óbreytt

Regex-hamirnir (`exact`, `prefix`, `substring`, `any`, `regex`), `facet_counts`, `keyword`-sían og `provision`-sían eru óbreytt á `documents`. `_order_clause`-fallið sem fellur `relevance` í `newest` fyrir regex-hami stendur.

### Fellt

`_search_by_chunks`, `_scope_is_chunked`, `CHUNKED_SCOPE_KEYS`, `chunker.py`, `scripts/backfill_chunks.py`, `tests/test_chunker.py`, `tests/test_models_chunk.py`.

### Skiptiflagg

Umhverfisbreytan `LAUSNIR_SEARCH_IMPL` með gildin `documents` (sjálfgefið þar til skipt er) og `passages` velur leiðina fyrir `keyword`/`proximity`. `search_documents` les hana einu sinni við innflutning einingarinnar og `eval_search.py` getur yfirskrifað með viðfangi. Flaggið og gamla leiðin eru fjarlægð í lokaskrefi útrúllunar.

## API — `engine/api/app.py`

### `GET /api/search` (viðbætur, afturvirkt samhæft)

Ný viðföng: `section_kind` (endurtekið; leyfð gildi `reifun, malsmedferd, malsatvik, malsastaedur, nidurstada, domsord, annad`; annað → HTTP 400).

Ný svið í hverri niðurstöðu þegar efnisgreinaleiðin er virk (annars `null`):

```json
{
  "passage_id": "uuid",
  "anchor": "4.–7. mgr.",
  "section_kind": "nidurstada",
  "layer": "body",
  "match_count": 3
}
```

`snippet` kemur úr bestu efnisgrein.

### Nýr `GET /api/document/{id}/passages`

| Viðfang | Sjálfgefið | |
|---|---|---|
| `from` | 0 | fyrsti `ordinal` |
| `to` | `from + 49` | síðasti `ordinal`, að hámarki 200 í einu |
| `section_kind` | — | endurtekið, sía |
| `layer` | — | `summary` / `body` / `lower_body` |

Svar:

```json
{
  "document_id": "uuid",
  "urlausn": "Lrd. 177/2024 6. mars 2024 – Dómur",
  "total": 41,
  "passages": [
    {"id": "uuid", "ordinal": 12, "layer": "body", "anchor": "4.–7. mgr.",
     "section_path": "Niðurstaða", "section_kind": "nidurstada",
     "para_from": 4, "para_to": 7, "char_start": 5120, "char_end": 6890,
     "word_count": 261, "text": "..."}
  ]
}
```

404 ef skjalið finnst ekki. Skjal með engar efnisgreinar skilar `total: 0`.

### Framendi

Ein breyting: `ResultCard` sýnir `anchor` sem lítið merki við hlið útdráttarins þegar það er til staðar. `types.ts` fær nýju sviðin. Djúptenging á efnisgrein í `DocPanel` er utan þessa spec.

## Pípa

### `engine/search/passage_index.py`

```python
async def rebuild_passages(conn, doc_id: uuid.UUID) -> int:
    """Eyðir og endurbyggir efnisgreinar eins skjals í sömu færslu.
    Setur documents.passage_hash og endurnýjar documents.fts_is úr sömu lemmum.
    Skilar fjölda efnisgreina. Lagasafn fær efnisgreinar eins og önnur skjöl."""

def stale_documents_sql(source: str | None) -> tuple[str, dict]:
    """WHERE passage_hash IS DISTINCT FROM md5(...) [AND s.short_name = :sn]"""
```

Lemmun (`lemmatize_text`) er örgjörvabundin og keyrð í Python; fallið tekur valfrjálst þegar lemmaða texta svo bakfyllingarskriptan geti lemmað í vinnsluferlum og skrifað í aðalferli.

### `scripts/backfill_passages.py`

- Sjálfgefið: aðeins úrelt skjöl (`stale_documents_sql`). `--all` þvingar endurbyggingu.
- `--source X`, `--limit N`, `--workers N` (sjálfgefið `os.cpu_count() - 1`).
- Vinnsluferli fá `(doc_id, body_text, lower_body_text, summary)`, skila lista af efnisgreinum með lemmum; aðalferlið skrifar í lotum af 50 skjölum og commitar.
- Framvinda á sama sniði og `backfill_fts_is.py` (`[n/total] % — docs/s — ETA`).
- Í lokin: þekjutafla per heimild (skjöl með texta, skjöl með efnisgreinar, fjöldi efnisgreina, miðgildi orða).

### Samþætting

- `scripts/update_all.py`: nýtt skref strax á eftir `backfill_fts_is.py`: `backfill_passages.py` (sjálfgefið, aðeins úrelt). Ódýrt þegar ekkert er nýtt.
- `scripts/import_baekur.py`: kallar `backfill_passages.py --source logfraedibaekur` í stað chunk-áminningarinnar.
- Engin breyting á öðrum `import_*.py`; þau treysta á `update_all.py` eins og fyrir `fts_is`.

### Alembic

`alembic/versions/` er tóm þótt `env.py` og `alembic.ini` séu til. Þrjár migrations:

1. `0001_baseline` — `alembic revision --autogenerate` gegn lifandi grunni, handyfirfarin svo hún endurspegli raunverulegt skema (þ.m.t. trigram-vísana og `ix_doc_cited_provisions` sem `models.py` skilgreinir ekki; þeir eru skráðir sem `op.execute` með `IF NOT EXISTS`). Grunnurinn er `alembic stamp`-aður á hana, ekki keyrður.
2. `0002_passages` — `passages` taflan, vísar, `documents.passage_hash`. `downgrade` fellir hvort tveggja.
3. `0003_drop_document_chunks` — fellir töfluna. `downgrade` býr hana til aftur tóma. Keyrð fyrst eftir að gullsettið hefur staðfest efnisgreinaleiðina.

`env.py` er óbreytt (les `DATABASE_URL`, skiptir asyncpg út fyrir psycopg2; `psycopg2-binary` er þegar í `pyproject.toml`).

## Gullsett og mæling

### `tests/golden/queries.yaml`

```yaml
- id: g001
  question: "Endurupptaka bótaákvörðunar vegna líkamstjóns sjómanns með fyrirvara við uppgjör"
  expected:
    - {source: haestirettur, case_number: "36/2022", document_date: 2023-02-08}
    - {source: landsrettur, case_number: "…", document_date: …}   # úr áfrýjunarkeðju
  scope: [domstolar]
  note: "Úr reifun Hrd. 36/2022"
```

50 færslur, samdar úr reifunum Hæstaréttar og Landsréttar (`summary` ≥ 300 stafir), dreift á ár 2010–2026 og ólík lykilorð, með **spurninguna orðaða öðruvísi en reifunin** (samheiti, önnur beygingarmynd, án málsnúmera) svo settið mæli leit en ekki afritun. Rétt svör: dómurinn sjálfur auk dóma sem tengjast honum í `document_links` (`appealed_to`/`appealed_from`). Notandi yfirfer skrána og strikar út eða lagar.

### `scripts/eval_search.py`

- `--impl documents|passages|both` (sjálfgefið `both`), `--k 10`, `--golden tests/golden/queries.yaml`.
- Keyrir hverja spurningu gegnum `search_documents(q=question, mode="keyword", scope=scope, page_size=k)`.
- Reiknar per spurningu: fannst væntanlegt skjal í efstu k (hit@k), staða besta rétta svars (fyrir MRR), svartími.
- Prentar töflu: `impl | recall@10 | MRR | hit@1 | p50 ms | p95 ms | 0-treff`, og lista yfir spurningar þar sem leiðirnar tvær eru ósammála (önnur finnur, hin ekki) svo hægt sé að skoða þær í höndunum.
- Prentar þekju: `skjöl með texta`, `þar af með efnisgreinar`, hlutfall.

Sömu tölur ráða síðan vali á `ts_rank`/`ts_rank_cd`, vægi `match_count` og öllum röðunarbreytingum eftir þetta.

## Villumeðferð

| Aðstæður | Hegðun |
|---|---|
| Skjal án texta og án reifunar | 0 efnisgreinar, `passage_hash` sett; ekki úrelt |
| `segment()` kastar villu á skjali | villa skráð með `doc_id`, engin skrift, `passage_hash` óbreytt (skjalið er áfram úrelt og reynt aftur næst) |
| Hnitapróf bregst (`source[start:end] != text`) | sama og villa: skjalinu sleppt og skráð; þetta er ósamkvæmni í bútaranum, ekki gögnunum |
| `section_kind` utan leyfðra gilda í API | HTTP 400 |
| Ógilt `from`/`to` (`to < from`, bil > 200) | HTTP 400 |
| Efnisgreinaleið virk en skjal án efnisgreina | finnst ekki í orðaleit; `eval_search.py` sýnir þekjuna svo það sé ekki þögult |

## Próf

- `tests/test_segmenter.py`: númeraðar málsgreinar með fyrirsögnum (Landsréttarsnið), fyrirsagnir án númera (héraðsdómssnið), flatur texti yfir 400 orðum (yfirskattanefndarsnið), ein málsgrein yfir hámarki, stutt skjal, tómt skjal, ártal/dagsetning sem ekki telst málsgreinanúmer, fyrirsögn fremst í bút, `para_from/para_to`, **hnitaheild** (`source[start:end] == text`) fyrir hvern bút í öllum tilvikum, engin skörun og engin eyða nema hvítt bil.
- `tests/test_sections.py`: `classify_section` fyrir öll sex kind, rómverskt forskeyti, bil milli stafa, `None`.
- `tests/test_passage_index.py`: `rebuild_passages` er idempotent, setur hash, þrjú lög í réttri ordinal-röð, lagasafn fær efnisgreinar eins og önnur skjöl. (Krefst DB; merkt eins og önnur DB-próf í verkefninu.)
- `tests/test_search_queries.py`: viðbætur fyrir efnisgreinaleiðina — SQL-form, `section_kind`-sía, 400 á ógilt gildi, `match_count` og `anchor` í svari, `provision` og `keyword` síur berast áfram (sama aðhvarfsflokkur og „Bug 2“ í `_search_by_chunks`).
- `tests/test_api_passages.py`: samningspróf á `/api/document/{id}/passages` — sjálfgefið bil, síur, 404, 400.
- `tests/test_passage_anchor.py`: fjögur tilvik `passage_anchor`.
- Frontend: `ResultCard.test.tsx` sýnir `anchor` þegar það er til og ekkert annars; `tsc -b` hreint.
- `eval_search.py` er ekki einingapróf en er keyrt og niðurstaðan skráð í plan-skjalinu áður en skipt er.

## Útrúllun

1. Alembic grunnlína og `0002_passages` — engin sýnileg breyting.
2. `backfill_passages.py` yfir allt (áætlað 600–800 þúsund raðir, klukkustundir með BÍN-lemmun; keyrt með `--workers`).
3. Gullsettið samið og yfirfarið; `eval_search.py --impl both`.
4. Ef nýja leiðin er jöfn eða betri á recall@10 og MRR og svartími ásættanlegur: `LAUSNIR_SEARCH_IMPL=passages` sjálfgefið. Annars er röðunin stillt (`ts_rank_cd`, vægi `match_count`) og mælt aftur áður en skipt er.
5. `0003_drop_document_chunks`, gamla leiðin og flaggið fjarlægð, wiki (`05-leit.md`, `02-gagnagrunnur.md`, `01-arkitektur.md`, `09-gildrur.md`) uppfært.

## Það sem þetta opnar næst

- **Embeddings:** `ALTER TABLE passages ADD COLUMN embedding halfvec(N)` og HNSW-vísir; hybrid RRF ofan á sömu `hits`-fyrirspurn.
- **MCP-tól:** `search_passages` er `/api/search` með `section_kind`; `get_passages` er nýi endapunkturinn.
- **Djúptenging í DocPanel:** `char_start` gefur nákvæma staðsetningu án frekari útreikninga.
