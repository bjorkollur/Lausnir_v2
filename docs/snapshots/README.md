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

## `dedupe_other_2026-09-30_manifest.json` — og fulla afritið sem er EKKI hér

130 raðir í 18 heimildum sem var eytt 30.09.2026. Hér er **aðeins samantekt**: heimild, málsnúmer, dagsetning, id, external_id, slóð, skráarnafn, tímastimpill, lengd, `md5` og fjöldi nafnleyndarmerkja. **Enginn meginmálstexti.**

Fulla afritið (allir dálkar, 3,8 MB) liggur á gagnadisknum: `Lausnir_Data/snapshots/dedupe_other_run_2026-09-30.json.gz` — skrifað af keyrslunni sjálfri, sömu 130 id og samantektin hér. Það er **vísvitandi ekki í git**: meðal raðanna eru hælismálsúrskurðir kærunefndar útlendingamála í útgáfum sem eru **verr nafnleyndar** en þær sem halda sér, og þær eiga ekki að fara á fjarlagið. `md5` í samantektinni leyfir samt að staðfesta að afritið á disknum sé óbreytt.

Innihaldið tapast ekki við eyðinguna: eftirlifandinn ber sama texta (≥ 0,995 líkindi á stöðluðum texta), svo afritið ver aðeins **útgáfumun**.

### Hvað var greint

690 flokkar deila lyklinum `(court, case_number, document_date, verdict_type)` þvert á allar heimildir, en aðeins 118 þeirra eru tvítekningar. Flokkun eftir líkindum á stöðluðum texta (bil og markdown-áherslur felld út):

| Band | Flokkar | Hvað það er |
|---|---|---|
| 1,0 (eins) | 75 | tvítekning |
| ≥ 0,995 | 43 | tvítekning |
| 0,90–0,995 | 57 | óljóst — `knhus 17/2015` er 0,57 líkt þótt lengdin sé innan 2 % (aðrir aðilar: „A" á móti „B, C og D") |
| < 0,90 | 423 | **ólík skjöl** undir sama málsnúmeri |
| tóm | 10 | enginn texti |
| NULL í lykli | 82 | lykillinn merkingarlaus (personuvernd 49, yfirskattanefnd 23) |

Mekanisminn er annar en hjá dómstólunum: heimildin sjálf birtir sama úrskurð tvisvar — tvö `newsid`-GUID á stjornarradid.is, WordPress-slug með `-1` hjá hugverk.is, tvö PDF-skráarnöfn, og hjá `personuvernd 2012/983` **sama slóð á báðum röðum** (hrein innflutningstvítekning).

**Slóðin skerúr sjaldan.** Könnun á báðum slóðum allra 118 para: 75 pör þar sem báðar lifa, 42 þar sem báðar eru dauðar, 1 með sömu slóð — **ekkert par þar sem aðeins ein lifir**. Allar `?newsid=`-slóðir stjornarradid skila tómri skel (~52,9 KB, sama og bogus-id), svo Blazor-endurbyggingin braut þær allar, ekki einstök id. Þess vegna er reglan „yngsta heldur sér" og ekki „sú með gildu slóðina".

### Niðurstaða keyrslunnar 30.09.2026

130 raðir eyddar, **16 skrár fjarlægðar og 114 skráarnöfn höfð áfram** — 114 af eyddu röðunum deildu `.md`-skrá með þeirri sem hélt sér, svo vörnin í `dedupe_documents.py` bjargaði þeim skrám.

| Mælikvarði | Fyrir | Eftir |
|---|---|---|
| `documents` | 93.038 | 92.908 (−130) |
| `passages` | 1.781.347 | 1.779.474 (−1.873, CASCADE) |
| `citations` | 60.655 | **60.655 (óbreytt)** |
| `document_links` | 48.111 | **48.111 (óbreytt)** |

Tilvitnanir og tenglar eru óbreyttir af því að þessar heimildir eru hvorki uppruni né skotmark í `citations`/áfrýjunarkeðjunni — `link_malskotsbeidnir.py` þurfti því ekki að keyra (NEXT-skilaboðin í skriftunni eru föst og miðuð við dómstólana).

Eftir keyrslu deila 489 flokkar enn lyklinum en **enginn** fer yfir 0,995-þröskuldinn: það sem eftir stendur eru ólík skjöl undir sama málsnúmeri, ekki tvítekningar.

**Engin skrá tapaðist:** 0 raðir með meginmál vantar `.md`. Þær 1.020 raðir sem eiga enga `.md` (umbodsmadur 895, hugverkastofa 121, samkeppni 4) eru allar með tómt meginmál — `write_markdown` skrifar enga skrá fyrir textalaust skjal, og það var svona fyrir.

## `orphan_sources_2026-10-01.json.gz` — EKKI hér, á gagnadisknum

`Lausnir_Data/snapshots/orphan_sources_2026-10-01.json.gz` geymir allar 14 raðir (allir dálkar nema `fts`, `fts_is`, `embedding`) tveggja heimilda sem voru í `sources`-töflunni en ekki í `engine/config/sources.py`, skráðar 09.06.2026 með HTML-táknum í heitinu (`Innvi&#240;ar&#225;&#240;uneyti&#240;`). `.md`-skrárnar þeirra eru í `Lausnir_Data/snapshots/orphan_sources_2026-10-01_markdown/`.

| Heimild | Raðir | Hvað var gert |
|---|---|---|
| `innvidara` | 12 | Eytt — hver röð var orðrétt eins (`body_text`) og röð í `innvida` með sama málsnúmeri og dagsetningu |
| `atvinnuvegar_ra` | 2 | Flutt í `vidskiptamal`: úrskurðir menningar- og viðskiptaráðuneytisins 30.11. og 20.12.2023 um stjórnvaldssektir ársreikningaskrár. Málsnúmerin voru lögnúmer (`37/1993` = stjórnsýslulög, `3/2006` = lög um ársreikninga) og urðu `URVM-2023-11-30` / `URVM-2023-12-20` eftir dagsetningarreglu heimildarinnar |

Báðar `sources`-raðirnar voru síðan felldar niður.

## `document_links_backup_20260916.sql.gz` — EKKI hér, á gagnadisknum

`Lausnir_Data/snapshots/document_links_backup_20260916.sql.gz` (`pg_dump` af töflunni, 25.310 raðir) — afrit af `document_links` frá 16.09.2026, áður en stefna áfrýjunarbrúna var leiðrétt. Taflan var felld niður 01.10.2026: nær öll pör hennar eru enn tengd, og þau 261 sem ekki eru það voru veikar `court_window`-ágiskanir (meðalöryggi ~0,6) sem endurkeyrsla `link_appeals.py` leysti af með öðrum pörum.
