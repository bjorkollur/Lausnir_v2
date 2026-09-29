# 02 — Gagnagrunnur

← [Wiki-forsíða](README.md)

PostgreSQL, gagnagrunnur `lausnir_v2`, tenging `postgresql+asyncpg://geiri@localhost/lausnir_v2`.
Viðbætur í notkun: **pgvector** (fyrir `embedding`), **pg_trgm** (fyrir regex-leit).

Fimm töflur + `alembic_version`.

## `sources` — 111 raðir

| Dálkur | Tegund | Athugasemd |
|---|---|---|
| `id` | uuid PK | |
| `short_name` | text UNIQUE | `'haestirettur'`, `'lagasafn_01'` — lykill inn í `SOURCE_REGISTRY` |
| `display_name` | text | `'Hæstiréttur'` |
| `base_url` | text | |
| `collector_config` | jsonb | |
| `created_at` | timestamp | |

## `documents` — 91.152 raðir

### LAG 1 — RAW
| Dálkur | Tegund | Athugasemd |
|---|---|---|
| `id` | uuid PK | |
| `source_id` | uuid FK → sources | |
| `external_id` | text | Einkvæmt **innan heimildar** |
| `url` | text | Upprunaslóð |
| `raw_api_data` | jsonb | Óbreytanlegt API-svar |

### LAG 2 — NORM
| Dálkur | Tegund | Athugasemd |
|---|---|---|
| `case_number` | text | **Ekki einkvæmt** — sjá gildrur |
| `document_date` | date | |
| `court` | text | Skammstöfun: `'Hrd.'`, `'Lrd.'`, `'Hérd. Rvk.'` |
| `verdict_type` | text | `'Dómur'`, `'Úrskurður'`, `'Álit'`, `'Bók'`, `'Lög'` … |
| `instance_tier` | smallint | 1=hérað, 2=Landsréttur, 3=Hæstiréttur |
| `case_type` | text | `'Einkamál'`, `'Sakamál'`, `'Stjórnsýslumál'` … |
| `plaintiffs` | jsonb | `[{name, lawyer}]` — endurnýtt sem **höfundar** fyrir bækur/ritgerðir |
| `defendants` | jsonb | `[{name, lawyer}]` |
| `keywords` | jsonb | `[str]` |
| `summary` | text | Reifun; fyrir lagasafn: heiti laga |
| `body_text` | text | Meginmál núverandi dómstóls |
| `lower_body_text` | text | Innfelldur texti lægra dómstigs (NULL ef enginn) |
| `provisions` | jsonb | `[{num, suffix, text, sub:[{num,text}]}]` — **aðeins lagasafn** (915 skjöl) |
| `isbn` | text | **Aðeins `logfraedibaekur`** |
| `publisher` | text | **Aðeins `logfraedibaekur`** |

### Leit
| Dálkur | Tegund | Athugasemd |
|---|---|---|
| `fts` | tsvector | **GENERATED ALWAYS** — `to_tsvector('simple', case_number ‖ court ‖ verdict_type ‖ summary ‖ body_text)`. Uppfærist sjálfkrafa. |
| `fts_is` | tsvector | **Venjulegur dálkur** — BÍN-lemmaður. Uppfærist **EKKI** sjálfkrafa; krefst `backfill_fts_is.py`. 100% fyllt í dag. |
| `embedding` | vector(3072) | Ætlað `text-embedding-3-large`. **0 skjöl fyllt** — merkingarleit er ekki byggð. |
| `cited_provisions` | jsonb | Lagatilvísanir fundnar í texta (83.638 skjöl) |
| `passage_hash` | text | `md5(summary ‖ \x1f ‖ body_text ‖ \x1f ‖ lower_body_text)` frá síðustu `passages`-smíð. NULL eða misræmi = úreltar efnisgreinar (sjá [09-gildrur](09-gildrur.md)) — **ekki trigger**, uppfært af `backfill_passages.py` |
| `citation_hash` | text | `sha256(summary ‖ \x1f ‖ body_text ‖ \x1f ‖ lower_body_text)` (`chr(31)` sem aðskiljari) frá síðustu `citations`-smíð. Sama mynstur og `passage_hash` en sér dálkur, sér hass-fall (sha256, ekki md5) og sér neyslutafla (`citations`, ekki `passages`). NULL eða misræmi = úreltar/vantandi tilvitnanir fyrir skjalið — sjá `STALE_WHERE` í `engine/processors/citation_build.py` og [04-innflutningur](04-innflutningur.md). **Ekki trigger**, uppfært af `scripts/build_citations.py` |

