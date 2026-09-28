# Slökuð orðaleit (relaxation) — design spec

**Dagsetning:** 2026-09-28
**Staða:** Innleitt 2026-09-28. `RELAX_BELOW = 10` og `RELAX_CAND_LIMIT = 300` valin af notanda út frá mælingu (sjá „Framhald / þekkt takmörk“ og `docs/superpowers/plans/2026-09-28-relaxed-keyword-search.md`).
**Bakgrunnur:** `docs/superpowers/specs/2026-09-27-passages-design.md` og mælingar í `docs/superpowers/plans/2026-09-27-passages.md` („Niðurstöður mælinga“).

## Samhengi

Orðaleit krefst þess að öll leitarorðin finnist í skjalinu (`plainto_tsquery` AND-ar lemmurnar). Á 492 spurninga gullsettinu gefa 78 spurningar (16 %) núll treff í bæði gömlu og nýju leiðinni, og margar til viðbótar gefa örfá. Notandinn fær tóma síðu án skýringar og engar vísbendingar um hvaða orð olli því. Þetta er stærsta mælda gæðatækifærið eftir efnisgreinaskiptin.

Efnisgreinaleitin er þegar tveggja þrepa: `cand` (skjöl sem uppfylla strönga fyrirspurn, þak 2.000) og efnisgreinaþrep með `hits_and` (öll orð í sömu efnisgrein) og `hits_or` (OR-varaleið fyrir frambjóðendur án samstæðs treffs). Slökunin er **eingöngu breyting á forsíunni í `cand`** og röðun eftir þrepum; efnisgreinaþrepið er óbreytt.

## Ákvarðanir

1. **Kveikja.** Aðeins `keyword`-hamur með tvær eða fleiri lemmur. Slökun kviknar þegar strangi fjöldinn (skjöl með öll orðin, með sömu síum og leitin) er **undir `RELAX_BELOW`**. Gildið er valið með mælingu á gullsettinu (`--relax-sweep`), ekki fyrirfram; bráðabirgðagildi 10. `RELAX_BELOW = 0` slekkur á slökun.
2. **Þrjú þrep, ekki tvö.** Í slökuðum ham fá skjöl þrep: `0` = öll orðin, `1` = öll nema eitt, `2` = eitthvert orðanna. Ströng treff koma alltaf fyrst, síðan þrep 1, svo þrep 2, og innan þreps gildir venjuleg röðun (`breadth_coloc` eða dagsetning). Þannig er „slaka þegar treff eru fá“ og „slaka þegar ekkert finnst“ sama vélin.
3. **„Öll nema eitt“** er sameining allra samsetninga þar sem eina lemmu vantar, hver samsetning AND-uð: fyrir `a b c` → `(a & b) | (a & c) | (b & c)`. **Breytt 2026-09-28 eftir lokayfirferð (M5):** fyrir tvær lemmur er þrep 1 („öll nema eitt“) orðrétt sama fyrirspurn og þrep 2 („eitthvert“) — í stað þess að tvítelja það er þrepi 1 sleppt með öllu fyrir n = 2 (`build_keyword_queries` skilar `nminus1 = None`), svo niðurstöður sýna aðeins þrep 0/2 og merkið er „sum orðin“, aldrei „flest orðin“, fyrir tveggja orða slökun. Yfir **8 lemmum** er þrepi 1 sömuleiðis sleppt (samsetningafjöldi) og aðeins þrep 0 og 2 notuð.
4. **Efnisgreinaþrepið er óbreytt.** `hits_and` notar áfram strönga fyrirspurn (öll orð í sömu efnisgrein); `hits_or` notar „eitthvert“ fyrir frambjóðendur án samstæðs treffs — sem er nákvæmlega það sem slökuðu skjölin þurfa. Útdráttur fylgir sömu reglu og í dag.
5. **`total` og `strict_total`.** `strict_total` er fjöldi skjala sem uppfylla ströngu fyrirspurnina (öll orðin), með síum. Óslakað: `strict_total == total`, `total` beint af GIN-vísinum (engin þaksetning). **Uppfært 2026-09-28** (Task 6b): í slökuðum ham er `total` **ekki** lengur doc-level fjöldi þeirra sem uppfylla víðustu fyrirspurnina — sá fjöldi getur verið tugir þúsunda þótt síðuflettingin geti aldrei náð nema `strict_total + RELAX_CAND_LIMIT` skjölum (`cand`-þakið). Í staðinn er `total = count(*) FROM hits` (sömu CTE-ir og niðurstöðurnar sjálfar) — nákvæmlega sá fjöldi sem hægt er að fletta að. Með `section_kind`-síu var þetta þegar svona (talan er bundin af frambjóðendaþakinu af því sían verkar aðeins á efnisgreinar).
6. **Facets fylgja.** ~~`facet_counts` tekur sömu ákvörðun með sínum eigin síum (dagsetningar, án sviðs): ef strangi fjöldinn er undir `RELAX_BELOW` telur hún með víðustu fyrirspurninni. Ákvörðunin er eitt fall sem bæði nota.~~
   **Breytt 2026-09-28 eftir lokayfirferð (I2):** `facet_counts` slakar aldrei. Hún síar alltaf með ströngu fyrirspurninni (`to_tsquery('simple', :q_strict)`), nákvæmlega eins og fyrir Task 3, og `should_relax`-ákvörðunin (og strangi-talningin sem hún þurfti) er alfarið fjarlægð úr `facet_counts` — einni gagnagrunnstalningu færri á hverja `keyword`-leit. Ástæðan: hliðarspjaldið sýnir dreifingu þeirra skjala sem innihalda **öll** orðin — sömu tölu og niðurstöðuhausinn kallar `strict_total` — á meðan listinn að auki sýnir slökuðu (þrep 1/2) treffin sem finnanleg eru. Að telja víðustu fyrirspurnina í hliðarspjaldinu skilaði tugum þúsunda við hliðina á lista með ≤ `RELAX_CAND_LIMIT` niðurstöðum, sem er ruglandi, ekki gagnlegt.
