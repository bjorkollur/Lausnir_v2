# Slökuð orðaleit (relaxation) — design spec

**Dagsetning:** 2026-09-28
**Staða:** Hönnun samþykkt munnlega (notandi valdi „slaka líka þegar treff eru fá“), spec til yfirferðar.
**Bakgrunnur:** `docs/superpowers/specs/2026-09-27-passages-design.md` og mælingar í `docs/superpowers/plans/2026-09-27-passages.md` („Niðurstöður mælinga“).

## Samhengi

Orðaleit krefst þess að öll leitarorðin finnist í skjalinu (`plainto_tsquery` AND-ar lemmurnar). Á 492 spurninga gullsettinu gefa 78 spurningar (16 %) núll treff í bæði gömlu og nýju leiðinni, og margar til viðbótar gefa örfá. Notandinn fær tóma síðu án skýringar og engar vísbendingar um hvaða orð olli því. Þetta er stærsta mælda gæðatækifærið eftir efnisgreinaskiptin.

Efnisgreinaleitin er þegar tveggja þrepa: `cand` (skjöl sem uppfylla strönga fyrirspurn, þak 2.000) og efnisgreinaþrep með `hits_and` (öll orð í sömu efnisgrein) og `hits_or` (OR-varaleið fyrir frambjóðendur án samstæðs treffs). Slökunin er **eingöngu breyting á forsíunni í `cand`** og röðun eftir þrepum; efnisgreinaþrepið er óbreytt.

## Ákvarðanir

1. **Kveikja.** Aðeins `keyword`-hamur með tvær eða fleiri lemmur. Slökun kviknar þegar strangi fjöldinn (skjöl með öll orðin, með sömu síum og leitin) er **undir `RELAX_BELOW`**. Gildið er valið með mælingu á gullsettinu (`--relax-sweep`), ekki fyrirfram; bráðabirgðagildi 10. `RELAX_BELOW = 0` slekkur á slökun.
2. **Þrjú þrep, ekki tvö.** Í slökuðum ham fá skjöl þrep: `0` = öll orðin, `1` = öll nema eitt, `2` = eitthvert orðanna. Ströng treff koma alltaf fyrst, síðan þrep 1, svo þrep 2, og innan þreps gildir venjuleg röðun (`breadth_coloc` eða dagsetning). Þannig er „slaka þegar treff eru fá“ og „slaka þegar ekkert finnst“ sama vélin.
3. **„Öll nema eitt“** er sameining allra samsetninga þar sem eina lemmu vantar, hver samsetning AND-uð: fyrir `a b c` → `(a & b) | (a & c) | (b & c)`; fyrir tvær lemmur er það sama og „eitthvert“. Yfir **8 lemmum** er þrepi 1 sleppt (samsetningafjöldi) og aðeins þrep 0 og 2 notuð.
4. **Efnisgreinaþrepið er óbreytt.** `hits_and` notar áfram strönga fyrirspurn (öll orð í sömu efnisgrein); `hits_or` notar „eitthvert“ fyrir frambjóðendur án samstæðs treffs — sem er nákvæmlega það sem slökuðu skjölin þurfa. Útdráttur fylgir sömu reglu og í dag.
5. **`total` og `strict_total`.** Í slökuðum ham er `total` fjöldi skjala sem uppfylla víðustu fyrirspurnina („eitthvert“) með síum, og `strict_total` fjöldi þeirra sem uppfylla ströngu. Óslakað: `strict_total == total`. Með `section_kind`-síu eru báðar tölur takmarkaðar af frambjóðendaþakinu eins og í dag.
6. **Facets fylgja.** `facet_counts` tekur sömu ákvörðun með sínum eigin síum (dagsetningar, án sviðs): ef strangi fjöldinn er undir `RELAX_BELOW` telur hún með víðustu fyrirspurninni. Ákvörðunin er eitt fall sem bæði nota.
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

Lemmur koma úr `lemmatize_query` (aðeins `[a-záéíóúýðþæö]+` tákn, bil á milli), svo strengirnir eru öruggir í `to_tsquery('simple', :param)`. Strangi strengurinn notar `&` í stað `plainto_tsquery` svo öll þrjú séu sama tegund (`to_tsquery`).

