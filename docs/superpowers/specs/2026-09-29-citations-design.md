# Tilvitnanir milli dóma (case-to-case citations) — hönnun

**Dagsetning:** 2026-09-29
**Staða:** Samþykkt hönnun í spjalli 2026-09-29. Áætlun: `docs/superpowers/plans/2026-09-29-citations.md`.
**Forsaga:** `docs/2026-09-27-mat-a-adferdafraedi-og-llm-leit.md` nefndi tilvitnanir milli dóma sem næstu rannsóknarvirkni á eftir leit og MCP. `document_links` ber í dag áfrýjunarkeðjuna (`appealed_to`/`appealed_from`), málskotsbeiðnir (`leyfisbeidni_um`, `leiddi_til_doms`); tilvitnanir í lög eru í `documents.cited_provisions`.

## 1. Markmið

Fyrir hvern dóm á að vera hægt að svara: **í hvaða dóma vitnar hann** og **hvaða dómar vitna í hann**, með setningunni þar sem vitnað er og efnisgreininni sem hún stendur í. Þetta á að birtast í vefnum, API-inu og MCP-þjóninum, og að byggjast sjálfkrafa fyrir ný skjöl.

**Ekki markmið í þessari útgáfu:** nefndir og stofnanir sem skotmörk (óbyggðanefnd, úrskurðarnefndir, Persónuvernd o.fl.), EFTA-dómstóllinn og Mannréttindadómstóll Evrópu, tilvitnanir í lög (þegar til), merkingarfræðileg flokkun tilvitnana (fylgt/hafnað/aðgreint).

## 2. Ákvarðanir notanda

| Spurning | Val | Afleiðing |
|---|---|---|
| Skotmörk í v1 | **Dómstólarnir sjö undir `domstolar`** | Hæstiréttur, Landsréttur, héraðsdómstólar, Félagsdómur, Landsdómur, Endurupptökudómur, málskotsbeiðnir. Töfluskipanið tekur við nefndum síðar án breytinga. |
| Geymsla | **Leið A: tafla `citations` + afleiddar `cites`-brúnir í `document_links`** | Óleystar tilvitnanir geymast; efnisgrein og setning fylgja; graf-fyrirspurnir nota `document_links` eins og áður. |

## 3. Mæling sem hönnunin byggir á (3.000 skjala úrtak, 29.09.2026)

| | Fjöldi |
|---|---|
| „máli nr. X“ með dómstól í samhengi | 1.933 |
| leyst á nákvæmlega eitt skjal | 1.656 (86 %) |
| tvíræð eftir dagsetningar- og dómur/úrskurður-þrengingu | 41 |
| óleysanleg (Hæstiréttur fyrir 1999, óbirtir héraðsdómar, sýslumenn) | 228 |
| án dómstóls í samhengi (mest aðrir úrskurðaraðilar) | 943 |
| aðgreindar leystar tilvitnanir á skjal | 0,42 → ≈37.000 brúnir í 87.000 dómum |

Lærdómar: prósaformið ræður („dómi Hæstaréttar 8. nóvember 2017 í máli nr. 700/2017“), skammstöfunin „Hrd. 700/2017“ kemur nánast ekki fyrir í dómstexta; dagsetning í setningunni leysir endurnotuð Landsréttarnúmer; dagsetning án númers er einkvæm í aðeins 15 % tilvika og er **ekki** notuð ein og sér; lagatilvísanir („laga nr. 19/1940“) verða að vera útilokaðar áður en málsnúmer eru lesin; dómstólsheiti innan 220 stafa var of vítt (úrskurður óbyggðanefndar rataði á Hæstarétt) — gluggi verður sama setning og mest 120 stafir.

## 4. Gagnalíkan

### 4.1 Tafla `citations` (alembic 0004)

