# Lausnir v2 — Architecture Guide

> **Full system wiki: [`docs/wiki/`](docs/wiki/README.md)** — architecture, DB schema, sources, import pipeline, search, API, frontend, gotchas. Verified against live code and DB on 2026-07-28. Where this file and the wiki disagree, the wiki is newer (`docs/wiki/09-gildrur.md` tracks known inconsistencies).

## Project Purpose
Icelandic legal research platform. Collects, normalises, and serves court verdicts and rulings from ~100 sources (~100k–1M documents). Stores them in PostgreSQL + pgvector for full-text and semantic search.

## Three-Layer Architecture

```
LAYER 1: RAW      — immutable, exactly what the API/PDF returned
LAYER 2: NORM     — validated, structured DB columns
LAYER 3: RENDER   — derived output (fully reconstructable from NORM)
```

**Golden rule**: RENDER is never source of truth. If raw_api_data and a DB column disagree, trust raw_api_data.

### Layer 1 – RAW
- `raw_api_data JSONB` — full API response, never mutated
- PDF bytes on disk at `Lausnir_Data/raw/{short_name}/{verdict_filename}.pdf` (books: `{external_id}`; most theses: their import-time `thesis_stem()`) — find them with `engine.processors.stored_pdf.find_stored_pdf()`
- Written once at import, never updated (only appended to if API adds fields)

### Layer 2 – NORM
Structured, validated DB columns:
- `case_number`, `document_date`, `court`, `verdict_type`, `instance_tier`
- `plaintiffs JSONB`, `defendants JSONB` (structured arrays, not raw strings)
- `keywords JSONB`, `summary TEXT`
- `body_text TEXT` — current court body, preamble stripped
- `lower_body_text TEXT` — embedded lower court text (NULL if none)
- `embedding vector(3072)` — reserved for semantic search; no rows filled and no vector search yet

### Layer 3 – RENDER
Always derivable from NORM. Never store separately unless caching for performance:
- `.md` file on disk — `to_markdown(doc, config)` in `engine/processors/renderer.py`
- `urlausn` — `to_urlausn(doc, config)` (e.g. "Hrd. E-25/2020 5. maí 2020 – Dómur")

## Validation Rules

Enforced by `engine/processors/validator.py`:
- `case_number` must match source's expected format; prefix number > 1500 flagged as likely parse error
- `document_date` must be ≥ 1900 and ≤ today+1
- `verdict_type` must be in allowed set
- `body_text` must be ≥ 200 chars (or flagged short)
- `plaintiffs`/`defendants` must be non-empty for adversarial cases
- `keywords` flagged missing if empty; no minimum count enforced

Validation errors stored in `validation_errors JSONB` column — never silently dropped.

See `docs/wiki/` for DB schema, `SourceConfig`, directory structure, the new-source checklist (or run the `lausnir-new-source` skill), environment setup, dev commands, and the full gotchas list.
