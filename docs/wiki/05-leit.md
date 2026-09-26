# 05 — Leit

← [Wiki-forsíða](README.md)

Öll leitarrökfræði er í `engine/search/queries.py` (829 línur, hrátt SQL — ekki ORM, vegna `ts_rank`/`ts_headline`/trigram-krafna).

## Leitarhamir — 7 talsins

`VALID_MODES = {keyword, exact, prefix, substring, any, proximity, regex}`

| Hamur | Útfærsla | Vísir | Röðun eftir vægi? |
|---|---|---|---|
| `keyword` | `fts_is @@ to_tsquery('simple', <lemmað>)` | `ix_doc_fts_is` (GIN) | ✅ `ts_rank` |
| `proximity` | `fts_is @@` með tvíátta `<N>` samsetningu | `ix_doc_fts_is` (GIN) | ✅ `ts_rank` |
| `exact` | regex `\m{orð}\M` | trigram | ❌ → dagsetning |
| `prefix` | regex `\m{orð}` | trigram | ❌ |
| `substring` | regex `{orð}` | trigram | ❌ |
| `any` | regex `(orð1\|orð2\|…)` | trigram | ❌ |
| `regex` | notandinn skrifar mynstrið sjálfur | trigram | ❌ |

Ef `sort=relevance` er valið í ham án FTS-vægis fellur það sjálfkrafa í `newest` (`_order_clause`).

### `keyword` — íslensk lemmuð leit

Aðal-hamurinn. Fyrirspurnin er lemmuð með BÍN (`processors/lemmatizer.py`, pakkinn `islenska`) áður en hún fer í `to_tsquery`. Þannig finnur *„gæsluvarðhald"* líka *„gæsluvarðhaldi"*, *„gæsluvarðhaldsins"*.

Orð sem BÍN þekkir ekki (málsnúmer, sérnöfn) fara óbreytt í gegn, lágstöfuð.

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

## Chunk-leið fyrir langan texta

```python
CHUNKED_SCOPE_KEYS = {"logfraediritgerdir", "logfraedibaekur", "baekur"}
```

Þegar **allt** leitarsviðið fellur innan þessara heimilda fer `keyword`-leit í gegnum `document_chunks` í stað `documents` (`_search_by_chunks`). Ástæðan: `ts_rank` og `ts_headline` á 1,4M stafa bók gefa ónothæft vægi og hræðilega útdrætti; á 500-orða bút gefa þau rétta niðurstöðu.

Skilyrðið er `all()` — blandað svið (t.d. bækur + Hæstiréttur) fer í venjulegu leiðina.

## Lagaákvæðaleit

Sérstakt `provision` viðfang. Þáttar íslenskar tilvísanir:

```
"2. mgr. 218. gr. laga nr. 19/1940"  →  (lög="19/1940", gr=218, sfx=None, mgr=2)
"218. gr. a. 19/1940"                →  (lög="19/1940", gr=218, sfx="a",  mgr=None)
```

Leitar í `cited_provisions` JSONB-dálknum með GIN-vísinum `ix_doc_cited_provisions` (jsonb_path_ops). Fyllt af `processors/provision_extractor.py`, sem notar tveggja umferða aðferð: skannar afturábak frá hverju laganúmeri og safnar keðju greinaakkera, og samþykkir aðeins fleiri akkeri ef bilið á milli þeirra inniheldur eingöngu tengiorð (*og*, *sbr.*, komma).

Athygli: `_PROVISION_NOISE = {mgr, gr, lag, lög, nr, sbr}` — eftir BÍN-lemmun á tilvísun standa aðeins þessir stofnar eftir (tölur eru strípaðar), svo venjuleg FTS-leit á tilvísun er gagnslaus. Þess vegna er sérstök leið.

## Facets

`facet_counts()` keyrir sömu síu og leitin en `GROUP BY source, verdict_type`, og skilar tölum fyrir hvern hnút í flokkunartrénu. Notað af hliðarstikunni í framendanum.

## Tvö `fts` — hvað er munurinn?

| | `fts` | `fts_is` |
|---|---|---|
| Tegund | **GENERATED ALWAYS** | Venjulegur dálkur |
| Uppfærsla | Sjálfkrafa við hverja skrift | **Handvirk** — `backfill_fts_is.py` |
| Innihald | Hrá orð (`to_tsvector('simple', …)`) | BÍN-lemmuð orð |
| Notkun | Óbeygð/nákvæm leit | **Aðalleitin** |

Þetta er algengasta gildran: nýtt skjal fer inn, `fts` uppfærist sjálfkrafa, en `fts_is` er NULL — og skjalið finnst þá ekki í venjulegri leit. `import_baekur.py` keyrir `backfill_fts_is` sjálfkrafa í lokin einmitt út af þessu; aðrar heimildir gera það gegnum `update_all.py`.

## Merkingarleit (semantic) — ekki byggð

`documents.embedding vector(3072)` er til í skemanu og `pgvector` er uppsett, en **0 af 91.152 skjölum eru með embedding**. Engin vektorleit er útfærð neins staðar í kóðanum. Þetta er ætlað framtíðarverk, ekki bilun.
