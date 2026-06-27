"""Icelandic lemmatizer using BÍN (Beygingarlýsing íslensks nútímamáls).

Converts inflected Icelandic word forms to their base/lemma form for FTS indexing.
Words not found in BÍN are kept as-is (lowercase), preserving case numbers etc.
"""
import re

from islenska import Bin

_bin = Bin()
_WORD_RE = re.compile(r"[a-záéíóúýðþæöÁÉÍÓÚÝÐÞÆÖ]+", re.UNICODE)


def _lemmatize_word(word: str) -> str:
    _, entries = _bin.lookup(word)
    if entries:
        return entries[0].ord
    return word.lower()


def lemmatize_text(text: str) -> str:
    """Return space-separated lemmas for all Icelandic words in text.

    Numbers, case identifiers, and unknown words pass through unchanged.
    """
    if not text:
        return ""
    tokens = _WORD_RE.findall(text)
    return " ".join(_lemmatize_word(t) for t in tokens)


def lemmatize_query(query: str) -> str:
    """Lemmatize a search query string for use with to_tsquery('simple', ...)."""
    return lemmatize_text(query)
