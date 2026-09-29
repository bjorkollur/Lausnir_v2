# 05 — Leit

← [Wiki-forsíða](README.md)

Öll leitarrökfræði er í `engine/search/queries.py` (hrátt SQL — ekki ORM, vegna `ts_rank`/`ts_headline`/trigram-krafna), með `keyword`/`proximity` framkvæmd í `engine/search/passage_search.py` yfir `passages`-töfluna.

## Leitarhamir — 7 talsins

`VALID_MODES = {keyword, exact, prefix, substring, any, proximity, regex}`

| Hamur | Útfærsla | Vísir | Röðun eftir vægi? |
|---|---|---|---|
| `keyword` | `passages.fts_is @@ to_tsquery('simple', <lemmað>)` | `ix_passage_fts_is` (GIN) | ✅ `ts_rank` |
| `proximity` | `passages.fts_is @@` með tvíátta `<N>` samsetningu | `ix_passage_fts_is` (GIN) | ✅ `ts_rank` |
| `exact` | regex `\m{orð}\M` | trigram | ❌ → dagsetning |
| `prefix` | regex `\m{orð}` | trigram | ❌ |
| `substring` | regex `{orð}` | trigram | ❌ |
| `any` | regex `(orð1\|orð2\|…)` | trigram | ❌ |
| `regex` | notandinn skrifar mynstrið sjálfur | trigram | ❌ |

Ef `sort=relevance` er valið í ham án FTS-vægis fellur það sjálfkrafa í `newest` (`_order_clause`).

### `keyword` — íslensk lemmuð leit

Aðal-hamurinn. Fyrirspurnin er lemmuð með BÍN (`processors/lemmatizer.py`, pakkinn `islenska`) áður en hún fer í `to_tsquery`. Þannig finnur *„gæsluvarðhald"* líka *„gæsluvarðhaldi"*, *„gæsluvarðhaldsins"*.

Orð sem BÍN þekkir ekki (málsnúmer, sérnöfn) fara óbreytt í gegn, lágstöfuð.

Leitin sjálf keyrir í tveimur þrepum í `passage_search.search_by_passages` — sjá „Efnisgreinaleit" hér að neðan.

### `proximity` — orð nálægt hvert öðru

Gildra sem búið er að leysa: **PostgreSQL `<N>` þýðir NÁKVÆMLEGA N stöðum í burtu, ekki „innan N"**. Því er byggð tvíátta sameining fyrir hvert par:

```
(A <1> B) | (B <1> A) | (A <2> B) | (B <2> A) | … | (A <N> B) | (B <N> A)
```

og öll pör AND-uð saman.

### `regex` — mynstursleit

Regex-svæði (`REGEX_COLUMNS`): `body_text`, `summary`, `case_number`, `lower_body_text`, `parties`, `keywords`.
Sjálfgefið: `["body_text", "lower_body_text"]`.

**Mikilvæg frammistöðuregla** (skjalfest í kóðanum): fyrir trigram-vísuðu dálkana verður SQL-segðin að vera **beri dálkurinn**. `coalesce(body_text, '')` eyðileggur vísinn og þvingar fram seq-scan yfir 1,7 GB af texta. NULL með `~*` skilar NULL (útilokað) sem er einmitt rétta hegðunin, svo `coalesce` er óþarft hvort eð er.

Regex-fyrirspurnir eru varðar með `SET LOCAL statement_timeout = '10s'` (`REGEX_TIMEOUT_MS`) gegn hörmulegu bakspori. Ógilt mynstur → `SearchError` → HTTP 400.

Útdrættir (snippets) fyrir regex eru byggðir Python-megin (`_regex_snippet`), aðeins fyrir raðir síðunnar, og aðeins fyrstu 100.000 stafir meginmáls eru sóttir (`_REGEX_SNIPPET_SCAN`).

## Efnisgreinaleit (`passages`)