7. **Viðmót.** Ein tilkynningarlína yfir niðurstöðum þegar `relaxed` er satt, og lítið merki á niðurstöðum með þrep > 0. Ekkert annað breytist.
8. **Utan þessa spec:** stafsetningarleiðrétting, samheiti, slökun í nálægðarleit, og að slaka á kröfu um orð innan efnisgreinar.

## Fyrirspurnasmíð — `engine/search/relaxation.py`

Hreint, engin DB.

```python
MAX_NMINUS1_LEMMAS = 8
RELAX_BELOW = 10                      # bráðabirgða; valið með --relax-sweep

@dataclass(frozen=True)
class KeywordQueries:
    strict: str        # 'a & b & c'            (to_tsquery-setning)
    nminus1: str|None  # '(a & b) | (a & c) | (b & c)'; None ef n < 2 eða n > 8
    any: str           # 'a | b | c'
    n: int

def build_keyword_queries(lemmas: str) -> KeywordQueries
def should_relax(strict_total: int, n: int, threshold: int | None = None) -> bool
    # n >= 2 and strict_total < (threshold if threshold is not None else RELAX_BELOW)
```

Lemmur koma úr `lemmatize_query` — **ekki** aðeins `[a-záéíóúýðþæö]+` tákn eins og fyrri útgáfa þessa skjals fullyrti (leiðrétt 2026-09-28 eftir lokayfirferð): BÍN varðveitir hástafi fyrir sérnöfn, og bandstriksskiptur innsláttur (t.d. „e-mál“) verður fjöltóka lemma með bili innan strengsins (t.d. „e mál“). Strangi strengurinn notar `&` í stað `plainto_tsquery` svo öll þrjú séu sama tegund (`to_tsquery`).