## SQL — breytingar á `build_hits_sql`

Nýtt viðfang `relax: tuple[str, str|None] | None` = (`any_tsq`, `nminus1_tsq`) þegar slakað er, annars `None`. Í slökuðum ham:

```sql
cand AS (
  SELECT d.id, {rank_fn}(d.fts_is, {strict_tsq}) AS doc_rank,
         CASE WHEN d.fts_is @@ {strict_tsq} THEN 0
              WHEN d.fts_is @@ {nminus1_tsq} THEN 1      -- sleppt ef nminus1 er None
              ELSE 2 END AS tier
  FROM documents d
  WHERE d.fts_is @@ {any_tsq}{doc_where_sql}
  ORDER BY tier ASC, {cand_order}
  LIMIT :cand_limit
)
```

Óslakað: `tier` er fasti `0` og `WHERE d.fts_is @@ {strict_tsq}` eins og í dag. `hits_and`/`hits_or` bæta við `max(c.tier) AS tier`; `hits` ber það áfram. `order_sql` fær `h.tier ASC` fremst í öllum þremur röðunum (relevance, newest, oldest). `doc_rank` með ströngu fyrirspurninni er 0 fyrir þrep 1–2 (engin samstæða), svo innan þreps ræður efnisgreinaskorið; það er ásættanlegt og mælt.

`total` í slökuðum ham: `count(*) FROM documents d WHERE d.fts_is @@ {any_tsq}{doc_where}`; `strict_total` er talan sem ákvörðunin byggði á (þegar reiknuð).

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

Keyrir gullsettið fyrir `RELAX_BELOW ∈ {0, 5, 10, 20, 50}` (0 = slökkt) með því að setja `relaxation.RELAX_BELOW` sem einingareigind (sama mynstur og `--rank-sweep`), og prentar töflu: recall@10, MRR, hit@1, núll-treff, p50/p95, og fjölda spurninga þar sem slökun kviknaði. Notandinn velur gildið; það er fest sem sjálfgefið með athugasemd um mælinguna.

Væntingar: núll-treff fara úr 78 í nálægt núll; recall@10 hækkar um það sem slökuðu leitirnar finna í efstu tíu; spurningar sem áður fundust breytast ekki þegar `strict_total ≥ RELAX_BELOW`, og geta aðeins batnað eða staðið í stað annars (ströng treff eru áfram fremst).

## Frammistaða

Víðasta fyrirspurnin getur passað við tugþúsundir skjala þegar eitt orðanna er algengt („krafa“ + sjaldgæft orð). Kostnaðurinn er doc-level talning með OR (GIN, ~1 s í versta falli) og `cand` með `ORDER BY tier` yfir stórt mengi áður en `LIMIT 2000` klippir. Mælt í sweep með p50/p95; ef p95 fer yfir ~1,5 s í slökuðum leitum er lækkun `RELAX_BELOW` eða takmörkun á „eitthvert“-þrepi við sjaldgæfustu lemmurnar næsta skref, ekki hluti af þessu spec.

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
- `tests/test_passage_search.py`: `build_hits_sql(relax=…)` inniheldur `CASE WHEN d.fts_is @@` og `ORDER BY tier ASC`, `WHERE d.fts_is @@ {any}`; óslakað óbreytt (engin `tier`-CASE, `WHERE … strict`); `order_sql` byrjar á `h.tier ASC` í öllum sorts.
- `tests/test_search_queries.py`: dispatch reiknar strangan fjölda einu sinni; `relaxed` satt/ósatt eftir `RELAX_BELOW`; facets nota `q_any` þegar slakað.
- `tests/test_api_passages.py`: svarið hefur `strict_total`, `relaxed`, og `match_tier` á niðurstöðum.
- Framendi: `ResultsList.test.tsx` sýnir tilkynninguna aðeins þegar `relaxed`; `ResultCard.test.tsx` sýnir þrepamerki fyrir 1 og 2, ekkert fyrir 0.
- Gullsett: `--relax-sweep` keyrt og taflan skráð í plan-skjalið; sjálfgefið `RELAX_BELOW` valið af notanda.
