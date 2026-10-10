# tests/test_timarit_hefti.py
"""Task 23f: the hefti of each article in a bound volume of Tímarit lögfræðinga, read from the hefti covers.

Covers are built line by line as firstpage.read_page returns them (text, size, top), mirroring what the bulk volumes
print (measured 2026-10-10): the outer cover "4. HEFTI 30. ÁRGANGUR DESEMBER 1980" at 28% of the page, the inner cover
"4. HEFTI" two pages on, the volume's own contents listing "1. hefti", "2. hefti" … as headings, the 1999– covers
"1. hefti • 49. árgangur", TL 1971/1972's "1. hefti 1972" and TL 1975's OCR'd "2. HEFTl". Three real pages come from the
pagekind fixture (TL 1974 pdf 194, TL 1985 pdf 224, TL 2004 pdf 480)."""
import json
from pathlib import Path

import pytest

from engine.timarit import hefti as H
from engine.timarit.firstpage import Line, Page
from engine.timarit.manifest import ArticleEntry, IssueManifest, assign_external_ids

FIX = json.loads((Path(__file__).parent / "fixtures/timarit/pages/pagekind.json").read_text(encoding="utf-8"))
_HEIGHT = 700.0
_TEXT = "meginmál greinarinnar heldur hér áfram með nægum texta í línunni"


def fixture_page(key: str) -> Page:
    d = FIX[key]
    return Page([Line(*ln) for ln in d["lines"]], d["height"], d["width"])


def text_page(*extra: Line) -> Page:
    """An article's page of running text (body 10 pt), with `extra` lines."""
    return Page(sorted([Line(_TEXT, 10.0, 90 + 18 * k) for k in range(25)] + list(extra), key=lambda ln: ln.y), _HEIGHT)


def cover(issue_line: str, *, size: float = 10.7, top: float = 0.28) -> Page:
    """An outer cover: the masthead, the issue line, the publisher and a few contents lines."""
    return Page([Line("TÍMARIT LÖGFRÆÐINGA", 24.0, 0.15 * _HEIGHT), Line(issue_line, size, top * _HEIGHT),
                 Line("ÚTGEFANDI: LÖGFRÆÐINGAFÉLAG ÍSLANDS", 9.2, 0.32 * _HEIGHT),
                 Line("EFNI:", 9.2, 0.36 * _HEIGHT)] +
                [Line(f"Grein númer {k} eftir höfund (bls. {k})", 9.2, (0.40 + 0.03 * k) * _HEIGHT) for k in range(8)],
                _HEIGHT)


def inner(issue: str) -> Page:
    """An inner cover: the issue line alone, small, over the contents (TL 1973–1993)."""
    return Page([Line(f"{issue}. HEFTI", 9.1, 0.3 * _HEIGHT)] +
                [Line(f"Grein númer {k} eftir höfund ........ {k}", 9.2, (0.36 + 0.03 * k) * _HEIGHT) for k in range(8)],
                _HEIGHT)


def volume_contents() -> Page:
    """The bound volume's own contents (pdf 1): its hefti as headings, lowercase and bare."""
    return Page([Line("EFNISYFIRLIT", 12.0, 0.08 * _HEIGHT), Line("1. hefti", 14.0, 0.25 * _HEIGHT),
                 Line("Fyrsta grein ........ 1", 10.0, 0.28 * _HEIGHT), Line("2. hefti", 14.0, 0.39 * _HEIGHT),
                 Line("Önnur grein ........ 65", 10.0, 0.42 * _HEIGHT)], _HEIGHT)


# --- the issue line ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("4. HEFTI 30. ÁRGANGUR DESEMBER 1980", ("4", 30, None)),
    ("1.HEFTI 23. ÁRGANGUR MARZ 1973", ("1", 23, None)),
    ("2. HEFTl 25. ÁRGANGUR OKTÓBER 1975", ("2", 25, None)),        # OCR: a lowercase l for the I
    ("1. hefti • 49. árgangur", ("1", 49, None)),
    ("2. hefti • 54. árqanqur", ("2", 54, None)),                   # OCR'd g
    ("1. hefti 1972", ("1", None, 1972)),
    ("3.-4. HEFTI 40. ÁRGANGUR", ("3-4", 40, None)),
])
def test_a_cover_names_its_hefti_and_its_volume_or_year(line, expected):
    assert H.issue_line(cover(line)) == expected


