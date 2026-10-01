# 09 — Gildrur og staða

← [Wiki-forsíða](README.md)

Þekktar gildrur, ósamræmi og ólokið verk. Staðfest 28.07.2026.

## Gildrur í gögnum og kóða

### `case_number` er EKKI einkvæmt
Notaðu `.scalars().all()`, **ekki** `scalar_one_or_none()`, þegar þú spyrð eftir málsnúmeri. Sama málsnúmer kemur fyrir í mörgum dómstólum og árum.

### `fts_is` uppfærist ekki sjálfkrafa
Ólíkt `fts` (sem er `GENERATED ALWAYS`) er `fts_is` venjulegur dálkur sem krefst `backfill_fts_is.py`. Nýtt skjal án þess finnst **ekki** í venjulegri leit. Sjá [05-leit](05-leit.md).

### `passage_hash` er ekki trigger
Ólíkt `fts`/`fts_is` er `passages`-taflan aldrei uppfærð sjálfkrafa við skrift á `documents`. `documents.passage_hash` geymir `md5()` af textalögunum eins og þau voru við síðustu `passages`-smíð; NULL eða misræmi þýðir úreltar/vantandi efnisgreinar fyrir það skjal. `scripts/backfill_passages.py` (keyrt sjálfkrafa af `update_all.py` sem `passages_refresh`-þrepið) ber saman og endursmíðar aðeins það sem er úrelt. Nýtt skjal án þessa finnst **ekki** í `keyword`/`proximity` leit — sama gildruflokkur og `fts_is`.

### `documents.fts_is` og `passages` verða að vera endurbyggð saman
`rebuild_passages()` (kallað af `backfill_passages.py`) endurnýjar `documents.fts_is` úr sömu lemmum og `passages`-röðunum, í sömu færslu — það er eina leiðin sem `fts_is` uppfærist eftir að skjal er endurunnið. `backfill_fts_is.py` fyllir aðeins `NULL`-gildi og lagar því **ekki** skjal sem átti `fts_is` fyrir en fékk nýjan `body_text` seinna (t.d. eftir endur-extraction). Þangað til `rebuild_passages()` keyrir á því skjali er `passages` p rétt en forsían `d.fts_is @@ tsq` í efnisgreinaleitinni (sjá F3, [05-leit](05-leit.md)) getur útilokað skjalið úr `keyword`/`proximity`-leit þótt efnisgreinarnar sjálfar passi.

### Eftir fulla `backfill_passages.py --all` þarf viðhald á vísum
Full endurbygging eyðir og setur inn allar 1,78 M `passages`-raðir og endurskrifar hvert `documents.fts_is` — GIN-vísarnir blása út við það (`ix_passage_fts_is` 661 → 1164 MB, `ix_doc_fts_is` 333 → 441 MB, mælt 27.09.2026). Keyrðu strax á eftir:

```sql
VACUUM (ANALYZE, VERBOSE) passages;
VACUUM (ANALYZE, VERBOSE) documents;
REINDEX INDEX CONCURRENTLY ix_passage_fts_is;   -- 1164 → 522 MB, ~1:43
REINDEX INDEX CONCURRENTLY ix_doc_fts_is;       -- 441 → 228 MB, ~0:38
```

Annars hægist verulega á leitinni þangað til það er gert — bæði vísarnir tvöfaldast í lestri (diskurinn er á USB, svo kaldir lestrar ráða) og `ANALYZE` eitt og sér lagar það ekki. Athugaðu að `VACUUM ANALYZE`/`REINDEX` lagar **ekki** `plan_cache_mode`-gildruna; þau eru sjálfstæð vandamál.

### `plan_cache_mode`: almennt plan drepur efnisgreinaleitina
asyncpg keyrir allt um prepared statements, svo PostgreSQL skiptir hverri setningu yfir í **almennt plan** (generic plan) eftir 5. keyrslu á tengingu. Fyrir efnisgreinaleitina er það hörmung: `tsquery`-ið kemur úr bindibreytu (`plainto_tsquery('simple', $1)`), svo almennt plan hefur engar upplýsingar um valkvæmni — hvert þrep er áætlað á ~1 röð og áætlarinn velur að endurskanna `ix_passage_fts_is` GIN-vísinn **einu sinni fyrir hvert frambjóðendaskjal** (2000 lykkjur × ~550 þús. TID) í stað eins GIN-skanns hash-joinaðs við `cand`. Mælt á g017 (492-spurninga gullmengið, 27.09.2026): 25 ms í keyrslum 1–5, **9.745 ms** frá keyrslu 6. Sama fyrirspurn í `psql` með bókstöflum er alltaf hröð, svo gildran sést ekki nema hún sé keyrð oftar en 5× í sömu tengingu.

Vörnin er `plan_cache_mode = force_custom_plan`, sett í `connect_args` í `engine/database/connection.py`. Endurplönun kostar ~0,5–7 ms á setningu, hverfandi í samanburði. Ef sú stilling er fjarlægð (eða ný `create_async_engine`-kallstöð bætist við án hennar) kemur regressjónin aftur og lítur út eins og I/O-vandamál þótt hún sé 100% CPU (`pg_stat_activity.wait_event` er NULL í öllum sýnum).

### `validation_errors` geymir tvenns konar „ekkert"
19.554 raðir hafa JSON-gildið `null` (skalar) og 25.546 hafa raunverulegan fylkjalista. SQL sem gerir ráð fyrir fylki springur:

```sql
-- ✗ ERROR: cannot extract elements from a scalar
select jsonb_array_elements(validation_errors) from documents
where validation_errors is not null;

-- ✓ verður að verja sig
... where jsonb_typeof(validation_errors) = 'array'
```

### Facets telja alltaf strangt — þær slaka aldrei (breytt 28.09.2026 eftir lokayfirferð, I2)
`facet_counts()` gerði áður sína eigin `should_relax()`-ákvörðun, óháð sjálfri leitinni (sem gat gert facets og leit ósammála um hvort slakað væri — sjá söguna hér að neðan). Það er fjarlægt: `facet_counts()` síar núna alltaf með ströngu fyrirspurninni (`to_tsquery('simple', :q_strict)`), sama hvað strangi fjöldinn er, og gerir enga strangi-talningu til að ákveða neitt — ein gagnagrunnsaðgerð færri á hverja `keyword`-leit. Ástæðan er notandaupplifun, ekki afköst: hliðarspjaldið á að sýna dreifingu skjala sem innihalda **öll** leitarorðin (sömu tölu og `strict_total` í niðurstöðuhausnum), meðan listinn sjálfur að auki sýnir slökuðu (þrep 1/2) treffin sem eru fáanleg. Að láta hliðarspjaldið telja víðustu fyrirspurnina (eins og gamla `should_relax()`-ákvörðunin gerði þegar hún slakaði) skilaði tugum þúsunda þar við hliðina á lista með ≤ `RELAX_CAND_LIMIT` niðurstöðum — ruglandi, ekki gagnlegt. Sjá [05-leit](05-leit.md) „Facets telja alltaf strangt".