**Uppfært 2026-09-28 eftir lokayfirferð (M6, táknhreinlæti):** af því kallendur eru ekki tryggðir að fá eingöngu úttak úr `lemmatize_query`, afmáir `build_keyword_queries` sjálft hvers kyns `to_tsquery`-stjórnstafi (`& | ! ( ) : ' * < >`) úr hverju tákni áður en fyrirspurnirnar eru samsettar, sleppir táknum sem verða tóm eftir það, og lyftir `ValueError` ef ekkert tákn stendur eftir. Kallendur (`search_documents` og `facet_counts`) grípa það villuástand og haga sér nákvæmlega eins og þegar ekkert er lemmanlegt — sía-eingöngu vafur án `keyword`-forsíu.

## SQL — breytingar á `build_hits_sql`

Nýtt viðfang `relax: tuple[str, str|None] | None` = (`any_tsq`, `nminus1_tsq`) þegar slakað er, annars `None`. Í slökuðum ham:

```sql
t0 AS MATERIALIZED (                      -- ströng treff; ≤ RELAX_BELOW raðir þegar slakað er
  SELECT d.id, d.document_date FROM documents d
  WHERE d.fts_is @@ {strict_tsq}{doc_where_sql}
),
t1 AS MATERIALIZED (                      -- sleppt alveg ef nminus1 er None
  SELECT d.id, d.document_date FROM documents d
  WHERE d.fts_is @@ {nminus1_tsq}{doc_where_sql}
    AND NOT EXISTS (SELECT 1 FROM t0 WHERE t0.id = d.id)
  ORDER BY d.document_date DESC NULLS LAST, d.id LIMIT :cand_limit
),
t2 AS (
  SELECT d.id, d.document_date FROM documents d
  WHERE d.fts_is @@ {any_tsq}{doc_where_sql}
    AND NOT EXISTS (SELECT 1 FROM t0 WHERE t0.id = d.id)
    AND NOT EXISTS (SELECT 1 FROM t1 WHERE t1.id = d.id)
  ORDER BY d.document_date DESC NULLS LAST, d.id LIMIT :cand_limit
),
tiers AS (
  SELECT id, 0 AS tier, document_date FROM t0
  UNION ALL SELECT id, 1, document_date FROM t1
  UNION ALL SELECT id, 2, document_date FROM t2
),
cand AS (
  SELECT c.id, {rank_fn}(d.fts_is, {strict_tsq}) AS doc_rank, c.tier
  FROM (SELECT id, tier, document_date FROM tiers
        ORDER BY tier ASC, {cand_cut_order} LIMIT :cand_limit) c
  JOIN documents d ON d.id = c.id
)
```

`{cand_cut_order}` er `document_date DESC NULLS LAST, id` (`ASC` þegar `sort = oldest`); röðunin getur ekki byggt á `doc_rank` af því hann er ekki til fyrr en eftir valið. Innri `ORDER BY` í `t1`/`t2` fylgir sömu átt.

**Breytt 2026-09-28 eftir mælingu**: CASE-útgáfan þurfti að afþjappa `fts_is` fyrir öll skjöl í víðasta menginu (870 ms fyrir 7.927 skjöl, 10–30 s fyrir algeng orð); nú eru þrep 1–2 valin eftir dagsetningu án þess að snerta `fts_is` og `doc_rank` aðeins reiknað fyrir valin ≤ 2.000 skjöl.

Málamiðlun: fyrir mjög algengar lemmur eru þrep-1/2 frambjóðendurnir *nýjustu* treffin, ekki þau hæst skoruðu — skorið er ekki þekkt fyrir valið. Röðun *innan* þreps er óbreytt (`order_sql` raðar síðunni áfram með `h.tier ASC, <breadth_coloc>…`), og þrep 0 er óskert því `t0` er ekki þakskorið.

### `hits_and`/`hits_or` í slökuðum ham — LATERAL á hvern frambjóðanda (2026-09-28)

