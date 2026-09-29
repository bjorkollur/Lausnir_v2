# 04 — Innflutningur

← [Wiki-forsíða](README.md)

## Grunnform import-skriptu

Hver heimild hefur `scripts/import_{short_name}.py`. Formið er alltaf það sama:

```python
# 1. Sækja hrágögn (GraphQL / REST / HTML-skröpun / PDF)
raw = fetch_from_api(page=N)

# 2. Draga út skipulögð gildi með SourceConfig
fields = Extractor(config).extract(raw)

# 3. Staðfesta ÁÐUR en geymt er — villur skráðar, skjali aldrei hent
errors = validate(doc, config)
doc.validation_errors = errors or None

# 4. Upsert í DB (á uq_doc_source_external)
await session.execute(pg_insert(Document).values(...).on_conflict_do_update(...))

# 5. Skrifa .md (RENDER-lag — má alltaf endurgera)
write_markdown(doc, config, vf=verdict_filename)
```

## Skriptuflokkar (61 alls í `scripts/`)

| Forskeyti | Fjöldi | Hlutverk |
|---|---|---|
| `import_*` | 23 | Sækja og flytja inn eina heimild |
| `backfill_*` | 13 | Endurvinna skjöl sem þegar eru í DB |
| `migrate_*` | 5 | Skemabreytingar (`ALTER TABLE`) |
| `sync_*` | 2 | Samstilla lagasafn / stjornarradid |
| `setup_*` | 2 | Búa til vísa (trigram, provisions) |
| Annað | 16 | `update_all`, `link_appeals`, greiningartól |

### Mikilvægustu backfill-skripturnar

| Skripta | Hvað hún gerir | Hvenær þarf að keyra |
|---|---|---|
| `backfill_fts_is.py` | BÍN-lemmar `body_text` → `fts_is` | **Eftir hvern innflutning** — annars finnst skjalið ekki í leit |
| `backfill_passages.py --source X` | Sníður `passages` fyrir skjöl með úreltan/vantandi `passage_hash` | **Eftir hvern innflutning** — keyrt sjálfkrafa af `update_all.py` (`passages_refresh`) |
| `backfill_cited_provisions.py` | Finnur lagatilvísanir í texta | Eftir innflutning ef lagaákvæðaleit á að virka |
| `backfill_render_all.py --source X` | Endurgerir allar `.md` skrár | Eftir breytingu á renderer |
| `backfill_verdict_type.py` | Les `verdict_type` upp á nýtt úr orðalagi skjalsins sjálfs (Hæstiréttur, Landsréttur, héraðsdómstólar) og lagfærir röðina: tegund, `verdict_filename`, endurnefnd `.md`/`.pdf`, `pdf_path`-vísun og `passage_hash = NULL` | Einu sinni (keyrt 29.09.2026, 6.590 skjöl). Aftur aðeins ef reglan í `_detect_verdict_type` breytist — `--dry-run` og `--report` fyrst |
| `backfill_book_metadata.py` | Sækir ISBN/útgefanda/höfunda aftur | Eftir 2026-07-27 skemabreytinguna |
| `link_appeals.py` | Byggir `document_links` áfrýjunarkeðjuna | Eftir innflutning á dómstólum |
| `build_citations.py --all` | Dregur út og leysir tilvitnanir milli dóma (`citations` + afleiddar `cites`-brýr) fyrir öll úrelt skjöl (`citation_hash` vantar/stemmir ekki) | **Eftir hvern innflutning á dómstólaheimildunum sjö** — keyrt sjálfkrafa af `update_all.py` sem skref 5 (`citations_refresh`), sjá „Keyrsluröð" hér að neðan |
| `build_citations.py --relink-unresolved` | Endurleysir `unresolved`/`ambiguous` raðir gegn uppfærðum vísi, **án** endurdráttar | Eftir að nýtt markskjal (sem eldri tilvitnun vísaði ranglega óleyst á) bætist í safnið — keyrt sjálfkrafa af `update_all.py` strax á eftir `--all` (`citations_relink`) |

