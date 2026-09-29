"""Write one document's citations and derived `cites` edges (spec §7). Owns only its own rows.

Split in two so the CPU-bound half can run in a worker process:
`build_rows()` is pure (extract + resolve, no DB) and `write_citations()` does
every statement, in the caller's transaction. `rebuild_citations()` is just the
two chained together for callers that have a connection anyway (tests, imports).

`STALE_WHERE` and `citation_hash()` must agree byte for byte: the SQL picks the
documents to rebuild, the Python writes the hash that takes them off that list.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date

from sqlalchemy import text

from engine.processors.citation_resolver import CitationIndex, resolve
from engine.processors.citations import extract_citations

SEP = chr(31)
STALE_WHERE = ("(d.citation_hash IS NULL OR d.citation_hash <> encode(sha256(convert_to("
               "coalesce(d.summary,'') || chr(31) || coalesce(d.body_text,'') || chr(31) || coalesce(d.lower_body_text,''),"
               " 'UTF8')), 'hex'))")
EDGE_LAYERS = ("summary", "body")           # lower_body cites nothing (spec §4.3)
# Not used here: the display layer reads these to mark a `cites` pair `also_appeal`
# when the same pair carries an appeal relation in either direction (spec §4.3).
APPEAL_RELATIONS = ("appealed_to", "appealed_from", "leyfisbeidni_um", "leiddi_til_doms")


def citation_hash(summary: str | None, body: str | None, lower: str | None) -> str:
    """sha256 of the three text layers joined by US (chr(31)), UTF-8 — the exact
    expression STALE_WHERE computes in SQL."""
    return hashlib.sha256(((summary or "") + SEP + (body or "") + SEP + (lower or "")).encode("utf-8")).hexdigest()


def build_rows(doc_id: uuid.UUID, *, summary, body, lower, doc_date: date | None,
               index: CitationIndex) -> list[dict]:
    """Pure: extract + resolve every layer, return insertable citation rows."""
    rows: list[dict] = []
    for layer, src in (("summary", summary), ("body", body), ("lower_body", lower)):
        if not src:
            continue
        for raw in extract_citations(src, doc_date=doc_date):
            r = resolve(raw, index=index, from_doc_id=doc_id, from_date=doc_date)
            rows.append({
                "id": uuid.uuid4(), "from_doc_id": doc_id, "layer": layer,
                "char_start": raw.char_start, "char_end": raw.char_end, "raw_text": raw.raw_text,
                "target_court": raw.target_court, "target_case_number": raw.target_case_number,
                "target_date": raw.target_date, "target_verdict": raw.target_verdict,
                "to_doc_id": r.to_doc_id, "status": r.status, "method": r.method, "confidence": r.confidence,
            })
    return rows


_INSERT = text("""
    INSERT INTO citations (id, from_doc_id, layer, char_start, char_end, raw_text, target_court, target_case_number,
                           target_date, target_verdict, to_doc_id, status, method, confidence)
    VALUES (:id, :from_doc_id, :layer, :char_start, :char_end, :raw_text, :target_court, :target_case_number,
            :target_date, :target_verdict, :to_doc_id, :status, :method, :confidence)
    ON CONFLICT ON CONSTRAINT uq_cit_doc_layer_start DO NOTHING
""")
_EDGE = text("""
    INSERT INTO document_links (id, from_doc_id, to_doc_id, relation, confidence, method)
    VALUES (gen_random_uuid(), :f, :t, 'cites', :c, 'citation')
    ON CONFLICT ON CONSTRAINT uq_link_from_to_rel DO UPDATE
      SET confidence = GREATEST(document_links.confidence, EXCLUDED.confidence), method = 'citation'
""")


async def write_citations(conn, doc_id, rows: list[dict], *, hash_: str,
                          write_edges: bool = True) -> tuple[int, int]:
    """Replace this document's citations and `cites` edges with `rows`, then set
    documents.citation_hash. Runs in the caller's transaction (no commit here).

    Returns (citation rows, `cites` edges). Only rows belonging to doc_id are
    touched — nothing else in citations or document_links is read or changed.
    """
    await conn.execute(text("DELETE FROM citations WHERE from_doc_id = :id"), {"id": doc_id})
    await conn.execute(text("DELETE FROM document_links WHERE from_doc_id = :id AND relation = 'cites'"), {"id": doc_id})
    if rows:
        await conn.execute(_INSERT, rows)
    edges = 0
    if write_edges:
        # One edge per target, carrying the highest confidence among the
        # citations that formed it (spec §4.3). lower_body never makes edges.
        best: dict[uuid.UUID, float] = {}
        for r in rows:
            if r["status"] == "resolved" and r["layer"] in EDGE_LAYERS and r["to_doc_id"] is not None:
                best[r["to_doc_id"]] = max(best.get(r["to_doc_id"], 0.0), r["confidence"] or 0.0)
        for t, c in best.items():
            await conn.execute(_EDGE, {"f": doc_id, "t": t, "c": c})
        edges = len(best)
    await conn.execute(text("UPDATE documents SET citation_hash = :h WHERE id = :id"), {"h": hash_, "id": doc_id})
    return len(rows), edges


async def rebuild_citations(conn, doc_id, *, summary, body, lower, doc_date, index: CitationIndex,
                            write_edges: bool = True) -> tuple[int, int]:
    """build_rows() + write_citations() for one document, in the caller's transaction."""
    rows = build_rows(doc_id, summary=summary, body=body, lower=lower, doc_date=doc_date, index=index)
    return await write_citations(conn, doc_id, rows, hash_=citation_hash(summary, body, lower),
                                 write_edges=write_edges)


async def relink_unresolved(conn, index: CitationIndex, *, limit: int | None = None) -> int:
    """Re-run resolve() on unresolved/ambiguous rows without re-extracting (spec §7)."""
    from engine.processors.citations import RawCitation
    q = ("SELECT c.id, c.from_doc_id, c.layer, c.char_start, c.char_end, c.raw_text, c.target_court, c.target_case_number, "
         "c.target_date, c.target_verdict, d.document_date FROM citations c JOIN documents d ON d.id = c.from_doc_id "
         "WHERE c.status IN ('unresolved','ambiguous')" + (f" LIMIT {int(limit)}" if limit else ""))
    rows = (await conn.execute(text(q))).all()
    fixed = 0
    for r in rows:
        raw = RawCitation(r.char_start, r.char_end, r.raw_text, r.target_court, r.target_case_number,
                          r.target_date, r.target_verdict, "prose")
        res = resolve(raw, index=index, from_doc_id=r.from_doc_id, from_date=r.document_date)
        if res.status == "resolved":
            await conn.execute(text("UPDATE citations SET to_doc_id=:t, status='resolved', method=:m, confidence=:c WHERE id=:id"),
                               {"t": res.to_doc_id, "m": res.method, "c": res.confidence, "id": r.id})
            if r.layer in EDGE_LAYERS:
                await conn.execute(_EDGE, {"f": r.from_doc_id, "t": res.to_doc_id, "c": res.confidence})
            fixed += 1
    return fixed
