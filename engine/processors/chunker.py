"""Paragraph-aware text chunker for long documents.

Splits text into overlapping chunks at double-newline paragraph boundaries.
Each chunk is roughly target_words words. Overlap preserves context across
chunk boundaries for FTS relevance.
"""
from __future__ import annotations


def chunk_document(
    text: str,
    target_words: int = 500,
    overlap_words: int = 50,
) -> list[str]:
    """Split text into overlapping chunks at paragraph boundaries.

    Returns [] for empty text.
    Returns [text] if text has fewer than 100 words.
    """
    if not text or not text.strip():
        return []

    words = text.split()
    if len(words) < 100:
        return [text]

    # Split into paragraphs on double newlines, preserve non-empty ones
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text]

    chunks: list[str] = []
    current_paras: list[str] = []
    current_words = 0

    for para in paragraphs:
        para_words = len(para.split())
        current_paras.append(para)
        current_words += para_words

        if current_words >= target_words:
            chunks.append("\n\n".join(current_paras))
            # Overlap: carry forward the last overlap_words worth of paragraphs
            current_paras, current_words = _tail_paragraphs(current_paras, overlap_words)

    # Last chunk: whatever remains
    if current_paras:
        # Only add if it's not a pure repeat of the last chunk's tail
        tail = "\n\n".join(current_paras)
        if not chunks or tail != chunks[-1]:
            chunks.append(tail)

    return chunks


def _tail_paragraphs(paras: list[str], max_words: int) -> tuple[list[str], int]:
    """Return the trailing paragraphs of `paras` that total <= max_words words.

    Always keeps at least one paragraph so we don't lose the boundary.
    """
    kept: list[str] = []
    total = 0
    for p in reversed(paras):
        pw = len(p.split())
        if total + pw > max_words and kept:
            break
        kept.insert(0, p)
        total += pw
    return kept, total