| Dálkur | Tegund | Skýring |
|---|---|---|
| `id` | UUID PK | |
| `from_doc_id` | UUID FK documents ON DELETE CASCADE, NOT NULL | vitnandi skjal |
| `layer` | TEXT NOT NULL | `body` eða `lower_body` — hvaða textalag tilvitnunin fannst í (sama og `passages.layer`) |
| `char_start`, `char_end` | INT NOT NULL | bil í því lagi; `UNIQUE (from_doc_id, layer, char_start)` |
| `passage_id` | UUID FK passages ON DELETE SET NULL | efnisgreinin sem inniheldur `char_start` í sama lagi; null ef engin |
| `raw_text` | TEXT NOT NULL | textabúturinn frá upphafi dómstólsorðs að enda málsnúmers, mest 200 stafir |
| `target_tier` | SMALLINT | 1 hérað, 2 Landsréttur, 3 Hæstiréttur, null fyrir Félagsdóm/Landsdóm/Endurupptökudóm (þeir hafa `instance_tier` null; leyst með `target_court`) |
| `target_court` | TEXT NOT NULL | skammstöfun eins og `documents.court`: `Hrd.`, `Lrd.`, `Hérd. Rvk.`, `Féld.`, `Ld.`, `Eud.`, `Hrd. málsk.`; `Hérd.` án staðar ef héraðsdómstóll ónefndur |
| `target_case_number` | TEXT | eins og lesið, án „nr.“; null fyrir dómasafnsform |
| `target_date` | DATE | ef nefnd í setningunni; afstæð dagsetning („sama ár“, „sl.“) leyst með ári vitnandi skjals |
| `target_verdict` | TEXT | `Dómur` / `Úrskurður` / null eftir orðinu sem notað er |
| `to_doc_id` | UUID FK documents ON DELETE SET NULL | leyst skjal |
| `status` | TEXT NOT NULL | `resolved`, `ambiguous`, `unresolved`, `self`, `pre_coverage` |
| `method` | TEXT | `casenum_date`, `casenum_verdict`, `casenum_unique`, `casenum_court_date`, null |
| `confidence` | REAL | 1,0 / 0,9 / 0,8 / null |
| `created_at` | TIMESTAMPTZ default now() | |

Vísar: `ix_cit_from (from_doc_id)`, `ix_cit_to (to_doc_id) WHERE to_doc_id IS NOT NULL`, `ix_cit_target (target_court, target_case_number)`, `ix_cit_status (status)`.

### 4.2 Tafla `citation_state` (sama flutningur)

`document_id UUID PK FK documents ON DELETE CASCADE`, `text_hash TEXT NOT NULL` (sha256 af `body_text || lower_body_text`), `n_found`, `n_resolved` INT, `built_at TIMESTAMPTZ`. Úreldingarmerki, sama hugmynd og `passage_hash` fyrir efnisgreinar; `documents` er ekki breytt.

### 4.3 `document_links`

Ný tengslategund `cites`: `from_doc_id` = vitnandi, `to_doc_id` = vitnaður, **einátta**, ein röð á hvert (from, to) par þótt vitnað sé oft. `confidence` = hæsta öryggi meðal tilvitnananna sem mynda brúnina, `method = 'citation'`. `check_link_orientation.py` sleppir `cites` (það þekkir aðeins áfrýjunartengslin) og fær athugasemd um það.

Tilvitnun í dóm sem er þegar í áfrýjunarkeðju sama máls (t.d. Hæstiréttur vitnar í Landsréttardóminn sem áfrýjað var) er **skráð eins og hver önnur** í `citations`, en `cites`-brún er **ekki** skrifuð þegar sama par (í hvorri stefnu sem er) ber þegar `appealed_to`/`appealed_from`/`leyfisbeidni_um`/`leiddi_til_doms`. Rökin: keðjan er sýnd sér í vefnum; tvöföld birting ruglar.

**Textalög.** Tilvitnanir í `lower_body_text` (innfelldur texti lægra dómstigs í Hæstaréttar- og Landsréttardómum) eru geymdar með `layer='lower_body'` en teljast rökstuðningur lægra dómstigsins, ekki dómsins sjálfs: `cites`-brúnir, `citations_out`, `cited_by` og MCP-svör nota **aðeins** `layer='body'`. Lægra dómstigs dómurinn sjálfur (þegar hann er í safninu) ber sínar eigin tilvitnanir úr eigin `body_text`.

## 5. Útdráttur — `engine/processors/citations.py`

Hreint fall, engin gagnagrunnstenging:

```python
@dataclass(frozen=True)
class RawCitation:
    char_start: int; char_end: int; raw_text: str
    target_court: str            # 'Hrd.' | 'Lrd.' | 'Hérd.' | 'Hérd. Rvk.' … | 'Féld.' | 'Ld.' | 'Eud.' | 'Hrd. málsk.'
    target_tier: int | None
    target_case_number: str | None
    target_date: date | None
    target_verdict: str | None   # 'Dómur' | 'Úrskurður' | None
    form: str                    # 'prose' | 'abbrev' | 'reporter'

def extract_citations(text: str, *, doc_date: date | None) -> list[RawCitation]
```

Reglur:

