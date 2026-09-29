# Afrit af gögnum sem heimildin lætur ekki lengur í té

Hér liggja afrit af dálkum sem **ekki er hægt að sækja aftur**. Þetta er ekki varaafrit af grunninum — það er síðasta eintakið af tilteknum gögnum.

## `case_type_2026-09-29.csv.gz`

46.699 raðir: `short_name, external_id, case_number, document_date, case_type`.

| Heimild | Raðir | Endurheimtanlegt? |
|---|---|---|
| heradsdomstolar | 24.093 | Já — `_heradsdomur_case_type()` les tegundina úr forskeyti málsnúmers (`E-`, `S-`, `X-` …) |
| haestirettur | 12.208 | **Nei fyrir ~10.000 skjöl** — sjá neðar |
| landsrettur | 6.171 | Já — island.is flokkar 100 % Landsréttardóma (6.591 = summa fjögurra sía) |
| logfraediritgerdir | 4.227 | Já — `Námsstig` í `raw_api_data` |

**Hvers vegna Hæstiréttur er ekki endurheimtanlegur:** `case_type` kemur hvergi fram á skjölunum sjálfum. `WebVerdictItem` í GraphQL-viðmóti island.is ber ekkert slíkt svið (prófað 29.09.2026: `caseType`, `caseTypes`, `caseCategories`, `caseCategory`, `type`, `category` — öllum hafnað; skemaskoðun er lokuð). Eina leiðin er `caseTypes`-sían á listafyrirspurninni, og **hún nær aðeins aftur til 5. janúar 2016**:

| case_type | Í grunninum frá 2016 | island.is 29.09.2026 | Í grunninum alls |
|---|---|---|---|
| Kært einkamál | 542 | 544 | 4.995 |
| Áfrýjað einkamál | 919 | 928 | 4.502 |
| Kært sakamál | 471 | 504 | 1.110 |
| Áfrýjað sakamál | 222 | 235 | 1.601 |

Ósíaður listi gefur 12.227 Hæstaréttarskjöl en síurnar fjórar samtals 2.211. Gildin í grunninum komu öll frá island.is þegar `backfill_case_type.py` var keyrð (hún giskar aldrei, skrifar aðeins það sem síurnar skila) — heimildin hefur síðan þrengt flokkunina við 2016+.

**Gildra:** dómstólslykillinn í fyrirspurninni er `Landsrettur` **án broddstafa**. `Landsréttur` skilar `total: 0` án villu. `Hæstiréttur` er hins vegar **með** broddstöfum; `Haestirettur` skilar 0. Sjá `_SOURCE_CASE_TYPES` í `scripts/backfill_case_type.py`.

### Endurheimt

```bash
gzip -dc docs/snapshots/case_type_2026-09-29.csv.gz > /tmp/ct.csv
psql -p 5433 -d lausnir_v2 <<'SQL'
CREATE TEMP TABLE ct(short_name text, external_id text, case_number text,
                     document_date date, case_type text);
\copy ct FROM '/tmp/ct.csv' WITH (FORMAT csv, HEADER true)
UPDATE documents d SET case_type = ct.case_type
  FROM ct JOIN sources s ON s.short_name = ct.short_name
 WHERE d.source_id = s.id AND d.external_id = ct.external_id
   AND d.case_type IS DISTINCT FROM ct.case_type;
SQL
```

Afritið liggur á sama diski og grunnurinn; git-fjarlagið er eina eintakið utan hans, svo `git push` er hluti af vörninni.

## `dedupe_2026-09-29.json.gz`

Tíu raðir sem `scripts/dedupe_documents.py` eyddi 29.09.2026 — allir dálkar, þar með `body_text`, `lower_body_text` og `raw_api_data`. Þetta voru sömu dómar fluttir inn tvisvar: island.is endurbirtir dóm undir nýju GUID-i, upsertið keyrir á `(source_id, external_id)`, og nýja GUID-ið verður ný röð.

Fimm af tíu GUID-um sem eyddust eru **dauð hjá heimildinni** (HTTP 404 á `_next/data`-slóðinni), svo fyrir þau er þetta afrit eina eintakið sem eftir er. Hin fimm lifa enn — island.is birtir þá dóma undir tveimur GUID-um.

Reglan var: **yngsti innflutningurinn heldur sér.** Hún er ekki smekksatriði — í öllum fimm pörunum sem heimildin gat skorið úr var eldri röðin dauð og yngri lifandi. Níu pör af tíu höfðu bætaeins meginmál; Lrd. 353/2022 var 16 bætum ólíkt af 40.706 og yngri röðin bar 15 bætum meiri lykilorð.

| Heimild | Mál |
|---|---|
| landsrettur | 249/2019, 262/2019, 353/2022, 418/2019, 419/2019, 562/2026, 564/2019, 592/2026 |
| heradsdomstolar | S-1300/2026, S-3991/2026 |

Tvær `leyfisbeidni_um`-tengingar (frá málskotsbeiðnum 2024-92 og 2020-118) hengu aðeins á eyddu röðunum; `link_malskotsbeidnir.py` var keyrð á eftir og festi þær á eftirlifendurna.
