# Mat á aðferðafræði Lausnir v2 — með áherslu á LLM-leit

**Dagsetning:** 2026-09-27
**Grunnur:** wiki (`docs/wiki/`), `engine/search/queries.py`, `engine/api/app.py`, `models.py`, `chunker.py`, `lemmatizer.py`, og lifandi tölur úr `lausnir_v2` (port 5433) þennan dag.

## Tölur sem matið byggir á

| Mæling | Gildi |
|---|---|
| Skjöl | 93.048 |
| `fts_is` fyllt | 100 % |
| `embedding` fyllt | 0 |
| `cited_provisions` fyllt | 83.638 |
| `summary` til | 79.988 |
| Miðgildi lengdar `body_text` | ~10.000 stafir |
| 90. hundraðshluti | ~47.500 stafir |
| 99. hundraðshluti | ~234.000 stafir |
| Skjöl með chunks | 3.068 (aðeins bækur/ritgerðir) |
| Stærð `documents` alls | 69 GB |
| — þar af `raw_api_data` | **56 GB** |
| — þar af `body_text` + `lower_body_text` | 1,2 GB |
| — þar af `fts_is` | 1,4 GB |
| Héraðsdómstólar `raw_api_data` | 53 GB (`pdfString`, base64 PDF, ~2,3 MB/skjal) |
| Sömu PDF á diski | 40 GB í `Lausnir_Data/raw/heradsdomstolar/` |
| Númeraðar málsgreinar (úrtak eftir 2018) | Lrd. 530/533, Hrd. 36/70, Hérd. 81/897 (en 888/897 með `##` fyrirsagnir) |

## 1. Það sem er rétt hugsað og á að halda sér

- **RAW/NORM/RENDER-lagskiptingin.** Óbreytanlegt hráefni er nákvæmlega það sem gerir alla endurvinnslu (ný chunking, ný embeddings, ný tilvitnanagreining) örugga. Þetta er stærsta eign kerfisins.
- **BÍN-lemmun í Postgres.** Rétt val fyrir íslensku; engin auka-innviðir, GIN-vísir, virkar. Þetta verður áfram „annar fóturinn“ í hybrid-leit.
- **`cited_provisions` + lagasafn með skipulögðum ákvæðum.** Þetta er ósvikið strúktúrmerki sem almennar RAG-lausnir hafa ekki. `provision_extractor` sannar að reglubundin tilvitnanagreining virkar á þessum texta.
- **Áfrýjunarkeðjan (`document_links`).** Grafið Hérd → Lrd → Hrd er til.
- **Leitarástand í URL, ekki í React-state.** Gott fyrir fólk og líka fyrir LLM sem getur búið til leitarslóðir.

## 2. Kjarnavandinn: leitareiningin er skjalið, ekki efnisgreinin

Allt í núverandi leit (nema bækur) miðar við **heilt skjal**: `fts_is` á allt meginmál, `ts_rank` á allt meginmál, `ts_headline` yfir allt meginmál. Miðgildisdómur er 10k stafir, tíundi hver er 47k+, og hundraðasti hver 234k+. Afleiðingar:

1. **Röðun er lengdarskekkt.** `ts_rank` án normaliseringar hyglar löngum skjölum sem nefna orðið oft. Það er engin BM25-lík lengdarleiðrétting.
2. **Útdrættir eru lélegir.** `ts_headline` með `MaxFragments=2` úr 50k stafa texta segir lítið um hvers vegna skjalið fannst.
3. **LLM getur ekki vitnað nákvæmlega.** Íslenskir lögfræðingar vitna í dóma eftir málsgrein („sbr. 23. mgr. dómsins“). Kerfið veit ekki hvar málsgrein 23 er — sú þekking er til í PDF-þáttaranum (`_NEW_PARA`) og í renderer-heuristíkum, en hún er **ekki geymd í NORM** og því ekki leitanleg.
4. **Chunk-leiðin er sérleið með gildru.** `_scope_is_chunked` krefst `all()`. Blandað svið (bækur + Hæstiréttur) dettur þögult í skjalaleiðina með ónothæfri röðun fyrir bækurnar. Þetta er sérreglan sem hverfur af sjálfu sér ef *allt* er chunkað.

Chunkerinn sjálfur er heldur ekki rétt smíðaður fyrir dóma: fastir 500 orða bútar með skörun skera niðurstöðukafla í sundur og gefa engan „heimilisfang“. Rétta einingin fyrir dóm er **númeruð málsgrein** (Lrd. nánast 100 %, Hrd. um helmingur nýrri dóma) eða **fyrirsagnakafli** (héraðsdómar eru með `##`-fyrirsagnir í 99 % tilvika).

**Tillaga:** ein tafla, t.d. `passages`, fyrir *öll* skjöl:

```
passages(
  id, document_id, ordinal,
  layer          -- 'body' | 'lower_body'
  anchor         -- '23' (mgr.), 'Niðurstaða', 'Dómsorð', 'IV' ...
  section_kind   -- malsatvik | malsastaedur | nidurstada | domsord | annad
  text,
  fts_is tsvector,          -- GIN
  embedding halfvec(1024)   -- HNSW (sjá kafla 3)
)
```

