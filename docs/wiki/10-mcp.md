# 10 — MCP-þjónn (read-only)

← [Wiki-forsíða](README.md)

**Hvað:** `python -m engine.mcp` er stdio MCP-þjónn sem gefur LLM-viðskiptavini á sömu vél aðgang að leitinni og lesaðgang að grunninum. Hönnunarskjal: `docs/superpowers/specs/2026-09-29-mcp-server-design.md`. Kóðinn er í `engine/mcp/` (`server.py` — þjónn og verkfæraskráning, `tools.py` — hrein async föll, `shaping.py` — stytting/samþjöppun svara, `sqlguard.py` — SELECT-vörður).

## Ræsing og tenging

Krefst `DATABASE_URL_READONLY` í `.env` (hlutverkið `lausnir_ro`, sjá neðar) — þjónninn les `.env` sjálfur (`override=False`, svo umhverfisbreytur sem skelin flytur út vinna). Vanti breytan (eða sé hún tóm strengur) ræsist þjónninn ekki: skilaboð á stderr og útgöngukóði 2 — tómur strengur fellur **ekki** aftur á `DATABASE_URL` (skrif-hlutverkið).

Claude Code:
```bash
claude mcp add lausnir -- uv run --directory /Volumes/RuleOfLaw/Lausnir python -m engine.mcp
```

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{"mcpServers": {"lausnir": {"command": "uv", "args": ["run", "--directory", "/Volumes/RuleOfLaw/Lausnir", "python", "-m", "engine.mcp"]}}}
```

Handvirk prófun án viðskiptavinar: `uv run pytest -q tests/test_mcp_stdio.py`.

## Verkfærin

Öll átta eru merkt `readOnlyHint=True`. Nöfn og fjöldi eru fest í `tests/test_mcp_server.py`/`test_mcp_stdio.py`.

| Verkfæri | Inntak | Skilar | Mörk |
|---|---|---|---|
| `search` | `q`, `mode`, `scope`, `date_from`/`date_to`, `sort`, `section_kind`, `page`, `page_size` | `total`, `strict_total`, `relaxed`, `results[]` (með `passage_id`/`anchor`/`snippet`/`match_tier`), `hint` | `page_size ≤ 25` (lægra en API-ið) |
| `passage_context` | `passage_id`, `before`, `after` | `doc_id`, `urlausn`, `focus_ordinal`, `total_passages`, `passages[]` | `before`/`after` ≤ 10 |
| `get_passages` | `doc_id`, `from_ordinal`, `count`, `section_kind`, `layer` | sami skammtur og `passage_context`, án `focus_ordinal`, með `from_ordinal`/`matching_passages`/`next_from_ordinal` | `count ≤ 50` |
| `get_document` | `doc_id`, `max_chars` | lýsigögn + `outline`, `total_passages`, `text`/`text_truncated`/`text_error` | `max_chars ≤ 40.000` |
| `list_sources` | — | `catalog` (tré) + flatur `sources` listi | — |
| `facets` | `q`, `mode`, `date_from`/`date_to` | `by_source`, `by_group` (alltaf ströng talning) | — |
| `describe_schema` | `table` (valkvætt) | án `table`: töflulisti með `kind`/`approx_rows`; með `table`: dálkar, vísar, athugasemdir | — |
| `sql_query` | `sql`, `max_rows` | `columns`, `rows`, `row_count`, `truncated_rows`, `truncated_cells`, `elapsed_ms` | `max_rows ≤ 1000`, reitir ≤ 500 stafir, 15 s tímamörk |

`keywords` í `search`-niðurstöðum er stytt í mest 5. `get_document` skilar aldrei `body_text`, `lower_body_text` eða `raw_api_data` — aðeins reiknuðum `text`/`text_error` (sjá „Mörk og hegðun").

## Lesaðgangshlutverkið `lausnir_ro`

- Stofnað með `deploy/sql/create_readonly_role.sql` (`psql -v ro_password=… -f …`, `\gexec`-form af því `DO`-blokk sér ekki psql-breytur inni í dollaravitnun). Keyrt 2026-09-29; `rolconfig = {default_transaction_read_only=on, statement_timeout=15s}` staðfest handvirkt eftir keyrslu.
- SELECT-only: `CONNECT`/`USAGE`/`SELECT ON ALL TABLES` + `ALTER DEFAULT PRIVILEGES` (nýjar töflur fá sjálfkrafa `SELECT`), engin `CREATE`, engin `TEMP`.
- Þrjú lög varnar: hlutverk (raunverulega vörnin) → READ ONLY færsla með `SET LOCAL statement_timeout` → `engine/mcp/sqlguard.py` (belti-við-axlabönd, gefur LLM skiljanlega villu strax). `tests/test_mcp_readonly_role_db.py` sannreynir að `UPDATE`/`CREATE` falli fyrir hlutverkið **óháð** verðinum.
- `mcp-postgres` (gamla tengingin með fullum skrifaðgangi): fjarlægja úr stillingum viðskiptavinar, eða beina á sömu `DATABASE_URL_READONLY` ef almennt SQL-verkfæri er enn óskað.

## Mörk og hegðun

- Þök (allt MCP-lagsins eigin, lægri en API-þökin): `page_size ≤ 25`, `count` (`get_passages`) `≤ 50`, `max_chars` (`get_document`) `≤ 40.000`, reitir í `sql_query` styttir í 500 stafi, `max_rows ≤ 1000`, `sql_query`/`describe_schema` 15 s `statement_timeout`.
- `hint` í `search`: sett þegar óþekkt `scope` leysist í tóma síu (`"Óþekkt scope: … Kallaðu á list_sources til að sjá gild heiti."`) eða þegar leitin slakaði (`relaxed: true`, sjá [05-leit](05-leit.md)) — `"Færri en 10 skjöl innihalda öll orðin; niðurstöður með match_tier 1–2 innihalda aðeins hluta þeirra."`. Villur úr kjarnanum (`SearchError`) verða `"Ógilt inntak: <skilaboð úr kjarnanum>"` — kjarnaskilaboðin sjálf halda sér á ensku.
- `get_passages`: `count` afmarkar **BIL Í RÖÐUNARTÖLUM** (ordinal span), ekki fjölda niðurstaðna — með `section_kind`/`layer`-síu getur glugginn skilað færri en `count` efnisgreinum þótt fleiri séu eftir í skjalinu. Svarið ber því `from_ordinal` (fyrsta röðunartalan sem uppfyllti síuna og var raunverulega notuð — getur verið hærri en umbeðin), `matching_passages` (fjöldi efnisgreina sem uppfylla síuna í öllu skjalinu), `total_passages` (heildarfjöldi efnisgreina skjalsins) og `next_from_ordinal` (`null` þegar ekkert er eftir). Ósíuð köll haga sér eins og venjulegur gluggi.
- `get_document`: `text_error` er `null` þegar allt gekk, annars `"UndantekningarHeiti: skilaboð"` þegar markdown-smíðin (`Renderer.to_markdown`) klikkaði — skjalið sjálft skilar sér samt, bara án `text`.
- `sql_query` keyrir gegnum netþjónahliðar-bendil (`session.stream`, server-side cursor) svo t.d. `SELECT text FROM passages LIMIT stórt` kostar langt undir megabæti í stað þess að hlaða allri töflunni í minni fyrst. Hvert kall opnar sína eigin READ ONLY færslu og afturkallar sjálfkrafa hvaða ólokna færslu sem var þegar opin á session-inu. Villuflokkun styðst við SQLSTATE (`57014` = tímamörk, `42501`/`25006` = réttindaleysi). Versta hugsanlega svartími er um það bil tvöfaldur 15 s tímamörkin (bæði `DECLARE` og `FETCH` fá sín eigin tímamörk).
- `describe_schema` telur `relkind IN ('r','p','v','m')` (töflur, sneiðar, sýnir, efnissýnir) með `kind`-svæði, og keyrir í sömu read-only-færsluhlíf og `sql_query`.

## Gildrur

- Stdout er MCP-rásin. Allt log á stderr; `tests/test_mcp_server.py::test_import_writes_nothing_to_stdout` ver þetta.
- `init_db(create_tables=False)` er skylda með lesaðgangshlutverki — annars reynir ræsingin `CREATE TABLE` með hlutverki sem má það ekki.
- `plan_cache_mode=force_custom_plan` gildir líka hér (sama `connect_args` og API-ið) — sjá [09-gildrur](09-gildrur.md) fyrir hvers vegna.

## Þekkt takmörk

- `sqlguard.py` þekkir `$$…$$`/`$tag$…$tag$` dollaravitnun, `E'…'` escape-strengi, gæsalappaða auðkenna og aftanávið-athugasemdir, og leyfir eina fremstu sviga (`(`). Það hafnar líka læsingaföllum (`pg_advisory_lock` o.fl.). Þekkt takmörk, alltaf í **höfnunar**-átt (aldrei sleppir gegn): tvöfaldar gæsalappir í auðkenni (`"a""b"`) og hreiðraðar blokkarathugasemdir eru mistúlkaðar.
- Prófaskrár: `tests/test_mcp_sqlguard.py`, `test_mcp_shaping.py`, `test_mcp_tools_unit.py`, `test_mcp_server.py` þurfa engan gagnagrunn. `test_mcp_tools_db.py`, `test_mcp_sql_db.py`, `test_mcp_stdio.py`, `test_mcp_readonly_role_db.py` þurfa grunn (fyrstu þrjú nota `DATABASE_URL_READONLY` ef sett, annars `DATABASE_URL`; `test_mcp_readonly_role_db.py` krefst `DATABASE_URL_READONLY`, því það prófar hlutverkið sjálft). Öll bakendaprófin (579) eru græn.