1. **Lagatilvísanir fyrst.** Öll bil sem passa `\bl(?:ög|aga|ögum|ögunum)\s+nr\.\s*\d{1,4}/\d{4}` og `reglugerð\w*\s+nr\.\s*\d+/\d{4}` eru merkt og málsnúmer innan þeirra hunsuð.
2. **Prósaform.** Fyrir hvert `mál(?:i|inu|s|um|unum)?\s+nr\.\s*<NÚMER>` (og upptalningar: `nr\.\s*A/YYYY(?:\s*,\s*B/YYYY)*(?:\s+og\s+C/YYYY)?` → ein tilvitnun á hvert númer) er leitað **aftur á bak innan sömu setningar** (stöðvað við `. ` með stórum staf á eftir, `;`, `:` sem ekki er hluti af `nr.:`-formi, eða 120 stafi) að fyrsta dómstólsorði: `Hæstaréttar|Hæstarétti|Hæstiréttur`, `Landsréttar|Landsrétti|Landsréttur`, `[Hh]éraðsdóm\w*(\s+(Reykjavíkur|Reykjaness|Vesturlands|Vestfjarða|Norðurlands\s+(?:eystra|vestra)|Austurlands|Suðurlands))?`, `Félagsdóm\w*`, `Landsdóm\w*`, `Endurupptökudóm\w*`. Finnist annar úrskurðaraðili nær (`nefnd\w*`, `sýslumann\w*`, `ráðuneyt\w*`, `stofn\w*`, `Persónuvernd`, `dómstól\w*` sem ekki er einn af ofangreindum) er tilvitnunin **ekki** dómstilvitnun og er sleppt. Númer með bókstafsforskeyti (`E-`, `S-`, `K-`, `R-`, `X-`, `Y-`, `Q-` …) er alltaf héraðsdómsnúmer, númer án forskeytis aldrei.
3. **Skammstafanir.** `Hrd\.\s*(\d{1,4}/\d{4})`, `Lrd\.\s*…`, `Hérd\.\s*(Rvk|Rvn|Vl|Vf|Nv|Ne|Al|Sl)\.\s*([A-ZÞÆÖ]-\d+/\d{4})`.
4. **Dómasafnsform** → `form='reporter'`, `target_case_number=None`, `status` verður `pre_coverage`: `Hrd\.?\s*(\d{4})[,:/]\s*(?:bls\.\s*)?(\d{1,5})`, `\bH\s?(\d{4}):(\d{1,5})`.
5. **Dagsetning og orð.** Í sömu setningu (60 stafir á undan eða 80 á eftir númerinu): `(\d{1,2})\.\s+(mánaðarnafn)\s+(\d{4}|sama\s+ár|sl\.|síðastliðin\w*)` → `target_date`; afstæð ár = ár `doc_date`. Síðasta `dóm\w*`/`úrskurð\w*` á undan dómstólsorðinu innan 40 stafa → `target_verdict`.
6. **Sjálfstilvitnun** er skilað (`status` ákveðið í leysara).
7. Föll skila engu ef `text` er tómt; aldrei undantekning á texta (allt sem ekki passar er sleppt).

## 6. Leysari — `engine/processors/citation_resolver.py`

```python
class CitationIndex:            # byggður einu sinni úr documents (instance_tier IN (1,2,3) OR source IN (felagsdomur, landsdomar, endurupptokudomur, malskotsbeidnir))
    def candidates(self, court_abbr_prefix: str, case_number: str) -> list[Candidate]

def resolve(raw: RawCitation, *, index, from_doc: FromDoc) -> Resolution   # (status, to_doc_id, method, confidence)
```

Frambjóðendur = skjöl þar sem `court` byrjar á `target_court` (svo `Hérd.` án staðar passi öll héruð en `Hérd. Rvk.` aðeins Reykjavík) og `case_number == target_case_number`. Þrenging í röð:

1. Sé `target_date` sett → aðeins frambjóðendur með þá `document_date` (`casenum_date`, 1,0).
2. Annars sé `target_verdict` sett → aðeins þeir með sama `verdict_type` (`casenum_verdict`, 0,9).
3. Standi einn eftir án þrengingar → `casenum_unique`, 0,8.
4. Standi einn eftir eftir héraðsdómsheiti og dagsetningu bæði → `casenum_court_date`, 1,0.

Niðurstöður: einn frambjóðandi → `resolved`; fleiri → `ambiguous` (aldrei giskað); enginn → `unresolved`, nema `target_court == 'Hrd.'` og ár númersins < 1999 eða `form == 'reporter'` → `pre_coverage`; sami dómur og vitnandi → `self`. Frambjóðandi með `document_date` eftir dagsetningu vitnandi skjals er felldur út (dómur getur ekki vitnað í síðari dóm); ef það tæmir hópinn → `unresolved`.

