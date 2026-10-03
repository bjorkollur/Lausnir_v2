# Tímarit og fræðigreinar — innflutningur (hluti 1 af 3) — hönnun

**Dagsetning:** 2026-10-03
**Staða:** Hönnun samþykkt í samtali 2026-10-01 til 2026-10-03; áætlun ekki skrifuð.
**Forsaga:** Notandi á safn um 1.700 PDF-skjala úr íslenskum lögfræðitímaritum og vill (a) flytja það inn, (b) gera bækur, ritgerðir og greinar aðgengilegar fyrir mállíkön svo þau geti svarað lögfræðilegum spurningum með nákvæmum tilvísunum, og (c) fá nafnakerfi sem stenst fræðilega skoðun. Verkefnið var skipt í þrjá hluta; þetta skjal er **hluti 1: tímaritainnflutningur**, þar með talin blaðsíðuvitund í efnisgreinum, sem hlutar 2 og 3 byggja á (sjá kafla 14).

## 1. Markmið

Hver grein í safninu verður sjálfstætt skjal í `documents` með titli, höfundum, tímariti, árgangi, hefti, ári og prentuðu blaðsíðubili, og `urlausn`-streng sem fylgir tilvísunarvenju lögfræðitímaritanna. Hver efnisgrein hennar ber prentað blaðsíðutal sem akkeri, svo mállíkan sem vitnar í greinina geti sagt „…, bls. 23“. Innflutningurinn þolir stopp og endurræsingu, skilar hverri skrá einni af þremur niðurstöðum (flutt inn, tvítekning, óþekkt með ástæðu) og skráir uppruna hvers lýsigagnagildis.

**Ekki markmið í hluta 1:** kaflaskipan og blaðsíðuakkeri afturvirkt á bækur og ritgerðir, útlínu- og blaðsíðusýn í lesandanum, MCP-verkfæri fyrir langa texta (hluti 2); merkingarleit með vektorum (hluti 3); MDE-efnið (`mde_is`, frestað að ósk notanda, sjá kafla 14); tilvitnanir úr greinum í dóma (eftirfylgni, kafli 14).

## 2. Ákvarðanir notanda

| Spurning | Val | Afleiðing |
|---|---|---|
| Hvað er skjal? | **Greinin**, einnig þegar PDF-ið er heilt hefti eða árgangur | Hefti eru klofin; heftið geymt á RAW með blaðsíðuvísun hverrar greinar |
| Hvað telst grein? | **Allt sem efnisyfirlit heftisins telur upp**, merkt tegund | Fræðigrein, Ritstjórnargrein, Ritdómur, Viðtal, Frétt, Dómareifun, Annað; auglýsingar og ótaldar síður falla burt |
| Stjórnmál og stjórnsýsla | **Allt tímaritið** | Stjórnmálafræðigreinar fylgja; enskar greinar fá `lang=en` |
| Nafngift (`urlausn`) | **Bókfræðilegt form tímaritanna** | `Höfundur: „Titill“ Tímarit, N. árg. M. hefti (ár), bls. x–y` |
| Akkeri efnisgreinar | **Prentuð blaðsíða** | „bls. 23“ / „bls. 23–24“ |
| Yfirferð | **A: aðeins vafaatriði**, í töflu | Hefti sem standast öryggismat fara beint inn; önnur í YAML-yfirferðarskrá |
| Klofningaraðferð | **Leið 1: skráin ræður, skjalið staðfestir**; mállíkan sem varaleið | Ytri skrár (Excel, leitir.is, skráarnöfn, efnisyfirlit) gefa listann; prentuð blaðsíðutöl staðfesta |
| MDE-efni | **Frestað** | `mde_is` fyrir íslenskar reifanir, `mde` geymt fyrir enskar heilar úrlausnir; sér forskrift síðar |

## 3. Mæling sem hönnunin byggir á (2026-10-01)

Allt safnið liggur í `Lausnir_Data/dropfolder_timarit/` (afrit notanda; frumsafnið er annars staðar). Skönnun með PyMuPDF yfir 1.663 PDF (76.210 síður, 12,8 GB), ekkert skjal gallað:

