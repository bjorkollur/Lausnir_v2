# Sources Catalogue — Lausnir v2

Yfirlit yfir allar heimildir í gagnagrunni. Uppfært: júní 2026.

**Heildartala skjala:** ~86.614  
**Heimildir:** 59 (59 með gögn)

---

## 1. Dómstólar — island.is GraphQL

API: `https://island.is/api/graphql` + Next.js detail via `/_next/data/{build_id}/domar/{id}.json`

| short_name | Heiti | Skst. | Tier | Aðilar | Skjöl | Tímabil | Athugasemdir |
|---|---|---|---|---|---|---|---|
| `haestirettur` | Hæstiréttur | Hrd. | 3 | gegn | 12.199 | 1999–2026 | richText + PDF fallback |
| `landsrettur` | Landsréttur | Lrd. | 2 | gegn | 6.100 | 2018–2026 | PDF-only; validation_errors = missing keywords (eðlilegt) |
| `heradsdomstolar` | Héraðsdómstólar | Hérd. | 1 | role_based | 24.040 | 2002–2026 | PDF-only; court í skammstöfun inniheldur staðsetningu, t.d. `Hérd. Rvk.` |
| `endurupptokudomur` | Endurupptökudómur | Eud. | 3 | role_based | 98 | 2021–2026 | PDF-only (`pdfString`); lykilorð sjaldgæf |
| `malskotsbeidnir` | Málskotsbeiðnir Hæstaréttar | Hrd. málsk. | 3 | gegn | 1.250 | 2013–2026 | verdict_type: Ákvörðun; engin reifun |

---

## 2. Umboðsmaður Alþingis

API: `https://umbodsmadur.is` (sér scraper)

| short_name | Heiti | Skst. | Tier | Aðilar | Skjöl | Tímabil | Athugasemdir |
|---|---|---|---|---|---|---|---|
| `umbodsmadur` | Umboðsmaður Alþingis | UA | 1 | none | 5.269 | 1987–2026 | verdict_types: Álit, Bréf |

---

## 3. Kærunefndir — stjórnarráðið

API: `https://www.stjornarradid.is/gogn/urskurdir-og-alit-/`

| short_name | Heiti | Skst. | Aðilar | Skjöl | Tímabil | Athugasemdir |
|---|---|---|---|---|---|---|
| `kaeruna_utlend` | Kærunefnd útlendingamála | KNU | none | 3.828 | 2015–2024 | |
| `knhus` | Kærunefnd húsamála | KNHÚS | gegn | 1.949 | 1995–2026 | |
| `kaeruna_utbod` | Kærunefnd útboðsmála | KÚ. | gegn | 1.180 | 2001–2023 | |
| `kaeruna_jafnr` | Kærunefnd jafnréttismála | KJM. | none | 354 | 1991–2026 | |
| `afryjunarnefnd_haskoli` | Áfrýjunarnefnd í kærumálum háskólanema | ÁKH. | gegn | 25 | 2000–2025 | |

---

## 4. Úrskurðarnefndir — stjórnarráðið

