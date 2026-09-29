# Lausnir v2 — Wiki

Uppflettirit yfir uppbyggingu kerfisins. Skrifað út frá raunverulegum kóða og lifandi gagnagrunni 28. júlí 2026 — ekki úr minni eða eldri skjölum.

**Hvað er Lausnir?** Íslenskt lögfræðigagnasafn. Safnar, staðlar og birtir dóma, úrskurði, álit, lagasafn og fræðirit frá ~63 heimildum í PostgreSQL, með íslenskri fulltextaleit.

## Efnisyfirlit

| Síða | Um hvað |
|---|---|
| [01 — Arkitektúr](01-arkitektur.md) | Þriggja laga módelið (RAW/NORM/RENDER), gagnaflæði, möppuskipan |
| [02 — Gagnagrunnur](02-gagnagrunnur.md) | Töflur, dálkar, vísar, raunverulegar tölur |
| [03 — Heimildir](03-heimildir.md) | `SourceConfig`, heimildaskrá, flokkunartréð |
| [04 — Innflutningur](04-innflutningur.md) | Import-pípan, skriptur, checkpoints, `update_all.py` |
| [05 — Leit](05-leit.md) | Leitarhamir, `fts_is`, efnisgreinar (`passages`), regex, lagaákvæði |
| [06 — API](06-api.md) | FastAPI endapunktar og svarform |
| [07 — Framendi](07-framendi.md) | React-appið, síður, íhlutir, leit í bók |
| [08 — Þróun](08-throun.md) | Umhverfi, skipanir, próf, verkflæði |
| [09 — Gildrur og staða](09-gildrur.md) | Þekktar gildrur, ólokið, ósamræmi |
| [10 — MCP-þjónn](10-mcp.md) | Read-only MCP fyrir LLM-viðskiptavini: leit, efnisgreinar, skjöl, SQL með lesaðgangi |

## Kerfið í tölum (27.09.2026)

| | |
|---|---|
| Skjöl | **93.048** |
| Heimildir | **111** (63 efnisheimildir + 48 lagasafnskaflar) |
| Efnisgreinar | 1.781.516 |
| Tengingar (áfrýjanir) | 23.824 |
| Stærð gagnagrunns | 76 GB |
| Stærð `Lausnir_Data/` | 49 GB |
| Fulltextaleit (`fts_is`) | 93.048 / 93.048 (100%) |
| Merkingarleit (`embedding`) | 0 / 93.048 (ekki byggt) |

## Tæknistafli

**Bakendi:** Python 3.13, FastAPI, SQLAlchemy 2 (async) + asyncpg, PostgreSQL + pgvector + pg_trgm, `islenska` (BÍN) fyrir lemmun, pdfplumber + PyMuPDF fyrir PDF, Playwright fyrir erfiða vefi, Anthropic API fyrir stök AI-verk.

**Framendi:** React 19, TypeScript, Vite, TailwindCSS 4, TanStack Query, React Router 7, react-markdown. Vitest + Testing Library.

**Umsjón:** `uv` fyrir Python, `npm` fyrir framenda.