| Tímarit | Skrár | Síður | Kornastig PDF | Texti vantar | Athugasemd |
|---|---|---|---|---|---|
| Úlfljótur (Tímarit) | 909 | 25.429 | 790 stakar greinar 1947–2001 og sum síðar; hefti 2002–2024 | 0 | Excel-tafla með 1.258 greinum, 1.–76. árg., höfundur og upphafsbls. í hverri röð; 5. árg. (1951) vantar |
| Úlfljótur Vefrit | 42 | — | stök grein | 0 | „Birt 21. janúar 2022“, höfundur „Eftir dr. …“, engar prentaðar blaðsíður |
| Stjórnmál og stjórnsýsla | 329 | 7.561 | stök grein, skráarnafn `{bls}-{bls}_{enskur titill}` | 0 | Íslenskur titill og höfundar á fyrstu síðu; tvö skráarnöfn með innsláttarvillu í blaðsíðutali |
| Tímarit lögfræðinga | 179 | 27.234 | hefti 1951–70 og 2003–25; **heill árgangur** 1971–2002 (1971–72 í einu skjali); sex stakar greinar 1975 | 0 | Efnisyfirlit læsilegt fremst; leitir.is þekkir greinarnar (staðfest: „2010; 60 (1): bls. 7-47“) |
| Lögmannablaðið | 117 | 3.812 | hefti | **8** (1995–97) | Fagblað: fréttir, viðtöl, auglýsingar; efnisyfirlit á bls. 2 |
| Lögrétta | 34 | 4.317 | hefti | **4** | Greinar hefjast á eigin efnisyfirliti; blaðsíðumerki „a 9“ |
| Lögfræðingur (Þemis, HA) | 17 | 1.257 | árgangur | 0 | `Önnur Gögn` er vinnuefni ritstjórnar (docx, jpg); árgangurinn 2010 er þar tvisvar |
| Rannsóknir í félagsvísindum, Lagadeild | 11 | 2.110 | árgangur (ráðstefnurit, ISSN+ISBN) | 0 | Rómverskar tölur IV–XII; 2010 í þremur útgáfum |
| Lögbrú | 2 | 290 | hefti | 0 | Efnisyfirlit með blaðsíðum á bls. 3 |
| Ýmsar fræðigreinar | 2 | 462 | stök rit | 0 | Skýrsla starfshóps og grein |
| MDE Dómareifanir | 63 | 3.738 | hefti og stakar reifanir | 0 | Frestað |

Önnur mæling:
- **OCR-gæði eru ójöfn.** Miðgildi stafa á síðu: TL 1951–1970 um 800, TL frá 1990 um 2.000; Lögmannablaðið 1990–99 479; Úlfljótur 1947–2009 um 2.900. Sýnishorn úr Úlfljóti 1947: „febrúar 2947“, „V iðskiptabréf sreglur“.
- **Prentuð blaðsíðutöl** sem stök lína efst eða neðst á síðu fundust á 50–60 % úrtakssíðna hjá TL og Stjórnmálum og stjórnsýslu, 10–20 % hjá Úlfljóti og Lögmannablaðinu (þar standa þau inni í hauslínu, t.d. „Úlfljótur 173“, og þarf mynstur hvers tímarits).
- **Tvítekningar bæti fyrir bæti:** 12 hópar, 24 skrár; 10 þeirra eru Úlfljótur 1957 ≡ 1958 (1958-mappan er afrit), ein er Lögfræðingur 2010 í `Önnur Gögn`.
- **Skráarnöfn eru í NFD-formi** (macOS): öll 1.663. „ð“ og „ó“ eru sundurliðuð tákn og passa ekki við NFC-strengi úr Excel eða leitir.is án stöðlunar.
- **Árgangsnúmer fylgja ekki árinu einfaldlega.** Úlfljótur: 1947 = 1. árg., 1952 = 6. árg. (1951 vantar), en 1959 = 12. árg. og 2001 = 54. árg., svo eitt ár féll úr eftir 1957. Lögfræðingur: 1. árg. 2006, 2. árg. 2008. Árgangur verður því aldrei reiknaður úr ári nema til vara og þá merktur til yfirferðar.
- **leitir.is:** Primo-API-ið sem bókaleiðin notar (`/primaws/rest/pub/pnxs`) skilar greinafærslum með `display.creator`, `display.title` og `display.ispartof` = „2010; 60 (1): bls. 7-47 Tímarit lögfræðinga“. Bil og íslenskir stafir í fyrirspurn verða að vera URL-kóðaðir, annars HTTP 400.

## 4. Gagnalíkan

### 4.1 Heimildir (`engine/config/sources.py`)

Ein `SourceConfig` á tímarit, nýr flokkur `timarit` í `source_groups.py` („Tímarit og fræðigreinar“) við hlið `baekur`:

