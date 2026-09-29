# Read-only MCP-þjónn fyrir Lausnir — hönnun

**Dagsetning:** 2026-09-29
**Staða:** Samþykkt hönnun í spjalli 2026-09-29 (leið A, stdio, rannsóknarverkfæri + read-only SQL). Áætlun: `docs/superpowers/plans/2026-09-29-mcp-server.md`.
**Forsaga:** `docs/2026-09-27-mat-a-adferdafraedi-og-llm-leit.md` (kafli 4 og aðgerðaliður í línu 121) lagði til read-only MCP ofan á `queries.py` og að loka skrifaðgangi `mcp-postgres`. `docs/superpowers/specs/2026-09-27-passages-design.md` nefndi verkfæraheitin `search_passages`/`get_passages` sem framtíðarverk.

## 1. Markmið

LLM-viðskiptavinur (Claude Code, Claude Desktop) á sömu vél og gagnagrunnurinn getur leitað í dómasafninu með sömu leit og vefurinn notar, víkkað treff í efnisgreinar, sótt lýsigögn og texta skjals, og vitnað í niðurstöðu með `urlausn` og `anchor` efnisgreinar. Þróandi getur að auki keyrt `SELECT`-fyrirspurnir gegn grunninum í gegnum lesaðgangshlutverk, svo `mcp-postgres` með fullum skrifaðgangi verði óþarft.

**Ekki markmið:** HTTP-tengileið, auðkenning, skrifaðgerðir, embeddings, breytingar á leitarkjarnanum.

## 2. Ákvarðanir sem notandi tók

| Spurning | Val | Afleiðing |
|---|---|---|
| Tengileið | **stdio á sömu vél** | Engin netstilling, engin auðkenning. Viðskiptavinur ræsir þjóninn sem undirferli. |
| Umfang | **Rannsóknarverkfæri + read-only SQL** | Nýtt Postgres-hlutverk `lausnir_ro`. Kemur í stað `mcp-postgres`. |
| Leið | **A: sjálfstæður þjónn ofan á `engine`** | Kallar beint í `search_documents`, `get_passages`, `get_document`, `catalog`, `facet_counts`. Ekki hlíf yfir HTTP-API-ið. |

## 3. Uppbygging

```
engine/mcp/
  __init__.py
  __main__.py      # python -m engine.mcp  → server.main() → MCPServer.run("stdio")
  server.py        # MCPServer("lausnir"), lifespan (init_db), verkfæraskráning
  tools.py         # útfærsla verkfæranna sem hrein async föll (session, **params) → dict
  shaping.py       # stytting og samþjöppun svara (compact_result, truncate_text, cell_truncate)
  sqlguard.py      # SELECT-vörður: validate_sql(sql) -> None | raises SqlRejected
```

- **SDK:** `mcp>=2.2,<3` (opinbera Python-SDK-ið; í 2.x heitir hlífin `mcp.server.mcpserver.MCPServer`, ekki `FastMCP`). Ný háðing í `pyproject.toml`. Verkfæri skráð með `@server.tool()` og fá `Context` til að ná í session úr lifespan.
- **Ræsing:** `uv run --directory /Volumes/RuleOfLaw/Lausnir python -m engine.mcp`. `__main__` les `.env` (sama og `scripts/` gera: `DATABASE_URL_READONLY` verður að vera sett, annars `RuntimeError` með skýrum texta á stderr og útgöngukóði 2).
- **Gagnagrunnstenging:** `engine/database/connection.py::init_db` fær tvo nýja valkosti: `url: str | None = None` (sjálfgefið `DATABASE_URL` eins og nú) og `create_tables: bool = True`. MCP kallar `init_db(url=DATABASE_URL_READONLY, create_tables=False)`. `connect_args` með `plan_cache_mode=force_custom_plan` gildir óbreytt — án þess kemur 400-faldi hægagangurinn (sjá `09-gildrur.md`).
- **Lifespan:** `init_db` einu sinni við ræsingu; hvert verkfærakall opnar `AsyncSessionLocal()` og lokar. Engin deilt session milli kalla.
- **Log:** Allt á **stderr**. Stdout er MCP-rásin. `__main__` stillir `logging.basicConfig(stream=sys.stderr)` áður en `engine` er flutt inn. Próf staðfestir að `import engine.mcp.server` skrifar ekkert á stdout.