`keyword` og `proximity` leita alltaf í `passages`, aldrei í heilu `documents.body_text` — sjá `engine/search/passage_search.py`. Ástæðan er sú sama og gamla chunk-leiðin (felld í Task 12) leysti: `ts_rank`/`ts_headline` á 1,4M stafa bók gefa ónothæft vægi og hræðilega útdrætti. `passages` leysir þetta almennt fyrir öll 93.000 skjölin, ekki bara langar heimildir.

**Bútun** (`processors/segmenter.py`): skjal er skipt á `##`-fyrirsögnum og númeruðum málsgreinum, 250 orð að markmiði (hámark 400); flatur texti án skila er klofinn á setningaskilum. Þrjú lög — `summary`, `body`, `lower_body` — hvert með sínar efnisgreinar, engin skörun. Hver efnisgrein fær `section_kind` (`processors/sections.py`: `reifun`, `malsmedferd`, `malsatvik`, `malsastaedur`, `nidurstada`, `domsord`, `annad`) og tilvitnanlegt, afleitt `anchor` (`passage_index.passage_anchor()`, aldrei geymt): `"Reifun"` fyrir samantektarlagið, `"12. mgr."` / `"12.–14. mgr."` þegar málsgreinabil er þekkt, annars `section_path` eða `"hluti N"`.

**`section_kind`-sía**: `section_kind` viðfangið í `/api/search` þrengir leitina við tilteknar tegundir efnisgreina (t.d. aðeins `nidurstada`), gagnlegt þegar spurningin snýst um niðurstöðu frekar en málsatvik.

**Fyrirspurnin er tveggja þrepa** (`build_hits_sql` í `passage_search.py`):
1. **Forsía á `documents.fts_is`** — `ts_rank` velur bestu allt að `PASSAGE_CANDIDATE_DOCS` (2000) skjölin sem innihalda öll leitarorðin, áður en efnisgreinar eru skoðaðar yfirhöfuð. Fyrir langflestar fyrirspurnir (sem passa í ≤2000 skjöl) er `total` nákvæm; fyrir mjög algeng stök orð sem passa í fleiri skjöl nær síðufletting aðeins yfir efstu 2000 (sjá gildru að neðan).
2. **Efnisgreinaleit meðal frambjóðendaskjalanna** — nákvæm samtenging (`hits_and`, öll orð í sömu efnisgrein) og, fyrir `keyword` ham, **OR-vararleið** (`hits_or`) sem finnur skjöl þar sem leitarorðin lifa í *ólíkum* efnisgreinum (t.d. eitt í `malsatvik`, annað í `nidurstada`) — annars myndi tveggja orða fyrirspurn missa af skjölum þar sem orðin eru sannarlega bæði til staðar en aldrei í sömu efnisgrein.

**Röðun** (sjálfgefið `ts_rank` / `breadth_coloc`, stillt í `PASSAGE_RANK_FN`/`PASSAGE_RANK_STRATEGY`): `doc_rank + best_rank·ln(1+match_count) + 0,25 ef samstaðsett`, svo dagsetning. Mælt á 492 spurninga gullsetti (`scripts/eval_search.py --rank-sweep`):

| impl | n | recall@10 | MRR | hit@1 | p50 ms | p95 ms | 0-hit |
|---|---|---|---|---|---|---|---|
| documents (fyrri leið) | 492 | 0.309 | 0.186 | 0.140 | 20 | 63 | 81 |
| passages (ts_rank / breadth_coloc) | 492 | 0.319 | 0.208 | 0.163 | 11 | 149 | 81 |

`total`-merkingin utan hámarksins: þegar `section_kind` er ekki sett kemur `total` beint af GIN-vísinum á `documents.fts_is` (nákvæm, óháð hámarki); með `section_kind`-síu kemur `total` úr efnisgreina-CTE-inu og er þá takmarkað við sömu 2000 frambjóðendaskjölin.

## Slökuð leit (relaxation)

`keyword`-leit með tveimur eða fleiri lemmum getur „slakað" á kröfunni um öll orðin þegar strangt treff er af skornum skammti. Rökfræðin er í `engine/search/relaxation.py`, hrein — engin gagnagrunnsaðgerð, engin DB-tenging.