| `short_name` | `display_name` | `abbreviation` (court) | `issue_term` | Árgangur frá |
|---|---|---|---|---|
| `timarit_logfraedinga` | Tímarit lögfræðinga | TL | hefti | leitir.is / efnisyfirlit; 1951 = 1. árg. |
| `ulfljotur` | Úlfljótur | Úlfljótur | tbl. | Excel-tafla / möppuheiti |
| `ulfljotur_vefrit` | Úlfljótur — vefrit | Úlfljótur vefrit | — | enginn; dagsetning birtingar |
| `logmannabladid` | Lögmannablaðið | Lögmannablaðið | tbl. | prentað („21. árg. 2015“) |
| `logretta` | Lögrétta | Lögrétta | tbl. | prentað / 2004 = 1. árg. til vara |
| `logfraedingur` | Lögfræðingur (Þemis, HA) | Lögfræðingur | tbl. | skráarnafn („4 arg“) |
| `logbru` | Lögbrú | Lögbrú | tbl. | prentað |
| `stjornmal_stjornsysla` | Stjórnmál og stjórnsýsla | Stjórnmál og stjórnsýsla | tbl. | möppuheiti (`Vol01` = 1. árg. 2005) |
| `rannsoknir_lagadeild` | Rannsóknir í félagsvísindum — Lagadeild | Rannsóknir í félagsvísindum | — | rómversk tala í skráarnafni |
| `fraedigreinar_ymsar` | Ýmsar fræðigreinar | Fræðigrein | — | — |

Ný svæði á `SourceConfig`: `kind: str = "ruling"` (`"book"` fyrir bækur og ritgerðir, `"journal"` hér), `issue_term: str | None`, `citation_name: str | None` (heiti í `urlausn`, sjálfgefið `display_name`), `lang_default: str = "is"`, og `volume_base_year: int | None` — árið sem 1. árgangur kom út, notað **aðeins til vara** þegar engin heimild gefur árganginn, og þá er greinin merkt til yfirferðar (sjá kafla 3 um Úlfljót og Lögfræðing). Öll tímaritin fá `case_number_is_title=True`, `has_footnotes=True`, `parse_parties="none"`, `verdict_type_default="Fræðigrein"` (Lögmannablaðið: „Annað“), `verdict_types_allowed=["Fræðigrein","Ritstjórnargrein","Ritdómur","Viðtal","Frétt","Dómareifun","Annað"]`, `instance_tier=1`.

### 4.2 Greinin í `documents`

Endurnýting eins og hjá bókum: `case_number` = titill; `plaintiffs` = höfundar `[{"name": …, "lawyer": null}]`; `court` = `abbreviation` tímaritsins; `verdict_type` = tegund greinar; `document_date` = útgáfudagur (nákvæm dagsetning þegar hún er prentuð, annars `ÁÁÁÁ-01-01` og `date_precision: "year"` í `raw_api_data`); `summary` = prentað ágrip; `keywords` = prentuð lykilorð; `external_id` = `{ár}-{árg}-{hefti}-{upphafsbls}` (Vefrit: `{dagsetning}-{slug}`; Rannsóknir: `{ár}-{rómversk}-{upphafsbls}`), einkvæmt innan heimildar og óháð skráarnöfnum.

**Nýir dálkar (alembic 0005):**

| Dálkur | Tegund | Innihald |
|---|---|---|
| `volume` | `smallint` | árgangur |
| `issue` | `text` | hefti/tbl., „1“ eða „3-4“; NULL fyrir árgangsrit og vefrit |
| `page_start`, `page_end` | `integer` | prentaðar blaðsíður; NULL þegar engar eru |
| `lang` | `text` | `is`, `en`, …; sjálfgefið úr heimild, þekkt úr texta |
| `page_offsets` | `jsonb` | `[{"char": 0, "pdf_page": 0, "printed": 7}, …]`: hvar hver síða byrjar í `body_text` og hvaða prentaða tölu hún ber |
| `journal_issue_id` | `uuid` FK → `journal_issues` | heftið sem greinin kom úr; NULL hjá heimildum sem eru ekki tímarit |

`page_offsets` á við allar PDF-heimildir og fyllist afturvirkt fyrir bækur og ritgerðir í hluta 2. `raw_api_data` geymir yfirferðarlínu greinarinnar óbreytta (kafli 7.2) auk `source_filename` (NFC), `pdf_pages` í heftinu, `leitir_record_id`, `xlsx_row`, `ocr_source` og `date_precision`.

### 4.3 Tafla `journal_issues` (alembic 0005)

| Dálkur | Tegund | Athugasemd |
|---|---|---|
| `id` | `uuid` PK | |
| `source_id` | `uuid` FK → `sources` | |
| `year` | `smallint` NOT NULL | |
| `volume` | `smallint` | árgangur; NULL ef óþekktur |
| `issue` | `text` | NULL fyrir árgangsrit |
| `label` | `text` NOT NULL | „Tímarit lögfræðinga, 60. árg. 1. hefti (2010)“ |
| `file_path` | `text` | slóð heftisins á RAW, afstæð við `DATA_DIR`; NULL þegar heftið er aðeins til sem stakar greinar |
| `sha256` | `text` | af heftinu |
| `page_count` | `integer` | |
| `toc_text` | `text` | efnisyfirlit eins og það las úr skjalinu |
| `manifest_path` | `text` NOT NULL | slóð yfirferðarskrár, afstæð við rót repósins |
| `manifest_sha256` | `text` | útgáfan sem síðast var flutt inn |
| `status` | `text` NOT NULL | `pending`, `review`, `approved`, `imported`, `failed` |
| `confidence` | `real` | lægsta öryggi greinar í heftinu |
| `error` | `text` | síðasta villa |
| `created_at`, `updated_at` | `timestamp` | house convention: naive, NOT NULL |