## 4. Verkfærin

Öll svör eru JSON-hlutir (structured output). Dagsetningar sem ISO-strengir, UUID sem strengir. Öll verkfæri eru merkt `readOnlyHint=True` í `ToolAnnotations`.

### 4.1 `search`

Inntak (allt valkvætt nema `q`):

| Reitur | Tegund | Sjálfgefið | Athugasemd |
|---|---|---|---|
| `q` | str | — | Leitarstrengur. Má vera tómur ef `scope`/dagsetningar eru gefin (browse). |
| `mode` | `keyword\|exact\|prefix\|substring\|any\|proximity\|regex` | `keyword` | Sama og API. |
| `scope` | list[str] | `None` | Scope-heiti úr `list_sources` (`domstolar`, `haestirettur`, `landsrettur_domar`, …) eða `all`. |
| `date_from`, `date_to` | ISO-dags. str | `None` | |
| `sort` | `relevance\|newest\|oldest` | `relevance` | |
| `section_kind` | list[str] | `None` | `reifun, malsmedferd, malsatvik, malsastaedur, nidurstada, domsord, annad`. Aðeins með `keyword`/`proximity`. |
| `page` | int ≥1 | 1 | |
| `page_size` | int 1–25 | 10 | **Lægra þak en API (100)** til að hlífa samhengisglugga LLM. |

Úttak:

```json
{
  "total": 123, "strict_total": 123, "relaxed": false, "page": 1, "page_size": 10,
  "results": [
    {"doc_id": "…", "urlausn": "Hrd. 123/2020 5. maí 2020 – Dómur", "source": "haestirettur_domar",
     "court": "Hæstiréttur", "case_number": "123/2020", "date": "2020-05-05", "verdict_type": "Dómur",
     "snippet": "… <b>…</b> …", "passage_id": "…", "anchor": "body/II/mgr. 14", "section_kind": "nidurstada",
     "match_tier": 0, "keywords": ["skaðabætur", "líkamstjón"]}
  ],
  "hint": null
}
```

- `keywords` styttur í mest 5. `plaintiffs`/`defendants` **ekki** með (fást í `get_document`).
- `hint` er strengur eða `null`: þegar `total == 0` og `scope` var gefið en `resolve_scope` skilaði tómri síu (óþekkt scope-heiti) → `"Óþekkt scope: X. Kallaðu á list_sources."`. Í dag skilar kjarninn þögult núlli; MCP-lagið greinir þetta með því að kalla `resolve_scope` sjálft áður en það leitar (ódýrt, ein uppfletting í minni). Þegar `relaxed` er satt → `"Færri en 10 skjöl innihalda öll orðin; niðurstöður með match_tier 1–2 innihalda aðeins hluta þeirra."`
- Villur: `SearchError` → verkfæravilla (`isError`) með skilaboðunum úr kjarnanum (t.d. ógilt `section_kind` með lista yfir leyfð gildi).

### 4.2 `passage_context`

Inntak: `passage_id` (str, UUID), `before` (int 0–10, sjálfgefið 2), `after` (int 0–10, sjálfgefið 2).

Útfærsla: fletta upp `document_id, ordinal, layer` efnisgreinarinnar; kalla `get_passages(session, doc_id, from_ordinal=max(0, ordinal-before), to_ordinal=ordinal+after, section_kinds=None, layer=None)`.

Úttak:

```json
{"doc_id": "…", "urlausn": "…", "focus_ordinal": 14, "total_passages": 88,
 "passages": [{"passage_id": "…", "ordinal": 12, "layer": "body", "section_kind": "malsastaedur",
               "anchor": "body/II/mgr. 12", "text": "…"}, …]}
```

Óþekkt `passage_id` → verkfæravilla `"Efnisgrein fannst ekki."`. Ógilt UUID → sama villa (ekki 500).

### 4.3 `get_passages`