Forsían (`cand` í `build_hits_sql`) fær þrjú þrep þegar slakað er:

| Þrep | Skilyrði | Athugasemd |
|---|---|---|
| 0 | öll orðin (`a & b & c`) | ströng fyrirspurn, óbreytt frá óslökuðu leitinni |
| 1 | öll nema eitt | sameining allra samsetninga, `(a&b)\|(a&c)\|(b&c)`; sleppt yfir `MAX_NMINUS1_LEMMAS` (8) lemmum, og einnig fyrir nákvæmlega tvær lemmur (þrep 1 væri þá orðrétt sama fyrirspurn og þrep 2 — breytt 28.09.2026 eftir lokayfirferð, sjá M5) |
| 2 | eitthvert orðanna (`a\|b\|c`) | víðasta fyrirspurnin |

**Kveikjan** (`should_relax()`): slökun kviknar þegar strangi fjöldinn — skjöl sem uppfylla þrep 0, með sömu síum og leitin (`scope`, dagsetningar, …) — er **undir `RELAX_BELOW`**. Þessi ákvörðunartalning er hrein doc-level talning á `where` (leiðrétt 28.09.2026 eftir lokayfirferð: hún tekur **ekki** `section_kind` með, því sú sía er eingöngu efnisgreinastigs og er aldrei hluti af `where`-listanum sem `search_documents` sendir í ákvörðunina — sjá M3 fyrir hvernig `section_kind` engu að síður hefur áhrif á `SearchResults.strict_total`, sem er reiknuð sér, síðar). `RELAX_BELOW` er einingareigind í `relaxation.py`, lesin á kalltíma. **`RELAX_BELOW = 10`, fest 28.09.2026** eftir mælingu á 492 spurninga gullsettinu (`scripts/eval_search.py --relax-sweep`, `RELAX_CAND_LIMIT = 300`):

| K | recall@10 | MRR | hit@1 | núll-treff | p50 ms | p95 ms |
|---|---|---|---|---|---|---|
| 0 | 0,327 | 0,210 | 0,161 | 78 | 23 | 216 |
| 10 | 0,402 | 0,244 | 0,181 | 0 | 91 | 707 |

Gæðin ná hámarki við K=10 (eins og K=20/K=50 á öllum mælikvörðum) — hærra gildi fjölgar bara slökuðum fyrirspurnum án ávinnings. Full tafla (K=0/5/10/20/50) í „Niðurstöður mælinga" í `docs/superpowers/plans/2026-09-28-relaxed-keyword-search.md`. `RELAX_BELOW = 0` slekkur alveg á slökun; ein lemma (`n < 2`) slekkur alltaf á henni, óháð `RELAX_BELOW`.

Slökuð leit fær sitt eigið, lægra frambjóðendaþak: `RELAX_CAND_LIMIT = 300` (óslökuð heldur `PASSAGE_CANDIDATE_DOCS = 2000`). Kandidötum er safnað þrep fyrir þrep beint af GIN-vísinum og samantekt á efnisgreinum keyrir sem `CROSS JOIN LATERAL` á hvern frambjóðanda (í stað `GROUP BY` yfir alla `passages`-töfluna) — sjá `_relaxed_cand_sql`/`_lateral_hits_sql` í `engine/search/passage_search.py`. Síðan 2026-09-28 (perf/strict-lateral) notar óslakaða leiðin sama LATERAL-form, með `cand AS MATERIALIZED` svo áætlunargerðin haldi sig við GIN-vísinn (sjá gildrur). Cap 1000 gaf sömu gæðatölur í mælingunni, bara hægar; 300 er valið.