## `update_all.py` — heildarkeyrsla

```bash
uv run python scripts/update_all.py                 # fullur endurinnflutningur
uv run python scripts/update_all.py --new-only      # aðeins ný skjöl (hratt)
uv run python scripts/update_all.py --only haestirettur landsrettur
uv run python scripts/update_all.py --skip logfraediritgerdir
```

Keyrir hverja heimild í röð, loggar í `/tmp/lausnir_update/{heimild}.log`. Á eftir heimildunum koma þrjú eftirvinnsluskref: `fts_refresh` (3), `passages_refresh` (4) og tilvitnanir (5 — `citations_refresh` + `citations_relink`). `--skip` tekur líka við `fts`, `passages` og `citations` til að sleppa þeim.

**`--new-only`** virkar aðeins fyrir heimildir í `_NEW_ONLY_CAPABLE` (19 heimildir). Þær styðja allar að hætta um leið og þekkt skjal finnst — t.d. `umbodsmadur` byrjar á `max(id)+1` og hættir eftir 50 samfelld 404, `fjolmidlanefnd` les WP REST í dagsetningarröð og hættir við fyrsta þekkta færslu. Stjornarradid-heimildir sleppa þekktum skjölum sjálfgefið.

## Checkpoints

`checkpoints/{heimild}.json` — 21 skrá. Geymir framvindu innflutnings svo hægt sé að halda áfram eftir hrun án þess að byrja upp á nýtt.

## Tilvitnanir milli dóma — `build_citations.py`

Byggir `citations` (tafla, spec `2026-09-29-citations-design.md`) og afleiddar `cites`-brýr í `document_links`. Hreinn útdráttur (`engine/processors/citations.py`) og leysari (`engine/processors/citation_resolver.py`) keyra án gagnagrunnstengingar; skriftan sjálf annast lestur/skrif og `multiprocessing.Pool` (sama mynstur og `backfill_passages.py` — útdráttur í vinnsluferlum, skrif í aðalferli).

**Keyrsluröð eftir innflutning:**

```
backfill_passages.py  →  build_citations.py --all  →  build_citations.py --relink-unresolved
```

`update_all.py` keyrir þetta **sjálfkrafa sem skref 5** (á eftir `passages_refresh`, skref 4): fyrst `build_citations.py --all` (`citations_refresh`), svo `build_citations.py --relink-unresolved` (`citations_relink`), hvort í sinni loggskrá undir `/tmp/lausnir_update/`. Eins og önnur skref stöðva þau ekki keyrsluna þótt þau mistakist — villan er logguð og birtist í lokasamantektinni. `--skip citations` sleppir báðum.