Einkvæmt: `(source_id, year, volume, coalesce(issue, ''))` sem expression-vísir. Staðan er bæði yfirlit fyrir notanda og endurræsingarpunktur pípunnar.

### 4.4 `passages`

Nýir dálkar `page_from smallint`, `page_to smallint` (prentaðar), reiknaðir úr `page_offsets` í `build_passage_rows()`. `passage_anchor()` fær röðina: málsgreinanúmer → blaðsíða („bls. 23“, „bls. 23–24“) → `section_path` → „hluti N“. Dómar breytast ekki, því þeir hafa málsgreinanúmer eða engin `page_offsets`.

### 4.5 RAW og RENDER

- Heftið óbreytt: `raw/{heimild}/hefti/{ár}_{árg}_{hefti}.pdf` (árgangsrit: `{ár}_{árg}.pdf`), sha256 í `journal_issues`.
- Grein sem er skorin úr hefti: `raw/{heimild}/{verdict_filename}.pdf`, síðurnar `pdf_pages` afritaðar óbreyttar með PyMuPDF. Þetta er afleidd skrá en geymd svo `find_stored_pdf()` og PDF-sýn lesandans virki óbreytt. Stök grein sem kom sem eigið PDF er afrituð óbreytt á þennan stað.
- `verdict_filename`: `{court-án-punkta}_{árg}-{hefti}_{ár}_bls-{frá}-{til}`, t.d. `TL_60-1_2010_bls-7-47`; Vefrit: `UlfVefrit_2022-01-21_{slug40}`; `unique_verdict_filename()` leysir árekstra eins og hjá dómum.
- Markdown: haus með titli, höfundum, tímariti, árgangi, hefti, ári, blaðsíðum og `urlausn`; meginmál með ósýnilegum blaðsíðumerkjum `<!-- bls. 23 -->` við upphaf hverrar síðu.
- `to_urlausn()`: `Kjartan Bjarni Björgvinsson: „Hvenær er vanhæfi smitandi?“ Tímarit lögfræðinga, 60. árg. 1. hefti (2010), bls. 7–47`; Úlfljótur notar „tbl.“; vefrit: `Kári Hólmar Ragnarsson: „Loftslagsváin og dómstólar“ Úlfljótur vefrit, 21. janúar 2022`; fleiri en tveir höfundar: „Fyrsti höfundur o.fl.“; án höfundar (fréttir): titill fyrst.

## 5. Klofning og lýsigögn

### 5.1 Greinalisti hvers heftis

| Tímarit | Heimild listans | Staðfesting í skjalinu |
|---|---|---|
| Úlfljótur 1947–2001, stök PDF | Excel-tafla (`_meta/Ulfljotur_efnisyfirlit.xlsx`): titill, höfundar (allt að þrír), árgangur, hefti, upphafsbls. Hver skrá pöruð við röð með árgangi úr möppuheiti, titilsamanburði (stöfluð, þolir OCR-villur) og prentuðu blaðsíðutali á fyrstu síðu | Titill og höfundur á fyrstu síðu; blaðsíðutal = `Bls` |
| Úlfljótur 2002–2024, hefti | Excel-tafla | Upphafssíða fundin með blaðsíðukorti, titill efst á henni |
| Tímarit lögfræðinga | leitir.is (`ispartof` gefur ár, árgang, hefti, blaðsíðubil) **og** efnisyfirlit heftisins (höfundur: titill … bls.); samhljómur hækkar öryggi | Titill og höfundur á upphafssíðu |
| Tímarit lögfræðinga 1971–2002, árgangsrit | Efnisyfirlit árgangsins fremst, skipt eftir „1. hefti“, „2. hefti“; leitir.is eftir ári | Sama |
| Stjórnmál og stjórnsýsla | Skráarnafn: blaðsíðubil; fyrsta síða: íslenskur titill, höfundar með starfsheiti, „1. tbl. 1. árg. 2005“ | Prentað blaðsíðutal á fyrstu síðu = fyrri tala skráarnafns (tvær villur í nöfnum þekktar) |
| Lögmannablaðið, Lögrétta, Lögfræðingur, Lögbrú, Rannsóknir | Efnisyfirlit heftisins úr texta; leitir.is þegar það þekkir greinina | Titill á upphafssíðu |
| Úlfljótur Vefrit, Ýmsar | Fyrsta síða: „Birt {dagsetning}“, titill, „Eftir {höfundur}“ | Engar prentaðar blaðsíður; `page_start` NULL, akkeri verður `section_path` eða PDF-síða |