Ströng treff (þrep 0) koma alltaf fyrst, svo þrep 1, svo þrep 2; innan hvers þreps gildir venjuleg röðun (`breadth_coloc` eða dagsetning eftir `sort`). Efnisgreinaþrepið sjálft er **óbreytt** hvort sem slakað er eða ekki: `hits_and` notar áfram strönga fyrirspurn (öll orð í sömu efnisgrein), `hits_or` notar „eitthvert"-vararleiðina sem er nú þegar til fyrir tveggja+ orða `keyword`-leit — nákvæmlega það sem slökuðu skjölin þurfa.

**`SearchResults`-svæði:**
- `strict_total` — fjöldi skjala sem uppfylla þrep 0 (með sömu síum). **`relaxed == false` ⇒ `strict_total == total`, án undantekninga, fyrir alla hami** (leiðrétt 28.09.2026 eftir lokayfirferð, I1) — ekki bara óslakaða `keyword`-leit, heldur líka `regex`/`exact`/`prefix`/`substring`/`any`/`proximity` og síu-eingöngu vafur. Með `section_kind`-síu (M3, leiðrétt 28.09.2026) er `strict_total` reiknuð úr sömu efnisgreina-bundnu `hits`-CTE og `total` (`count(*) FILTER (WHERE tier = 0)`), ekki lengur úr doc-level talningunni sem ákvörðunin notaði — sú tala hefur enga `section_kind`-síu og gat því verið hærri en `total` um leið og `section_kind` þrengdi niðurstöðurnar frekar.
- `total` — **í slökuðum ham er þetta fjöldi þeirra skjala sem raunverulega er hægt að fletta að** (`count(*) FROM hits`, sömu CTE-ir og niðurstöðurnar), sem er bundið af `RELAX_CAND_LIMIT` — **ekki** doc-level fjöldi skjala sem uppfylla víðustu (þrep 2) fyrirspurnina, sem getur verið tugir þúsunda þótt síðuflettingin geti aldrei náð nema broti af því (breytt 28.09.2026 — sjá gildru í [09-gildrur](09-gildrur.md)). Óslakað er `total` áfram strangi doc-level fjöldinn, sama og áður.
- `relaxed: bool` — hvort slökun kviknaði fyrir þessa fyrirspurn.
- Hver niðurstaða fær `match_tier: int` (0/1/2; alltaf 0 fyrir hina hamina og fyrir óslakaða `keyword`-leit; fyrir nákvæmlega tvær lemmur kemur `1` aldrei fyrir, sjá M5 hér að ofan).

**Facets telja alltaf strangt (breytt 28.09.2026 eftir lokayfirferð, I2).** `facet_counts()` slakar **aldrei** — hún síar alltaf með ströngu fyrirspurninni (`to_tsquery('simple', :q_strict)`), nákvæmlega eins og fyrir slökunarbreytinguna, og gerir enga eigin `should_relax()`-ákvörðun lengur (ein gagnagrunnstalning færri á hverja `keyword`-leit). Ástæðan: hliðarspjaldið sýnir dreifingu skjala sem innihalda **öll** orðin — sömu tölu og niðurstöðuhausinn kallar `strict_total` — á meðan sjálfur listinn að auki sýnir slökuðu (þrep 1/2) treffin. Að telja víðustu fyrirspurnina í hliðarspjaldinu skilaði tugum þúsunda við hliðina á lista með ≤ `RELAX_CAND_LIMIT` niðurstöðum. Sjá [09-gildrur](09-gildrur.md) fyrir gömlu (nú úreltu) gildruna um að facets og leit gætu verið ósammála um slökun.

Utan gildissviðs: stafsetningarleiðrétting, samheiti, slökun í `proximity`/`regex`, og slökun á kröfunni um að orðin standi í sömu efnisgrein.

## Lagaákvæðaleit

Sérstakt `provision` viðfang. Þáttar íslenskar tilvísanir:

```
"2. mgr. 218. gr. laga nr. 19/1940"  →  (lög="19/1940", gr=218, sfx=None, mgr=2)
"218. gr. a. 19/1940"                →  (lög="19/1940", gr=218, sfx="a",  mgr=None)
```

