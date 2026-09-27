# 08 — Þróun

← [Wiki-forsíða](README.md)

## Umhverfi

`.env` í rót (sniðmát í `.env.example`):

```bash
DATABASE_URL=postgresql+asyncpg://geiri@localhost/lausnir_v2
DATA_DIR=/Volumes/RuleOfLaw/Lausnir_Data
ANTHROPIC_API_KEY=sk-ant-...
```

**Athugið:** `engine/database/connection.py` kastar `RuntimeError` ef `DATABASE_URL` vantar. Skriptur og eins-skiptis fyrirspurnir þurfa því:

```bash
set -a; . ./.env; set +a
uv run python scripts/...
```

## Skipanir

### Bakendi
```bash
uv run python scripts/import_haestirettur.py        # flytja inn eina heimild
uv run python scripts/update_all.py --new-only      # uppfæra allar heimildir
uv run python scripts/backfill_fts_is.py            # endurbyggja íslenska leitarvísinn
uv run uvicorn engine.api.app:app --reload --port 8077
uv run pytest                                        # öll bakendapróf
uv run pytest tests/test_search_queries.py -v        # ein skrá
uv run ruff check .
```

### Framendi
```bash
cd frontend
npm run dev          # localhost:5173
npm test             # vitest run (69 próf)
npx tsc --noEmit     # typecheck
npm run build
```

### Gagnagrunnur
```bash
psql "postgresql://geiri@localhost/lausnir_v2" -c "\d documents"
psql "postgresql://geiri@localhost/lausnir_v2" -c "select count(*) from documents;"
```

## Próf

**Bakendi:** 18 pytest-skrár í `tests/`. `asyncio_mode = "auto"` er stillt í `pyproject.toml` — engin `@pytest.mark.asyncio` skreyting þarf.

| Skrá | Nær yfir |
|---|---|
| `test_search_queries.py` | Leitarhamir, síur, röðun |
| `test_provision_search.py` | Lagaákvæðaleit |
| `test_source_groups.py` | Flokkunartréð — **tryggir að hver heimild lendi í nákvæmlega einum flokki** |
| `test_sources.py` | `SourceConfig` réttmæti |
| `test_extractor_*.py` | Útdráttur per heimild |
| `test_book_metadata.py` / `test_import_baekur.py` | Bókapípan |
| `test_pdf_parser.py`, `test_segmenter.py`, `test_lagasafn_parser.py`, `test_provision_extractor.py` | Vinnslueiningar |
| `test_models_*.py` | ORM |
| `test_http_utils.py` | Endurtekningarrökfræði |

**Framendi:** 19 skrár, 69 próf (Vitest). Sjá [07-framendi](07-framendi.md).

## Verkflæði með Claude Code

Verkefnið notar `superpowers`-skil:

| Skil | Hvenær |
|---|---|
| `brainstorming` | Hugmynd → hönnun → spec í `docs/superpowers/specs/` |
| `writing-plans` | Spec → verkáætlun í `docs/superpowers/plans/` |
| `subagent-driven-development` | Keyra áætlun, einn undiragent per verk + rýni |
| `finishing-a-development-branch` | Ljúka grein (merge / PR / geyma / henda) |
| `lausnir-new-source` | 7-fasa verkflæði fyrir nýja heimild |
| `ponytail` | YAGNI-agi — einfaldasta virka lausnin |

`docs/superpowers/` geymir 6 specs og 10 áætlanir frá maí–júlí 2026 — góð heimild um *af hverju* hlutir eru eins og þeir eru.

## Skjöl

| Skjal | Innihald |
|---|---|
| `CLAUDE.md` | Arkitektúrleiðbeiningar sem Claude les sjálfkrafa |
| `docs/wiki/` | Þetta wiki |
| `docs/READINESS_PLAYBOOK.md` | Rekstrar- og neyðarhandbók (afritun, AI-réttindi, áhætta) |
| `docs/sources/*.md` | Djúpar nótur um 3 flóknustu heimildirnar |
| `docs/superpowers/specs/` + `plans/` | Hönnun og verkáætlanir |
| `docs/logfraedibaekur-pdf-extraction-investigation.md` | Ólokin rannsókn á PDF-útdrætti |
| `docs/2026-07-27-baekur-metadata-og-leit.md` | Dagbók: bókametadata + leit í bók |
| `sources_catalogue.md` | API-lýsingar per heimild (**úrelt** — sjá gildrur) |

## Endurheimt

Engin gagnagrunnsafritun er tekin — **meðvituð ákvörðun**, ekki gat. Öll frumgögn liggja opinberlega hjá heimildunum, svo endurheimt fer fram með `scripts/update_all.py` án `--new-only`.

Afgangsáhætta: heimildir geta breytt eða fjarlægt eldri gögn, svo full endurheimt endurspeglar það sem heimildin sýnir *þá stundina*.

Sjá `docs/READINESS_PLAYBOOK.md` fyrir ítarlega umfjöllun.