Hvert gildi ber uppruna: `xlsx`, `leitir`, `toc`, `filename`, `page`, `llm`, `user`.

### 5.2 Blaðsíðukort

Keyrt á hvert PDF á undan klofningu. Prentuð blaðsíðutöl eru lesin úr haus- og fótlínum með mynstri hvers tímarits (stök tala; „Úlfljótur 173“; „a 9“ hjá Lögréttu; „3 / LÖGBRÚ“). Fyrir hvert skjal er fundið línulegt samband `printed = pdf_index + k` sem flestar síður styðja; síður sem víkja frá (kápa, auglýsing, rangt lesin tala) fá gildið úr sambandinu og merkið `inferred`. Niðurstaðan er `page_offsets` greinarinnar og `pdf_pages` hennar í heftinu: frá PDF-síðu upphafsblaðsíðu til síðunnar á undan upphafsblaðsíðu næstu greinar (síðasta grein: til síðustu síðu með texta). Kortið fer í `raw_api_data.page_map_quality` (hlutfall síðna með lesna tölu).

### 5.3 Varaleið með mállíkani

Kviknar aðeins þegar efnisyfirlitstexti gefur engan lista eða listi og skjal stangast á (greinafjöldi, blaðsíðubil út fyrir síðufjölda). Efnisyfirlitssíðan (og ef þarf fyrstu síður greina) er send Claude með strangri JSON-forskrift (`title`, `authors[]`, `page_start`, `article_type`). Svarið fær uppruna `llm`, öryggi að hámarki miðlungs, og heftið fer alltaf í `review`. Svör eru skrifuð í skyndiminni á disk. Lýsigagnakeðja bókanna notar Claude þegar á sama hátt.

### 5.4 Öryggismat

Fjögur merki á grein, hvert `true`/`false`: `title_on_page`, `printed_page_match`, `author_on_page`, `external_record` (leitir/xlsx). Öryggi greinar er vegið meðaltal; vægi og þröskuldar fyrir `approved` eru **ekki** fastsettir í þessari forskrift heldur valdir af notanda út frá dreifingu í prufukeyrslunni (kafli 9), í samræmi við vinnureglu verkefnisins um mælda þröskulda. Hefti fer í `approved` aðeins ef allar greinar ná þröskuldi **og** blaðsíðubilin þekja heftið án eyðu umfram kápu, efnisyfirlit og auglýsingar; annars `review`.

### 5.5 Tegund, höfundar, tungumál

- Tegund úr kaflafyrirsögnum efnisyfirlits („Fræðigreinar“, „Ritdómar“, „Frá ritstjóra“, „Viðtal“, „Dómareifanir“) og titilmynstri; sjálfgefið gildi heimildar annars.
- Höfundanöfn hreinsuð af titlum og starfsheitum („dr.“, „prófessor“, „hrl.“, „hdl.“, „cand. jur.“, „LL.M.“), sem geymast í `raw_api_data.author_titles`; „Höfundur II/III“ dálkar töflunnar og „og“/„,“ í texta skipta höfundum.
- `lang` úr hlutfalli íslenskra og enskra algengisorða í meginmáli; vafi → `lang_default` heimildar og merki til yfirferðar.

### 5.6 Tvítekningar

1. Sama skrá: sha256 reiknað í `discover`; seinni eintök skráð sem `duplicate_of` í `_unmatched.yaml` og ekki unnin (Úlfljótur 1958-mappan, Lögfræðingur 2010 í `Önnur Gögn`).
2. Sama grein: `(source_id, external_id)` er einkvæmt; þegar grein finnst bæði í hefti og sem stök skrá ræður heftið og staka skráin verður `duplicate_of` með vísun á greinina.
3. Önnur en PDF-skrár (docx, jpg, xlsx, `desktop.ini`, `Thumbs.db`) eru hunsaðar og taldar í samantekt.

## 6. Textaútdráttur og efnisgreinar