### Umsýsla
| Dálkur | Tegund | Athugasemd |
|---|---|---|
| `verdict_filename` | text | Skráarnafn `.md` (án endingar) |
| `validation_errors` | jsonb | `[{field, message}]` — villur skráðar, aldrei felldar |
| `created_at` / `updated_at` | timestamp | |

**Einkvæmni:** `UNIQUE (source_id, external_id)` (`uq_doc_source_external`) — grunnurinn fyrir allar upsert-aðgerðir.

### Vísar á `documents`

| Vísir | Tegund | Til hvers |
|---|---|---|
| `ix_doc_fts` | GIN | Nákvæm/óbeygð fulltextaleit |
| `ix_doc_fts_is` | GIN | Íslensk (lemmuð) fulltextaleit — aðal-leitarvísir |
| `ix_doc_body_trgm` | GIN (gin_trgm_ops) | Regex á `body_text` |
| `ix_doc_lower_body_trgm` | GIN (gin_trgm_ops) | Regex á `lower_body_text` |
| `ix_doc_summary_trgm` | GIN (gin_trgm_ops) | Regex á `summary` |
| `ix_doc_case_number_trgm` | GIN (gin_trgm_ops) | Regex á `case_number` |
| `ix_doc_cited_provisions` | GIN (jsonb_path_ops) | Lagaákvæðaleit |
| `ix_doc_source_date` | btree (source_id, document_date) | Sviðs- + dagsetningarsíun |
| `ix_doc_case_number`, `ix_doc_court`, `ix_doc_date`, `ix_doc_source_id`, `ix_doc_verdict_type`, `ix_doc_instance_tier`, `ix_doc_case_type` | btree | Facets og síur |

**Mikilvægt:** trigram-vísarnir eru **ekki** í `models.py` heldur búnir til af `scripts/setup_search_indexes.py`, því þeir krefjast `pg_trgm` viðbótarinnar við DDL-tíma sem `create_all()` setur ekki upp. `ix_doc_cited_provisions` kemur frá `scripts/setup_provision_index.py`.

## `passages` — 1.769.259 raðir

Hver skjal er skipt í tilvitnanlegar efnisgreinar (spec `2026-09-27-passages-design.md`), byggt af `processors/segmenter.py` (bútar á `##`-fyrirsögnum og númeruðum málsgreinum, 250 orð að markmiði, hámark 400; flatur texti klofinn á setningaskilum) og flokkað eftir `processors/sections.py`. Þrjú lög: `summary` / `body` / `lower_body`, hvert vísandi í `char_start`/`char_end` upprunadálksins. Engin skörun (ólíkt gamla `document_chunks`) — hver efnisgrein er sjálfstæð tilvitnun með `anchor`.

| Layer | Fjöldi |
|---|---|
| `body` | 1.409.465 |
| `lower_body` | 268.039 |
| `summary` | 91.755 |

| Dálkur | Tegund | Athugasemd |
|---|---|---|
| `id` | uuid PK | |
| `document_id` | uuid FK → documents **ON DELETE CASCADE** | |
| `ordinal` | integer | Röð innan skjals, yfir öll þrjú lög |
| `layer` | text | `'summary'` \| `'body'` \| `'lower_body'` |
| `section_path` | text | Texti næstu fyrirsagnar fyrir ofan, t.d. `"Niðurstaða"`, `"III"`, `"Dómsorð"`, eða fyrir lagasafn `"12. gr"`; NULL ef engin fyrirsögn |
| `section_kind` | text | Flokkur frá `processors/sections.py`: `reifun`, `malsmedferd`, `malsatvik`, `malsastaedur`, `nidurstada`, `domsord`, `annad` |
| `para_from` / `para_to` | smallint | Málsgreinabil innan lags (NULL fyrir flatan texta) |
| `char_start` / `char_end` | integer | Staðsetning í upprunadálki lagsins |
| `text` | text | Efnisgreinartextinn sjálfur |
| `word_count` | smallint | |
| `fts_is` | tsvector | BÍN-lemmað, sama aðferð og `documents.fts_is` |

