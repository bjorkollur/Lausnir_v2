"""Structure-aware passage segmenter (replaces chunker.py).

Splits a document's text into citable passages along its own structure:
``##`` headings and numbered paragraphs ("4. Texti…"). Passages accumulate to
``target_words`` and never exceed ``max_words`` unless a single block does, in
which case that block is split on sentence (then word) boundaries.

Pure function: no DB, no lemmatisation. Every passage carries exact offsets
into the input so that ``text[char_start:char_end] == passage.text`` always.
No overlap between passages.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from engine.processors.sections import classify_section, normalize_heading

# Blank line = paragraph break. Tolerates \r\n and whitespace-only lines.
_BLOCK_SPLIT_RE = re.compile(r'\n[ \t\r]*\n[ \t\r\n]*')
_MD_HEADING_RE = re.compile(r'^#{1,6}\s+\S')
# Bold Roman heading on its own line, as in committee rulings: **IV. Niðurstaða.**
_BOLD_ROMAN_HEADING_RE = re.compile(r'^\*\*[IVX]+\.(?:\*\*\*\*|\s+)[^*\n]{1,80}?\.*\*\*\s*$')
# Numbered paragraph: 1–3 digits, period, space, uppercase. Rejects years ("2015.")
# and dates ("24. júní"), same rule as pdf_parser._NEW_PARA.
_NUMBERED_RE = re.compile(r'^(\d{1,3})\.\s+[A-ZÁÉÍÓÚÝÞÆÖ]')
_SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÝÞÆÖ„"(])')
_WORD_RE = re.compile(r'\S+')
# Headings are short by construction. Some OCR-garbled paragraphs happen to start
# with "# " (a stray hash from scan noise) and run for hundreds of words on one
# line; without this cap that whole paragraph would be classified as a heading
# and bypass the max_words guard below.
MAX_HEADING_WORDS = 30


@dataclass(frozen=True)
class Passage:
    ordinal: int
    section_path: str | None
    section_kind: str
    para_from: int | None
    para_to: int | None
    char_start: int
    char_end: int
    text: str
    word_count: int


@dataclass
class _Block:
    start: int
    end: int
    kind: str            # 'heading' | 'para' | 'plain'
    para: int | None
    words: int


def _word_count(s: str) -> int:
    return len(_WORD_RE.findall(s))


def _iter_blocks(text: str):
    """Yield _Block for each non-blank block, with exact trimmed offsets.

    A heading (``##`` or bold-Roman) glued to following text with no blank
    line between them — e.g. ``"## Niðurstaða\\n1. Fyrsta..."`` — is split
    into two blocks: the heading line alone, and the remainder classified
    normally (para/plain). This keeps ``normalize_heading`` seeing only the
    heading line, restores paragraph-number tracking for the glued text, and
    lets an oversize glued remainder still be exploded to ``max_words``.
    """
    pos = 0
    spans: list[tuple[int, int]] = []
    for m in _BLOCK_SPLIT_RE.finditer(text):
        spans.append((pos, m.start()))
        pos = m.end()
    spans.append((pos, len(text)))
    for s, e in spans:
        raw = text[s:e]
        lead = len(raw) - len(raw.lstrip())
        trail = len(raw) - len(raw.rstrip())
        s2, e2 = s + lead, e - trail
        if s2 >= e2:
            continue
        body = text[s2:e2]
        nl = body.find("\n")
        first_line = body if nl == -1 else body[:nl]
        is_heading = (
            _word_count(first_line) <= MAX_HEADING_WORDS
            and (_MD_HEADING_RE.match(first_line) or _BOLD_ROMAN_HEADING_RE.match(first_line))
        )
        if is_heading:
            if nl == -1:
                yield _Block(s2, e2, "heading", None, _word_count(body))
                continue
            head_end = s2 + len(first_line.rstrip())
            yield _Block(s2, head_end, "heading", None, _word_count(text[s2:head_end]))
            rest_raw = body[nl + 1:]
            rest_lead = len(rest_raw) - len(rest_raw.lstrip())
            rest_start = s2 + nl + 1 + rest_lead
            if rest_start < e2:
                rest_body = text[rest_start:e2]
                kind, para = _classify_body(rest_body)
                yield _Block(rest_start, e2, kind, para, _word_count(rest_body))
            continue
        kind, para = _classify_body(body)
        yield _Block(s2, e2, kind, para, _word_count(body))


def _classify_body(body: str) -> tuple[str, int | None]:
    """Classify a non-heading block body as a numbered paragraph or plain text."""
    m = _NUMBERED_RE.match(body)
    return ("para", int(m.group(1))) if m else ("plain", None)


def _explode(text: str, b: _Block, max_words: int) -> list[_Block]:
    """Split an oversize block into sentence pieces (then word pieces) ≤ max_words."""
    body = text[b.start:b.end]
    # sentence spans relative to block
    cuts = [0] + [m.end() for m in _SENTENCE_END_RE.finditer(body)] + [len(body)]
    pieces: list[tuple[int, int]] = []
    for a, z in zip(cuts, cuts[1:]):
        seg = body[a:z]
        if _word_count(seg) <= max_words:
            pieces.append((a, z))
            continue
        # one sentence longer than max: cut on words
        words = list(_WORD_RE.finditer(seg))
        for i in range(0, len(words), max_words):
            ws = words[i:i + max_words]
            pieces.append((a + ws[0].start(), a + ws[-1].end()))
    out: list[_Block] = []
    for a, z in pieces:
        # trim whitespace inside the piece
        seg = body[a:z]
        lead = len(seg) - len(seg.lstrip())
        trail = len(seg) - len(seg.rstrip())
        s, e = b.start + a + lead, b.start + z - trail
        if s < e:
            out.append(_Block(s, e, b.kind, b.para, _word_count(text[s:e])))
    return out


def segment(text: str, *, target_words: int = 250, max_words: int = 400) -> list[Passage]:
    if not text or not text.strip():
        return []

    passages: list[Passage] = []
    cur: list[_Block] = []
    cur_words = 0
    cur_section: str | None = None
    cur_kind = "annad"
    section: str | None = None      # section in effect for the next block
    kind = "annad"

    def flush() -> None:
        nonlocal cur, cur_words
        if not cur:
            return
        start, end = cur[0].start, cur[-1].end
        paras = [b.para for b in cur if b.para is not None]
        body = text[start:end]
        passages.append(Passage(
            ordinal=len(passages), section_path=cur_section, section_kind=cur_kind,
            para_from=min(paras) if paras else None, para_to=max(paras) if paras else None,
            char_start=start, char_end=end, text=body, word_count=_word_count(body),
        ))
        cur, cur_words = [], 0

    def add(b: _Block) -> None:
        nonlocal cur_words, cur_section, cur_kind
        if cur and cur_words + b.words > max_words:
            flush()
        if not cur:
            cur_section, cur_kind = section, kind
        cur.append(b)
        cur_words += b.words
        if cur_words >= target_words:
            flush()

    for b in _iter_blocks(text):
        if b.kind == "heading":
            flush()
            section = normalize_heading(text[b.start:b.end])
            kind = classify_section(section)
            add(b)
            continue
        if b.words > max_words:
            for piece in _explode(text, b, max_words):
                add(piece)
            continue
        add(b)
    flush()
    return passages
