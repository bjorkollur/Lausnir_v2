# 02 — Gagnagrunnur

← [Wiki-forsíða](README.md)

PostgreSQL, gagnagrunnur `lausnir_v2`, tenging `postgresql+asyncpg://geiri@localhost/lausnir_v2`.
Viðbætur í notkun: **pgvector** (fyrir `embedding`), **pg_trgm** (fyrir regex-leit).

Fjórar töflur + `alembic_version`.

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

## `document_links` — 23.824 raðir

Áfrýjunarkeðjan. Stefnan er **alltaf lægra → hærra dómstig**.

| Dálkur | Tegund | Athugasemd |
|---|---|---|
| `id` | uuid PK | |
| `from_doc_id` | uuid FK CASCADE | Lægra dómstig |
| `to_doc_id` | uuid FK CASCADE | Hærra dómstig |
| `relation` | text | `'appealed_to'` |
| `confidence` | float | Samanlagt líkindaskor |
| `method` | text | `casenum` \| `court_date` \| `court_window` |

`UNIQUE (from_doc_id, to_doc_id, relation)`.

Keðjan Hérd → Lrd → Hrd er rakin með því að fylgja brúnum; `instance_tier` aðgreinir þrepin. Byggt af `scripts/link_appeals.py` með því að para innfelldan `lower_body_text` hærra dómstigs við `body_text` þess lægra.

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
