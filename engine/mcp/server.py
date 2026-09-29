"""MCPServer wiring for the Lausnir read-only server (spec §3, §6).

Build-time (build_server) touches no database, so tests can list tools
offline. main() loads .env, requires DATABASE_URL_READONLY, initialises the
engine with create_tables=False and serves stdio. Logging goes to stderr —
stdout is the protocol channel.
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

import engine.database.connection as _db
from engine.mcp import tools
from engine.mcp.tools import ToolInputError

REPO_ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("lausnir.mcp")

TOOL_NAMES = ("search", "passage_context", "get_passages", "get_document",
              "list_sources", "facets", "describe_schema", "sql_query")

INSTRUCTIONS = (
    "Lausnir er safn íslenskra dóma, úrskurða og stjórnsýsluákvarðana. Byrjaðu á `search` "
    "(orðaleit með BÍN-lemmun; `scope` þrengir að dómstigi eða heimild, sjá `list_sources`). "
    "Hver niðurstaða vísar á bestu efnisgreinina (`passage_id`, `anchor`). Notaðu "
    "`passage_context` til að lesa í kringum treffið og `get_document` fyrir lýsigögn, aðila og "
    "reifun. Vitnaðu alltaf með `urlausn` og `anchor` (t.d. „Hrd. 123/2020, mgr. 14“). "
    "`relaxed: true` þýðir að færri en 10 skjöl innihéldu öll leitarorðin; `match_tier` 1–2 "
    "innihalda aðeins hluta þeirra. `sql_query` er fyrir tölfræði og gagnaathuganir sem "
    "leitarverkfærin svara ekki; það er read-only og skilar mest 1000 röðum.\n\n"
    "English: Lausnir is a corpus of Icelandic court rulings and administrative decisions. Start "
    "with `search` (lemmatised keyword search; `scope` narrows by court tier or source, see "
    "`list_sources`). Each hit points at its best passage (`passage_id`, `anchor`); use "
    "`passage_context` to read around it and `get_document` for metadata, parties and summary. "
    "Always cite with `urlausn` plus `anchor`. `relaxed: true` means fewer than 10 documents "
    "contained all query terms; `match_tier` 1–2 hits contain only some of them. `sql_query` is "
    "read-only SQL for statistics the search tools cannot answer (max 1000 rows)."
)

_RO = ToolAnnotations(read_only_hint=True)


def _session_factory():
    if _db.AsyncSessionLocal is None:
        raise ToolError("Gagnagrunnstenging er ekki tilbúin.")
    return _db.AsyncSessionLocal()


async def _run(fn, **kw):
    try:
        async with _session_factory() as session:
            return await fn(session, **kw)
    except ToolInputError as exc:
        raise ToolError(str(exc))
    except ToolError:
        raise
    except Exception as exc:  # DB down mid-run etc.: readable error, no traceback to the LLM
        log.exception("tool failure")
        raise ToolError(f"Gagnagrunnstenging brást: {exc.__class__.__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")


@asynccontextmanager
async def _lifespan(_server: MCPServer) -> AsyncIterator[None]:
    url = os.environ["DATABASE_URL_READONLY"]
    await _db.init_db(url=url, create_tables=False)
    log.info("lausnir mcp: db ready")
    yield


def build_server() -> MCPServer:
    server = MCPServer("lausnir", instructions=INSTRUCTIONS, lifespan=_lifespan, log_level="WARNING")

    @server.tool(annotations=_RO, description=(
        "Leit í dómasafninu (orðaleit með lemmun, sjálfgefið mode='keyword'). Skilar allt að page_size "
        "(≤25) skjölum með bestu efnisgrein hvers (passage_id, anchor, snippet). scope: heiti úr "
        "list_sources (t.d. 'domstolar', 'haestirettur', 'landsrettur_domar') eða 'all'. section_kind: "
        "reifun, malsmedferd, malsatvik, malsastaedur, nidurstada, domsord, annad. Dagsetningar ISO."))
    async def search(q: str, mode: str = "keyword", scope: list[str] | None = None,
                     date_from: str | None = None, date_to: str | None = None, sort: str = "relevance",
                     section_kind: list[str] | None = None, page: int = 1, page_size: int = 10) -> dict:
        return await _run(tools.search, q=q, mode=mode, scope=scope, date_from=date_from, date_to=date_to,
                          sort=sort, section_kind=section_kind, page=page, page_size=page_size)

    @server.tool(annotations=_RO, description=(
        "Efnisgreinarnar í kringum eina efnisgrein (passage_id úr search). before/after 0–10."))
    async def passage_context(passage_id: str, before: int = 2, after: int = 2) -> dict:
        return await _run(tools.passage_context, passage_id=passage_id, before=before, after=after)

    @server.tool(annotations=_RO, description=(
        "Gluggi af efnisgreinum skjals. count (≤ 50) afmarkar BIL Í RÖÐUNARTÖLUM (ordinal span), ekki "
        "fjölda niðurstaðna: með section_kind/layer-síu getur glugginn skilað færri en count "
        "efnisgreinum þótt fleiri séu eftir í skjalinu. layer: summary|body|lower_body. Svarið inniheldur "
        "from_ordinal (fyrsta röðunartalan sem uppfyllir síuna og var í raun notuð — getur verið hærri en "
        "sú sem beðið var um), matching_passages (fjöldi efnisgreina sem uppfylla síuna í öllu skjalinu) "
        "og total_passages (heildarfjöldi efnisgreina skjalsins). Flettu með next_from_ordinal; "
        "null þýðir að ekkert er eftir."))
    async def get_passages(doc_id: str, from_ordinal: int = 0, count: int = 20,
                           section_kind: list[str] | None = None, layer: str | None = None) -> dict:
        return await _run(tools.get_passages_tool, doc_id=doc_id, from_ordinal=from_ordinal, count=count,
                          section_kind=section_kind, layer=layer)

    @server.tool(annotations=_RO, description=(
        "Lýsigögn skjals: urlausn, aðilar, reifun, lykilorð, áfrýjunartengingar, kaflayfirlit (outline). "
        "max_chars > 0 bætir við markdown-texta styttum að max_chars (≤ 40000); notaðu frekar "
        "passage_context/get_passages fyrir lestur."))
    async def get_document(doc_id: str, max_chars: int = 0) -> dict:
        return await _run(tools.get_document_tool, doc_id=doc_id, max_chars=max_chars)

    @server.tool(annotations=_RO, description=(
        "Heimildatréð með fjölda skjala. Hvert key og hvert short_name er gilt scope í search."))
    async def list_sources() -> dict:
        return await _run(tools.list_sources)

    @server.tool(annotations=_RO, description=(
        "Fjöldi strangra treffa eftir heimild (by_source) og heimildahópi (by_group) fyrir fyrirspurn."))
    async def facets(q: str = "", mode: str = "keyword", date_from: str | None = None,
                     date_to: str | None = None) -> dict:
        return await _run(tools.facets, q=q, mode=mode, date_from=date_from, date_to=date_to)

    @server.tool(annotations=_RO, description=(
        "Töflur gagnagrunnsins (án table) eða dálkar, vísar og athugasemdir einnar töflu (með table)."))
    async def describe_schema(table: str | None = None) -> dict:
        return await _run(tools.describe_schema, table=table)

    @server.tool(annotations=_RO, description=(
        "Read-only SQL (SELECT/WITH/EXPLAIN) gegn lesaðgangshlutverki, 15 s tímamörk, max_rows ≤ 1000, "
        "reitir styttir í 500 stafi. Notaðu describe_schema fyrst."))
    async def sql_query(sql: str, max_rows: int = 200) -> dict:
        return await _run(tools.sql_query, sql=sql, max_rows=max_rows)

    return server


def _load_env() -> None:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=False)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _load_env()
    if not os.environ.get("DATABASE_URL_READONLY"):
        print("lausnir mcp: DATABASE_URL_READONLY vantar í umhverfi/.env — þjónninn notar aðeins "
              "lesaðgangshlutverkið lausnir_ro (sjá docs/wiki/10-mcp.md).", file=sys.stderr)
        return 2
    build_server().run("stdio")
    return 0