- **Síða fyrir síðu.** `parse_pdf()` er kallað á hverja síðu greinarinnar (eða með síðubili) svo upphaf hverrar síðu í `body_text` sé þekkt; úr því kemur `page_offsets`. Núverandi fyrirsagna-, töflu- og fótnótuleið (`footnotes=True`) heldur sér, svo tilvísanir greinarinnar verði `[^n]`.
- **Haus- og fótlínur** (heiti tímarits, blaðsíðutal, höfundarnafn í haus) eru þekktar með því að sama lína kemur fyrir á ≥ 30 % síðna skjalsins og felldar burt; línubrotin orð („breyting- ar“) sameinuð þegar seinni hlutinn byrjar á lágstaf.
- **OCR.** Skjölin tólf án textalags fara í `docling_ocr_pdf()` (tesseract, `isl`). Fyrir skjöl með lélegt textalag er reiknað hlutfall orða sem BÍN þekkir (`lemmatizer`) á úrtaki síðna; dreifingin er sýnd notanda í prufukeyrslunni og hann velur þröskuld þar sem endur-OCR tekur við. Valið textalag skráist í `raw_api_data.ocr_source` (`embedded` | `tesseract-isl`). RAW-skjalið er aldrei breytt.
- **Tungumál og lemmun.** `passage_index` lemmar með BÍN aðeins þegar `lang='is'`; annars fara orðin sjálf, lágstöfuð, í `fts_is`. Það krefst þess að `lemmatize_rows()` taki við `lang` og að `rebuild_passages()` lesi hann úr `documents`.
- **Efnisgreinar.** `segment()` óbreytt í grunninn: tölusettar fyrirsagnir greina („2.1 Söguleg þróun“) verða `section_path`, `section_kind` „annad“. Tvær viðbætur: `page_from`/`page_to` úr `page_offsets`; og **þakvörður** í `_classify_body`/`flush` sem skiptir blokk yfir `max_words` á setningaskilum (`. `, `? `, `! ` á eftir orði með lágstaf). Gallinn er þegar til í bókum (ein efnisgrein er 57.168 stafir) og lagast þar um leið við næstu endurbyggingu.
- **Validator.** Fyrir `kind="journal"`: titill og `document_date` skyld; höfundur skyldur fyrir Fræðigrein og Ritdóm, aðeins flaggað fyrir aðrar tegundir; `page_start ≤ page_end`; `body_text < 200` stafir flaggað (ein síða „Frá ritstjóra“ er lögleg). Málsnúmerareglur gilda ekki (`case_number_is_title`).

## 7. Pípan og yfirferðin

### 7.1 Þrep

`scripts/import_timarit.py {discover|analyze|import|status} [--source S] [--issue ÁR[_ÁRG[_HEFTI]]] [--dry-run] [--workers N] [--limit N]`

1. **`discover`** — gengur um `dropfolder_timarit`, staðlar nöfn í NFC, þekkir heimild, ár, árgang og hefti úr möppu og skráarnafni með föstum mynstrum hvers tímarits (kafli 10 prófar þau á öllum 1.663 nöfnum), reiknar sha256, skráir hefti sem `pending` í `journal_issues` og skrifar tóma yfirferðarskrá með skráalista. Óþekkt skrá → `data/timarit/_unmatched.yaml` með ástæðu (`no_pattern`, `duplicate_of`, `not_pdf`). Keyrir aftur án aukaverkana.
2. **`analyze`** — fyrir hvert `pending` hefti: blaðsíðukort, efnisyfirlit, ytri skrár, greinalisti, öryggismat; skrifar yfirferðarskrána og setur `approved` eða `review`. Keyrir á mörgum vinnsluferlum eins og `backfill_passages.py`.
3. **Yfirferð** — notandi lagar skrár í `review` og setur `status: approved`. Gildi með uppruna `user` eru aldrei skrifuð yfir af `analyze` í seinni keyrslu.
4. **`import`** — fyrir `approved` hefti: sker greinar úr heftinu, textaútdráttur með blaðsíðukorti, `Extractor`-grein fyrir `kind="journal"` sem byggir `Document` úr yfirferðarlínunni, `validate()`, upsert á `(source_id, external_id)` með sömu `coalesce`-reglu og dómstólarnir fyrir gildi sem heimildin ræður, `write_markdown()`, afrit heftisins á RAW, `imported` með `manifest_sha256`. Hefti sem er `imported` en hvers yfirferðarskrá hefur breyst er flutt inn aftur eitt og sér. Á eftir: `backfill_passages.py` og `build_citations.py` eins og við aðra innflutninga; `update_all.py` þarf ekki að þekkja tímaritin, því innflutningurinn er handvirkur eins og bækur.

### 7.2 Yfirferðarskrá heftis

`data/timarit/manifests/{heimild}/{ár}_{árg}_{hefti}.yaml`, geymd í git:

```yaml
source: timarit_logfraedinga
year: 2010
volume: 60
issue: "1"
label: "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)"
file: "Tímarit Lögfræðinga/TL 2010_01.pdf"     # NFC, afstætt við dropfolder_timarit
sha256: "…"
pages: 109
page_map: {printed_eq_pdf_index_plus: -1, quality: 0.94}
status: review            # pending | review | approved | imported | failed
confidence: 0.82
error: null
articles:
  - title: "Hvenær er vanhæfi smitandi? Hugleiðingar í kjölfar dóms Hæstaréttar frá 17. desember 2009 í máli nr. 665/2008"
    authors: ["Kjartan Bjarni Björgvinsson"]
    author_titles: []
    article_type: Fræðigrein
    page_start: 7
    page_end: 47
    pdf_pages: [8, 48]
    lang: is
    provenance: {title: leitir, authors: leitir, pages: leitir+toc, article_type: toc}
    checks: {title_on_page: true, printed_page_match: true, author_on_page: true, external_record: true}
    confidence: 0.97
    leitir_record_id: "991001868109706886"
    note: ""
```