| short_name | Heiti | Skst. | Aðilar | Skjöl | Tímabil | Athugasemdir |
|---|---|---|---|---|---|---|
| `urvel` | ÚRV velferðarmála – Almannatryggingar | ÚRVEL | none | 3.346 | 2001–2026 | |
| `urvel_atv` | ÚRV velferðarmála – Atvinnuleysistryggingar | ÚRVEL.Atv. | gegn | 1.619 | 2008–2025 | |
| `urvel_felag` | ÚRV velferðarmála – Félagsþjónusta og húsnæðismál | ÚRVEL.Fþh. | gegn | 779 | 2010–2022 | |
| `urvel_faed` | ÚRV velferðarmála – Fæðingar- og foreldraorlof | ÚRVEL.Fo. | gegn | 543 | 2001–2013 | Lokað 2013 |
| `urvel_greid` | ÚRV velferðarmála – Greiðsluaðlögunarmál | ÚRVEL.Ga. | gegn | 405 | 2011–2016 | Lokað 2016 |
| `urvel_barna` | ÚRV velferðarmála – Barnaverndarmál | ÚRVEL.Bv. | gegn | 342 | 2012–2026 | |
| `urnefnd_uppl` | Úrskurðarnefnd um upplýsingamál | ÚU. | none | 862 | 1997–2023 | |
| `urnefnd_hollusta` | ÚRN samkvæmt lögum um hollustuhætti og mengunarvarnir | ÚRHOL. | none | 91 | 2000–2014 | Lokað 2014 |
| `urnefnd_verdtryggt` | ÚRN um leiðréttingu verðtryggðra fasteignaveðlána | ÚRVFL. | none | 79 | 2015–2016 | Lokað 2016 |
| `urnefnd_raforka` | Úrskurðarnefnd raforkumála | ÚRR. | none | 18 | 2014–2026 | |
| `urnefnd_kosninga` | Úrskurðarnefnd kosningamála | ÚRKM. | none | 9 | 2022–2026 | |
| `mannanafnanefnd` | Mannanafnanefnd | MNN | none | 1.601 | 2001–2024 | |
| `yfirfasteignamat` | Yfirfasteignamatsnefnd | YFM. | none | 398 | 1975–2025 | 270/667 eru skannuð PDF — OCR þyrfti fyrir þau |
| `matsnefnd_eignarnam` | Matsnefnd eignarnámsbóta | MN.Enb. | gegn | 298 | 1977–2025 | |
| `matsnefnd_lax` | Matsnefnd samkvæmt lögum um lax- og silungsveiði | M.Lax. | none | 14 | 1996–2005 | Lokað 2005 |
| `endurupptakunefnd` | Endurupptökunefnd | Enduruppt. | none | 107 | 2013–2020 | Lokað 2020 — leysti af hólmi Endurupptökudómur |
| `yfirskattanefnd` | Yfirskattanefnd | YSKN. | none | 4.155 | 1973–2026 | HTML scrape; date aðeins 2016+; case_number alltaf úr listing |

---

## 5. Sérstakir dómstólar — stjórnarráðið

| short_name | Heiti | Skst. | Tier | Aðilar | Skjöl | Tímabil | Athugasemdir |
|---|---|---|---|---|---|---|---|
| `felagsdomur` | Félagsdómur | Féld. | 1 | gegn | 304 | 2000–2026 | 106 söguleg frá stjornarradid.is (2000–2010); 198 frá felagsdomur.is (2010–2026) með F- forskeyti |
| `lausn_stundar` | Nefnd vegna lausnar um stundarsakir | Nefnd.Lausn | 1 | none | 27 | 2002–2019 | |
| `landsdomar` | Landsdómur | Ld. | 3 | gegn | 5 | 2011–2012 | HTML scrape; 4/5 PDF skönnuð→Docling OCR; engin mál síðan 2012 |

---

## 6. Ráðuneytaúrskurðir — stjórnarráðið