## 7. Bygging — `scripts/build_citations.py`

Valkostir: `--all`, `--source SHORT_NAME`, `--doc UUID`, `--since YYYY-MM-DD`, `--stale-only` (sjálfgefið með `--all`: aðeins skjöl þar sem `citation_state.text_hash` vantar eða stemmir ekki), `--relink-unresolved` (endurleysa `unresolved`/`ambiguous` raðir án útdráttar), `--dry-run`, `--workers N`.

Fyrir hvert skjal í einni færslu: eyða `citations` þess, eyða `document_links` þar sem `from_doc_id = :id AND relation = 'cites'`, setja inn nýjar raðir, skrifa `citation_state`. Skriftan á aðeins sínar eigin raðir; snertir aldrei aðrar tengslategundir eða `documents`. `passage_id` fundið með `SELECT id FROM passages WHERE document_id=:id AND layer=:layer AND char_start <= :pos AND char_end > :pos`. Lokasamantekt: fjöldi eftir `status`, eftir dómstigi vitnandi og vitnaðs, brúnir skrifaðar, keyrslutími.

Innflutningur: efnisgreinar eru byggðar með `scripts/backfill_passages.py` eftir innflutning, ekki í pípunni sjálfri. Tilvitnanir fylgja sama mynstri: `build_citations.py --all` (úrelt skjöl aðeins) keyrir á eftir `backfill_passages.py`, og skjalað í `docs/wiki/04-innflutningur.md`. `--relink-unresolved` keyrir í sömu lotu svo eldri tilvitnanir sem vísa á nýkomið skjal tengist.

## 8. Birting

### 8.1 API (`engine/api/app.py`, `engine/search/queries.py`)

- `get_document` skilar að auki:
  - `citations_out`: listi leystra tilvitnana úr þessu skjali, aðgreindar eftir `to_doc_id` (fyrsta tilvitnun hvers skjals), hver með `document_id`, `urlausn`, `passage_id`, `anchor`, `raw_text`, `confidence`; röðuð eftir `document_date` vitnaðs; mest 50 auk `citations_out_total`.
  - `cited_by`: listi skjala sem vitna í þetta, hvert með `document_id`, `urlausn`, `passage_id`, `anchor`, `raw_text`, `document_date`; nýjast fyrst; mest 50 auk `cited_by_total`.
  - `citations_unresolved_total`: fjöldi `unresolved`+`ambiguous`+`pre_coverage` úr skjalinu (til að sýna „auk N tilvitnana sem ekki fundust“).
- Ný slóð `GET /api/document/{id}/citations?direction=out|in&page=&page_size=` (≤100) fyrir fleiri en 50; sama raðasnið; 400 við rangt `direction`.
- Leitarniðurstöður fá `cited_by_count` (heiltala) svo listinn geti sýnt „vitnað í 12 sinnum“; reiknað með `LEFT JOIN LATERAL (SELECT count(*) …)` á `document_links` `relation='cites'` — mæla; ef það kostar >10 ms á síðu er það sleppt úr listanum og aðeins sýnt í `get_document`.

### 8.2 Framendi

- `DocPanel.tsx`: nýr kafli **„Tilvitnanir“** með tveimur undirlistum: „Vitnar í (n)“ og „Vitnað í þennan dóm (n)“; hver lína `urlausn` sem hlekkur á `/domur/{id}` og undir henni `raw_text` í smáu letri; hnappur „Sýna fleiri“ kallar á `/citations`-slóðina. Neðst „N tilvitnanir fundust ekki í safninu“ ef við á.
- Merkingar í kaflanum „Tengd mál“ lagaðar: `appealed_to` → „Áfrýjað til“, `appealed_from` → „Áfrýjað frá“, `leyfisbeidni_um` → „Málskotsbeiðni um“, `leiddi_til_doms` → „Leiddi til dóms“; óþekkt tengsl sýna heitið óbreytt.
- `types.ts`: `CitationRef`, `relation` sem sambandsgerð (union) þekktra heita.

### 8.3 MCP (`engine/mcp/tools.py`, `server.py`)

- `get_document` fær `citations_out_total`, `cited_by_total` og fimm efstu úr hvorum lista (sama snið og API).
- Nýtt verkfæri `citations(doc_id, direction="out"|"in", page=1, page_size≤25)`; lýsing segir að `raw_text` sé setningin sem vitnar og `anchor` efnisgreinin. Verkfærin verða níu; `TOOL_NAMES`, prófin og `docs/wiki/10-mcp.md` uppfærð.

## 9. Gæðamörk

