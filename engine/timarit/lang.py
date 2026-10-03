# engine/timarit/lang.py
"""Cheap is/en decision from closed-class words; anything unclear → default."""
import re

_IS = {"og", "að", "í", "á", "er", "sem", "um", "við", "ekki", "til", "af", "var", "með", "fyrir", "því", "þess", "hafi", "væri", "málinu", "dómstóllinn"}
_EN = {"the", "and", "of", "to", "in", "that", "is", "was", "for", "with", "court", "held", "not", "by", "this", "which"}
_W = re.compile(r"[a-záéíóúýðþæö]+", re.I)


def detect_lang(text: str, default: str = "is") -> str:
    words = [w.lower() for w in _W.findall(text or "")[:3000]]
    is_n = sum(w in _IS for w in words)
    en_n = sum(w in _EN for w in words)
    if is_n + en_n < 3:
        return default
    return "en" if en_n > 2 * is_n else "is" if is_n >= en_n else default