def test_an_inner_cover_names_its_hefti_in_capitals_alone():
    assert H.issue_line(inner("3")) == ("3", None, None)


def test_real_covers_from_the_bulk():
    assert H.issue_line(fixture_page("tl1985/224")) == ("4", 35, None)
    assert H.issue_line(fixture_page("tl2004/480")) == ("4", 54, None)
    assert H.issue_line(fixture_page("tl1974/194")) == ("3", 25, None)  # 1975's 3. hefti, bound into the 1974 volume


@pytest.mark.parametrize("page", [
    volume_contents(),                                               # headings of the volume's contents
    text_page(Line("2. hefti, maí 1998.", 9.1, 0.24 * _HEIGHT)),     # a reference in running text (TL 1998 pdf 386)
    text_page(Line("1. tbl. 1997.", 9.1, 0.23 * _HEIGHT)),           # a footnote (TL 1999 pdf 35)
    text_page(Line("Tímarit lögfræðinga, 2. hefti 1993", 8.4, 0.3 * _HEIGHT)),
    text_page(Line("1. HEFTI 26. ÁRGANGUR MAÍ 1978", 4.0, 0.17 * _HEIGHT)),   # an old cover pictured small (TL 2000)
    cover("4. HEFTI 30. ÁRGANGUR DESEMBER 1980", top=0.08),          # in the margin band: a running head's place
    cover("4. HEFTI 30. ÁRGANGUR DESEMBER 1980", top=0.6),           # in the lower half
])
def test_what_is_no_cover(page):
    assert H.issue_line(page) is None


@pytest.mark.parametrize("key", ["ulf2023/115", "tl2024/10", "tl1985/141"])
def test_a_running_head_a_citation_or_a_footnote_is_no_cover(key):
    assert H.issue_line(fixture_page(key)) is None


# --- the hefti of a volume --------------------------------------------------------------------------------------------

def _volume(covers: dict[int, Page], pages: int = 60):
    """A reader over a volume of `pages` pages: the covers where given, the volume's contents on pdf 1, text elsewhere."""
    book = {1: volume_contents(), **covers}
    return lambda i: book.get(i, text_page())


def test_each_hefti_starts_on_its_first_cover_and_the_volumes_contents_start_none():
    page = _volume({4: cover("1. HEFTI 30. ÁRGANGUR APRÍL 1980"), 6: inner("1"),
                    20: cover("2. HEFTI 30. ÁRGANGUR ÁGÚST 1980"), 22: inner("2"),
                    36: inner("3"),                                   # the outer cover is missing: the inner one starts it
                    50: cover("4. HEFTI 30. ÁRGANGUR DESEMBER 1980"), 52: inner("4")})
    assert H.find_hefti(page, 0, 59, volume=30, years=(1980, 1980)) == [
        H.Hefti("1", 4), H.Hefti("2", 20), H.Hefti("3", 36), H.Hefti("4", 50)]


def test_a_hefti_of_another_volume_bound_in_is_foreign():
    # TL 1974 binds 1975's "3. HEFTI 25. ÁRGANGUR NÓVEMBER 1975" between its own 3. and 4. hefti
    page = _volume({4: cover("1. HEFTI 24. ÁRGANGUR JÚLÍ 1974"), 20: cover("3. HEFTI 24. ÁRGANGUR NÓVEMBER 1974"),
                    30: cover("3. HEFTI 25. ÁRGANGUR NÓVEMBER 1975"), 32: fixture_page("tl1974/194"),
                    44: cover("4. HEFTI 24. ÁRGANGUR DESEMBER 1974")})
    assert H.find_hefti(page, 0, 59, volume=24, years=(1974, 1974)) == [
        H.Hefti("1", 4), H.Hefti("3", 20), H.Hefti("3", 30, "25. árg."), H.Hefti("4", 44)]


