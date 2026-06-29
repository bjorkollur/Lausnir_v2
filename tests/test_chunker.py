"""Unit tests for engine.processors.chunker."""
import pytest
from engine.processors.chunker import chunk_document


def _words(n: int) -> str:
    """Make a text with n words, one paragraph per 50 words."""
    paras = []
    for i in range(0, n, 50):
        paras.append(" ".join(f"word{j}" for j in range(i, min(i + 50, n))))
    return "\n\n".join(paras)


def test_empty_text_returns_empty():
    assert chunk_document("") == []


def test_short_text_returns_single_chunk():
    text = _words(80)
    result = chunk_document(text)
    assert len(result) == 1
    assert result[0] == text


def test_long_text_produces_multiple_chunks():
    text = _words(2000)
    result = chunk_document(text)
    assert len(result) > 1


def test_chunks_cover_all_content():
    """Every word in the original text appears in at least one chunk."""
    text = _words(1500)
    chunks = chunk_document(text)
    all_chunk_text = " ".join(chunks)
    # Every unique word in the source should appear in the combined chunks
    source_words = set(text.split())
    chunk_words = set(all_chunk_text.split())
    assert source_words <= chunk_words


def test_chunk_size_within_bounds():
    """No chunk should exceed 3× the target word count (pathological paragraph)."""
    text = _words(3000)
    chunks = chunk_document(text, target_words=500)
    for i, c in enumerate(chunks):
        wc = len(c.split())
        assert wc <= 500 * 3, f"Chunk {i} has {wc} words"


def test_overlap_provides_context():
    """Second chunk starts with words from the end of the first chunk."""
    text = _words(1200)
    chunks = chunk_document(text, target_words=500, overlap_words=50)
    if len(chunks) >= 2:
        last_words_of_first = chunks[0].split()[-50:]
        first_words_of_second = chunks[1].split()[:50]
        overlap = set(last_words_of_first) & set(first_words_of_second)
        assert len(overlap) > 0, "Expected some overlap words between chunk 0 and chunk 1"


def test_single_long_paragraph_is_not_dropped():
    """A single paragraph with 1000 words becomes a single chunk."""
    text = " ".join(f"word{i}" for i in range(1000))  # no double-newlines
    result = chunk_document(text)
    assert len(result) == 1
    assert len(result[0].split()) == 1000


def test_no_empty_chunks():
    text = _words(1500)
    chunks = chunk_document(text)
    for c in chunks:
        assert c.strip() != ""