Hver grein í `documents` geymir sína línu óbreytta í `raw_api_data`, svo ferillinn frá skrá til gildis sé rekjanlegur án yfirferðarskrárinnar.

### 7.3 Ytri þjónustur og afköst

- leitir.is: mest ein fyrirspurn á sekúndu; hver fyrirspurn og svar skrifuð í `Lausnir_Data/dropfolder_timarit/_meta/cache/leitir/{sha1}.json`; endurkeyrsla les úr skyndiminni. Claude-svör á sama hátt undir `cache/llm/`.
- Textaútdráttur 76 þúsund síðna: klukkustundaverk; `--workers` eins og í `backfill_passages.py`; OCR skjalanna tólf keyrir í röð (Docling er þungt).
- Frumskjölin eru **afrituð, ekki færð**, og aldrei eytt; `dropfolder_timarit` er safn notanda, RAW er eintak kerfisins.

### 7.4 Villur og eftirlit

Hefti sem bregst fær `failed` og villutexta í `journal_issues.error` og yfirferðarskrá; keyrslan heldur áfram og lokasamantekt telur stöður. `status` prentar fjölda hefta á hverju stigi fyrir hvert tímarit og listann yfir `failed`/`review`. Loggar í `Lausnir_Data/logs/timarit/{þrep}_{dagsetning}.log`.

## 8. Birting

### 8.1 API
`search_documents`/`search_by_passages`: `scope=timarit` og hvert tímarit sem laufblöð í `source_groups`. Niðurstöður bera `urlausn` greinar; `get_document` skilar nýju dálkunum (`volume`, `issue`, `page_start`, `page_end`, `lang`) og `journal_issue` (label, slóð heftisins). `has_pdf` og `/api/document/{id}/pdf` virka óbreytt gegnum `find_stored_pdf()`.

### 8.2 Framendi
`DocHeader`: fyrir `kind="journal"` sýnir höfunda, tímarit, árgang, hefti, ár og blaðsíður í stað „Mál nr.“/„gegn“ (sama leið sem `case_number_is_title` bækur nota). Flokkunartréð fær „Tímarit og fræðigreinar“. Blaðsíðuspássía í lesandanum bíður hluta 2.

### 8.3 MCP
Engin breyting á verkfærunum; akkerið „bls. 23“ kemur gegnum `passage_anchor()`, `list_sources` sýnir flokkinn, `get_document` nýju dálkana. `INSTRUCTIONS` fær eina setningu um að greinar séu vitnaðar með `urlausn` og `bls.`.

## 9. Prufukeyrsla og þröskuldar

Fimm hefti úr hverju tímariti, valin þvert á tímabil (TL 1955, 1985, 2004, 2010, 2024 o.s.frv.), fara alla leið fyrst. Úr henni koma þrjár dreifingar sem notandi tekur ákvörðun um: (1) öryggi greina → þröskuldur fyrir `approved`; (2) hlutfall BÍN-orða → þröskuldur fyrir endur-OCR; (3) gæði blaðsíðukorts → hvenær akkeri falla aftur á `section_path`. Niðurstöður og valin gildi skráist í áætlunina og `docs/wiki/`.

## 10. Prófun

TDD eins og annað í verkefninu. Föst prófgögn úr raunskjölunum:
- `tests/test_timarit_discover.py`: mynstur allra tímarita á öllum 1.663 skráarnöfnum (listinn geymdur sem prófgögn), NFC-stöðlun, sha256-tvítekningar, `_unmatched`.
- `tests/test_page_map.py`: tilbúnar síður með gloppum, rangt lesnum tölum, kápu án tölu; mynstur hvers tímarits.
- `tests/test_toc_parser.py`: efnisyfirlit TL 2010_01 (bls. 1), TL 1985 (bls. 2–4), Lögbrú 2013 (bls. 3), Lögfræðingur 2010 (bls. 3) sem textaskrár.
- `tests/test_ulfljotur_xlsx.py`: lestur töflunnar, víxlaðar raðir, samsett hefti „3-4“, pörun við fyrstu síðu með OCR-villum.
- `tests/test_leitir_articles.py`: þáttun `ispartof` („2010; 60 (1): bls. 7-47“), URL-kóðun, skyndiminni.
- `tests/test_author_names.py`, `tests/test_article_type.py`, `tests/test_lang_detect.py`, `tests/test_header_footer_strip.py`.
- `tests/test_segmenter.py`: þakvörður á 57 þúsund stafa blokk; `tests/test_passage_index.py`: `page_from`/`page_to` og akkeri.
- `tests/test_renderer_journal.py`: `to_urlausn`, `verdict_filename`, markdown-haus, blaðsíðumerki; `tests/test_extractor_journal.py`.
- `tests/test_import_timarit.py`: innflutningur með gervisetu eins og `test_import_haestirettur.py`; `test_import_upsert_parity.py` nær yfir nýju skriftuna.
- Lifandi grunnur (read-only, sleppt án `DATABASE_URL`): eftir prufukeyrslu, `passages.page_from` fyllt fyrir allar greinar og `passage_anchor()` skilar „bls.“.