| short_name | Heiti | Skst. | Skjöl | Tímabil | Athugasemdir |
|---|---|---|---|---|---|
| `innvida` | Úrskurðir á málefnasviði innviðaráðuneytisins | Ú.Ir. | 629 | 1996–2012 | Söguleg, lokað 2012 |
| `velferdar_raduneyti` | Úrskurðir velferðarráðuneytisins 2011–2018 | ÚRVEL.11-18 | 236 | 2005–2018 | Söguleg, lokað 2018 |
| `sjavarutv` | Úrskurðir um sjávarútveg og fiskeldi | ÚRSJÁ. | 203 | 2001–2026 | |
| `heilbrigdi_raduneyti` | Úrskurðir heilbrigðisráðuneytis | Ú.Hr. | 174 | 2019–2026 | |
| `stjornsyslu_kaerur` | Stjórnsýslukærur – úrskurðir | Ú.SSK | 156 | 2007–2026 | |
| `umhverfi_raduneyti` | Úrskurðir umhverfis-, orku- og loftslagsráðuneytisins | Ú.UOLr. | 134 | 2000–2018 | Söguleg, lokað 2018 |
| `matvael_land` | Úrskurðir um matvæli og landbúnað | ÚRMAT. | 125 | 1999–2025 | |
| `mennta_raduneyti` | Úrskurðir mennta- og barnamálaráðuneytisins | Ú.MBr. | 108 | 1997–2024 | |
| `felag_hus_raduneyti` | Úrskurðir félags- og húsnæðismálaráðuneytisins | Ú.FHr. | 63 | 2018–2025 | |
| `sveitarstj_alit` | Álit á sviði sveitarstjórnarmála | Sveitastj.M. | 50 | 2012–2025 | verdict_type: Álit |
| `ferdathjod` | Úrskurðir ferðaþjónusta | ÚRFÞ. | 41 | 2014–2025 | |
| `mnh_raduneyti` | Úrskurðir menningar-, nýsköpunar- og háskólaráðuneytisins | Ú.MNHr. | 11 | 2017–2025 | |
| `innanr_utl` | Úrskurðir innanríkisráðuneytisins – útlendingamál (–2015) | Ú.Ir.Ú. | 12 | 2014 | Söguleg, lokað 2015; IRR-númer (t.d. IRR12030163) |
| `vidskiptamal` | Úrskurðir viðskiptamál | ÚRVM. | 14 | 2020–2025 | |
| `forseta_raduneyti` | Úrskurðir forsætisráðuneytisins | Ú.Fr. | 5 | 2013–2016 | Söguleg |
| `kosninga_ursk` | Úrskurðir vegna kosninga | ÚRKOSN. | 4 | 2012–2019 | |
| `utanr_raduneyti` | Úrskurðir utanríkisráðuneytisins | Ú.ur. | 1 | 2021 | |
| `landskjor` | Úrskurðir landskjörstjórnar | LKS. | 78 | 2024 | |

---

## 7. Sérstakir eftirlitsaðilar

| short_name | Heiti | Skst. | Tier | Aðilar | Skjöl | Tímabil | Athugasemdir |
|---|---|---|---|---|---|---|---|
| `samkeppni` | Samkeppniseftirlitið | SKE | 1 | none (plaintiffs=Fyrirtæki) | 1.646 | 1994–2026 | WP REST; 3 gerðir: Ákvörðun/Úrskurður/Álit; 10 pending samrunamál sleppt |
| `fjolmidlanefnd` | Fjölmiðlanefnd | FMN. | 1 | none | 81 | 2012–2026 | WP REST cat=6; summary=HTML, body_text=PDF; 2/81 án PDF (7/2016 404, 1/2018 skannað) |
| `fjarskiptastofa` | Fjarskiptastofa | FST. / PFS. | 1 | defendants | 730 | 1999–2026 | Blazor/Playwright scrape; FST. ≥2021-07-01, PFS. eldra; ÚRSK-PDF via Docling OCR (Tesseract/isl) |
| `samgongustofa` | Samgöngustofa | SGS. | 1 | none | ~524 | 2011–2026 | Contentful rich-text síða; PDF-tenglar úr assets.ctfassets.net; dagsetning neðst í PDF |
| `kaeruna_voruthjonusta` | Kærunefnd vöru- og þjónustukaupa | KVÞ. | 1 | none | 595 | 2020–2026 | POST /dashboard/odr/rulings — opið API án auðkenningar; keywords úr subject; dagsetning úr PDF |
| `urskurdarnefnd_logmanna` | Úrskurðarnefnd lögmanna | ÚLM. | 1 | gegn | 453 | 2004–2025 | HTML scrape; reifun milli ÚRSKURÐUR: og Málsatvik; aðilar nafnlægar (A, B lögmanni) |
| `uua` | Úrskurðarnefnd umhverfis- og auðlindamála | UUA. | 2 | none | 2.960 | 1998–2026 | HTML scrape; ein listing síða með öllum tenglum; keyword=staðarheiti úr H1; áður: úrskurðarnefnd skipulags- og byggingarmála |
| `enf` | Eftirlitsnefnd fasteignasala | ENF. | 1 | none | 125 | 2015–2026 | PDF-only; 3 tegundir: Álit (88), Ákvörðun (11), Umburðarbréf (26 skannað→Docling) |
| `hugverkastofa` | Hugverkastofa | HVS. | 1 | gegn | 1.108 | 1985–2026 | Umbraco Delivery API; öll PDF; 9 verdict_types (Andmæli/Áfrýjun/Niðurfelling vörumerki o.fl.); aðilar úr content-reit + PDF-fallback |