def test_a_part_of_a_split_file_reads_only_its_window_and_checks_the_year():
    page = _volume({4: cover("1. hefti 1971"), 30: cover("2. hefti 1971"), 40: cover("1. hefti 1972"),
                    50: cover("2. hefti 1972")})
    assert H.find_hefti(page, 40, 59, volume=22, years=(1972, 1972)) == [H.Hefti("1", 40), H.Hefti("2", 50)]
    assert H.find_hefti(page, 0, 59, volume=22, years=(1972, 1972))[0] == H.Hefti("1", 4, "1971")


@pytest.mark.parametrize("hefti,why", [
    ([], "engin heftisforsíða fannst"),
    ([H.Hefti("1", 4), H.Hefti("3", 20), H.Hefti("2", 40)], "heftin á forsíðunum eru ekki í röð (1, 3, 2)"),
    ([H.Hefti("1", 4), H.Hefti("2", 20), H.Hefti("1", 40)], "heftin á forsíðunum eru ekki í röð (1, 2, 1)"),
])
def test_boundaries_that_cannot_be_trusted(hefti, why):
    assert H.doubt(hefti) == why


def test_boundaries_in_order_with_a_hefti_missing_or_one_bound_in_are_trusted():
    assert H.doubt([H.Hefti("1", 4), H.Hefti("2", 58), H.Hefti("4", 108)]) is None      # TL 1975: 3. hefti is apart
    assert H.doubt([H.Hefti("3", 20), H.Hefti("3", 30, "25. árg."), H.Hefti("4", 44)]) is None


# --- assigning the hefti ----------------------------------------------------------------------------------------------

def _tl(year=1980, volume=30, articles=(), **over) -> IssueManifest:
    base = dict(source="timarit_logfraedinga", year=year, volume=volume, issue=None,
                label=f"Tímarit lögfræðinga, {volume}. árg. ({year})", file=f"x/TL {year}.pdf", pages=284,
                status="imported")
    base.update(over)
    return IssueManifest(**base, articles=list(articles))


def _art(page_start, pdf, issue=None, prov=None, title=None) -> ArticleEntry:
    a = ArticleEntry(title=title or f"Grein á bls. {page_start}", page_start=page_start,
                     page_end=None if page_start is None else page_start + 5,
                     pdf_pages=None if pdf is None else [pdf, pdf + 5], provenance={"title": "toc"})
    if issue is not None:
        a.issue, a.provenance["issue"] = issue, prov
    return a


# TL 1980 as measured: covers on pdf 4, 72, 136, 212; inner covers two pages on
_TL1980 = {4: cover("1. HEFTI 30. ÁRGANGUR APRÍL 1980"), 72: cover("2. HEFTI 30. ÁRGANGUR ÁGÚST 1980"), 74: inner("2"),
           136: cover("3. HEFTI 30. ÁRGANGUR NÓVEMBER 1980"), 138: inner("3"),
           212: cover("4. HEFTI 30. ÁRGANGUR DESEMBER 1980"), 214: inner("4")}


def test_an_article_without_a_hefti_gets_the_hefti_its_first_page_falls_in():
    # the audit's TL 1980 bls. 248 (pdf 265; masthead "4. hefti")
    m = _tl(articles=[_art(65, 74), _art(248, 265)])
    r = H.assign_hefti(m, _volume(_TL1980, 284), 0, 283)
    assert [(a.issue, a.provenance["issue"]) for a in m.articles] == [("2", "page"), ("4", "page")]
    assert r.assigned == [0, 1] and r.doubt is None
    assert assign_external_ids(m) == ["1980-30-2-65", "1980-30-4-248"]


def test_tl_1981_pages_160_and_167_are_in_the_third_hefti():
    covers = {4: cover("1. HEFTI 31. ÁRGANGUR MAÍ 1981"), 60: cover("2. HEFTI 31. ÁRGANGUR SEPTEMBER 1981"),
              128: cover("3. HEFTI 31. ÁRGANGUR NÓVEMBER 1981"), 188: cover("4. HEFTI 31. ÁRGANGUR DESEMBER 1981")}
    m = _tl(1981, 31, [_art(160, 172), _art(167, 179)])
    H.assign_hefti(m, _volume(covers, 240), 0, 239)
    assert [a.issue for a in m.articles] == ["3", "3"]