Sameiginlega formið (`FROM passages p JOIN cand c ON c.id = p.document_id … GROUP BY p.document_id`) er áætlað sem Bitmap Heap Scan á `ix_passage_fts_is` fyrir *alla* (OR-)fyrirspurnina og tengt við `cand` fyrst á eftir. Fyrir algengar lemmur eru það hundruð þúsunda efnisgreina, hver afþjöppuð til að reikna `ts_rank`. Mælt á `krafa dómur skaðabót` (scope `domstolar`, `cand_limit = 2000`): `cand` 0,72 s, `hits_and` 3,75 s, `hits` (and+or) **43,6 s**. Slökun er einmitt tilvikið þar sem OR-fyrirspurnin er víð, svo vinnan verður að afmarkast af frambjóðendunum:

```sql
hits_and AS (
  SELECT c.id AS document_id, x.best_rank, x.match_count, x.best_passage_id, c.doc_rank, c.tier
  FROM cand c
  CROSS JOIN LATERAL (
    SELECT max({rank_fn}(p.fts_is, {strict_tsq})) AS best_rank,
           count(*) AS match_count,
           (array_agg(p.id ORDER BY {rank_fn}(p.fts_is, {strict_tsq}) DESC, p.ordinal))[1] AS best_passage_id
    FROM passages p
    WHERE p.document_id = c.id AND p.fts_is @@ {strict_tsq}
      [AND p.section_kind = ANY(:section_kinds)]
  ) x
  WHERE x.match_count > 0
),
hits_or AS (            -- sama form með {any_tsq}
  … WHERE x.match_count > 0
      AND NOT EXISTS (SELECT 1 FROM hits_and ha WHERE ha.document_id = c.id)
),
hits AS ( … UNION ALL eins og áður )
```

Þannig gengur áætlunin um `ix_passage_doc (document_id, ordinal)` einu sinni fyrir hvern frambjóðanda (`Index Scan using ix_passage_doc … loops = :cand_limit`) í stað bitmap-skönnunar yfir allt safnið. LATERAL-samantektin skilar alltaf einni röð (`count(*) = 0`, hitt NULL, fyrir frambjóðanda án treffs), svo `WHERE x.match_count > 0` heldur merkingu gamla `GROUP BY`. Dálkanöfn og röð þeirra (`document_id, best_rank, match_count, best_passage_id, doc_rank, tier`) eru óbreytt — `hits` sameinar þrepin og allt niðurstreymis les þau eftir nafni. `section_kind`-sían býr **inni í** LATERAL-undirfyrirspurninni, því hún velur hvaða efnisgreinar telja.

**Óslakaða SQL-ið er byte-óbreytt** (gamla `JOIN cand … GROUP BY`-formið); breytingin snertir aðeins `relax is not None`.

### `RELAX_CAND_LIMIT = 300` (`engine/search/relaxation.py`)

Af því slökuð leit gengur um vísinn einu sinni á hvern frambjóðanda er kostnaðurinn línulegur í `:cand_limit`. Slökuð leit fær því sitt eigið, mun lægra þak; óslökuð heldur `PASSAGE_CANDIDATE_DOCS = 2000`. `search_by_passages` les `relaxation.RELAX_CAND_LIMIT` við kall (ekki bundið við innflutning) svo `scripts/eval_search.py --relax-cand-limit N` geti breytt því fyrir mælingu.

Mælt 2026-09-28 (lifandi gagnagrunnur, scope `domstolar`, `WITH <ctes> SELECT count(*) FROM hits`, kalt/heitt):

| fyrirspurn | fyrir (2000, gamalt form) | eftir (300, LATERAL) | eftir (1000, LATERAL) |
|---|---|---|---|
| `krafa dómur skaðabót` (strict 10.502, any 44.461) | **47.696 ms** | **1.064 / 607 ms** | 1.495 / 828 ms |
| `tómlæti verklaun byggingarframkvæmd gjaldþrot` (strict 3, any 5.742) | 1.117 / 65 ms | **158 / 51 ms** | 735 / 174 ms |