Ástæðan: `build_citations.py` les `summary`/`body_text`/`lower_body_text` beint af `documents`, ekki af `passages`, svo röðin er ekki um gagnaháð milli þeirra tveggja — en efnisgreinarnar verða að vera í lagi **áður** en tilvitnanir eru byggðar, því birtingarlagið (API/framendi/MCP) finnur `anchor` fyrir hverja tilvitnun með því að fletta `char_start` upp í `passages` (sjá `citations`-töfluna í [02-gagnagrunnur](02-gagnagrunnur.md), „Hvers vegna enginn `passage_id`"); úreltar efnisgreinar þar myndu birta ranga eða vantandi `anchor` fyrir annars réttar tilvitnanir. `--relink-unresolved` keyrir síðast og eingöngu gegn `unresolved`/`ambiguous` röðum, án nokkurs endurdráttar — gagnlegt eftir að nýtt skjal bætist í safnið sem eldri tilvitnun vísaði á en gat ekki leyst á sínum tíma.

**Staleness** virkar eins og `passage_hash`: `--all` (sjálfgefið) vinnur aðeins skjöl þar sem `documents.citation_hash` vantar eða stemmir ekki við núverandi `sha256(summary ‖ body_text ‖ lower_body_text)` (`STALE_WHERE` í `citation_build.py`); `--force` vinnur líka óúrelt skjöl.

**Valkostir** (`--help`):

| Valkostur | Þýðing |
|---|---|
| `--all` | Öll úrelt skjöl (`citation_hash` vantar/stemmir ekki) |
| `--force` | Með `--all`/`--source`: einnig óúrelt skjöl |
| `--source SHORT_NAME` | Takmarka við eina heimild |
| `--doc UUID` | Eitt tiltekið skjal |
| `--since YYYY-MM-DD` | Aðeins skjöl með `document_date` frá og með dagsetningunni |
| `--relink-unresolved` | Endurleysa `unresolved`/`ambiguous` raðir án útdráttar |
| `--dry-run` | Draga út + leysa, prenta samantekt, skrifa ekkert |
| `--limit N` | Hámarksfjöldi skjala |
| `--workers N` | Fjöldi vinnsluferla, sjálfgefið `cpu_count() - 1` |

Keyrsla #2 (29.09.2026, eftir lagfæringar á útdrætti og leysara): 44.513 skjöl, 60.669 `citations`-raðir, 22.868 `cites`-brýr, 0 villur, 409 s með 8 vinnsluferlum. Full sundurliðun eftir stöðu/lagi/dómstólapörum: „Niðurstöður keyrslu" í `docs/superpowers/plans/2026-09-29-citations.md`.

## Sérstakar pípur

### Neðanmálsgreinar — aðeins fræðirit

`SourceConfig.has_footnotes=True` (aðeins `logfraediritgerdir` og `logfraedibaekur`). Dómar og úrskurðir hafa engar neðanmálsgreinar og snerta þetta ekki.

**Vandinn:** neðanmálsgrein er þrennt aðskilið á síðunni — yfirskrifað tákn í meginmáli, samsvarandi tala í neðanmálsblokkinni, og texti greinarinnar. `pdftotext` (sem ritgerðirnar notuðu) og `page.extract_text()` lesa blokkina **dálk fyrst**, svo tölurnar koma aðskildar frá textunum (`1\n2\n3\n4` og svo fjórar setningar). Pörunin er þá glötuð.

**Lausnin** (`parse_pdf(footnotes=True)`): endurbyggt út frá staðsetningu orða, svo hver tala helst á sömu línu og sinn texti.

| Merki | Gildi (dæmigert) |
|---|---|
| Meginmál | 10–11 pt |
| Neðanmálstexti | 9 pt, neðri helmingur síðu |
| Yfirskrifað tákn | 6–7 pt, ~3,6 pt ofan grunnlínu |
| Framhaldslína | inndregin (x0 > vinstri jaðar blokkar) |

Skilar GFM: `[^1]` í meginmáli, `[^1]: skýring` í lokin.

⚠️ **Línuhópun þarf vikmörk.** Yfirskrifað tákn situr ofan við grunnlínu, svo `round(top)` setur það á sína eigin línu og þá þekkist það ekki lengur. `_LINE_CLUSTER_TOLERANCE = 5.0` leysir þetta (línubil er 10–14 pt). Án þess fundust aðeins 13 af 28 táknum í prófskjali; með því 25 af 28.

Endurvinnsla: `scripts/backfill_footnotes.py --source X [--redo]`. Les PDF af diski (sækir ekkert aftur). Ritgerða-PDF bera enn nöfn frá því fyrir skráarnafnabreytingu, svo þau eru fundin með `thesis_stem()` — ekki `verdict_filename`.

### Fjórar gildrur sem kostuðu texta (allar leystar)

| Gildra | Afleiðing | Lausn |
|---|---|---|
| Yfirskrifað tákn situr ofan grunnlínu | `round(top)` setti það á eigin línu → þekktist ekki | `_LINE_CLUSTER_TOLERANCE = 5.0` |
| Blokkatilvitnanir eru **líka** í minna letri | Lagatilvitnanir og álit **eyddust** (4.728 stafir í einu skjali) | Neðanmálsblokk = samfelld runa **neðst** á síðu (`_footnote_block_start`), ekki „smátt letur í neðri helmingi“ |
| Línur sem tilheyra grein af fyrri síðu | Þeim var hent þegjandi → **12% textatap** í safnriti | `_parse_footnote_lines` skilar þeim sem „leftovers“ og þær fara aftur í meginmálið |
| Rangstærður texti | 1.263 setningar urðu að fyrirsögnum | Fyrirsögn er **1–2 línur** (`_heading_line_indexes`); lengdarmörk duga ekki |

### Safnrit: númering endurræsist

Afmælisrit og tímaritshefti númera hverja grein frá 1. Í einu 682 síðna safnriti voru **1.053 tilvísanir en aðeins 121 einstakt númer** (`[^3]` kom 21 sinni). Skjalsvíð `[^n]` tenging hefði sent lesandann á ranga grein í 20 af 21 tilviki.

`_numbering_restarts()` greinir þetta (hlutfall einstakra númera < 0,5). Þá eru greinarnar **dregnar út úr meginmálinu og settar í blokk undir hverri síðu** (`> 30. ...`) — þær slíta ekki setningar, en fá engar tenglar.

Ritgerðir eru einshöfundaverk og sleppa: af 400 könnuðum sýndu aðeins 8 (2%) einhverja endurtekningu, allar vægar.

### Öryggisvarnir

- `parse_pdf` fellur aftur í ótengda meðferð ef númering endurræsist
- `import_baekur.extract_text()` og `import_logfraediritgerdir.pdf_bytes_to_text()` hafna fótnótuumferð sem skilar <97% af venjulegum útdrætti
- `backfill_footnotes.py` sleppir skjali sem myndi styttast (`_MIN_LENGTH_RATIO = 0.97`) og skráir það sem `shrunk`

### PDF-heimildir (Hæstiréttur, Landsréttur, Héraðsdómstólar)

`processors/pdf_parser.py` með `pdfplumber`:
- `parse_pdf(bytes, crop)` — texti með fyrirsagnaskynjun (stærð eða letur skv. `PdfCrop`)
- Töfluskynjun: `_is_data_table()` → `_table_to_markdown()`
- `_collapse_spaced_letters()` lagar `S T E F N A` → `STEFNA`
- `docling_ocr_pdf(bytes, timeout)` — OCR-varaleið þegar textalag er tómt

### Lagasafn

`sync_lagasafn.py` + `processors/lagasafn_parser.py`. Sækir ZIP af HTML frá Alþingi, þáttar í skipulögð ákvæði:
```json
provisions: [{num: 218, suffix: "a", text: "...", sub: [{num: 1, text: "..."}]}]
```
Hver kafli (1–48) er sín eigin „heimild" (`lagasafn_01` … `lagasafn_48`).

### Stjornarradid

`sync_stjornarradid.py` + `import_stjornarradid.py --source X [--cid N]`. Ein sameiginleg skripta fyrir ~17 ráðuneytis-/nefndarheimildir sem allar deila sama vefkerfi. Merktar með `stjornarradid_source=True` í `SourceConfig`.

### Lögfræðibækur — dropfolder

Eina heimildin **án ytra API**. Notandi hendir PDF í `{DATA_DIR}/dropfolder/`, svo:

```bash
uv run python scripts/import_baekur.py --dry-run   # prófa fyrst
uv run python scripts/import_baekur.py
```

Pípan:
1. `extract_text()` — `parse_pdf()` með 30 mín OCR-varaleið (bækur eru hundruð síðna)
2. `resolve_book_metadata()` — þrepaskipt: **ISBN úr texta** (með checksum-staðfestingu) → **OpenLibrary** → **leitir.is** (Primo VE) → **skráarnafn + regex** → **Claude API**
3. `build_document()` → `Extractor` → `validate` → upsert
4. Skrifar `.md`, færir PDF í `raw/logfraedibaekur/{external_id}.pdf`
5. Keyrir `backfill_fts_is` og `backfill_passages` sjálfkrafa í lokin

Kortlagning bókagilda (sjá [02-gagnagrunnur](02-gagnagrunnur.md)): titill → `case_number`, höfundar → `plaintiffs` (ein færsla per höfund), ISBN → `isbn`, útgefandi → `publisher`.
