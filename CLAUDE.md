# Lausnir v2 — Architecture Guide

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
- PDF bytes on disk at `Lausnir_Data/raw/{short_name}/{external_id}.pdf`
- Written once at import, never updated (only appended to if API adds fields)

### Layer 2 – NORM
Structured, validated DB columns:
- `case_number`, `document_date`, `court`, `verdict_type`, `instance_tier`
- `plaintiffs JSONB`, `defendants JSONB` (structured arrays, not raw strings)
- `keywords JSONB`, `summary TEXT`
- `body_text TEXT` — current court body, preamble stripped
- `lower_body_text TEXT` — embedded lower court text (NULL if none)
- `embedding vector(3072)` — text-embedding-3-large on body_text

### Layer 3 – RENDER
Always derivable from NORM. Never store separately unless caching for performance:
- `.md` file on disk — `Renderer.to_markdown(doc)` 
- `urlausn` — `Renderer.to_urlausn(doc)` (e.g. "Hrd. E-25/2020 5. maí 2020 – Dómur")

## DB Schema

### `sources`
```sql
id           UUID PRIMARY KEY DEFAULT gen_random_uuid()
short_name   TEXT UNIQUE NOT NULL      -- 'heradsdomstolar', 'haestirettur'
display_name TEXT NOT NULL             -- 'Héraðsdómstólar'
base_url     TEXT
collector_config JSONB                 -- see SourceConfig below
created_at   TIMESTAMPTZ DEFAULT now()
```

### `documents`
```sql
id              UUID PRIMARY KEY DEFAULT gen_random_uuid()
source_id       UUID REFERENCES sources(id)
external_id     TEXT NOT NULL           -- API id or url-hash, unique within source
url             TEXT                    -- canonical URL for this document
raw_api_data    JSONB                   -- immutable API payload

-- Structured fields (extracted from raw_api_data at import)
case_number     TEXT
document_date   DATE
court           TEXT                    -- abbreviation: 'Hrd.', 'Lrd.', 'Hérd. Rvk.'
verdict_type    TEXT                    -- 'Dómur'|'Úrskurður'|'Niðurstaða'|'Álit'
instance_tier   SMALLINT                -- 1=first, 2=appeals, 3=supreme
plaintiffs      JSONB                   -- [{name, lawyer}]
defendants      JSONB                   -- [{name, lawyer}]
keywords        JSONB                   -- [str]
summary         TEXT                    -- Reifun/abstract
body_text       TEXT                    -- current court body text
lower_body_text TEXT                    -- embedded lower court text (NULL if none)

-- Search
embedding       vector(3072)            -- text-embedding-3-large on body_text
tsvector_col    TSVECTOR GENERATED ALWAYS AS (
                  to_tsvector('simple', coalesce(body_text,''))
                ) STORED

-- Paths
markdown_path   TEXT                    -- absolute path to .md file

-- Metadata
validation_errors JSONB                 -- [{field, message}] or NULL
created_at      TIMESTAMPTZ DEFAULT now()
updated_at      TIMESTAMPTZ DEFAULT now()

UNIQUE (source_id, external_id)
```

## SourceConfig

Each source is configured in `engine/config/sources.py`:

```python
@dataclass
class SourceConfig:
    short_name: str
    display_name: str
    abbreviation: str            # Court abbreviation for `court` column
    instance_tier: int           # 1/2/3
    has_lower_court: bool        # Whether body may embed lower court text
    parse_parties: str           # 'gegn' | 'role_based' | 'none'
    verdict_type_default: str    # 'Dómur' | 'Úrskurður' | etc.
    case_number_prefix: str      # e.g. 'E-', 'S-', '' for validation
    pdf_crop: dict | None        # header_pt, footer_pt, etc.
```

Add per-source config instead of `if source_short_name == "X":` branches in pipeline.

## Import Pipeline

Each source has a script in `scripts/import_{short_name}.py`. Structure:

```python
# 1. Fetch raw API data (GraphQL / HTTP / HTML scrape)
raw = fetch_from_api(page=N)

# 2. Extract structured fields using SourceConfig
doc = Extractor(config).extract(raw)

# 3. Validate before storing
errors = Validator(config).validate(doc)
if errors:
    doc.validation_errors = errors  # store but don't skip

# 4. Save to DB
await session.merge(doc)
await session.commit()

# 5. Render .md and urlausn (derived — regenerate on demand)
Renderer(config).write_markdown(doc)
```

## Rendering

`engine/processors/renderer.py` — pure functions, no DB access:

```python
def to_markdown(doc: Document, config: SourceConfig) -> str:
    """Builds .md from DB fields. No external calls."""
    ...

def to_urlausn(doc: Document, config: SourceConfig) -> str:
    """'Hrd. E-25/2020 5. maí 2020 – Dómur'"""
    ...
```

Call `Renderer.rebuild_all(source_short_name)` to regenerate all .md files for a source.

## Validation Rules

Enforced by `engine/processors/validator.py`:
- `case_number` must match source's expected format
- `document_date` must be ≥ 1900 and ≤ today+1
- `verdict_type` must be in allowed set
- `body_text` must be ≥ 200 chars (or flagged short)
- `plaintiffs`/`defendants` must be non-empty for adversarial cases
- `keywords` recommended ≥ 3 items

Validation errors stored in `validation_errors JSONB` column — never silently dropped.

## Directory Structure

```
engine/
  config/
    sources.py          # SourceConfig dataclass + SOURCE_REGISTRY
  database/
    connection.py       # async SQLAlchemy engine
    models.py           # Document, Source ORM models
  processors/
    extractor.py        # Extract structured fields from raw_api_data
    validator.py        # Validate before storage
    renderer.py         # Derive .md and urlausn from DB fields
    pdf_reader.py       # PDF → text (pdfplumber + PyMuPDF)
    http_utils.py       # Retry logic, WAF-safe fetch
    parties_parser.py   # Parse plaintiff/defendant from text
scripts/
  import_{source}.py    # One per source
  backfill_{source}.py  # Re-process existing docs
  migrate_v1.py         # ETL from old lausnir DB to v2 schema
tests/
```

## Adding a New Source — Checklist

```
[ ] SourceConfig entry in engine/config/sources.py
[ ] COURT_ABBR if needed (for PDF extraction court name)
[ ] Import script: scripts/import_{short_name}.py
[ ] Test 3 docs in DB: case_number, verdict_type, parties, body_text non-empty
[ ] Verify no validation_errors for ≥90% of docs
[ ] sources_catalogue.md updated
```

## Environment

```
DATABASE_URL=postgresql+asyncpg://geiri@localhost/lausnir_v2
DATA_DIR=/Volumes/RuleOfLaw/Lausnir_Data
ANTHROPIC_API_KEY=...
```

## Running

```bash
# Import a source
uv run python scripts/import_haestirettur.py

# Rebuild all .md files for a source
uv run python scripts/backfill_render.py --source heradsdomstolar

# Run under supervisor (auto-restart on crash)
nohup ./scripts/supervised.sh scripts/import_X.py /tmp/X.log > /tmp/X_sup.log 2>&1 &
```

## Migration from v1

Script: `scripts/migrate_v1.py`
- Connects to old `lausnir` DB on 192.168.1.X (local Mac)
- Reads `raw_text`, `parties`, `keywords`, etc.
- Re-extracts `body_text` using new Extractor
- Validates and writes to `lausnir_v2`
- Writes validation report to `/tmp/migration_report.json`