Raunleiðin `search_documents(..., scope=["domstolar"], page_size=10)`: versta tilvikið (slökun þvinguð með `RELAX_BELOW = 20000`) 947 / 842 / 1.036 ms, dæmigerð slökuð leit 44 ms. Markmiðið (versta tilvik < 1,5 s kalt, dæmigert < 200 ms) næst.

Óslakað: `tier` er fasti `0` og `WHERE d.fts_is @@ {strict_tsq}` eins og í dag. `hits_and`/`hits_or` bæta við `max(c.tier) AS tier`; `hits` ber það áfram. `order_sql` fær `h.tier ASC` fremst í öllum þremur röðunum (relevance, newest, oldest). `doc_rank` með ströngu fyrirspurninni er 0 fyrir þrep 1–2 (engin samstæða), svo innan þreps ræður efnisgreinaskorið; það er ásættanlegt og mælt.

**Uppfært 2026-09-28** (Task 6b): `total` í slökuðum ham er `count(*) FROM hits` (sjá kaflann um `total`/`strict_total` hér að ofan) — ekki lengur `count(*) FROM documents d WHERE d.fts_is @@ {any_tsq}{doc_where}`, sem taldi doc-level víðustu fyrirspurnina óháð frambjóðendaþakinu. `strict_total` er áfram talan sem ákvörðunin byggði á (þegar reiknuð).

## `search_documents` (queries.py)

Í `keyword`-greininni, eftir að `lemmas` eru til:
1. `kq = build_keyword_queries(lemmas)`; strangi fjöldinn reiknaður (doc-level, með `where`) — þetta er sama talning og `search_by_passages` gerir í dag fyrir `total`, flutt fram fyrir dispatch og send inn sem `strict_total` svo hún sé ekki tvítalin.
2. `relaxed = should_relax(strict_total, kq.n)`.
3. `search_by_passages(..., tsq_fn="to_tsquery", tsq_param="q_strict", or_tsq_param="q_any" (ef n ≥ 2), relax=(any, nminus1) if relaxed else None, strict_total=strict_total)`.
4. `SearchResults` fær `strict_total: int` og `relaxed: bool`; hver niðurstaða fær `match_tier: int` (0 óslakað).

`facet_counts`: sama `build_keyword_queries` + strangi fjöldi með sínum síum + `should_relax` → `where.append("d.fts_is @@ to_tsquery('simple', :q_any)")` í stað ströngu þegar slakað er.

## API

`GET /api/search` svar: `total`, `page`, `page_size`, `results`, **`strict_total`**, **`relaxed`**. Hver niðurstaða: **`match_tier`** (0/1/2). `GET /api/facets` óbreytt að formi; tölurnar fylgja slökuninni.

## Framendi

- `types.ts`: `SearchResponse.strict_total`, `.relaxed`; `SearchResult.match_tier`.
- `ResultsList`: þegar `relaxed` er satt birtist ein lína yfir listanum: „**{strict_total}** skjöl innihalda öll leitarorðin. Sýni einnig **{total − strict_total}** skjöl sem innihalda flest eða sum þeirra.“ (Ef `strict_total` er 0: „Engin skjöl innihalda öll leitarorðin. Sýni skjöl sem innihalda flest eða sum þeirra.“)
- `ResultCard`: merki „flest orðin“ (þrep 1) eða „sum orðin“ (þrep 2) við hlið `anchor`-merkisins; ekkert merki fyrir þrep 0.

## Mæling — `scripts/eval_search.py --relax-sweep`

Keyrir gullsettið fyrir `RELAX_BELOW ∈ {0, 5, 10, 20, 50}` (0 = slökkt) með því að setja `relaxation.RELAX_BELOW` sem einingareigind (sama mynstur og `--rank-sweep`), og prentar töflu: recall@10, MRR, hit@1, núll-treff, p50/p95, og fjölda spurninga þar sem slökun kviknaði. `--relax-cand-limit N` setur `relaxation.RELAX_CAND_LIMIT` fyrir keyrsluna (endurheimt í `finally`) og virkar bæði með sweep og venjulegri keyrslu; gildið er prentað yfir töflunni. Notandinn velur gildið; það er fest sem sjálfgefið með athugasemd um mælinguna.