---

## 8. Lögfræðiritgerðir

Gögn: `https://skemman.is` (DSpace OAI-PMH + HTML scrape)

| short_name | Heiti | Skst. | Tier | Aðilar | Skjöl | Tímabil | Athugasemdir |
|---|---|---|---|---|---|---|---|
| `logfraediritgerdir` | Lögfræðiritgerðir (Skemman) | Ritg. | 1 | none | 4.276 | 2006–2026 | 2.807 opin (PDF→poppler); 1.469 læst (body_text=NULL); plaintiffs=höfundur; case_number=titill; verdict_type=Ritgerð |

---

## 9. Persónuvernd

API: `https://island.is/api/graphql` — `getGenericListItems` (list) + `getGenericListItemBySlug` (detail)

| short_name | Heiti | Skst. | Tier | Aðilar | Skjöl | Tímabil | Athugasemdir |
|---|---|---|---|---|---|---|---|
| `personuvernd` | Persónuvernd | Persónuvnd. | 1 | none | 1.238 | 1996–2026 | 1.002/1.238 með case_number; 236 án (ráðgjöf, fréttir); keywords alltaf tóm |

---

## Lagasafn Alþingis

**Uppruni:** https://www.althingi.is/lagasafn/zip/nuna/allt.zip (ZIP, ISO-8859-1)
**Uppfærsla:** `scripts/sync_lagasafn.py` (MD5-miðað, skoðar Last-Modified haus)
**Skjöl:** 915 lög, eitt per HTML-skrá í ZIP
**Greinar:** Geymdar í `provisions JSONB` dálki; `body_text` er sniðinn sem `"N. gr.\n{texti}"` per grein til grein-meðvitaðra snippets
**Grein-leit:** `GET /api/provision?law=33/1944&gr=1`

| short_name | display_name | Tier | Skjöl |
|---|---|---|---|
| lagasafn_01 | 1. Stjórnskipunarlög o.fl. | 0 | 12 |
| lagasafn_02 | 2. Mannréttindi | 0 | 17 |
| lagasafn_03 | 3. Forseti Íslands | 0 | 4 |
| lagasafn_04 | 4. Alþingi og lagasetning | 0 | 6 |
| lagasafn_05 | 5. Dómstólar og réttarfar | 0 | 28 |
| … (48 heimildir) | … | 0 | … |
| lagasafn_48 | 48. Byggðamál | 0 | — |

**Verdict types:** Lög, Forsetaúrskurður, Forsetabréf, Auglýsing, Reglugerð, Samþykkt, Tilskipun, Bréf

---

## Tæknileg yfirlit

### API-heimildir

| API | Heimildir | Athugasemdir |
|---|---|---|
| island.is GraphQL | haestirettur, landsrettur, heradsdomstolar, endurupptokudomur, malskotsbeidnir, personuvernd | richText, pdfString, eða Contentful rich text |
| umbodsmadur.is | umbodsmadur | Sér scraper |
| stjórnarráðið | ~40 heimildir | `Committee=` filter; disjoint API-hegðun þegar >200 mál |

### Þekkt vandamál

| Vandamál | Hefur áhrif á | Staða |
|---|---|---|
| Skannuð PDF (engin texti) | yfirfasteignamat (270/667 mál) | OCR óleyst |
| Disjoint API-hegðun | stjórnarráðið-heimildir með >200 mál | Leyst — sameining beggja sets |
| Missing keywords | Flestar heimildir | Eðlilegt — validation warning, ekki villa |
| felagsdomur validation_errors=100% | felagsdomur (106 mál) | Rannsaka — case_number og date kunna að vera NULL |