`UNIQUE (document_id, ordinal)`.

### Vísar á `passages`

| Vísir | Tegund | Til hvers |
|---|---|---|
| `ix_passage_doc` | btree (document_id, ordinal) | Sækja allar efnisgreinar eins skjals í röð |
| `ix_passage_fts_is` | GIN | Aðal-leitarvísir fyrir `keyword`/`proximity` |
| `ix_passage_section_kind` | btree | `section_kind`-sían |

Fyllt/uppfært af `scripts/backfill_passages.py --source X` (eða `update_all.py`, sem keyrir það sjálfkrafa á öll skjöl með úreltan/vantandi `documents.passage_hash`).

## `document_links` — ≈ 48.113 raðir (25.245 áfrýjun/málskotsbeiðni + 22.868 `cites`)

Öll tengsl milli skjala — áfrýjunarkeðjan, málskotsbeiðnir **og** tilvitnanir — búa í einni töflu, aðgreind með `relation`.

| Dálkur | Tegund | Athugasemd |
|---|---|---|
| `id` | uuid PK | |
| `from_doc_id` | uuid FK CASCADE | Sjá „stefna" fyrir hverja `relation` hér að neðan |
| `to_doc_id` | uuid FK CASCADE | |
| `relation` | text | `appealed_to` \| `appealed_from` \| `leyfisbeidni_um` \| `leiddi_til_doms` \| `cites` |
| `confidence` | float | Samanlagt líkindaskor |
| `method` | text | `casenum` \| `court_date` \| `court_window` (áfrýjun) — `citation` (`cites`) |

`UNIQUE (from_doc_id, to_doc_id, relation)`. Vísir `ix_link_to_rel (to_doc_id, relation)` — flýtir „hvað vísar á þetta skjal með þessum relation" (t.d. `cited_by`-talning, „vitnað í þennan dóm").

### Öll `relation`-gildi

| `relation` | Stefna | Einátta eða parað? | Byggt af |
|---|---|---|---|
| `appealed_to` | Lægra dómstig → hærra dómstig | **Parað** við `appealed_from` — sama tengsl, ein röð í hvora átt | `scripts/link_appeals.py` |
| `appealed_from` | Hærra dómstig → lægra dómstig | **Parað** við `appealed_to` (sömu tvö skjöl, gagnstæð röð) | `scripts/link_appeals.py` |
| `leyfisbeidni_um` | Málskotsbeiðni → sá dómur/úrskurður sem beðið er um áfrýjunarleyfi fyrir | Einátta | Málskotsbeiðna-tenging (sjá `project_malskotsbeidnir_links`) |
| `leiddi_til_doms` | Málskotsbeiðni sem var samþykkt → dómurinn sem leyfið leiddi til | Einátta | Sama tenging |
| `cites` | Vitnandi skjal → vitnað skjal | **Einátta** — engin gagnstæð röð er skrifuð | `scripts/build_citations.py` (`engine/processors/citation_build.py`) |