Væntingar: núll-treff fara úr 78 í nálægt núll; recall@10 hækkar um það sem slökuðu leitirnar finna í efstu tíu; spurningar sem áður fundust breytast ekki þegar `strict_total ≥ RELAX_BELOW`, og geta aðeins batnað eða staðið í stað annars (ströng treff eru áfram fremst).

## Frammistaða

Víðasta fyrirspurnin getur passað við tugþúsundir skjala þegar eitt orðanna er algengt („krafa“ + sjaldgæft orð). Kostnaðurinn er doc-level talning með OR (GIN, ~1 s í versta falli) og `cand` með `ORDER BY tier` yfir stórt mengi áður en `LIMIT :cand_limit` klippir. Mælt í sweep með p50/p95; ef p95 fer yfir ~1,5 s í slökuðum leitum er lækkun `RELAX_BELOW` eða takmörkun á „eitthvert“-þrepi við sjaldgæfustu lemmurnar næsta skref, ekki hluti af þessu spec.

**Uppfært 2026-09-28.** Fyrsta sweep-mælingin (n = 492, K = 10) gaf p95 8,7 s — flöskuhálsinn reyndist `hits`-þrepið, ekki `cand`. Tvær breytingar: LATERAL-samantekt á hvern frambjóðanda og `RELAX_CAND_LIMIT = 300` (sjá kaflann um `build_hits_sql`). Eftir þær er versta tilvikið ~1,0 s og dæmigerð slökuð leit ~45 ms. `--relax-cand-limit N` heldur þakinu mælanlegu: sweep var keyrt bæði með 300 og 1000 og báðar töflur skráðar í plan-skjalið.

## Villumeðferð

| Aðstæður | Hegðun |
|---|---|
| Ein lemma | engin slökun, `relaxed=false`, óbreytt hegðun |
| > 8 lemmur | þrep 1 sleppt; þrep 0 og 2 |
| `RELAX_BELOW = 0` | slökun alltaf slökkt |
| `section_kind` með slökun | sían gildir á efnisgreinar eins og í dag; `total` bundið af þaki |
| nálægðarleit, regex | óbreytt, `relaxed=false`, `match_tier=0` |

## Próf

- `tests/test_relaxation.py`: `build_keyword_queries` fyrir n = 1, 2, 3, 4, 9 (þrep 1 `None`), táknform strengja; `should_relax` með mörkum (`strict_total == RELAX_BELOW` → ósatt), n = 1 → ósatt, threshold 0 → ósatt.
- `tests/test_passage_search.py`: `build_hits_sql(relax=…)` inniheldur `t0`/`t1`/`t2` með `NOT EXISTS`-útilokun, dagsetningarþak á `t1`/`t2` (`ORDER BY d.document_date … LIMIT :cand_limit`), `ORDER BY tier ASC` á sameiningunni og **enga** `CASE WHEN d.fts_is`; `t1` sleppt þegar `nminus1` er `None`; slakað `hits_and`/`hits_or` innihalda `CROSS JOIN LATERAL`, `p.document_id = c.id`, `WHERE x.match_count > 0` og (or) `NOT EXISTS (SELECT 1 FROM hits_and …)`, með `section_kind`-síuna inni í LATERAL-inu; óslakað byte-óbreytt (fasti `0 AS tier`, `WHERE … strict`, `JOIN cand … GROUP BY`); `search_by_passages` bindur `cand_limit = RELAX_CAND_LIMIT` þegar slakað er og `PASSAGE_CANDIDATE_DOCS` annars; `order_sql` byrjar á `h.tier ASC` í öllum sorts.
- `tests/test_search_queries.py`: dispatch reiknar strangan fjölda einu sinni; `relaxed` satt/ósatt eftir `RELAX_BELOW`; facets nota `q_any` þegar slakað.
- `tests/test_api_passages.py`: svarið hefur `strict_total`, `relaxed`, og `match_tier` á niðurstöðum.
- Framendi: `ResultsList.test.tsx` sýnir tilkynninguna aðeins þegar `relaxed`; `ResultCard.test.tsx` sýnir þrepamerki fyrir 1 og 2, ekkert fyrir 0.
- Gullsett: `--relax-sweep` keyrt og taflan skráð í plan-skjalið; sjálfgefið `RELAX_BELOW` valið af notanda.

