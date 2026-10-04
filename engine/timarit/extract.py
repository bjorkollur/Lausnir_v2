"""Page-wise text extraction for journal articles (spec §6)."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from engine.processors.lemmatizer import _WORD_RE, _bin
from engine.processors.pdf_parser import parse_pdf_pages, resolve_hyphen_break
from engine.timarit.pagemap import build_page_map, numbers_from_start

_HYPHEN_BREAK = re.compile(r"-\n(?=\S)")
_DIGITS = re.compile(r"\d+")
_MIN_PAGE_CHARS = 50


@dataclass
class ExtractedText:
    body: str
    page_offsets: list[dict] = field(default_factory=list)
    ocr_source: str = "embedded"      # "embedded" | "tesseract-isl" | "none" (no text layer, no OCR text)
    bin_ratio: float = 0.0
    page_texts: list[str] = field(default_factory=list)
    page_note: str | None = None      # the print departs from counting on from printed_start (numbers_from_start)


def _edge_indices(lines: list[str]) -> set[int]:
    """Indices (into `lines`) of the header/footer candidates: up to two
    non-blank lines at each end. A page of three or more lines always keeps at
    least one line in the middle, so a short page is never read as all edge
    (digit folding would otherwise make "Efni 1." / "Efni 2." look like a
    running head)."""
    nonblank = [i for i, ln in enumerate(lines) if ln.strip()]
    window = max(1, min(2, (len(nonblank) - 1) // 2))
    return set(nonblank[:window]) | set(nonblank[-window:])


def _edge_lines(page: str) -> list[str]:
    lines = page.splitlines()
    return [lines[i].strip() for i in sorted(_edge_indices(lines))]


def _shape(line: str) -> str:
    return _DIGITS.sub("#", line).strip().lower()


def strip_running_lines(pages: list[str], *, min_share: float = 0.3, min_pages: int = 3) -> list[str]:
    """Drop header/footer lines that recur on ≥ min_share of pages (and ≥ min_pages):
    journal name, author running head, bare page numbers (digits are shape-folded)."""
    if len(pages) < min_pages:
        return pages
    freq = Counter()
    for p in pages:
        for shape in set(map(_shape, _edge_lines(p))):
            freq[shape] += 1
    running = {s for s, n in freq.items() if n >= max(min_pages, int(round(min_share * len(pages))))}
    out = []
    for p in pages:
        lines = p.splitlines()
        edge_idx = _edge_indices(lines)
        kept = [ln for i, ln in enumerate(lines) if not (i in edge_idx and _shape(ln) in running)]
        out.append("\n".join(kept))
    return out


def dehyphenate(text: str) -> str:
    """Resolve '-\\n' line breaks with the shared hyphen rules (R18): a broken word
    is re-joined, a suspended hyphen ('ríkis- og') or a real one after an acronym
    or number ('EES-samningurinn', '10-ára') survives, anything else is left."""
    def _repl(m: re.Match) -> str:
        before = m.string[max(0, m.start() - 80):m.start() + 1]      # ends with the hyphen
        fix = resolve_hyphen_break(before, m.string[m.end():m.end() + 12])
        if fix is None:
            return m.group(0)
        keep_hyphen, sep = fix
        return ("-" if keep_hyphen else "") + sep

    return _HYPHEN_BREAK.sub(_repl, text)


def bin_known_share(text: str, sample_words: int = 2000) -> float | None:
    """Share of the text's first `sample_words` words (longer than two letters) that BÍN knows; None when the
    text has no such words, so a caller can tell "no text" from "all noise"."""
    words = [w for w in _WORD_RE.findall(text) if len(w) > 2][:sample_words]
    if not words:
        return None
    known = sum(1 for w in words if _bin.lookup(w)[1])
    return known / len(words)


def bin_known_ratio(text: str, sample_words: int = 2000) -> float:
    share = bin_known_share(text, sample_words)
    return 0.0 if share is None else share


def has_text_layer(page_texts: list[str]) -> bool:
    return any(len(t.strip()) >= _MIN_PAGE_CHARS for t in page_texts)


def _page_texts(pdf_bytes: bytes) -> list[str]:
    import fitz
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [page.get_text() for page in doc]


def extract_article(pdf_bytes: bytes, *, source: str, footnotes: bool,
                    ocr: Callable[[bytes], str | None] | None = None,
                    ocr_bin_threshold: float | None = None,
                    printed_start: int | None = None) -> ExtractedText:
    """Text of one article PDF with the char offset of every page.

    `printed_start`: printed page number of the article's first PDF page, when
    the caller knows it (R17, R46). The later pages then take the numbers that
    pagemap.numbers_from_start finds: counted on from printed_start, unless the
    print skips a value (`page_note` says so). Otherwise the article-local page
    map is used, which cannot be fitted for articles of fewer than three
    numbered pages.
    """
    page_texts = _page_texts(pdf_bytes)

    def _run_ocr() -> str:
        return ((ocr(pdf_bytes) if ocr else None) or "").strip()

    def _ocr_result(text: str) -> ExtractedText:
        if not text:                  # nothing was OCR'd: do not claim a source
            return ExtractedText("", [], "none", 0.0, page_texts)
        # OCR output is one flat text: it carries no page boundaries.
        return ExtractedText(text, [], "tesseract-isl", bin_known_ratio(text), page_texts)

    if not has_text_layer(page_texts):
        return _ocr_result(_run_ocr())

    def _filter(pages: list[str]) -> list[str]:
        return [dehyphenate(p) for p in strip_running_lines(pages)]

    body, offsets = parse_pdf_pages(pdf_bytes, footnotes=footnotes, page_filter=_filter)
    ratio = bin_known_ratio(body)
    if ocr_bin_threshold is not None and ratio < ocr_bin_threshold and ocr is not None:
        text = _run_ocr()
        if text:                      # a failed re-OCR keeps the embedded text
            return _ocr_result(text)

    page_note = None
    if printed_start is not None:
        numbers, page_note = numbers_from_start(page_texts, source, printed_start)

        def printed_for(i: int) -> int | None:
            return numbers[i] if i < len(numbers) else None
    else:
        printed = build_page_map(page_texts, source).printed

        def printed_for(i: int) -> int | None:
            return printed[i] if i < len(printed) else None

    page_offsets = [{"char": c, "pdf_page": i, "printed": printed_for(i)} for i, c in offsets]
    return ExtractedText(body, page_offsets, "embedded", ratio, page_texts, page_note)