Inntak: `doc_id` (str), `from_ordinal` (int ≥0, 0), `count` (int 1–50, 20), `section_kind` (list[str] | None), `layer` (`summary|body|lower_body` | None).

Útfærsla: `get_passages(session, doc_id, from_ordinal=from_ordinal, to_ordinal=from_ordinal+count-1, …)`. Þak 50 er MCP-þak (kjarninn leyfir 200).

Úttak: sama snið og `passage_context` án `focus_ordinal`, með `next_from_ordinal` (int | null) svo LLM geti flett áfram.

### 4.4 `get_document`

Inntak: `doc_id` (str), `max_chars` (int 0–40.000, sjálfgefið 0).

Úttak: allt sem `queries.get_document` skilar (lýsigögn, `summary`, `keywords`, `plaintiffs`, `defendants`, `appeal_links`, `urlausn`) **plús**:

- `outline`: listi `{layer, section_kind, from_ordinal, to_ordinal, passages}` reiknaður með einni fyrirspurn: `SELECT layer, section_kind, min(ordinal), max(ordinal), count(*) FROM passages WHERE document_id = :id GROUP BY layer, section_kind ORDER BY min(ordinal)`.
- `total_passages`: int.
- `text`: markdown úr `Renderer.to_markdown` styttur í `max_chars` stafi (við orðaskil) ef `max_chars > 0`, annars `null`.
- `text_truncated`: bool.

`raw_api_data` og `body_text`/`lower_body_text` í heild eru **ekki** skilað (samhengisglugginn). Skjal finnst ekki → verkfæravilla.

### 4.5 `list_sources`

Ekkert inntak. Skilar `catalog()` úr `engine/config/source_groups.py` (tré með `key`, `label`, `count`, `children`) og flatan lista `sources` (`short_name`, `display_name`, `abbreviation`, `count`), sama og `GET /api/sources`, án `regex_fields`. Í lýsingu verkfærisins stendur að hvert `key`/`short_name` sé gilt `scope`.

### 4.6 `facets`

Inntak: `q`, `mode` (sjálfgefið `keyword`), `date_from`, `date_to`. Skilar `{"by_source": {short_name: n}, "by_group": {group_key: n}}` þar sem `by_group` er summa yfir tréð (sama og `GET /api/facets` reiknar fyrir hnúta trésins). Telur alltaf **ströng** treff (`facet_counts` gerir það).

### 4.7 `describe_schema`

Inntak: `table` (str | None). Án `table`: listi yfir töflur í `public` með raðafjölda úr `pg_class.reltuples` (áætlun) og einni línu lýsingu fyrir þekktu töflurnar (`documents`, `passages`, `document_links`, `sources`, `alembic_version`) úr föstu orðabók í `tools.py`. Með `table`: dálkar (`name`, `type`, `nullable`) úr `information_schema.columns`, vísar úr `pg_indexes`, og fyrir `documents` ábending um að `raw_api_data` sé stórt JSONB sem eigi ekki að velja í heild.

### 4.8 `sql_query`

Inntak: `sql` (str), `max_rows` (int 1–1000, sjálfgefið 200).

Ferli:
1. `sqlguard.validate_sql(sql)` (sjá kafla 5). Höfnun → verkfæravilla með ástæðu.
2. `async with session.begin(): await session.execute(text("SET TRANSACTION READ ONLY")); await session.execute(text("SET LOCAL statement_timeout = '15s'"))`, síðan fyrirspurnin með `.fetchmany(max_rows + 1)`.
3. Úttak: `{"columns": [...], "rows": [[...], ...], "row_count": n, "truncated_rows": bool, "truncated_cells": int, "elapsed_ms": int}`. Hver reitur styttur í 500 stafi (`shaping.cell_truncate`), `bytes`/`memoryview` skilað sem `"<bytes n>"`, UUID/dags. sem strengir, JSONB sem JSON.
4. `asyncpg`-tímamörk (`QueryCanceledError`) → verkfæravilla `"Fyrirspurn féll á 15 s tímamörkum."`. Réttindavilla (`InsufficientPrivilegeError`) → `"Lesaðgangshlutverkið má ekki gera þetta."`.