Bútun eftir strúktúr skjalsins (málsgrein/kafli), með hámarkslengd og aðeins þá fallið á orðafjölda. `document_chunks` verður undirtilvik af þessu og hverfur. Skjalaleitin breytist í: finna bestu efnisgreinar → hópa á skjal → sýna bestu efnisgrein sem útdrátt og `anchor` sem tilvitnun. Það eitt lagar liði 1–4.

## 3. Merkingarleit: dálkurinn sem er til er rangt hannaður

`documents.embedding vector(3072)` er á **skjalastigi** og með **3072 víddir**.

- Ein vigur fyrir 50k stafa dóm mælir ekkert gagnlegt; merkingarleit þarf að vera á efnisgreinastigi.
- pgvector (0.8.2 uppsett) getur **ekki** sett HNSW/IVFFlat-vísi á `vector` með fleiri en 2.000 víddum. Dálkurinn eins og hann er getur aldrei orðið vísaður; hann getur aðeins farið í seq-scan. `halfvec` leyfir allt að 4.000 víddir, en 1024–1536 víddir er skynsamlegra val hvort eð er.

**Tillaga:** embeddings á `passages` (og sérstaklega á `summary`, því Hæstaréttarreifanirnar eru mjög góðar „headnotes“), `halfvec(1024)` með HNSW. Módelval á að vera **reynslubundið** á gullsetti (sjá kafla 7): prófa t.d. `text-embedding-3-large` með `dimensions=1024`, `bge-m3` (keyrt staðbundið, fjöltyngt, gefur líka sparse-vigra) og `multilingual-e5-large`. Textamagnið er ~1,2 GB ≈ 300–400 M tokens; kostnaður hjá OpenAI á bilinu 40–60 USD, staðbundið módel ókeypis en tekur klukkustundir á Mac.

Leitin verður þá **hybrid**: `fts_is` (BÍN) + vigur, sameinað með Reciprocal Rank Fusion, síðan valfrjáls endurröðun (cross-encoder eða LLM) á efstu 30–50. Fyrir íslenskan lagatexta er lemmaða FTS-leitin líklega áfram sterkari á málsnúmer, sérnöfn og fagorð; vigurinn nær hugtökum sem eru orðuð öðruvísi. Hvorugt eitt og sér dugir.

## 4. Það sem LLM þarf til að *rökstyðja*, ekki bara finna

Núverandi API er lagað að manneskju með skjá: 20 raðir á síðu, `<mark>`-HTML í útdrætti, facet-tré fyrir hliðarstiku. LLM-leit er **ítrunarleit með tólum**: margar litlar, ódýrar fyrirspurnir, þrengt með síum, síðan sótt nákvæmt samhengi. Það sem vantar:

1. **Tilvitnanir dóma í aðra dóma.** Þetta er ekki greint neins staðar. Mynstrin eru fyrirsjáanleg („dómur Hæstaréttar 12. mars 2020 í máli nr. 5/2020“, „Hrd. 2005, bls. 1234“, „Lrd. 177/2024“). Sama tveggja-umferða aðferð og í `provision_extractor` á við. Úr þessu kemur `document_citations`-tafla: fordæmisnet, „hvaða dómar vitna í þennan“, og tilvitnanafjöldi sem röðunarmerki. Þetta er ódýrasta og verðmætasta viðbótin í öllu kerfinu og þarf ekkert LLM.
2. **Kaflar sem NORM-gögn.** Renderer þekkir Dómsorð/Niðurstaða/Úrskurðarorð með heuristíkum í RENDER-laginu. Sú þekking á heima í `passages.section_kind` svo hægt sé að spyrja: „finndu niðurstöðukafla þar sem 218. gr. er beitt“.
3. **Tól í stað hrá-SQL.** Í dag er eina LLM-leiðin að grunninum `mcp-postgres` með fullum skrif-réttindum á framleiðslugrunn (opið TODO í READINESS_PLAYBOOK). Það er bæði áhættusamt og óskilvirkt: LLM sem skrifar SQL gegn 69 GB töflu án þess að þekkja vísana gerir mistök. Réttara er lítill, **read-only MCP-þjónn** ofan á `queries.py` með fáum, vel lýstum tólum:

   | Tól | Skilar |
   |---|---|
   | `search_passages(q, scope, date, provision, section_kind, k)` | efnisgreinar með `urlausn`, `anchor`, hreinum texta (engin HTML), skori |
   | `get_passages(doc_id, from_anchor, to_anchor)` | samfellt samhengi í kringum treff |
   | `get_document(doc_id)` | lýsigögn + kaflalisti (ekki 200k stafa texti sjálfkrafa) |
   | `get_provision(law, gr, mgr)` | er til í dag |
   | `cases_citing(doc_id)` / `cases_cited_by(doc_id)` | úr `document_citations` |
   | `cases_applying_provision(law, gr, mgr, scope)` | úr `cited_provisions` |
   | `appeal_chain(doc_id)` | úr `document_links` |
   | `facets(q, ...)` | er til; gagnlegt fyrir LLM til að ákveða næstu þrengingu |

   Sami kjarni þjónar framendanum; MCP er bara annað skinn.

