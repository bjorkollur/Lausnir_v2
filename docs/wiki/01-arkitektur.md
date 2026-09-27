# 01 — Arkitektúr

← [Wiki-forsíða](README.md)

## Þriggja laga módelið

Grundvallarreglan í öllu kerfinu. Skilgreind í `CLAUDE.md` og útfærð í `engine/database/models.py`.

```
LAG 1: RAW     — óbreytanlegt, nákvæmlega það sem API/PDF skilaði
LAG 2: NORM    — staðfest, skipulögð gildi í DB-dálkum
LAG 3: RENDER  — afleitt (má alltaf endurgera úr NORM)
```

**Gullna reglan:** RENDER er aldrei sannleikurinn. Ef `raw_api_data` og DB-dálkur stangast á, gildir `raw_api_data`.

### Lag 1 — RAW
- `documents.raw_api_data JSONB` — allt API-svarið, aldrei breytt eftir fyrstu skrift
- PDF-bæti á diski: `Lausnir_Data/raw/{short_name}/{external_id}.pdf`

### Lag 2 — NORM
Skipulagðir, staðfestir dálkar: `case_number`, `document_date`, `court`, `verdict_type`, `instance_tier`, `plaintiffs`, `defendants`, `keywords`, `summary`, `body_text`, `lower_body_text`, `case_type`, `provisions`, `isbn`, `publisher`.

### Lag 3 — RENDER
Alltaf afleiðanlegt úr NORM, aldrei geymt sem sannleikur:
- `.md` skrá á diski — `renderer.to_markdown(doc, config)`
- `urlausn` tilvitnun — `renderer.to_urlausn(doc, config)`, t.d. `"Hrd. 59/2025 10. júní 2026 – Dómur"`
- Markdown í API-svari `/api/document/{id}` er reiknað á staðnum við hverja beiðni

`scripts/backfill_render_all.py` endurgerir allar `.md` skrár fyrir heimild.

## Gagnaflæði

```
       Heimild (GraphQL / REST / HTML / PDF / dropfolder)
                          │
                          ▼
              scripts/import_{heimild}.py
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   raw_api_data     pdf_parser      book_metadata
   (LAG 1)          (PDF → texti)   (ISBN → OpenLibrary/leitir.is)
          │               │               │
          └───────────────┼───────────────┘
                          ▼
              processors/extractor.py          ← ein fall per heimild
                          │                       (_EXTRACTORS registry)
                          ▼
              processors/validator.py           ← villur skráðar, aldrei hent
                          │
                          ▼
                   documents (LAG 2)
                          │
          ┌───────────────┼──────────────────┬─────────────────┐
          ▼               ▼                  ▼                 ▼
   renderer.py     backfill_fts_is    backfill_passages  backfill_cited_
   (.md, LAG 3)    (BÍN-lemmun)       (passages)         provisions
                          │                  │                 │
                          └──────────────────┴─────────────────┘
                                             ▼
                              engine/search/queries.py
                                             ▼
                                engine/api/app.py (FastAPI)
                                             ▼
                                    frontend/ (React)
```

## Möppuskipan

```
engine/                       Bakendi-bókasafn (ekkert keyranlegt sjálft)
  api/app.py                  FastAPI-appið, allir endapunktar (306 línur)
  config/
    sources.py                SourceConfig + SOURCE_REGISTRY (937 línur)
    source_groups.py          Flokkunartré fyrir leitarsvið (243 línur)
  database/
    connection.py             Async engine + AsyncSessionLocal
    models.py                 ORM: Source, Document, DocumentLink, Passage
    renderer.py               ⚠️ DAUÐUR KÓÐI — enginn flytur hann inn (sjá 09-gildrur)
  processors/
    extractor.py              Hráugögn → NORM-dálkar, ein fall per heimild (2508 línur)
    validator.py              Staðfesting, skilar villulista (101 lína)
    renderer.py               NORM → markdown/urlausn (548 línur)
    pdf_parser.py             PDF → texti með fyrirsagna-/töfluskynjun (453 línur)
    segmenter.py              Skjal → efnisgreinar á fyrirsögnum/málsgreinum, 250–400 orð (194 línur)
    sections.py               `section_kind` flokkun efnisgreina (101 lína)
    lemmatizer.py             BÍN-lemmun fyrir íslenska fulltextaleit (34 línur)
    provision_extractor.py    Finnur lagatilvísanir í texta (204 línur)
    lagasafn_parser.py        Alþingis-lagasafn HTML → skipulögð ákvæði (258 línur)
    book_metadata.py          ISBN → OpenLibrary/leitir.is/Claude (246 línur)
    court_names.py            Dómstólaskammstafanir
    http_utils.py             Endurtekningar, WAF-örugg sókn
  search/
    queries.py                Öll leitarrökfræði, hrátt SQL (703 línur)
    passage_index.py          Byggir/heldur `passages` uppfærðum (`passage_hash`) (112 línur)
    passage_search.py         `keyword`/`proximity` leit í `passages` (291 lína)

scripts/                      50 keyranlegar skriptur (import_*, backfill_*, migrate_*, sync_*)
frontend/                     React + Vite appið
tests/                        18 pytest-skrár
docs/                         Skjöl (þ.m.t. þetta wiki)
alembic/                      `0001_baseline` → `0002_passages` → `0003_drop_document_chunks`
checkpoints/                  21 JSON-skrá með framvindu innflutnings
```

### Gögn utan repo

`DATA_DIR` = `/Volumes/RuleOfLaw/Lausnir_Data` (49 GB):

```
raw/{short_name}/         Upprunaleg PDF-skjöl
markdown/{short_name}/    Afleiddar .md skrár (LAG 3)
dropfolder/               Bækur sem bíða innflutnings (logfraedibaekur)
embeddings/               (frátekið — engar embeddings byggðar enn)
```