## 5. SELECT-vörðurinn — þrjú lög

1. **Hlutverkið.** `lausnir_ro`: `LOGIN`, `CONNECT` á `lausnir_v2`, `USAGE` á `public`, `SELECT ON ALL TABLES IN SCHEMA public`, `ALTER DEFAULT PRIVILEGES … GRANT SELECT ON TABLES`, `ALTER ROLE lausnir_ro SET default_transaction_read_only = on`, `ALTER ROLE lausnir_ro SET statement_timeout = '15s'`. Engin `CREATE`, engin `TEMP`. Lykilorð í `.env` sem hluti `DATABASE_URL_READONLY` (git-hunsað). **Stofnun hlutverksins er DB-breyting sem notandi samþykkir sérstaklega** í áætluninni (Task með skýru SQL og `\du`-staðfestingu); þjónninn sjálfur býr það aldrei til.
2. **Færslan.** Hvert `sql_query` keyrir í `READ ONLY` færslu með `SET LOCAL statement_timeout`. Leitarverkfærin nota sama hlutverk, svo villa í kjarnanum getur heldur ekki skrifað.
3. **`sqlguard.validate_sql`.** Hafnar ef: strengurinn er tómur; eftir að athugasemdir (`--`, `/* */`) og strengir eru fjarlægðir finnst `;` annars staðar en aftast; fyrsta lykilorð er ekki `SELECT`, `WITH`, `EXPLAIN`, `SHOW`, `TABLE` eða `VALUES`; `WITH` inniheldur `INSERT|UPDATE|DELETE|MERGE` (data-modifying CTE); textinn inniheldur `\b(pg_sleep|pg_read_file|pg_read_binary_file|pg_ls_dir|lo_import|lo_export|dblink|copy)\b` (case-insensitive); eða `INTO` sem ekki er hluti af `INSERT` en gæti verið `SELECT … INTO` (nýja töflu). Vörðurinn er **belti við axlabönd**: hlutverkið er raunverulega vörnin, vörðurinn gefur LLM skiljanlega villu strax og stöðvar augljós slys.

## 6. Leiðbeiningar til LLM (`instructions` á þjóninum)

Íslenska með enskri þýðingu í sama streng:

> Lausnir er safn íslenskra dóma, úrskurða og stjórnsýsluákvarðana. Byrjaðu á `search` (orðaleit með BÍN-lemmun; `scope` þrengir að dómstigi eða heimild, sjá `list_sources`). Hver niðurstaða vísar á bestu efnisgreinina (`passage_id`, `anchor`). Notaðu `passage_context` til að lesa í kringum treffið og `get_document` fyrir lýsigögn, aðila og reifun. Vitnaðu alltaf með `urlausn` og `anchor` (t.d. „Hrd. 123/2020, mgr. 14“). `relaxed: true` þýðir að færri en 10 skjöl innihéldu öll leitarorðin; `match_tier` 1–2 innihalda aðeins hluta þeirra. `sql_query` er fyrir tölfræði og gagnaathuganir sem leitarverkfærin svara ekki; það er read-only og skilar mest 1000 röðum.

## 7. Villumeðferð og mörk

| Tilvik | Hegðun |
|---|---|
| `SearchError` úr kjarna | verkfæravilla með sömu skilaboðum |
| Ógilt UUID / skjal eða efnisgrein finnst ekki | verkfæravilla, skýr texti, engin stakkrakning |
| `DATABASE_URL_READONLY` vantar | þjónn ræsist ekki, skilaboð á stderr, exit 2 |
| Grunnur niðri við ræsingu | `init_db` kastar, sama meðferð |
| Grunnur dettur út í keyrslu | verkfæravilla `"Gagnagrunnstenging brást: …"`; næsta kall reynir aftur (pool) |
| Tímamörk 15 s | verkfæravilla, sjá 4.8 |
| Óþekkt scope | `total: 0` + `hint` |
| Stór svör | `page_size ≤ 25`, `count ≤ 50`, `max_chars ≤ 40.000`, reitir ≤ 500 stafir, `max_rows ≤ 1000` |