*Saga (úrelt hegðun, fyrir 28.09.2026):* Áður tóku `facet_counts()` og sjálf leitin `should_relax()`-ákvörðunina hvor í sínu lagi, með sínum eigin síum. Facets slepptu `scope` en leitin notaði það, svo strangi fjöldinn gat lent sitt hvorum megin við `RELAX_BELOW` fyrir hvora hlið, og facets gátu því slakað (eða ekki) óháð leitinni sjálfri. Sú hegðun er ekki lengur til staðar — facets slaka aldrei framar.

### Slökuð leit: víðasta fyrirspurnin getur verið dýr — en er núna bundin af þaki og LATERAL
Þrep 2 (`a | b | c`) getur passað við tugþúsundir skjala þegar eitt orðanna er algengt en er samt AND-að við sjaldgæft orð í ströngu fyrirspurninni (t.d. „krafa" + sjaldgæft sérnafn). Fyrsta útfærslan (fyrir 2026-09-28) reiknaði `hits_and`/`hits_or` sem `JOIN cand … GROUP BY` yfir alla `passages`-töfluna, sem áætlaðist sem Bitmap Heap Scan fyrir *allt* OR-mengið — mælt 43,6 s fyrir `hits` eitt og sér á `krafa dómur skaðabót` (cand_limit 2000), p95 8,7 s í fyrsta `--relax-sweep`. Lagfært með per-frambjóðanda `CROSS JOIN LATERAL` samantekt (`_lateral_hits_sql`, gengur um `ix_passage_doc` einu sinni fyrir hvern frambjóðanda í stað bitmap-skanns yfir allt safnið) og eigin, lægra þaki `RELAX_CAND_LIMIT = 300` (óslökuð leit heldur `PASSAGE_CANDIDATE_DOCS = 2000`). Eftir það: `hits`-þrepið á sömu fyrirspurn ~0,6–1,1 s, p95 í `--relax-sweep` við K=10 **707 ms** (var 8.660 ms). Full mæling (cap 300 vs. 1000, K=0..50) í „Niðurstöður mælinga" í `docs/superpowers/plans/2026-09-28-relaxed-keyword-search.md` og í `.superpowers/sdd/2026-09-28-relaxed-keyword-search/task-2c-report.md`. `RELAX_BELOW = 10` og `RELAX_CAND_LIMIT = 300` eru núverandi föst gildi (28.09.2026), valin af notanda út frá þeirri mælingu.

### Óslökuð `keyword`-leit með algengum lemmum var hæg — lagað 2026-09-28 (perf/strict-lateral)
Sami galli og hrjáði slökuðu leiðina (`JOIN cand … GROUP BY` yfir alla `passages`-töfluna, sjá gildruna hér að ofan) var áfram í óslökuðu leiðinni þar til seinna sama dag. `krafa dómur skaðabót` (strangt treff 10.502 skjöl, langt yfir `RELAX_BELOW = 10` svo engin slökun kviknar) fékk Seq Scan yfir alla `passages`-töfluna (1,78 M raðir, 743 þúsund þeirra passa) fyrir `hits_or` — **31,5 s** fyrir það þrep eitt, 19,4 s / 5,2 s kalt/heitt gegnum `search_documents`. Nú nota báðir hamir sama `CROSS JOIN LATERAL`-formið (`_lateral_hits_sql`) og GROUP BY-formið er horfið úr `build_hits_sql`. Mælt eftir breytingu: 0,86 s / 0,45 s á sömu fyrirspurn, 2,1 s → 0,34 s á `dómur utan kröfugerðar`, sjaldgæf orð óbreytt (4 ms), efstu 5 niðurstöður nákvæmlega þær sömu á öllum átta prófunarfyrirspurnunum. Gullsett (n=492) fyrir/eftir í `docs/superpowers/specs/2026-09-28-relaxed-keyword-search-design.md` („Framhald / þekkt takmörk“, liður (a)).

### `cand` verður að vera `MATERIALIZED` — annars raðskannar áætlunargerðin `documents` og aftoastar 5 GB
Fannst við LATERAL-flutninginn hér að ofan. Þegar óslakaða `cand`-CTE-in er innfelld undir LATERAL-lykkjuna getur áætlunargerðin valið **Parallel Seq Scan á `documents`** í stað `ix_doc_fts_is`. Aðaltafla `documents` er aðeins 121 MB (15 þúsund síður) svo skannið kostar lítið á pappír, en `d.fts_is @@ …` sían aftoastar `fts_is` fyrir allar 93 þúsund raðirnar og TOAST-taflan er 5 GB — kostnaður sem áætlunargerðin reiknar ekki með. Mælt á `gæsluvarðhald` án scope: 10,5 s kalt (594 þúsund síður lesnar) á móti 136 ms með `cand AS MATERIALIZED (…)`. MATERIALIZED kemur í veg fyrir innfellinguna undir lykkjuna svo CTE-in er kostnaðarmetin sér; fyrir valkvæma stranga fyrirspurn vinnur GIN-bitmap þá. Það er **ekki** trygging: fyrir óvalkvæma stranga fyrirspurn (`krafa dómur`, ~48 þúsund skjöl) velur áætlunargerðin samt raðskann inni í CTE-inni (8,1 s kalt / 0,4 s heitt), og með heitt skyndiminni getur raðskannið jafnvel verið hraðara en bitmap-leiðin (`gæsluvarðhald` heitt: 338 ms á móti 899 ms). Ákvörðunin snýst um versta tilvikið, ekki meðaltalið. Ef raðskann sést í áætlun þýðir það því ekki að MATERIALIZED hafi dottið út. (`t0`/`t1` í `_relaxed_cand_sql` eru MATERIALIZED af annarri ástæðu — sjá docstring þess.) Prófið `test_hits_sql_unrelaxed_uses_materialized_cand_and_lateral_hits` festir hvort tveggja; ekki fjarlægja `MATERIALIZED` þótt það líti út fyrir að vera óþarft.

### `keyword`-leit nær aðeins yfir efstu 2000 frambjóðendaskjölin
`passage_search.PASSAGE_CANDIDATE_DOCS = 2000` takmarkar hversu mörg skjöl Stig 1 (`ts_rank` á `documents.fts_is`) skilar áfram í efnisgreinaleitina. Fyrir venjulegar fyrirspurnir er þetta ósýnilegt — færri en 2000 skjöl passa hvort eð er. En fyrir mjög algeng stök orð sem passa í fleiri en 2000 skjöl nær síðufletting umfram það aldrei niðurstöðum, þótt fleiri skjöl séu til sem innihalda orðið. Sjá [05-leit](05-leit.md).

### Regex verður að vera á berum dálki
`coalesce(body_text, '')` eyðileggur trigram-vísinn og þvingar seq-scan yfir 1,7 GB. Skjalfest í `REGEX_COLUMNS` í `queries.py`.

### `<N>` í PostgreSQL FTS er NÁKVÆMLEGA N, ekki „innan N"
Þess vegna byggir `proximity`-hamurinn tvíátta sameiningu fyrir hvert bil 1..N. Sjá [05-leit](05-leit.md).

### `lower_body_text = None` er rétt fyrir Landsrétt
Landsréttardómar sem fella ekki inn texta héraðsdóms eiga að hafa NULL þarna — það er ekki villa.

### Landsréttar-PDF: leitað að `"Bold"`, ekki `"BoldMT"`
Til að ná bæði eldri (`Times New Roman,Bold`) og nýrri (`TimesNewRomanPS-BoldMT`) leturheitum. Skáletruð afbrigði eru útilokuð með `"Ital"` athugun í `_heading_marker()`.

### `_correct_date_if_wrong()` er ekki blanket-override
Það hnekkir API-dagsetningunni **aðeins** þegar texti skjalsins sjálfs sýnir aðra dagsetningu.

### Þriðjungur ritgerða hefur engan texta — og það er rétt
1.484 af 4.311 `logfraediritgerdir` hafa `body_text IS NULL`. Af þeim eru **1.480 læstar** (`raw_api_data->>'locked' = 'true'`) hjá Skemman, oft með `embargo_until` dagsetningu. Þetta er aðgangsstefna útgefandans, ekki bilun í innflutningi — ekki „laga" með endurinnflutningi.

Aðeins **4** eru ólæstar en textalausar; þær eru raunverulegar eyður:
`a889d477`, `8f01a48c`, `6c8bb094`, `05de5a01`.

Athugið að `locked` er geymt sem strengurinn `"true"`/`"false"` í `raw_api_data`, ekki JSON-boolean.

### Sami dómur tvisvar: nýtt GUID býr til nýja röð (hreinsað 29.09.2026)
island.is endurbirtir dóm undir nýju GUID-i. Upsertið keyrir á `(source_id, external_id)`, svo nýja GUID-ið verður **ný röð** — sami dómstóll, sama málsnúmer, sami dagur, sama tegund, og í 9 af 10 tilvikum bætaeins meginmál. Tvær raðir á einn dóm tvítelja í flokkunartrénu og í `cited_by_count`.

`scripts/dedupe_documents.py` eyddi tíu slíkum (átta Landsréttar, tveir héraðsdóma; sjá [docs/snapshots/README.md](../snapshots/README.md)). Reglan er **yngsti innflutningurinn heldur sér**, og hún er mæld: könnun á öllum 20 GUID-um gegn `https://island.is/_next/data/{buildId}/domar/{id}.json` gaf eldri röðina dauða (404) og yngri lifandi í öllum fimm pörunum sem heimildin gat skorið úr. Fyrir hin fimm birtir island.is báða. **Athugið að `/domar/{id}` (án `_next/data`) skilar 404 fyrir lifandi dóma líka** — sú slóð er ekki nothæf til könnunar.

**Lykillinn `(court, case_number, document_date, verdict_type)` gildir EKKI almennt.** Sama fyrirspurn án heimildarsíu finnur 618 „tvítekin" mál og 912 raðir þvert á allar heimildir — og þau eru að stórum hluta raunveruleg, ólík skjöl:
- `heilbrigdi_raduneyti 008/2020` eru **fjórir ólíkir úrskurðir** undir sama málsnúmeri, þrír með sama dagsetningu og sama `verdict_filename`, en 4.192 / 4.935 / 5.348 bæti af ólíkum texta.
- `hugverkastofa` dagsetur allt `01-01-<ár>`, svo dagsetningin afmarkar ekkert.

Lykillinn velur því aðeins frambjóðendur; það sem sker úr er **líkindi á stöðluðum texta** (`--min-similarity`, sjálfgefið 0,995). Sjálfgefið afmarkast skriftan við dómstólaheimildirnar þrjár (`DEFAULT_SOURCES`); `--all-sources` nær yfir allar, og þar sem líkindaþröskuldurinn ver umfangið skilar hún sömu röðum og upptalning heimildanna. Keyrslan 30.09.2026 eyddi **130 röðum í 18 heimildum** utan dómstólanna — þar birtir heimildin sjálf sama úrskurð tvisvar (tvö `newsid` á stjornarradid.is, `-1`-slóð hjá hugverk.is). Greining, líkindabönd og afrit: [docs/snapshots/README.md](../snapshots/README.md).

**Eyðing tekur afleidd gögn með sér.** `passages`, `citations` og `document_links` eru öll `ON DELETE CASCADE` (`citations.to_doc_id` er `SET NULL`). Eftirlifandinn bar sín eigin afrit af öllu nema tveimur `leyfisbeidni_um`-tengingum, svo `link_malskotsbeidnir.py` verður að keyra á eftir — hún er sjálfsömul (`ON CONFLICT DO NOTHING`).

### `case_type` er ekki `verdict_type` — og hann var aldrei skrifaður við innflutning (lagað 29.09.2026)
Tvær ólíkar víddir sem er auðvelt að blanda: `verdict_type` er **hvað dómstóllinn kvað upp** (dómur eða úrskurður), `case_type` er **hvernig málið kom til hans** (kært eða áfrýjað) og hvers kyns það er (einkamál eða sakamál). Þær eru sjálfstæðar:

- **Landsréttur kveður upp úrskurð í kærumálum** — öll 3.719 kærumál hans eru úrskurðir (100 %). En 122 **áfrýjuð** mál eru líka úrskurðir (frávísun, ómerking, hæfi dómara), svo `verdict_type` er ekki kærumálamerki.
- **Hæstiréttur dæmir kærumál.** Ekkert af 12.216 Hæstaréttarskjölum kallar sig úrskurð: öll sem bera fyrirsögn segja „Dómur Hæstaréttar" og öll sem bera sitt eigið úrslitaorð segja „Dómsorð" — kærumál og áfrýjuð mál, fyrir og eftir 2018. Gamla `verdict_type`-villan hér að neðan var því óvart orðin kærumálaskynjari með 82 % nákvæmni, og það er sú vídd sem menn lásu úr henni.

**Lekinn:** `case_type` var eini dálkurinn sem extractorinn framleiðir en `_upsert_doc` í öllum þremur dómstólainnflutningsskriftunum sleppti. Hann hafði því aldrei verið skrifaður við innflutning — aðeins af einskiptis-`backfill_case_type.py`, síðast 18.–22. júní 2026. 328 skjöl sem komu inn eftir það voru óflokkuð. `tests/test_import_upsert_parity.py` ber nú dálkasett extractorsins saman við upsertið hjá öllum þremur svo næsti nýi dálkur hverfi ekki þegjandi líka.

**Innflutningur má fylla `case_type` en aldrei skrifa yfir hann.** Upsertið notar `coalesce(documents.case_type, excluded.case_type)`, því gildið í grunninum kemur frá dómstólnum en gildi extractorsins er ágiskun (`_infer_hrd_lrd_case_type`, ~97 % hjá Hæstarétti og ~70 % hjá Landsrétti). Ástæðan er alvarlegri en nákvæmnin: **island.is flokkar Hæstaréttardóma aðeins frá 5. janúar 2016**, svo fyrir ~10.000 eldri raðir er gildið í grunninum síðasta eintakið sem til er. Sjá [docs/snapshots/README.md](../snapshots/README.md) fyrir afritið og tölurnar.

**Röðin skiptir máli: heimildin fyrst, ágiskun á eftir.** Innflutningurinn skilar `case_type = NULL` fyrir Hæstarétt og Landsrétt af ásettu ráði. Skrifaði hann ágiskun væri röðin ekki lengur NULL og `--missing-only` slepti henni — dómstóllinn fengi aldrei orðið. Héraðsdómstólar eru undantekningin: þar **er** forskeyti málsnúmersins svar dómstólsins, svo það er fyllt við innflutning. `tests/test_import_upsert_parity.py` festir þetta.

**Varaleiðin** (`_infer_hrd_lrd_case_type`) tekur við þeim fáu málum sem heimildin flokkar í **enga** tegund — þrjú Landsréttarmál 29.09.2026 (592/2026, 562/2026, 564/2019). Hún keyrir aðeins í `--missing-only`, **eftir** að síuflettingin hefur fengið sitt, aldrei við innflutning. Leiðin kemur úr lykilorðinu „Kærumál" (99,98 % rétt hjá Hæstarétti, 99,66 % hjá Landsrétti), tegundin úr stefnanda og hún er dómstólasértæk. Heild: **97,22 % hjá Hæstarétti og 98,89 % hjá Landsrétti** (mælt gegn 18.515 gildum frá heimildinni).

Dómstólasértæknin er ekki smekksatriði — hún er mæld. Sama sækjandaheiti flokkast ekki eins:

| Stefnandi | Hæstiréttur (saka/einka) | Landsréttur (saka/einka) |
|---|---|---|
| Ákæruvaldið | 2.336 / 0 | 1.239 / 1 |
| Héraðssaksóknari | 37 / 0 | 110 / 0 |
| Lögreglustjórinn á … | **321 / 983** | **1.671 / 3** |
| Ríkislögreglustjóri | 0 / 99 | (fá) |
| Sérstakur saksóknari | 0 / 30 | (fá) |
| Ríkissaksóknari | 3 / 20 | 50 / 0 |

Hjá Hæstarétti flokkar island.is lögreglustýrt kærumál (gæsluvarðhald o.þ.h.) sem **einkamál** í langflestum tilvikum, hjá Landsrétti sem **sakamál**. Reglan telur því aðeins tvö heiti hjá Hæstarétti en öll hjá Landsrétti; víkkun fyrir bæði lyfti Landsrétti í 99,4 % en felldi Hæstarétt í 87,7 %. Það sem eftir stendur af skekkjunni (337 skjöl hjá Hæstarétti) er nánast allt þessi eini flokkur. **Samræmi við geymdu gildin gildir hér framar „réttri" lögfræði** — dálkurinn verður að vera einn taxonómía, ekki tvær.

**Ágiskun er ekki merkt sérstaklega** (enginn upprunadálkur, ákvörðun notanda 29.09.2026). Leiðréttingarleiðin er því **full keyrsla** á `backfill_case_type.py` (án `--missing-only`): hún skrifar það sem heimildin segir núna og snertir ekkert sem heimildin hefur ekkert svar við — þar á meðal Hæstaréttarraðirnar fyrir 2016.

**Tvær gildrur í fyrirspurninni sjálfri:** dómstólslykillinn er `Landsrettur` **án broddstafa** (`Landsréttur` skilar `total: 0` án villu) en `Hæstiréttur` **með** þeim (`Haestirettur` skilar 0). Og skjölin bera ekkert tegundarsvið — `caseType`, `caseTypes`, `caseCategories`, `caseCategory`, `type`, `category` er öllum hafnað á `WebVerdictItem`, og skemaskoðun er lokuð, svo eina leiðin er `caseTypes`-sían á listanum.

### `verdict_type` má aldrei lesa úr tilvísun í annan úrskurð (lagað 29.09.2026)
Gamla reglan í `extractor.py::_detect_verdict_type` skilaði `'Úrskurður'` ef `Úrskurðarorð|úrskurðar` stóð **hvar sem er** í meginmálinu. Hvert einasta kærumál nefnir „úrskurð héraðsdóms" — þann sem kærður var — svo **5.368 Hæstaréttardómar, 151 Landsréttarmál og 1.071 héraðsdómur** voru skráðir sem úrskurðir. Hæstiréttur átti eftir það enga úrskurði í grunninum: heimildin birtir aðeins dóma (líka í kærumálum, fyrir og eftir 2018).

Nýja reglan les það sem skjalið segir um **sjálft sig**, í þessari röð:
1. Eigin fyrirsögn með dómstólsheiti — `Dómur Hæstaréttar`, `Úrskurður Landsréttar`. Fyrsta treffið vinnur: ~98 Hæstaréttarsíður bera dóminn og EFTA-álitsúrskurðinn í sömu skrá, og dómurinn er aðalskjalið.
2. Stök fyrirsögn (`## ÚRSKURÐUR`) — **aðeins** heimildir sem fella ekki undirréttartexta inn í `body_text`.
3. Lokaformúlan („kveður upp dóm **þennan**", „Úrskurð**inn** kveður upp …"). Ábendingarfornafnið/greinirinn er það sem bindur formúluna við þetta skjal: „Tjónanefndin kvað upp úrskurð" er annars aðila úrskurður. Síðasta treffið vinnur — formúlan lokar skjalinu.
4. Fyrsta dómsorðs-/úrskurðarorðsfyrirsögn.
5. „tekið til dóms/úrskurðar", „dómtekið".

**Tvær gildrur sem bitu:**
- `has_lower_court`-heimildir (Hæstiréttur, Landsréttur) fá aðeins þrep 1 og 4. Þegar `_split_lower_court` missir skiptinguna (t.d. `ÚrskurðurHéraðsdóms` án bils) hangir úrskurður héraðsdóms aftan í `body_text` með sinni eigin fyrirsögn, lokaformúlu og „tekið til úrskurðar" — Hrd. 411/2000 og 412/2000 urðu að úrskurðum af þeim sökum í fyrstu útgáfu reglunnar. Aðeins **fyrsta** úrskurðarorðið er örugglega okkar.
- Fyrirsagnir gamalla dóma eru bókstafaglesnar (`Ú r s k u r ð a r o r ð`) og lifa það af úr PDF-inum; `_spaced()` leyfir bil milli allra bókstafa.

43 héraðsdómar segja tvennt jafn afdráttarlaust (t.d. „kveður upp úrskurð þennan" undir fyrirsögninni `## DÓMSORÐ`). Fallið skilar `None` fyrir þá og `scripts/backfill_verdict_type.py` lætur röðina ósnerta — ekkert giskað, sbr. tilvitnanaleysarann.

90 skjöl standa eftir sem óþekkt: 43 héraðsdómar með mótsögn, 31 héraðsdómur án nokkurs merkis, 12 með tómt meginmál (7 héraðsdómar, 5 Hæstiréttur) og 4 Hæstaréttarskjöl án úrskurðar-/dómsorðs. Skriftan er lyklunarlaus og sjálfsömul, svo listinn er endurgeranlegur hvenær sem er: `uv run python scripts/backfill_verdict_type.py --dry-run --report /tmp/vt.csv` — raðir merktar `óþekkt`.

### `verdict_type` dregur skráarnafnið og `fts_is` með sér
`verdict_filename` ber kóðann `_D_`/`_U_` (`renderer._VERDICT_CODE`) og `.md`-hausinn segir „# Úrskurður Landsréttar – 184/2022", svo lagfærð tegund þýðir endurnefnd `.md`+`.pdf`, endurgerð markdown, færð `raw_api_data->>'pdf_path'`-vísun og `passage_hash = NULL` svo `backfill_passages.py` endurbyggi `documents.fts_is` (verdict_type er í strúktúr-forskeytinu, sjá `passage_index.py`). `backfill_verdict_type.py` gerir allt fjögur í sömu færslu.

**Skrá sem enga röð á bak við sig er jafn frátekin og röð.** Landsréttur ber ~6.000 munaðarlausar `.md`/`.pdf` frá endurinnflutningi þar sem `unique_verdict_filename` taldi skjalið sitt eigið nafn frátekið og gaf öllum `_2`-viðskeyti. Endurnefning á slíkt nafn eyðir eina afritinu, svo skriftan tekur bæði DB-nöfn **og** skráarnöfn á disknum í `taken`-mengið.

### Héraðsdómur birtir ekki alla dóma
~30% „no-candidate" hlutfall í `hrd_herd` tengingum er væntanlegt, ekki bilun.

### MCP: stdout er samskiptarásin
`engine/mcp` er stdio MCP-þjónn (sjá [10-mcp](10-mcp.md)) — stdout er lokað fyrir prótókollinn, ekki fyrir venjulegt `print`/log. Allt log verður að fara á stderr (`logging.basicConfig(stream=sys.stderr)` í `server.py`, sett upp áður en `engine` er flutt inn). `tests/test_mcp_server.py::test_import_writes_nothing_to_stdout` staðfestir að sjálft `import engine.mcp.server` sé þögult á stdout — bætist einhvern tímann `print()`-kall eða óvart-stillt logger með `StreamHandler(sys.stdout)` inn í `engine`, brotnar prótókollið þögult hjá viðskiptavininum.

### Lesaðgangshlutverk + `Base.metadata.create_all`
`engine/database/connection.py::init_db` keyrir sjálfgefið `Base.metadata.create_all` við ræsingu — nauðsynlegt fyrir API-ið og skriptur, en `lausnir_ro` má ekki `CREATE`. MCP-þjónninn kallar því `init_db(url=DATABASE_URL_READONLY, create_tables=False)` (`engine/mcp/server.py::_lifespan`). Ef `create_tables` gleymist (t.d. ný kallstöð sem afritar gamla `init_db()`-kallið) mistekst ræsingin á fyrstu `CREATE TABLE` með réttindavillu — augljóst í log, en villuboðin benda á `CREATE`, ekki á orsökina (`create_tables=True` með read-only hlutverki).

### `npx tsc --noEmit` í `frontend/` athugar EKKERT
Rót-`tsconfig.json` er `{"files": [], "references": [...]}`. Án `-b` þýðir það **núll skrár**:

```bash
npx tsc --noEmit          # ✗ þögult, athugar 0 skrár — alltaf „hreint"
npx tsc -b                # ✓ raunveruleg athugun (það sem npm run build keyrir)
npx tsc -b --force        # ✓ hunsar tsbuildinfo skyndiminnið
```

Staðfest 28.07.2026 með `--listFiles`: `--noEmit` snerti enga skrá úr `src/`. Þetta faldi bæði fyrirliggjandi villu í `SourceTree.tsx` (sem braut `npm run build`) og rangar prófsniðmátsgerðir. **Notaðu alltaf `tsc -b`.**

### Ósniðmátuð prófsniðmát reka frá API-inu
`const doc = {...}` án `: DocumentDetail` þýðir að TypeScript ber það aldrei saman við raunverulegt svarform. Sniðmátið í `DocPanel.test.tsx` er nú sniðmátað viljandi — nýtt svæði í `DocumentDetail` brýtur þá prófin strax í stað þess að læðast fram hjá.

### Auður lestur er ekki alltaf tómt `content`
`content = doc.markdown ?? doc.body_text ?? ""`. API-ið býr til lýsigagna-`markdown` (titil, dagsetningu, slóð) jafnvel fyrir skjöl **án nokkurs texta**, svo `content` er aldrei tómt. Til að greina „enginn texti" verður að skoða `doc.body_text` beint. Þetta olli raunverulegri villu sem einingapróf náði ekki (sniðmátið hafði `markdown: null`, sem gerist aldrei í raun) — vafraprófun fann hana.

### JSX strengir vinna ekki `\n`
`text="## Titill\n\nMál"` í JSX gefur bókstaflegt bakstrik-n (char 92,110), ekki línuskil (char 10). Verður að vera `text={"## Titill\n\nMál"}`. Þetta olli röngum prófum sem voru „lagfærð" með því að slaka á fullyrðingum áður en rót fannst.

## Tilvitnanir milli dóma — sjö gildrur

Kóðinn er í `engine/processors/citations.py` (útdráttur) og `citation_resolver.py` (leysing); hönnunin er skjölluð í `docs/superpowers/specs/2026-09-29-citations-design.md`.

### Lög verða að vera merkt ÁÐUR en málsnúmer eru lesin
`"laga nr. 91/1991"` inniheldur tölustafaform sem lítur út eins og málsnúmer. Ef lagatilvísanir eru ekki merktar og útilokaðar fyrst (`_LAW_RX`, §5.1) les útdrátturinn `91/1991` sem tilvitnun í dóm. Sama gildir um reglugerðir, auglýsingar og samþykktir (`reglugerð\w*`, `auglýsing\w*`, `samþykkt\w*` + `nr. NNNN/ÁÁÁÁ`).

### Dómstólsorðið verður að standa í sömu setningu og innan 120 stafa
`WINDOW = 120` í `citations.py` er hörð fjarlægðartakmörkun aftur á bak frá málsnúmerinu, og leitin fer aldrei yfir setningarmörk (`_sentence_start`). Fyrsta útgáfan notaði 220 stafi, sem var of vítt — úrskurður óbyggðanefndar sem nefndur var langt á undan í sömu efnisgrein rataði ranglega á Hæstarétt. Einnig: standi annar úrskurðaraðili (nefnd, sýslumaður, ráðuneyti, stjórnvald o.fl. — `_OTHER_BODY_RX`) **nær** númerinu en dómstólsorðið, vinnur sá aðili og engin tilvitnun er skráð.

### Afstæð ár — „sama ár" tekur næsta STAKA ártal, aldrei ártal falið inni í öðru númeri
„8. nóvember 2017 … 18. nóvember sama ár" á að leysast á 2017. En `\b` eitt og sér er ekki nóg: það passar líka við ártalið inni í `nr. 91/1991` eða `2020-2021`. `_YEAR_RX` í `citations.py` krefst þess að ártalið standi **stakt** — hvorki tölustafur né `/` né `-` má standa við hliðina á því (`(?<![\d/\-])(1[89]\d\d|20\d\d)(?![\d/\-])`). Án þessa leysti „laga nr. 91/1991 … 18. nóvember sama ár" á 1991 og tilvitnunin mistókst að leysast á rétt skjal. `sl.`/`síðastliðinn` er annað afstætt form: það notar `doc_date` (skjalsins eigin dagsetning), ekki næsta ártal í setningunni.

### Hæstaréttarnúmer með forskeyttum núllum (`055/2001`) verða að staðlast fyrir samanburð
Gagnagrunnurinn geymir sum Hæstaréttarmálsnúmer með forskeyttum núllum (`055/2001`) og fjögur með stöku bili (`243 /2002`), en texti dóma vitnar alltaf í þau án núllanna (`55/2001`). `norm_case_number()` í `citation_resolver.py` fjarlægir bil, hástafar bókstafi og fellir forskeytt núll úr tölulega hlutanum á undan `/` — beitt á **báðar** hliðar (bæði þegar vísirinn er byggður úr `documents.case_number` og þegar númer eru lesin úr texta), svo þau bera alltaf saman rétt.

### Landsréttarnúmer eru endurnýtt milli úrskurðar og dóms — leyst með dagsetningu/dómsorði, aldrei giskað
Sama málsnúmer getur átt bæði úrskurð og síðar dóm í Landsrétti (og víðar). Sé fleiri en einn frambjóðandi eftir stöðlun númers, þrengir leysarinn með dagsetningu í setningunni (`target_date`, ef til) og annars með orðinu á undan dómstólsorðinu (`Dómur`/`Úrskurður`/`Ákvörðun` → `target_verdict`). Standi fleiri en einn frambjóðandi eftir **allar** þrengingar → `status='ambiguous'`, aldrei valið af handahófi. Tæmi dagsetningarþrengingin frambjóðendahópinn alveg → `status='unresolved'` — leysarinn fellur **aldrei** aftur á ódagsetta frambjóðendur eftir að hafa reynt dagsetningu.

### Héraðsdómsnúmer eru ekki einkvæm milli héraða
Sama málsnúmersform (`E-1234/2020`) getur átt sitt skjal í hverju héraði. Sé staður ekki nefndur í texta (`Hérd.` án viðskeytis) leitar vísirinn yfir **öll** héruð (`court LIKE 'Hérd. %'`) og fleiri en eitt tréff → `ambiguous`; sé staður nefndur (`Hérd. Rvk.` o.s.frv., átta möguleg gildi) leysist það venjulega ótvírætt.

### `F-` er Félagsdómur, og bara-tölu-form Félagsdómsnúmera þurfa sérstaka fallleið
Félagsdómur skipti um málsnúmeraform 2010: `13/2001` fyrir þann tíma, `F-9/2019` eftir — en dómstexti vitnar áfram í bara-tölu-formið óháð ártali (`„í máli nr. 5/2012"`). Forskeytið `F-` **ræður alltaf** Félagsdómi, sama hvaða dómstólsorð stendur við hliðina (§5.3: forskeyti vinnur gegn orði). Vísirinn (`CitationIndex.candidates()`) reynir því, fyrir `target_court == 'Féld.'` með bara-tölu-formi, **báða** lyklana — bara-töluna og `F-`-forskeytta útgáfuna — og sameinar niðurstöðurnar; annars myndi bara-lykillinn missa af öllum Félagsdómsmálum eftir 2010. Mæling: þessi fallleið ein og sér bætti þekju um **+0,87 hlutfallsstig** í deterministic A/B-mælingu.

## Áfrýjunarbrúnir: þriðji ritarinn með ranga stefnu (lagað 01.10.2026)
Reglan er sú sama og í `DocumentLink`: lægra dómstig → hærra ber `appealed_to`, hærra → lægra ber `appealed_from`. `check_link_orientation.py` staðfestir hana yfir alla töfluna. Lagfæringin 26.09.2026 (751a5d1) lagaði `link_appeals.py` og `backfill_hrd_lrd_links.py` en missti af þriðja ritaranum, `_link_via_resolution_link` í `scripts/import_haestirettur.py`, sem skrifaði Hrd → neðra dómstig sem `appealed_to`. Fjögur tilvik sem skriptan fann 29.09. og voru skráð hér sem „eldri, óskyld" voru því ekki leifar heldur **tvö pör úr Hæstaréttarinnflutningi 23.09.** (Hrd. 26/2026 ↔ Lrd. 388/2026 og Hrd. 50/2025 ↔ Hérd. Rvk. E-6483/2024), og hver nýr innflutningur hefði bætt við fleirum.

Ritarinn er lagaður og festur með `test_resolution_link_edges_follow_the_tier_invariant`. Röðunum fjórum (`6fdb5df6…`, `0f000e17…`, `19e5c3d5…`, `45b51474…`) var víxlað í einni færslu; `check_link_orientation.py` skilar núlli á öllum fjórum athugunum. Afturköllun er sama víxlun.

## Ósamræmi í skjölum

Yfirfarið 01.10.2026. Atriðin sem töflan taldi áður upp um CLAUDE.md (`scripts/supervised.sh`, `Renderer.rebuild_all()`, `backfill_render.py`, `migrate_v1.py`, ófullkomin skemalýsing) hurfu þegar CLAUDE.md var stytt og vísar nú á þetta wiki. `engine/collectors/` er ekki lengur til. `sources_catalogue.md` var uppfærð úr grunninum sama dag (92.896 skjöl, 109 heimildir). Tvær heimildir sem voru í `sources`-töflunni en ekki í `engine/config/sources.py` voru lagðar niður sama dag: `innvidara` (12 orðréttar tvítekningar á `innvida`, eytt) og `atvinnuvegar_ra` (2 úrskurðir menningar- og viðskiptaráðuneytisins með lögnúmer sem málsnúmer, fluttir í `vidskiptamal` sem `URVM-2023-11-30`/`URVM-2023-12-20`). Sjá [docs/snapshots/README.md](../snapshots/README.md).

| Atriði | Staða |
|---|---|
| `sources_catalogue.md` → *Athugasemdir*-dálkurinn | Fjöldi og tímabil eru úr grunninum, en tölur inni í athugasemdunum (t.d. „2.807 opin“ hjá ritgerðum) eru frá júní 2026. |

## PDF-skrár bera ekki sama nafn hjá öllum heimildum (lagað 01.10.2026)

Dómstólar (héraðsdómstólar, Landsréttur, Endurupptökudómur) geyma PDF undir `verdict_filename`, bækur undir `external_id` (ISBN) og flestar ritgerðir undir `thesis_stem()`-nafninu frá innflutningi, sem er eldra en núverandi `verdict_filename` þeirra. `has_pdf` og `/api/document/{id}/pdf` leituðu aðeins eftir `external_id` og fundu því **enga** PDF fyrir 30.704 dómsskjöl og ~2.800 ritgerðir sem eiga skrá á diski — lesandinn bauð ekki upp á PDF-sýn fyrir þau. Allir lesendur nota nú `engine.processors.stored_pdf.find_stored_pdf()`, sem reynir nöfnin þrjú í þessari röð. Hæstiréttur á engar PDF-skrár (`raw/haestirettur/` er tóm, dómarnir koma sem HTML).

## Tvíteknar renderer-skrár — eytt 01.10.2026

`engine/database/renderer.py` (197 línur) var eldra eintak af RENDER-lags rökfræðinni sem engin skrá flutti inn (staðfest 28.07. og aftur 01.10.2026). Henni var eytt; `engine/processors/renderer.py` er eina eintakið.

## Verkfæri sem voru metin og hafnað fyrir PDF-fótnótur

### `firecrawl/anydoc` — metið 11.08.2026
Rust-bókasafn, engin ML-módel, engir API-lyklar, **23× hraðara** en okkar þáttari (1,2 s á móti 28 s fyrir 682 síður). Prófað á 4 ritgerðum og 2 bókum.

**Skilar núll neðanmálsgreinum úr PDF** — í öllum sex skjölunum. Ástæðan er kerfisleg: anydoc styður neðanmálsgreinar fyrir **skrifstofuskjöl** (DOCX, ODT, EPUB) þar sem þær eru merkingarleg bygging í skránni. Í PDF eru þær eingöngu sjónrænar og það les þær sem venjulegan texta:

```
1Alþt. 2011-2012, A-deild, þskj. 328 – 290 mál. 2Samanber lög nr. 61/2012 ...
```

`anydoc --help` staðfestir að **engar PDF-stillingar eru til** — aðeins `-o`, `-f`, `-h`, `-V`. Ekkert til að stilla. Skjalfest takmörkun: skannaðar PDF-skrár þarfnast OCR sem það gerir ekki.

**Þar sem það er betra:** raunveruleg töflugreining (475 töflulínur í safnriti þar sem við skilum 0 — sumar ósviknar, aðrar ranglega greindar titilsíður) og hraði. **Vert að muna ef DOCX/EPUB-heimild bætist við** — þar væri það líklega besti kosturinn.

⚠️ Talning ein og sér var villandi við matið: fyrsta samanburðurinn sýndi anydoc með fleiri fyrirsagnir og töflur, en skoðun úttaksins leiddi í ljós að „töflurnar“ voru ranglega greint tveggja dálka umbrot með meginmáli klemmdu í reiti. **Skoðaðu úttakið, ekki bara tölurnar.**

## `pdfString` fjarlægt úr `raw_api_data` — 27.09.2026

Fjórar heimildir (heradsdomstolar, haestirettur, landsrettur, endurupptokudomur) geymdu PDF-skjalið **tvisvar**: einu sinni sem skrá á diski (`Lausnir_Data/raw/{short_name}/{verdict_filename}.pdf`, sjá athugasemdina í töflunni hér að ofan um `pdf_path`) og aftur sem base64-streng í `raw_api_data->>'pdfString'`. Það tvítekna afrit var ~56 GB af ~76 GB `documents`-töflunni (heradsdomstolar eitt og sér 53 GB / 24.282 skjöl).

`scripts/migrate_pdfstring_to_disk.py` fjarlægði tvítekninguna, en aðeins eftir að hafa sannreynt sha256 samsvörun milli JSON-strengsins og skráarinnar á diski fyrir hverja línu. Niðurstaðan er ein af:

- **Diskskrá vantaði** → skrifuð atomically úr JSON-bætunum fyrst, svo `pdfString` fjarlægt.
- **Diskskrá er til og er byte-eins** → `pdfString` fjarlægt.
- **Diskskrá er til en er ólík** → **ekkert breytt**, línan skráð og skilin eftir með `pdfString` óhreyft.

Í staðinn fyrir `pdfString` er nú:

```json
{"pdf_sha256": "<sha256 hex>", "pdf_path": "raw/heradsdomstolar/HerdRvk_E-4047-2018_D_27-11-2020.pdf"}
```

`pdf_path` er afstætt við `DATA_DIR` (ekki `RAW_DIR`), svo það er stöðugt óháð því hvar diskurinn er tengdur. `pdf_sha256` er sha256 af skránni sjálfri (staðfest jöfn JSON-bætunum fyrir flutning) og má nota til að greina spillingu í framtíðinni án þess að lesa allt skjalið aftur í JSON.

**`pdf_sha256`/`pdf_path` eru `null` þegar heimildin skilaði aldrei PDF-skjali.** Innflutningsskriftur hafa alltaf varið PDF-ritun með `if pdf_b64:`, svo tómur `pdfString` hefur alltaf þýtt „ekkert PDF til fyrir þetta skjal" — aldrei „0-bæta skrá". Fyrsta útgáfa `migrate_pdfstring_to_disk.py` meðhöndlaði þetta rangt: hún skrifaði 0-bæta `.pdf`-skrá á disk og geymdi `pdf_sha256` sem sha256 af tómum streng (`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`) plús samsvarandi `pdf_path` — nákvæmlega eins og fyrir skjöl sem raunverulega vantaði skrá fyrir. Það var lagfært 27.09.2026: tómur `pdfString` (eða streng sem afkóðast í núll bæti — hvítrými, stakt `data:...;base64,` forskeyti) fær núna útkomuna `{"pdf_sha256": null, "pdf_path": null}` og engin skrá er skrifuð. Sama lagfæring bætti við `fsync` fyrir `os.replace` og endurlestri-og-sannprófun (`WRITE_VERIFY_FAILED`) eftir hverja ritun áður en JSON er uppfært.

Um 11.800 skjöl höfðu þegar keyrt gegnum gömlu, gölluðu útgáfuna þegar keyrslan var stöðvuð (mest haestirettur). `scripts/remediate_pdfstring_empty.py` hreinsaði þau upp í tveimur umferðum, keyrt einu sinni 27.09.2026: umferð 1 fann línur með `pdf_sha256` = tóma-streng-hassið og eyddi samsvarandi 0-bæta skrá (aðeins ef hún var 0 bæti og breytt sama dag) og núllstillti `pdf_sha256`/`pdf_path`; umferð 2 skannaði diskinn eftir 0-bæta skrám sem umferð 1 fann ekki (skjöl þar sem `UPDATE`-inn hafði verið afturkallaður þegar keyrslan var drepin, svo `pdfString` var enn í JSON-inu) og paraði þær aftur við skjal með `short_name` + `verdict_filename`/`external_id`. Skriptan eyðir aldrei öðru en þessum nákvæmlega skilgreindu 0-bæta gripum.

**Þetta var eina skiptið sem `raw_api_data` var breytt eftir að hafa verið skrifað** — RAW-lagið er annars ósnertanlegt (sjá CLAUDE.md). Breytingin var stýrð, sannreynd byte-fyrir-byte áður en nokkru var eytt úr JSON, og skráð hér til að hún sé ekki misskilin sem brot á RAW-reglunni síðar.

**Taflan minnkar ekki á diski af sjálfu sér.** `UPDATE ... raw_api_data = ...` skilur eftir „dead tuples" — Postgres endurnýtir það pláss innan `documents` en gefur það ekki aftur til stýrikerfisins. Til að taflan taki raunverulega minna pláss á diskinum þarf `VACUUM FULL documents;` (eða samsvarandi, t.d. `pg_repack`) — sem `migrate_pdfstring_to_disk.py` **keyrir ekki sjálft** (læsir töflunni og þarf laust pláss í kringum stærð eftirlifandi töflu, u.þ.b. 20 GB). Sú ákvörðun er hjá þeim sem stýrir keyrslunni, ekki hluti af þessari skriptu.

## Innviðir

### Engin gagnagrunnsafritun
Meðvituð ákvörðun (sjá [08-þróun](08-throun.md)) — endurheimt með endurinnflutningi.

### Import-loggar
`update_all.py` skrifar loggana í `$DATA_DIR/logs/update/{heimild}.log` (sjálfgefið `/Volumes/RuleOfLaw/Lausnir_Data/logs/update/`), ekki lengur í `/tmp/lausnir_update/` sem hreinsaðist við endurræsingu (breytt 01.10.2026). Hver keyrsla skrifar yfir log fyrri keyrslu fyrir sömu heimild.

## Ólokið verk

### 1. PDF-útdráttur fyrir bækur
**Staða:** rannsókn kláruð, engin innleiðing.

Bækur eru tvenns konar og þurfa ólíkar pípur:
- **Native-text** (engin heilsíðumynd) — texti er réttur, vantar aðeins markdown-strúktúr → Docling í venjulegum ham
- **Skönnuð** (heilsíðumynd á hverri síðu) — textalagið er skemmt (rómverskar tölur, efnisyfirlit) → Docling `--force-ocr`

Greiningaraðferð: `page.images` þekja í pdfplumber, ~5 síður í sýni. Af 3 bókum í dag eru 2 native-text og 1 skönnuð.

Full úttekt: `docs/logfraedibaekur-pdf-extraction-investigation.md`.

### 2. ~~Bókahaus í `DocPanel.tsx`~~ — LOKIÐ 28.07.2026
Leyst með `case_number_is_title` svæðinu í `/api/document`. Á við bæði bækur og ritgerðir. Sjá [07-framendi](07-framendi.md).

### 3. Merkingarleit (embeddings)
`embedding vector(3072)` dálkurinn er til og `pgvector` uppsett, en 0 skjöl eru fyllt og engin vektorleit er útfærð. Kerfisvítt verk (öll 92.908 skjölin), ekki bókasértækt. Matið 27.09.2026 leggur til vektora á efnisgreinar og `summary` (`halfvec(1024)`, HNSW, RRF með `fts_is`) frekar en á heilt `body_text`.

### 4. Smærri atriði úr READINESS_PLAYBOOK
- ~~Ákveða hvort `mcp-postgres` MCP-þjónninn eigi að hafa read-only aðgang í stað fulls read/write~~ Leyst 2026-09-29: `lausnir_ro` + `engine/mcp` (sjá [10-mcp](10-mcp.md)).
- ~~Íhuga varanlegri geymslu import-logga~~ Leyst 2026-10-01: `$DATA_DIR/logs/update/`.

## Öryggisákvarðanir sem standa

Þrjár þriðju aðila OCR-greinar voru metnar og **hafnað**, óháð því hvaða útdráttartól verður fyrir valinu:

| Módel | Ástæða |
|---|---|
| `baidu/Unlimited-OCR` | Krefst NVIDIA CUDA (hardkóðað `.cuda()`), enginn GPU á þessari vél |
| `sabafallah/Unlimited-OCR-Universal` | Óstaðfestur einstaklingsútgefandi, krefst `trust_remote_code=True` (keyrsla á handahófskenndum kóða) |
| `AutomatosX/AX-Unlimited-OCR-3B-MoE-MLX-MXFP8` | Óstaðfestur útgefandi, mælir með óendurskoðuðum þýddum C++ keyrsluham frá sama aðila |