def test_a_hefti_that_is_set_is_never_changed_whatever_its_provenance():
    m = _tl(articles=[_art(59, 120, "4", "leitir"), _art(100, 150, "2", "user"), _art(149, 162, "2", None)])
    r = H.assign_hefti(m, _volume(_TL1980, 284), 0, 283)
    assert [(a.issue, a.provenance.get("issue")) for a in m.articles] == [("4", "leitir"), ("2", "user"), ("2", None)]
    assert r.assigned == [] and r.disagree == [(0, "4", "2"), (1, "2", "3"), (2, "2", "3")]
    assert all(not a.analysis_notes for a in m.articles)


def test_restarted_numbering_is_told_apart_by_the_hefti():
    # TL 1973 restarts its page numbers in every hefti: two articles on bls. 1 were one id (and "-2") before
    covers = {4: cover("1.HEFTI 23. ÁRGANGUR MARZ 1973"), 60: cover("2. HEFTI 23. ÁRGANGUR JÚLÍ 1973")}
    m = _tl(1973, 23, [_art(1, 8), _art(1, 64)])
    assert assign_external_ids(m) == ["1973-23-x-1", "1973-23-x-1-2"]
    H.assign_hefti(m, _volume(covers, 100), 0, 99)
    assert assign_external_ids(m) == ["1973-23-1-1", "1973-23-2-1"]


def test_a_volume_whose_boundaries_cannot_be_read_is_left_unset_with_a_plain_note():
    covers = {4: cover("1. HEFTI 30. ÁRGANGUR APRÍL 1980"), 40: cover("3. HEFTI 30. ÁRGANGUR"),
              60: cover("2. HEFTI 30. ÁRGANGUR")}
    m = _tl(articles=[_art(5, 10), _art(30, 45, "2", "leitir")])
    r = H.assign_hefti(m, _volume(covers, 100), 0, 99)
    assert r.doubt and m.articles[0].issue is None and r.assigned == []
    assert m.articles[0].analysis_notes == [
        "hefti greinarinnar var ekki skráð: heftin á forsíðunum eru ekki í röð (1, 3, 2)"]
    assert m.articles[1].analysis_notes == []                        # its hefti is set: nothing to say


def test_articles_before_the_first_cover_in_a_bound_in_hefti_or_without_pages_are_left_with_a_note():
    covers = {4: cover("1. HEFTI 24. ÁRGANGUR"), 30: cover("3. HEFTI 25. ÁRGANGUR NÓVEMBER 1975"),
              44: cover("4. HEFTI 24. ÁRGANGUR")}
    m = _tl(1974, 24, [_art(None, 2), _art(150, 32), _art(400, None), _art(170, 46)])
    r = H.assign_hefti(m, _volume(covers, 60), 0, 59)
    assert [a.issue for a in m.articles] == [None, None, None, "4"]
    notes = [a.analysis_notes for a in m.articles]
    assert notes[0] == ["hefti greinarinnar var ekki skráð: hún hefst á undan fyrstu heftisforsíðu bindisins (pdf-síðu 5)"]
    assert notes[1] == ["hefti greinarinnar var ekki skráð: hún er í 3. hefti 25. árg., sem er bundið inn í bindið"]
    assert notes[2] == ["hefti greinarinnar var ekki skráð: engar síður eru skráðar á hana (pdf_pages)"]
    assert r.assigned == [3] and sorted(r.unset) == [0, 1, 2]


def test_assigning_twice_changes_nothing_and_keeps_one_note():
    m = _tl(articles=[_art(None, 2), _art(248, 265)])
    m.articles[0].analysis_notes = ["önnur athugasemd"]
    page = _volume(_TL1980, 284)
    H.assign_hefti(m, page, 0, 283)
    first = [(a.issue, list(a.analysis_notes)) for a in m.articles]
    r = H.assign_hefti(m, page, 0, 283)
    assert [(a.issue, list(a.analysis_notes)) for a in m.articles] == first and r.assigned == []
    assert m.articles[0].analysis_notes[0] == "önnur athugasemd" and len(m.articles[0].analysis_notes) == 2


def test_only_tl_bound_volumes_are_assigned():
    assert H.bound_volume(_tl())
    assert not H.bound_volume(_tl(issue="1"))                        # an issue PDF
    assert not H.bound_volume(_tl(file=None))                        # article files
    assert not H.bound_volume(_tl(source="logfraedingur"))           # an annual of one issue