- **Nákvæmni ≥ 98 %** á 200 leystum tilvitnunum dregnum af handahófi eftir fulla keyrslu: fyrir hverja er `raw_text` borið saman við `urlausn` skjalsins sem hún leystist á (dómstóll, númer, dagsetning ef nefnd, dómur/úrskurður). Úttektin er verkefni í áætluninni; taflan fer til notanda með dæmum um villur; sameining bíður staðfestingar hans ef viðmiðið næst ekki.
- **Þekja**: hlutfall `resolved` af (`resolved`+`ambiguous`+`unresolved`) ≥ 80 % í fullri keyrslu (mælingin gaf 86 %).
- **Engin brún á síðari dóm**: `SELECT count(*) FROM document_links dl JOIN documents a ON a.id=dl.from_doc_id JOIN documents b ON b.id=dl.to_doc_id WHERE dl.relation='cites' AND b.document_date > a.document_date` = 0, sem próf í `scripts/check_link_orientation.py` (ný athugun, sérstök fyrir `cites`).

## 10. Prófun

- `tests/test_citations_extract.py`: fastar setningar úr mælingunni — prósaform með og án dagsetningar, upptalning, afstæð dagsetning, lagatilvísun sem má ekki verða tilvitnun, óbyggðanefnd/úrskurðarnefnd sem má ekki eigna Hæstarétti, héraðsdómur með og án staðar, `Hrd. 1983/1538`, `H 1999:123`, bókstafsforskeyti, tvær tilvitnanir í sömu setningu, `sama ár`, dæmi þar sem `nr.` stendur næst setningaskilum.
- `tests/test_citation_resolver.py`: tilbúinn vísir með endurnotuðu Landsréttarnúmeri (úrskurður + dómur), tvö héruð með sama númeri, síðari dómur útilokaður, pre-1999.
- `tests/test_citations_db.py` (skipif): keyra `--doc` á þrjú þekkt skjöl úr mælingunni og staðfesta `status`-dreifingu og `passage_id`; endurkeyrsla er idempotent (sami fjöldi raða, sömu id ekki krafist).
- API-, MCP- og framendapróf fyrir nýju reitina og verkfærið; `test_mcp_server.py` uppfært í níu verkfæri.
- Fulltrúaprófun eftir fulla keyrslu: nákvæmniúttektin í kafla 9.

## 11. Skjölun

`docs/wiki/02-gagnagrunnur.md` (töflurnar tvær, öll tengslaheiti `document_links` — síðan er úrelt um `appealed_from`, `leyfisbeidni_um`, `leiddi_til_doms`), `04-innflutningur.md` (keyrsluröð eftir innflutning), `05-leit.md`/`06-api.md`/`07-framendi.md` (nýju reitirnir og slóðin), `10-mcp.md` (níunda verkfærið), `09-gildrur.md` (lög á undan málsnúmerum; dómstóll innan setningar; Landsréttarnúmer endurnotuð; héraðsnúmer ekki einkvæm milli héraða), `README.md`.

## 12. Samþykktarhlið

Tvær aðgerðir snerta lifandi grunninn og bíða sérstaks „já“ frá notanda í áætluninni, eins og fyrri DB-breytingar: (1) `alembic upgrade head` með flutningi 0004 (tvær nýjar töflur, engin breyting á eldri), (2) fyrsta fulla keyrsla `build_citations.py --all` sem skrifar ≈37.000 `cites`-brúnir. Allt annað (kóði, próf, þurrkeyrslur með `--dry-run`, mælingar á úrtaki) gengur án hlés.

## 13. Áhætta og mótvægi

- **Rangar tengingar** eru verri en engar: leysarinn giskar aldrei (nákvæmlega einn frambjóðandi eða `ambiguous`), síðari dómar útilokaðir, nákvæmniúttekt með 98 % viðmiði fyrir sameiningu.
- **Regex-mynstur missa af formum**: `unresolved`/`ambiguous` raðirnar geymast með `raw_text`, svo næsta umferð mynsturs getur endurleyst án útdráttar; samantekt eftir keyrslu sýnir stærstu flokka óleystra.
- **Efnisgreinar endurbyggðar** (`passage_id` slitnar): `ON DELETE SET NULL` og `char_start`/`layer` duga til að finna efnisgreinina aftur; `build_citations.py --relink-passages` er ekki í v1 — full endurbygging (`--all` án `--stale-only`) leysir það.
- **Keyrslutími**: 87.000 skjöl × regex yfir body_text; áætlað 10–20 mínútur með 4 ferlum, sama mynstur og `backfill_passages.py`.