## 8. Prófun

- **Einingapróf án grunns** (`tests/test_mcp_sqlguard.py`, `tests/test_mcp_shaping.py`): töflupróf yfir samþykkt/hafnað SQL (SELECT, WITH, EXPLAIN, `;`-keðjur, athugasemdafelur, `WITH … DELETE`, `SELECT … INTO`, `pg_sleep`, `COPY`), stytting texta við orðaskil, `cell_truncate`, keywords ≤ 5, hint-reglur.
- **Innflutningspróf** (`tests/test_mcp_server.py`): `import engine.mcp.server` með `capsys` → stdout tómt; `server.list_tools()` skilar nákvæmlega átta verkfærum með væntum heitum og `readOnlyHint`.
- **DB-próf** (`tests/test_mcp_tools_db.py`, `skipif not DATABASE_URL_READONLY`): hvert verkfæri kallað í ferli gegn lifandi grunni: `search("gæsluvarðhald", scope=["domstolar"])` skilar `total > 0` og hverri niðurstöðu með `passage_id`; `passage_context` á fyrstu niðurstöðu skilar `focus_ordinal` innan gluggans; `get_document(max_chars=2000)` skilar `text_truncated == True` fyrir langt skjal og `outline` sem þekur `total_passages`; `sql_query("SELECT count(*) FROM documents")` skilar eina röð; `sql_query("DELETE FROM documents")` er hafnað af verðinum **og** `sql_query("SELECT pg_sleep(20)")` er hafnað; til viðbótar ein bein `session.execute(text("UPDATE sources SET display_name = display_name WHERE false"))` gegn `lausnir_ro` sem verður að kasta `InsufficientPrivilegeError` eða read-only villu (staðfestir lag 1 óháð verðinum).
- **Stdio-reykpróf** (`tests/test_mcp_stdio.py`, skipif sama): `mcp.client.stdio.stdio_client(StdioServerParameters(command="uv", args=["run","python","-m","engine.mcp"]))` → `initialize`, `list_tools` → átta heiti; `call_tool("list_sources")` skilar tré.
- **Handvirkt:** `claude mcp add lausnir -- uv run --directory /Volumes/RuleOfLaw/Lausnir python -m engine.mcp` og ein rannsóknarspurning í Claude Code; Claude Desktop-stilling skjalfest.

## 9. Skjölun

- Ný wiki-síða `docs/wiki/10-mcp.md`: tilgangur, ræsing, stillingar fyrir Claude Code og Claude Desktop (`claude_desktop_config.json` bútur), verkfæratafla, `lausnir_ro` og hvernig `mcp-postgres` er fjarlægt eða beint á sama hlutverk, mörk.
- `docs/wiki/README.md` og `08-throun.md` fá tilvísun. `09-gildrur.md` fær gildruna „stdout er MCP-rásin“ og „`init_db` með `create_tables=False` fyrir lesaðgangshlutverk“.
- `docs/READINESS_PLAYBOOK.md` línur 47/59/105 og `09-gildrur.md:208` (opna TODO-ið um `mcp-postgres`) uppfærðar þegar hlutverkið er komið.

## 10. Áhætta og mótvægi

- **SDK 2.x er nýlegt og API getur breyst.** Festa `mcp>=2.2,<3`. Verkfærin eru hrein föll í `tools.py` sem SDK-hlífin kallar; skipti á SDK snerta aðeins `server.py`.
- **LLM sækir of mikið.** Öll þök í kafla 7 eru MCP-lagsins, óháð API-þökum, og lýsingar verkfæra segja LLM að nota `passage_context` frekar en `get_document(max_chars=40000)`.
- **Vörðurinn hleypir einhverju í gegn.** Hlutverkið er vörnin. Prófið í kafla 8 staðfestir að skrifaðgerð gegn `lausnir_ro` fellur óháð verðinum.
- **`create_all` með lesaðgangi.** `init_db(create_tables=False)`; próf staðfestir að MCP-ræsing keyrir engin DDL (engar `CREATE`-setningar í `echo`-log engine-sins).