Leitar í `cited_provisions` JSONB-dálknum með GIN-vísinum `ix_doc_cited_provisions` (jsonb_path_ops). Fyllt af `processors/provision_extractor.py`, sem notar tveggja umferða aðferð: skannar afturábak frá hverju laganúmeri og safnar keðju greinaakkera, og samþykkir aðeins fleiri akkeri ef bilið á milli þeirra inniheldur eingöngu tengiorð (*og*, *sbr.*, komma).

Athygli: `_PROVISION_NOISE = {mgr, gr, lag, lög, nr, sbr}` — eftir BÍN-lemmun á tilvísun standa aðeins þessir stofnar eftir (tölur eru strípaðar), svo venjuleg FTS-leit á tilvísun er gagnslaus. Þess vegna er sérstök leið.

## Facets

`facet_counts()` keyrir sömu síu og leitin en `GROUP BY source, verdict_type`, og skilar tölum fyrir hvern hnút í flokkunartrénu. Notað af hliðarstikunni í framendanum. Fyrir `keyword`-leit telur hún alltaf ströng treff (`q_strict`), líka þegar leitin sjálf er slökuð — sjá „Slökuð leit" hér að ofan.

## Tvö `fts` — hvað er munurinn?

| | `fts` | `fts_is` |
|---|---|---|
| Tegund | **GENERATED ALWAYS** | Venjulegur dálkur |
| Uppfærsla | Sjálfkrafa við hverja skrift | **Handvirk** — `backfill_fts_is.py` |
| Innihald | Hrá orð (`to_tsvector('simple', …)`) | BÍN-lemmuð orð |
| Notkun | Óbeygð/nákvæm leit | **Aðalleitin** |

Þetta er algengasta gildran: nýtt skjal fer inn, `fts` uppfærist sjálfkrafa, en `fts_is` er NULL — og skjalið finnst þá ekki í venjulegri leit. `import_baekur.py` keyrir `backfill_fts_is` sjálfkrafa í lokin einmitt út af þessu; aðrar heimildir gera það gegnum `update_all.py`.

## Tilvitnanir milli dóma — ótengt fulltextaleitinni

`citations`/`cites` (sjá [02-gagnagrunnur](02-gagnagrunnur.md) og [06-api](06-api.md)) er **ekki** hluti af leitarpípunni hér að ofan — engin af leitarhömunum sjö leitar í `citations`, og tilvitnanir hafa engin áhrif á `fts_is`, `passages` eða röðun leitarniðurstaðna. Eina snertiflöturinn er birtingarlags-liður: leitarniðurstöður fá `cited_by_count` (fjöldi skjala sem vitna í hverja niðurstöðu, skalar-undirspurn í SELECT-listanum — ekki `LEFT JOIN LATERAL` — yfir `ix_link_to_rel`), notaður eingöngu til birtingar á niðurstöðukorti — sjá [07-framendi](07-framendi.md).

Tilvitnun er staðsett í texta með `char_start`/`char_end` í sama textadálki (`summary`/`body_text`/`lower_body_text`) sem `passages` er byggð úr, en tilvitnanataflan geymir **ekki** `passage_id` (efnisgreinar fá nýtt `id` við hverja endurbyggingu). Til að sýna tilvitnun með tilvitnanlegu heimilisfangi (`anchor`, t.d. „12. mgr.") er efnisgreinin fundin við lestur með sömu aðferð og `passage_index.passage_anchor()` notar annars staðar: `char_start`-bil tilvitnunarinnar er flett upp í `passages` fyrir sama `document_id`/`layer`. Sjá „Hvers vegna enginn `passage_id`" í [02-gagnagrunnur](02-gagnagrunnur.md).

## Merkingarleit (semantic) — ekki byggð

`documents.embedding vector(3072)` er til í skemanu og `pgvector` er uppsett, en **0 af 91.152 skjölum eru með embedding**. Engin vektorleit er útfærð neins staðar í kóðanum. Þetta er ætlað framtíðarverk, ekki bilun.