**`appealed_to`/`appealed_from` eru mirrored pair**: sama áfrýjunartengsl milli tveggja skjala er alltaf skrifað sem tvær raðir, ein í hvora átt (`from`↔`to` víxlað, `relation` víxlað) — `check_link_orientation.py` ver þetta (sjá „Vantar spegilröð" þar). `leyfisbeidni_um`/`leiddi_til_doms`/`cites` eru öll **einátta**: aðeins ein röð er skrifuð, engin gagnstæð er ætluð né athuguð.

**`cites` er ekki hluti af áfrýjunarkeðjunni** þótt hún búi í sömu töflu — hún kemur frá `engine/processors/citations.py`/`citation_resolver.py` (sjá [09-gildrur](09-gildrur.md)), er byggð af `scripts/build_citations.py`, aðeins úr `summary`/`body` lögunum (aldrei `lower_body` — sjá `citations`-töfluna hér að neðan), og `method` er alltaf `'citation'`. Ein röð á hvert (from, to)-par þótt vitnað sé í sama skjal oftar en einu sinni í textanum; `confidence` er þá hæsta gildið meðal þeirra tilvitnana sem mynda brúnina. Ein `cites`-brún getur fallið saman við eitt af áfrýjunar-/málskotstengslunum að ofan (Hæstiréttur sem ræðir Landsréttardóminn sem áfrýjað var) — báðar raðirnar eru skrifaðar, birtingarlagið merkir slíkt par `also_appeal: true` (sjá [06-api](06-api.md)/[07-framendi](07-framendi.md)) svo notandinn sér það ekki tvítekið.

`check_link_orientation.py` hefur fjórar athuganir: þrjár upprunalegu (röng stefna / báðar áttir bera relation / vantar spegilröð) ná aðeins yfir `appealed_to`/`appealed_from`; sú fjórða er sértæk fyrir `cites` — engin `cites`-brún má vísa á skjal með síðari `document_date` en vitnandi skjalið (sjá [09-gildrur](09-gildrur.md) fyrir opið atriði um fjögur eldri, ótengd tilvik af rangri stefnu í áfrýjunarpörum).

Keðjan Hérd → Lrd → Hrd er rakin með því að fylgja `appealed_to`/`appealed_from`-brúnum; `instance_tier` aðgreinir þrepin. Byggt af `scripts/link_appeals.py` með því að para innfelldan `lower_body_text` hærra dómstigs við `body_text` þess lægra.

## `citations` — 60.669 raðir (alembic 0004, 2026-09-29)

Hver rað er **ein tilvísun** í texta á aðra úrlausn — fundin af `engine/processors/citations.py` (hreinn textaútdráttur, engin gagnagrunnstenging) og leyst af `engine/processors/citation_resolver.py` gegn skjölum dómstólanna sjö (Hæstiréttur, Landsréttur, héraðsdómstólar, Félagsdómur, Landsdómur, Endurupptökudómur, málskotsbeiðnir). Leystar tilvitnanir mynda að auki `cites`-brýr í `document_links` (sjá að ofan) — `citations` er því **stærra og nákvæmara** en `document_links`: hún geymir líka óleystar/tvíræðar tilvitnanir og nákvæma staðsetningu í texta, sem `cites` ein brú á milli tveggja skjala ekki getur.

| Dálkur | Tegund | Athugasemd |
|---|---|---|
| `id` | uuid PK | |
| `from_doc_id` | uuid FK documents CASCADE | Vitnandi skjal |
| `layer` | text | `summary` \| `body` \| `lower_body` — textalagið sem tilvitnunin fannst í (sama flokkun og `passages.layer`) |
| `char_start` / `char_end` | integer | Bil **málsnúmersins** í því lagi. Í upptalningu (`"máli nr. 116/1999, 274/1999 og 3/2000"`) fær hvert númer eigin röð með eigin `char_start` |
| `raw_text` | text | Textabúturinn frá dómstólsorðinu (eða skammstöfuninni) að enda málsnúmersins, mest 240 stafir |
| `target_court` | text | Nákvæmlega eins og `documents.court`: `Hrd.`, `Lrd.`, `Hérd.` (staður ónefndur), átta staðbundnu `Hérd. …` gildin, `Féld.`, `Ld.`, `Eud.`, eða `Hrd. málsk.` fyrir málskotsbeiðnaform |
| `target_case_number` | text | Staðlað með `norm_case_number` (sjá [09-gildrur](09-gildrur.md)); NULL fyrir dómasafnsform (`form='reporter'`, t.d. „Hrd. 1983/1538") |
| `target_date` | date | Dagsetning nefnd í setningunni, sé hún til (§5.5 í hönnunarskjalinu) |
| `target_verdict` | text | `Dómur` \| `Úrskurður` \| `Ákvörðun` \| NULL |
| `to_doc_id` | uuid FK documents SET NULL | Leyst skjal — NULL nema `status = 'resolved'` (`self`-raðir hafa líka NULL: skjalið vitnar í sjálft sig og engin brú er skrifuð) |
| `status` | text | `resolved` \| `ambiguous` \| `unresolved` \| `self` \| `pre_coverage` |
| `method` | text | `casenum_date` \| `casenum_verdict` \| `casenum_unique` \| NULL |
| `confidence` | float | `1.0` / `0.9` / `0.8` / NULL |
| `created_at` | timestamp without time zone | `now()` — húsreglan (alembic 0004), eins og `created_at` á öðrum töflum |

`UNIQUE (from_doc_id, layer, char_start)`.

### Vísar á `citations`

| Vísir | Tegund | Til hvers |
|---|---|---|
| `ix_cit_from` | btree (from_doc_id, layer, char_start) | Sækja allar tilvitnanir eins skjals í röð |
| `ix_cit_to` | btree (to_doc_id) WHERE to_doc_id IS NOT NULL | „Hvað vitnar í þetta skjal" |
| `ix_cit_target` | btree (target_court, target_case_number) | Greining/endurleysing eftir marki, óháð því hvort það leystist |
| `ix_cit_status` | btree (status) | Samantektir og `--relink-unresolved` |

**Hvers vegna enginn `passage_id`:** efnisgreinar (`passages`) eru endurbyggðar með eyðingu-og-innsetningu (`backfill_passages.py`/`rebuild_passages()`) og fá **nýtt** `id` í hvert sinn — ef `citations` geymdi `passage_id` yrði hann þögult ógildur (eða vísaði á ranga efnisgrein) við hverja slíka endurbyggingu, án nokkurrar villu. Efnisgreinin er þess í stað fundin **við lestur** út frá `char_start`:

```sql
SELECT id, ordinal FROM passages
WHERE document_id = :id AND layer = :layer AND char_start <= :pos
ORDER BY char_start DESC LIMIT 1
```

svo tilvitnun og efnisgrein eru alltaf í samræmi, óháð því hvenær hvor taflan var síðast endurbyggð.

## Staða gagna (28.07.2026)

| Mæling | Tala |
|---|---|
| Skjöl alls | 91.152 |
| — þar af lagasafn | 915 (48 kaflar) |
| — þar af annað | 90.237 (63 heimildir) |
| Heimildir án skjala | 0 |
| `fts_is` fyllt | 91.152 (100%) |
| `embedding` fyllt | 0 |
| `cited_provisions` fyllt | 83.638 |
| `provisions` fyllt | 915 (aðeins lagasafn) |
| Með staðfestingarvillur | 25.546 |

### Sundurliðun staðfestingarvillna

| Svið | Fjöldi |
|---|---|
| `keywords` | 14.384 |
| `document_date` | 6.885 |
| `case_number` | 4.733 |
| `body_text` | 3.502 |
| `defendants` | 1.573 |
| `court` | 640 |
| `verdict_type` | 11 |
| `detail_fetch` | 6 |
| `lower_body_text` | 5 |
| `plaintiffs` | 2 |

Flestar „villurnar" eru `keywords: Missing` sem er væntanlegt fyrir heimildir sem birta engin lykilorð — ekki raunveruleg bilun. Sjá [09-gildrur](09-gildrur.md) um `validation_errors` gagnatýpuna.

## Staða tilvitnana (29.09.2026, keyrsla #2)

44.513 skjöl unnin (öll sjö dómstólaheimildir með texta), 60.669 `citations`-raðir, 22.868 `cites`-brýr, 0 villur, 409 s með 8 verkferlum. Sjá „Niðurstöður keyrslu" í `docs/superpowers/plans/2026-09-29-citations.md` fyrir fulla sundurliðun eftir stöðu og lagi.