## Framhald / þekkt takmörk

Skráð 2026-09-28 (Task 6b) þegar `RELAX_BELOW` var fest og `total` breytt:

**(a) Óslakaða leiðin er sá hægi kaflinn núna, ekki sá slakaði.** `krafa dómur skaðabót`
(strangt treff 10.502 skjöl, scope `domstolar`) slakar ekki (`RELAX_BELOW = 10`, langt undir
strangri tölunni) og keyrir samt gamla `JOIN cand … GROUP BY`-formið yfir alla `passages`-töfluna
— mælt 2,8–32 s köldu/heitu (sjá task-2c-report.md). Þetta er sami galli og slökuðu leiðin hafði
fyrir LATERAL-lagfæringuna, en hann snertir *fleiri* fyrirspurnir en slökunin gerði (hverja
óslakaða `keyword`-leit með algengu orði). Ekki lagað hér — dæmt utan gildissviðs þessa spec
(aðeins slökuð leið). Næsta skref væri sama LATERAL-hugmynd fyrir `hits_and`/`hits_or` í
óslökuðum ham, en það þarf eigin mælingu: 2000 frambjóðendur × einn vísisskann hver vinnur ekki
endilega betur en bitmap-skannið þegar strangi tsquery-inn er valkvæmur (fá treff) — aðeins þegar
hann er breiður (mörg treff) er LATERAL-formið augljóslega betra. Sjá einnig gildruna „Slökuð
leit: víðasta fyrirspurnin getur verið dýr" í [09-gildrur](../../wiki/09-gildrur.md), sem nú er
uppfærð með þessum tölum.

**(b) Slökuð niðurstaða er bundin við `strict_total + RELAX_CAND_LIMIT` skjöl,** og `total`
tilkynnir núna nákvæmlega þá tölu (sjá „`total` og `strict_total`" hér að ofan) — ekki lengur
doc-level fjölda víðustu fyrirspurnarinnar, sem gat verið tugþúsundir þótt síðuflettingin gæti
aldrei náð nema broti af þeim. Notandinn getur því ekki flett lengra en það, jafnvel þótt fleiri
skjöl innihaldi eitthvert leitarorðanna — sami háttur og `PASSAGE_CANDIDATE_DOCS`-þakið hefur
alltaf haft á óslakaðri leit (sjá gildru „`keyword`-leit nær aðeins yfir efstu 2000
frambjóðendaskjölin"), bara við lægra þak (300) og eingöngu í slökuðum ham.

**(c) `RELAX_BELOW = 10` valið af notanda 28.09.2026** út frá `--relax-sweep`-töflunni með
`RELAX_CAND_LIMIT = 300` (sjá `RELAX_BELOW`-athugasemdina í `engine/search/relaxation.py` og
„Niðurstöður mælinga" í plan-skjalinu): gæðin (recall@10/MRR/hit@1/núll-treff) eru nákvæmlega
eins frá K=10 og upp úr, svo hærra gildi hefði aðeins fjölgað slökuðum fyrirspurnum án ávinnings,
og p95 vex hægt en jafnt með K (91 ms → 707 ms milli K=0 og K=10 er stökkið sem skiptir máli;
eftir það er ábatinn núll).
