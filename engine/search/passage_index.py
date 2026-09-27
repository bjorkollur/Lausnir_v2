"""Build and persist passages for one document (spec 2026-09-27-passages-design.md).

Pure parts (rows, hash, anchor) are testable without a DB. rebuild_passages()
deletes + inserts a document's passages and sets documents.passage_hash in the
caller's transaction.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import text

from engine.processors.lemmatizer import lemmatize_text
from engine.processors.segmenter import segment

LAYERS = ("summary", "body", "lower_body")

PASSAGE_HASH_SQL = (
    "md5(coalesce(d.summary,'') || E'\\x1f' || coalesce(d.body_text,'') "
    "|| E'\\x1f' || coalesce(d.lower_body_text,''))"
)
STALE_WHERE = "d.passage_hash IS DISTINCT FROM " + PASSAGE_HASH_SQL


def passage_hash(summary: str | None, body: str | None, lower: str | None) -> str:
    blob = f"{summary or ''}\x1f{body or ''}\x1f{lower or ''}"
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def passage_anchor(layer: str, para_from: int | None, para_to: int | None,
                   section_path: str | None, ordinal: int) -> str:
    """Human-readable citation label. Derived, never stored (RENDER layer)."""
    if layer == "summary":
        return "Reifun"
    if para_from is not None and para_to is not None:
        return f"{para_from}. mgr." if para_from == para_to else f"{para_from}.–{para_to}. mgr."
    if section_path:
        return section_path
    return f"hluti {ordinal + 1}"


def build_passage_rows(summary: str | None, body: str | None, lower: str | None,
                       *, is_lagasafn: bool = False) -> list[dict[str, Any]]:
    """Segment all three layers into insert-ready rows (without lemmas)."""
    if is_lagasafn:
        return []
    rows: list[dict[str, Any]] = []
    for layer, src in zip(LAYERS, (summary, body, lower)):
        if not src or not src.strip():
            continue
        for p in segment(src):
            # Offset integrity is the contract every consumer relies on. A violation
            # is a segmenter bug: fail this document loudly rather than store bad rows.
            if src[p.char_start:p.char_end] != p.text:
                raise ValueError(f"offset mismatch in layer {layer!r} at ordinal {len(rows)}")
            rows.append({
                "ordinal": len(rows),
                "layer": layer,
                "section_path": p.section_path,
                "section_kind": "reifun" if layer == "summary" else p.section_kind,
                "para_from": None if layer == "summary" else p.para_from,
                "para_to": None if layer == "summary" else p.para_to,
                "char_start": p.char_start,
                "char_end": p.char_end,
                "text": p.text,
                "word_count": p.word_count,
            })
    return rows


def lemmatize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CPU-bound; safe to run in a worker process. Returns new dicts with 'lemmas'."""
    return [{**r, "lemmas": lemmatize_text(r["text"])} for r in rows]


_INSERT_SQL = text("""
    INSERT INTO passages (id, document_id, ordinal, layer, section_path, section_kind,
                          para_from, para_to, char_start, char_end, text, word_count, fts_is)
    VALUES (gen_random_uuid(), :doc_id, :ordinal, :layer, :section_path, :section_kind,
            :para_from, :para_to, :char_start, :char_end, :text, :word_count,
            to_tsvector('simple', :lemmas))
""")


async def rebuild_passages(conn, doc_id: uuid.UUID | str, *,
                           rows_with_lemmas: list[dict[str, Any]] | None = None) -> int:
    """Delete + insert this document's passages and set passage_hash.

    Runs in the caller's transaction (no commit here). If rows_with_lemmas is
    None, the document's text is read and lemmatised in-process.
    Returns the number of passages written.
    """
    did = uuid.UUID(str(doc_id))
    row = (await conn.execute(text("""
        SELECT d.summary, d.body_text, d.lower_body_text, s.short_name
        FROM documents d JOIN sources s ON s.id = d.source_id WHERE d.id = :id
    """), {"id": did})).first()
    if row is None:
        raise ValueError(f"document {did} not found")
    summary, body, lower, short_name = row
    if rows_with_lemmas is None:
        rows_with_lemmas = lemmatize_rows(
            build_passage_rows(summary, body, lower, is_lagasafn=short_name.startswith("lagasafn_")))

    await conn.execute(text("DELETE FROM passages WHERE document_id = :id"), {"id": did})
    if rows_with_lemmas:
        await conn.execute(_INSERT_SQL, [{**r, "doc_id": did} for r in rows_with_lemmas])
    await conn.execute(text("UPDATE documents SET passage_hash = :h WHERE id = :id"),
                       {"h": passage_hash(summary, body, lower), "id": did})
    return len(rows_with_lemmas)
