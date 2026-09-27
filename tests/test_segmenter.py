"""segment(): structure-aware passage splitting with offset integrity."""
import pytest
from engine.processors.segmenter import Passage, segment


def _check_integrity(text: str, passages: list[Passage]) -> None:
    """Invariants that hold for every input: offsets map back exactly,
    passages are ordered, contiguous, non-overlapping, and cover all non-blank text."""
    prev_end = 0
    for i, p in enumerate(passages):
        assert p.ordinal == i
        assert text[p.char_start:p.char_end] == p.text
        assert p.char_start >= prev_end
        assert text[prev_end:p.char_start].strip() == ""   # only whitespace between passages
        assert p.word_count == len(p.text.split())
        prev_end = p.char_end
    assert text[prev_end:].strip() == ""


LRD = """Úrskurður Landsréttar

Landsréttardómararnir A, B og C kveða upp úrskurð í máli þessu.

## Málsmeðferð og dómkröfur aðila

1. Sóknaraðili skaut málinu til Landsréttar með kæru 10. apríl 2025. Kæruheimild er í 1. mgr. 16. gr. lögræðislaga.

2. Sóknaraðili krefst þess að hinn kærði úrskurður verði felldur úr gildi.

3. Varnaraðili krefst staðfestingar hins kærða úrskurðar.

## Niðurstaða

4. Með vísan til forsendna hins kærða úrskurðar verður hann staðfestur.

5. Samkvæmt 1. mgr. 17. gr. lögræðislaga greiðist úr ríkissjóði þóknun verjanda.

## Úrskurðarorð:

Hinn kærði úrskurður er staðfestur.
"""


def test_lrd_headings_close_passages_and_set_kind():
    ps = segment(LRD)
    _check_integrity(LRD, ps)
    kinds = [p.section_kind for p in ps]
    assert kinds == ["annad", "malsmedferd", "nidurstada", "domsord"]
    assert ps[0].section_path is None
    assert ps[1].section_path == "Málsmeðferð og dómkröfur aðila"
    assert ps[1].text.startswith("## Málsmeðferð")        # heading leads its passage
    assert (ps[1].para_from, ps[1].para_to) == (1, 3)
    assert (ps[2].para_from, ps[2].para_to) == (4, 5)
    assert ps[3].section_path == "Úrskurðarorð"
    assert ps[3].para_from is None and ps[3].para_to is None


def test_numbered_paragraphs_accumulate_to_target_without_splitting_a_paragraph():
    paras = [f"{i}. " + ("Orð " * 60).strip() + "." for i in range(1, 21)]   # 20 × 61 words
    text = "\n\n".join(paras)
    ps = segment(text, target_words=250, max_words=400)
    _check_integrity(text, ps)
    assert all(p.word_count <= 400 for p in ps)
    # every passage starts at a paragraph boundary
    for p in ps:
        assert p.text.split(".")[0].strip().isdigit()
    assert ps[0].para_from == 1 and ps[-1].para_to == 20
    assert len(ps) == 4            # 5 paragraphs × 61 = 305 ≥ 250 → 4 passages of 5


def test_flat_text_is_split_on_sentences_under_max():
    sentence = "Þetta er setning sem hefur nokkur orð í sér og endar hér. "
    text = (sentence * 200).strip()          # ~2200 words, no blank lines at all
    ps = segment(text, target_words=250, max_words=400)
    _check_integrity(text, ps)
    assert len(ps) >= 5
    assert all(p.word_count <= 400 for p in ps)
    assert all(p.text.endswith(".") for p in ps)   # cuts at sentence ends
    assert all(p.section_kind == "annad" and p.para_from is None for p in ps)


def test_single_oversize_numbered_paragraph_is_split_but_keeps_para_range():
    long_para = "7. " + ("Orð sem endar. " * 300).strip()     # ~900 words, one paragraph
    text = "## Niðurstaða\n\n" + long_para
    ps = segment(text, target_words=250, max_words=400)
    _check_integrity(text, ps)
    assert len(ps) >= 3
    assert all(p.word_count <= 400 for p in ps)
    assert all((p.para_from, p.para_to) == (7, 7) for p in ps)
    assert all(p.section_kind == "nidurstada" for p in ps)


def test_sentence_longer_than_max_is_split_on_words():
    text = ("orð " * 1000).strip()            # one 1000-word "sentence", no punctuation
    ps = segment(text, target_words=250, max_words=400)
    _check_integrity(text, ps)
    assert all(p.word_count <= 400 for p in ps)


def test_crlf_and_whitespace_only_lines_are_paragraph_breaks():
    text = "## Málsatvik\r\n\r\n1. Fyrsta málsgrein.\r\n  \r\n2. Önnur málsgrein."
    ps = segment(text)
    _check_integrity(text, ps)
    assert ps[0].section_kind == "malsatvik"
    assert (ps[0].para_from, ps[0].para_to) == (1, 2)


def test_years_and_dates_are_not_paragraph_numbers():
    text = "Atvik gerðust\n\n2015. Þar er fjallað um málið.\n\n24. júní var haldinn fundur.\n\n3. Þriðja málsgrein."
    ps = segment(text)
    _check_integrity(text, ps)
    assert (ps[0].para_from, ps[0].para_to) == (3, 3)


def test_short_document_without_headings_is_one_passage():
    text = "Stutt skjal.\n\nMeð tveimur málsgreinum."
    ps = segment(text)
    _check_integrity(text, ps)
    assert len(ps) == 1 and ps[0].section_kind == "annad"


def test_empty_and_blank_return_nothing():
    assert segment("") == []
    assert segment("   \n\n  ") == []


def test_heading_with_colon_and_bold_roman_is_normalised():
    text = "**IV. Niðurstaða.**\n\nTexti.\n\n## Dómsorð:\n\nStefndi er sýkn."
    ps = segment(text)
    _check_integrity(text, ps)
    assert ps[0].section_path == "IV. Niðurstaða" and ps[0].section_kind == "nidurstada"
    assert ps[1].section_path == "Dómsorð" and ps[1].section_kind == "domsord"