4. **LLM í fyrirspurnarhliðinni.** Ódýrir sigrar sem þurfa enga skema-breytingu: fyrirspurnarútvíkkun (samheiti og lagafagorð á íslensku, t.d. „líkamsárás“ ↔ „218. gr.“), og endurröðun efstu 30 með litlu módeli (Haiku). Hvort tveggja mælanlegt á gullsettinu.

## 5. Afleiddir vísar eru viðhaldnir í þremur ósamstilltum skriptum

`fts_is`, `document_chunks` og (væntanlega) `embedding` eru hvert um sig fyllt með sinni `backfill_*`-skriptu sem einhver þarf að muna að keyra. Wiki-ið kallar þetta sjálft „algengustu gildruna“. Þegar `passages` og embeddings bætast við verður þetta óviðráðanlegt.

**Tillaga:** eitt skref, `derive_search_artifacts(doc)`, sem keyrir í sömu færslu og upsert: byggir `passages`, `fts_is` (skjal og efnisgreinar), setur embeddings í biðröð. Auk þess `index_state(document_id, content_hash, fts_at, passages_at, embed_at)` svo að „hvað er úrelt“ sé fyrirspurn, ekki ágiskun. `update_all.py` keyrir þá bara „allt sem er úrelt“.

## 6. Geymsla: 56 af 69 GB eru afrit af PDF-skrám

`raw_api_data` fyrir héraðsdómstóla geymir `pdfString` (base64 PDF) fyrir 24.282 skjöl, 53 GB, og **sömu PDF-skrár eru þegar á diski** (40 GB í `Lausnir_Data/raw/heradsdomstolar/`). Arkitektúrinn segir sjálfur að PDF-bæti eigi heima á diski.

Þetta brýtur ekki leitina (TOAST heldur þessu utan aðaltöflunnar, sem er aðeins 163 MB), en það gerir `pg_dump`, `VACUUM`, flutning milli véla og alla `SELECT *`-fyrirspurn (þ.m.t. `session.get(Document)` í `/api/law` og MCP-postgres) að 2,3 MB aðgerð á skjal. Lagfæring sem brýtur ekki gullnu regluna: færa `pdfString` úr JSONB í skrá (staðfesta að sha256 stemmi við diskskrána), skilja eftir `{"pdfString": null, "pdf_sha256": "..."}`. RAW er áfram heilt, bara á réttum stað. Sama athugun á við `landsrettur` (2,1 GB).

## 7. Ekkert gullsett — allar röðunarbreytingar eru blindar

Það er ekkert sett af (spurning → réttu dómarnir) til að mæla leitargæði. Án þess er ekki hægt að segja hvort BM25-normalisering, embeddings-módel A eða B, eða RRF-vogir bæti nokkuð. Fyrsta skrefið á undan öllu í köflum 2–4 er **50–100 raunverulegar rannsóknarspurningar** með 1–5 „réttum“ dómum hver, og lítið skript sem reiknar recall@10 og MRR. Þetta passar við vinnuregluna sem þegar gildir í verkefninu: sýna gögn og velja út frá þeim, ekki harðkóða sjálfgefið.

## Forgangsröð

| # | Verk | Áhrif | Kostnaður | Þarf LLM? |
|---|---|---|---|---|
| 0 | Gullsett + mælingaskript | forsenda alls | lítill | nei |
| 1 | `passages` fyrir öll skjöl, strúktúrbundin bútun, leit á efnisgreinastigi; `document_chunks` og `all()`-sérreglan hverfa | mjög mikil | miðlungs | nei |
| 2 | Tilvitnanir dóms í dóm → `document_citations` | mjög mikil fyrir lögfræðirannsókn | lítill–miðlungs | nei |
| 3 | `pdfString` út úr JSONB á disk | rekstur, hraði, flutningur | lítill | nei |
| 4 | Embeddings á `passages` + `summary`, `halfvec(1024)` HNSW, hybrid RRF; módel valið á gullsetti | mikil | miðlungs (+ tími/kostnaður við embedding) | já (embedding) |
| 5 | Read-only MCP með tólunum í kafla 4; loka skrif-aðgangi `mcp-postgres` | opnar LLM-leit á öruggan hátt | lítill–miðlungs | nei |
| 6 | `derive_search_artifacts` + `index_state` í stað þriggja backfill-skripta | viðhald, áreiðanleiki | miðlungs | nei |
| 7 | Fyrirspurnarútvíkkun og endurröðun með LLM | góð viðbót | lítill | já |

Liðir 1–3 og 5 þurfa engin embeddings og ekkert nýtt módel. Þeir einir og sér gera kerfið mun betra fyrir LLM en það er í dag, og þeir eru forsenda þess að liður 4 skili sér.