## 11. Skjölun

`docs/wiki/03-heimildir.md` (flokkurinn og tímaritin tíu), `02-gagnagrunnur.md` (nýir dálkar, `journal_issues`), `04-innflutningur.md` (`import_timarit.py`, yfirferðarferlið), `05-leit.md` (akkeri með blaðsíðum, `lang`), `09-gildrur.md` (NFD-nöfn, árgangur ≠ ár, leitir.is URL-kóðun, OCR-gæði), `sources_catalogue.md` (nýr kafli). Minnisskrá um verkefnið og ákvarðanir þess.

## 12. Samþykktarhlið

1. Hver af 1.663 skrám hefur eina niðurstöðu: `imported`, `duplicate_of`, eða `_unmatched` með ástæðu.
2. Hver grein hefur titil, höfund (þar sem tegund krefst), tímarit, árgang, hefti, ár, blaðsíðubil (þar sem prentað) og `urlausn`; hver efnisgrein hennar `page_from`.
3. Úrtak 100 greina, lagskipt eftir tímariti og tímabili, lesið gegn PDF-skjölunum: ≥ 98 % með rétta `urlausn` (titill, höfundur, árgangur, hefti, blaðsíðubil), sama viðmið og tilvitnanirnar stóðust.
4. Hlutfall hefta sem þurftu yfirferð og meðaltími á hefti skráð í áætluninni.
5. Öll próf græn; `check_link_orientation.py` og dómaprófin óbreytt.

## 13. Áhætta og mótvægi

| Áhætta | Mótvægi |
|---|---|
| Efnisyfirlit les ekki í elstu heftum (OCR) | leitir.is og Excel-tafla óháð OCR; mállíkan sem varaleið; yfirferð |
| Blaðsíðukort bregst (engar tölur, tvöfaldar síður í skanni) | `page_map_quality` lágt → akkeri fellur á `section_path`, heftið í `review` |
| leitir.is breytir API eða lokar | allar niðurstöður í skyndiminni á disk; pípan virkar án leitir.is með lægra öryggi |
| Árgangur reiknaður rangt | aldrei reiknaður úr ári nema til vara og þá flaggaður; prentað „N. árg.“ staðfestir |
| Enskur texti í BÍN-lemmun | `lang` á skjali, lemmun háð því |
| Tvítekning milli heftis og stakrar greinar | `(source_id, external_id)` einkvæmt; heftið ræður |
| Yfirferðarskrá breytt eftir innflutning | `manifest_sha256` greinir það og endurflytur heftið eitt |
| Höfundarréttur (TL eftir 2004 er hjá Fons Juris) | kerfið er rannsóknartæki eins notanda á staðarneti; engin breyting á aðgangsstefnu |

## 14. Út fyrir umfang og næstu hlutar

- **Hluti 2 — LLM-aðgengi að löngum ritum:** `page_offsets` afturvirkt á 241 bók og 4.329 ritgerðir; kaflaskipan úr efnisyfirliti og fyrirsögnum; `section_kind` fyrir fræðitexta; útlínu- og blaðsíðusýn í lesandanum; MCP-verkfæri `get_outline`/`get_pages`; endurbygging oflangra efnisgreina.
- **Hluti 3 — Merkingarleit:** vektorar á efnisgreinar og ágrip, blönduð leit (RRF) með `fts_is`, módel valið á gullsetti sem fær spurningar um greinar og bækur.
- **MDE-efni:** `mde_is` (íslenskar reifanir, hefti klofin í eina reifun á dóm, málsnúmer = kærunúmer), `mde` fyrir enskar heilar úrlausnir síðar; sér forskrift.
- **Tilvitnanir úr greinum í dóma:** `build_citations.py` afmarkast við dómstólaheimildir sem vitnandi skjöl; að leyfa greinar þar gæfi „hvaða fræðigreinar fjalla um þennan dóm“. Metið þegar greinarnar eru inni.
- **Framendasíða fyrir yfirferð** ofan á YAML-skrárnar, ef töfluvinnan reynist þung.
