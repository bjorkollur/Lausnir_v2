# tests/test_timarit_ranges.py
"""Task 23d: article ranges (audit 5). An article keeps the top of the next item's start page when its last lines are
there (pagekind.article_end with `shared`, printed.end_ranges) and the top of a page whose lower part is an advert; an
entry the page map could not locate is placed by the offset of its located neighbours in list order where the page
there prints its title (printed.place_by_neighbours, R48a); a page with nothing but a folio and the download stamp up
its margin is blank. Every page is a real one from the archive (tests/fixtures/timarit/pages/pagekind.json, made by
make_pagekind_fixture.py there; running text in mid-page replaced by filler of the same length); each test names the
audited case it mirrors."""
import json
from pathlib import Path

import pytest

from engine.timarit import firstpage as FP
from engine.timarit import pagekind as PK
from engine.timarit import printed as PR
from engine.timarit.firstpage import Line, Page
from engine.timarit.manifest import ArticleEntry
from engine.timarit.pagekind import Kind, Typography
from engine.timarit.pagemap import PageMap

FIX = json.loads((Path(__file__).parent / "fixtures/timarit/pages/pagekind.json").read_text(encoding="utf-8"))


def page(key: str, edit=None) -> Page:
    """A fixture page; `edit(lines)` may change its lines (text, size, top, bold, left, right) first."""
    lines = [list(ln) for ln in FIX[key]["lines"]]
    if edit is not None:
        edit(lines)
    return Page([Line(*ln) for ln in lines], FIX[key]["height"], FIX[key]["width"])


class Reader:
    """A PageReader over fixture pages: by position in a list of keys, or by PDF index in a dict (other pages empty)."""

    def __init__(self, keys, edits=None):
        self.keys = dict(enumerate(keys)) if isinstance(keys, list) else dict(keys)
        self.pages = {i: page(k, (edits or {}).get(k)) for i, k in self.keys.items()}

    def __call__(self, i: int) -> Page:
        return self.pages.get(i, FP.EMPTY)

    def picture(self, i: int) -> float:
        return FIX[self.keys[i]]["picture"] if i in self.keys else 0.0

    def drawings(self, i: int) -> int:
        return FIX[self.keys[i]]["drawings"] if i in self.keys else 0


def shared_end(keys: list[str], title: str, authors: tuple[str, ...] = (), *, shared: bool = True,
               edits=None) -> PK.End:
    """article_end over all but the last of the pages (as if they followed each other); the last is the page where the
    next located item starts."""
    r = Reader(keys, edits)
    return PK.article_end(r, 0, len(keys) - 2, title, r.picture, list(authors), r.drawings, shared=shared)


# --- 1. the article's last lines on the next item's start page --------------------------------------------------------

@pytest.mark.parametrize("keys,title,signed", [
    # TL 1980 idx 44: signed "Guðrún Erlendsdóttir." on p. 257 over "EMBÆTTISSKIPANIR FRÁ 1. JANÚAR 1979"
    (["tl1980/273", "tl1980/274"], "Jafnréttisráð", ("Guðrún Erlendsdóttir",)),
    # TL 1976 idx 13: its table of judges runs on to p. 36, signed "Ólafur Egilsson." over "AMNESTY INTERNATIONAL"
    (["tl1976/39", "tl1976/40", "tl1976/41"], "Nýir dómarar í Haag", ("Ólafur Egilsson",)),
])
def test_an_article_signed_above_the_next_items_heading_ends_on_that_page(keys, title, signed):
    e = shared_end(keys, title)
    assert (e.last, e.signed, e.unlisted) == (len(keys) - 1, signed, None)
    heading = next(ln for ln in page(keys[-1]).lines if ln.size > 13 and ln.y > 200)
    assert e.cut == heading.y                       # the next item's heading: the article owns the page above it


def test_an_article_that_runs_on_mid_sentence_to_the_top_of_the_next_items_page_ends_there():
    # LMBL 2021-2 idx 5: its last two paragraphs ("hún hefði það almenna viðmið …") stand at the top of p. 19 over
    # "HIN MEINTA MEGINREGLA Í MATSFRÆÐUM Í MÁLI NR. 5/2021"
    e = shared_end(["lmbl2021b/17", "lmbl2021b/18"], "Hugleiðingar vegna dóms Hæstaréttar í máli nr. 5/2021",
                   ("Eva Bryndís Helgadóttir",))
    assert (e.last, e.signed) == (1, ())
    assert e.cut == next(ln.y for ln in page("lmbl2021b/18").lines if ln.text == "HIN MEINTA")


@pytest.mark.parametrize("keys,title", [
    # TL 1982 idx 35: "Frá Alþingi 1981–1982" ends on p. 209 with "Þ. V." over "NÝTT LÖGFRÆÐITÍMARIT"
    (["tl1982/224", "tl1982/225", "tl1982/226"], "Frá Alþingi 1981–1982"),
    # LMBL 2009-4 idx 4: the deans' answers run on to the top half of p. 16, signed "EI" over "Framhaldsnám skilar …"
    (["lmbl2009/11", "lmbl2009/12", "lmbl2009/13", "lmbl2009/14", "lmbl2009/15"], "Laganám í kastljósinu"),
])
def test_initials_set_apart_over_the_next_items_heading_sign_the_article_off(keys, title):
    e = shared_end(keys, title)
    assert (e.last, e.signed) == (len(keys) - 1, ())          # initials name nobody: no author from them


def _without(*needles):
    """An edit that drops the lines that start with any of `needles`."""
    def edit(lines):
        lines[:] = [ln for ln in lines if not ln[0].startswith(needles)]
    return edit


def _tail_dropped(lines):
    """TL 1980 p. 257 as if the item started at its top: every line above its heading dropped."""
    at = next(k for k, ln in enumerate(lines) if ln[0].startswith("EMBÆTTISSKIPANIR"))
    lines[:] = [ln for k, ln in enumerate(lines) if k >= at or ln[0] == "257"]


@pytest.mark.parametrize("why,edit,shared", [
    ("the next item starts at the top of its page", _tail_dropped, True),
    ("the lines above its heading neither go on with a sentence nor sign anything off",
     _without("3. nóvember", "Guðrún Erlendsdóttir"), True),
    ("the page is not where the next located item starts", None, False),
])
def test_the_next_items_page_stays_its_own_without_evidence_of_the_articles_last_lines(why, edit, shared):
    e = shared_end(["tl1980/273", "tl1980/274"], "Jafnréttisráð", shared=shared, edits={"tl1980/274": edit})
    assert (e.last, e.cut) == (0, None), why


def test_the_part_of_a_shared_page_above_the_next_items_heading_is_the_articles():
    # what read_printed_page reads as the last page of TL 1980 idx 44: its tail, not "Embættisskipanir"
    e = shared_end(["tl1980/273", "tl1980/274"], "Jafnréttisráð")
    tail = PK.above(page("tl1980/274"), e.cut)
    assert tail.lines[-1].text == "Guðrún Erlendsdóttir." and not any("EMBÆTTIS" in ln.text for ln in tail.lines)
    assert PK.above(page("tl1980/274"), None) == page("tl1980/274")


def test_end_ranges_ends_an_article_on_the_next_items_start_page_and_leaves_that_item_alone():
    # TL 1980 idx 44/45: "Jafnréttisráð" (bls. 256) runs on to the top of bls. 257, where "Embættisskipanir" starts
    r = Reader(["tl1980/273", "tl1980/274"])
    a = ArticleEntry(title="Jafnréttisráð", page_start=256, pdf_pages=[0, 0], provenance={"title": "llm"})
    b = ArticleEntry(title="Embættisskipanir frá 1. janúar 1979 til 31. desember 1980", page_start=257,
                     pdf_pages=[1, 1], provenance={"title": "llm"})
    printed = {0: 256, 1: 257}
    ends = PR.end_ranges([a, b], r, lambda i, lo=None, hi=None: printed.get(i))
    assert (a.pdf_pages, a.page_end, b.pdf_pages) == ([0, 1], 257, [1, 1])
    assert ends.ends[id(a)].signed == ("Guðrún Erlendsdóttir",) and ends.ends[id(b)].cut is None
    assert not any(n.startswith("FRÁVIK") for n in a.analysis_notes)


def test_an_article_ending_on_a_page_two_items_start_on_keeps_its_range():
    # TL 1979 idx 3 and 4 start on the same page: the first ends there (the next item in page order starts on it)
    r = Reader(["tl1980/273", "tl1980/274"])
    a = ArticleEntry(title="Réttarráðgjöfin", page_start=256, pdf_pages=[0, 0], provenance={"title": "llm"})
    b = ArticleEntry(title="Jafnréttisráð", page_start=256, pdf_pages=[0, 0], provenance={"title": "llm"})
    c = ArticleEntry(title="Embættisskipanir frá 1. janúar 1979", page_start=257, pdf_pages=[1, 1],
                     provenance={"title": "llm"})
    PR.end_ranges([a, b, c], r, lambda i, lo=None, hi=None: {0: 256, 1: 257}.get(i))
    assert (a.pdf_pages, b.pdf_pages) == ([0, 0], [0, 1])


# --- 1b. the article's last lines over an advert ----------------------------------------------------------------------

@pytest.mark.parametrize("key,t,title", [
    # Lögfræðingur 2009 idx 5: "Tax is a Feminist Issue" ends on p. 55 above Lögmannsstofan's advert
    ("lf2009/55", Typography((8.6, 5.9), 75.0), "Tax is a Feminist Issue"),
    # Úlfljótur 2001 idx 1: the last lines of "Lokaorð" stand on p. 69 over the sponsors' adverts
    ("ulf2001/28", Typography((23.4,), 40.0), "Gildistaka nýrra laga um lausafjárkaup hinn 1. Júní 2001"),
])
def test_the_articles_last_lines_over_an_advert_are_its_own(key, t, title):
    assert PK.classify(page(key), t, title, after_text=True) == Kind.TEXT
    assert PK.classify(page(key), t, title) == Kind.ADVERT            # (not after its text: the advert decides)


def test_an_advert_page_whose_first_line_does_not_go_on_with_a_sentence_stays_an_advert():
    def capital(lines):
        lines[0][0] = "W" + lines[0][0][1:]
    assert PK.classify(page("lf2009/55", capital), Typography((8.6, 5.9), 75.0), "Tax is a Feminist Issue",
                       after_text=True) == Kind.ADVERT


# --- 3. a blank last page ---------------------------------------------------------------------------------------------

def test_a_page_with_only_a_folio_and_the_download_stamp_is_blank_and_ends_no_range():
    # TL 2016-4 idx 4: p. 689 (pdf 190) prints its folio and, up the left margin, "logfraedingafelag.is - Hlaðið niður
    # af aðgangi …"; the stamp, read as a line of running text at the top, made it the article's last page
    blank = page("tl2016d/190")
    assert PK.content(blank) == []
    assert PK.classify(blank, Typography((10.5,), 64.0), "Sjóðir hlutafélaga", after_text=True) == Kind.BLANK
    r = Reader(["tl2016d/164", "tl2016d/165", "tl2016d/166", "tl2016d/189", "tl2016d/190"])
    e = PK.article_end(r, 0, 4, "Sjóðir hlutafélaga, færsla þeirra og upplausn", r.picture,
                       ["Stefán Már Stefánsson"], r.drawings)
    assert (e.last, e.outside) == (3, (4,))
    assert PR.last_body_page(r, 0, 4) == 3


def test_a_line_up_the_margin_is_a_stamp_only_when_it_is_turned_and_outside_the_text():
    stamp = Line("logfraedingafelag.is - Hlaðið niður af aðgangi Háskólinn á Akureyri", 9.0, 152.1, False, 3.1, 15.5)
    p = Page([stamp], 637.8, 419.5)
    assert PK.margin_stamp(stamp, p)
    assert not PK.margin_stamp(Line(stamp.text, 9.0, 152.1, False, 51.0, 371.0), p)     # set across the page
    assert not PK.margin_stamp(Line(stamp.text, 9.0, 152.1), p)                         # no box known
    assert not PK.margin_stamp(Line("Hverfi 101", 8.0, 300.0, False, 200.0, 208.0), p)  # a chart's label in the text


# --- 2. unlocated entries placed by their neighbours ------------------------------------------------------------------

def _place(entries, reader, n=300):
    pm = PageMap(None, [None] * n, [False] * n, 0.0)
    return PR.place_by_neighbours(entries, {}, pm, ["x"] * n, reader)


def _notes(a):
    return [n for n in a.analysis_notes if "nágrönnum" in n]


def test_an_unlocated_entry_is_placed_where_one_neighbours_offset_puts_a_page_printing_its_title():
    # TL 1993 idx 4 (bls. 43): its neighbours disagree (idx 3 pdf 34 = bls. 33, offset 1; idx 7 pdf 78 = bls. 73,
    # offset 5: 2. hefti between); pdf 44 prints "SKÝRSLA STJÓRNAR DÓMARAFÉLAGS ÍSLANDS STARFSÁRIÐ 1991-1992" in bold
    # body type under "Á VIÐ OG DREIF". Idx 3 then ends before it (it ran on to pdf 77).
    a3 = ArticleEntry(title="Samræmd túlkun Luganosamningsins", page_start=33, pdf_pages=[34, 77],
                      provenance={"title": "llm", "pages": "llm"})
    a4 = ArticleEntry(title="Skýrsla stjórnar Dómarafélags Íslands starfsárið 1991–1992", page_start=43,
                      provenance={"title": "llm", "pages": "llm"})
    a7 = ArticleEntry(title="Samstarf við samtök lögfræðinga á hinum Norðurlöndunum", page_start=73,
                      pdf_pages=[78, 81], provenance={"title": "llm", "pages": "llm"})
    _place([a3, a4, a7], Reader({43: "tl1993/43", 44: "tl1993/44", 45: "tl1993/45"}))
    assert (a3.pdf_pages, a4.pdf_pages, a7.pdf_pages[0], a4.page_start) == ([34, 43], [44, 77], 78, 43)
    assert _notes(a4) == ["staðsett eftir nágrönnum sínum í listanum (pdf-síða 45)"]
    assert not any(n.startswith("FRÁVIK") for n in a4.analysis_notes)


def test_a_short_section_item_is_placed_by_its_neighbours_though_its_heading_stands_low_on_the_page():
    # TL 1973 idx 29 "Bókaþáttur" (bls. 68): idx 28 pdf 128 = bls. 67 and idx 30 pdf 131 = bls. 70 agree; pdf 129
    # prints "BÓKAÞÁTTUR" under Gaukur Jörundsson's signature, near the foot of the page
    a28 = ArticleEntry(title="Frá lagadeild Háskólans", page_start=67, pdf_pages=[128, 130],
                       provenance={"title": "llm"})
    a29 = ArticleEntry(title="Bókaþáttur", page_start=68, provenance={"title": "llm"})
    a30 = ArticleEntry(title="Hafréttarráðstefna Sameinuðu þjóðanna", page_start=70, pdf_pages=[131, 133],
                       provenance={"title": "llm"})
    tops = _place([a28, a29, a30], Reader({128: "tl1973/128", 129: "tl1973/129", 130: "tl1973/130"}))
    assert (a28.pdf_pages, a29.pdf_pages, a30.pdf_pages[0]) == ([128, 128], [129, 130], 131)
    assert tops == {id(a29): next(ln.y for ln in page("tl1973/129").lines if ln.text == "BÓKAÞÁTTUR")}


@pytest.mark.parametrize("why,title,after", [
    ("no page near the place prints the title", "Dómsmálaþing 1992", 78),
    ("the page that prints it lies past the next located entry's start", None, 43),
    ("the title is printed only in running text (pdf 43, the end of idx 3)", "Dómstólar í EB ríkjunum", 78),
])
def test_an_entry_stays_unlocated_without_its_title_printed_between_its_neighbours(why, title, after):
    a3 = ArticleEntry(title="Samræmd túlkun Luganosamningsins", page_start=33, pdf_pages=[34, after - 1],
                      provenance={"title": "llm"})
    a4 = ArticleEntry(title=title or "Skýrsla stjórnar Dómarafélags Íslands starfsárið 1991–1992", page_start=43,
                      provenance={"title": "llm"})
    a7 = ArticleEntry(title="Samstarf við samtök lögfræðinga", page_start=73, pdf_pages=[after, 81],
                      provenance={"title": "llm"})
    _place([a3, a4, a7], Reader({43: "tl1993/43", 44: "tl1993/44", 45: "tl1993/45"}))
    assert (a4.pdf_pages, a3.pdf_pages, _notes(a4)) == (None, [34, after - 1], []), why


def test_an_entry_without_a_printed_start_or_without_located_neighbours_is_left_alone():
    a = ArticleEntry(title="Á víð og dreif", page_start=None, provenance={"title": "llm"})
    b = ArticleEntry(title="Bókaþáttur", page_start=68, provenance={"title": "llm"})
    _place([a, b], Reader({129: "tl1973/129"}))
    assert (a.pdf_pages, b.pdf_pages) == (None, None)


def test_an_entry_placed_by_its_neighbours_does_not_count_as_found_by_the_list():
    # TL 1996 (bulk): the parsed contents list found 19 of its 28 titles on their start pages and gave way to Claude's;
    # the entries rule 2 places were not located by the list's own page numbers, so they leave the measure as it was
    from engine.timarit import config as C
    from engine.timarit import tocgate as TG
    from engine.timarit.analyze import _on_page, _visual_text
    pages = {44: page("tl1993/44"), 129: page("tl1973/129")}
    entries = [ArticleEntry(title="Skýrsla stjórnar Dómarafélags Íslands starfsárið 1991–1992", pdf_pages=[44, 44],
                            provenance={"title": "toc"}),
               ArticleEntry(title="Bókaþáttur", pdf_pages=[129, 129], provenance={"title": "toc"})]
    for a in entries:
        a.checks = {"title_on_page": _on_page(a.title, _visual_text(pages[a.pdf_pages[0]]), C.TITLE_ON_PAGE_MIN)}
    on = lambda title, i: _on_page(title, _visual_text(pages.get(i, FP.EMPTY)), C.TITLE_ON_PAGE_MIN)  # noqa: E731

    def measure(placed):
        return TG.check(entries, {id(a): a.title for a in entries}, lambda i: pages.get(i, FP.EMPTY), lambda i: 0.0,
                        [], on, 200, lambda i: _visual_text(pages.get(i, FP.EMPTY)), placed=placed)
    assert measure(frozenset()).found == 1
    assert (measure(frozenset({id(entries[0])})).entries, measure(frozenset({id(entries[0])})).found) == (2, 0)


@pytest.mark.parametrize("why,keys,t,title", [
    # Lögrétta 2013-1 idx 2 (bulk sample): the next article's byline over its title ("Margrét Einarsdóttir, / lektor
    # … / Lena Mjöll Markusdóttir / BA í lögfræði") is no signature of the article before; it ended on its bibliography
    ("the next item's byline over its heading", ["logretta2013/33", "logretta2013/34"], Typography((9.7,), 41.0),
     "Tjón vegna missis hagnaðar við verkframkvæmdir"),
    # LMBL 2023-3 idx 8 (bulk sample): the golf item's photo caption half way down the page, under its photograph
    ("a caption under a picture, not at the top", ["lmbl2023c/20", "lmbl2023c/21"], Typography((10.2,), 120.0),
     "Gleði hjá FKL"),
    # TL 2003 idx 9 (bulk sample): the article ends on its list of cases (no full stop); the next article's author box
    # ("Ágúst Geir Ágústsson lauk lagaprófi …") stands at the top of its first page, over "Ágúst Geir Ágústsson:"
    ("the next article's author box after a list that ends without a full stop", ["tl2003/209", "tl2003/210"],
     Typography((10.3,), 51.0), "Lagaskil á sviði samningaréttar"),
    # LMBL 2016-2 idx 6 (bulk FRÁVIK count): the section's running head "LAGADAGURINN 2016" at the top, then a picture,
    # then its captions ("… Hörður H. Helgason og Helga Þórisdóttir.") over "Málstofa:"; the article ended on p. 17,
    # signed Guðríður Lára Þrastardóttir
    ("a running head over a picture and its captions", ["lmbl2016e/16", "lmbl2016e/17"], Typography((10.1, 11.3), 35.0),
     "Aðalmálstofa Lagadagsins 2016: Hver á síðasta orðið?"),
])
def test_the_next_items_own_lines_over_its_heading_are_no_tail(why, keys, t, title):
    assert PK.tail_on_next(page(keys[1]), t, title) is None, why


def test_an_entry_is_not_placed_on_the_start_of_another_entry_with_the_same_title():
    # TL 1973 idx 44/45 (bulk sample): two memorials of "Jóhann J. Ragnarsson"; the second starts under the first's
    # signature without a heading of its own, and the heading one page off is the first's
    a28 = ArticleEntry(title="Frá lagadeild Háskólans", page_start=67, pdf_pages=[128, 128],
                       provenance={"title": "llm"})
    first = ArticleEntry(title="Bókaþáttur", page_start=68, pdf_pages=[129, 130], provenance={"title": "llm"})
    second = ArticleEntry(title="Bókaþáttur", page_start=69, provenance={"title": "llm"})
    a30 = ArticleEntry(title="Hafréttarráðstefna Sameinuðu þjóðanna", page_start=70, pdf_pages=[131, 133],
                       provenance={"title": "llm"})
    _place([a28, first, second, a30], Reader({128: "tl1973/128", 129: "tl1973/129", 130: "tl1973/130"}))
    assert (second.pdf_pages, first.pdf_pages) == (None, [129, 130])


@pytest.mark.parametrize("keys,before,entry,after,placed", [
    # TL 1993 idx 8 "Þórður Björnsson" (bls. 75): the name also stands small over its heading "ÞÓRÐUR BJÖRNSSON /
    # DÁNARMINNING"; placed on pdf 80, idx 7 then ends on pdf 79 as audit 5 found (it took in the memorial)
    ({79: "tl1993/79", 80: "tl1993/80", 81: "tl1993/81"}, ("Samstarf við samtök lögfræðinga", 73, 78),
     ("Þórður Björnsson", 75), ("Stutt yfirlit um skaðabótaskyldu lögmanna", 77, 82), 80),
    # Lögrétta 2011-1 idx 1 (bls. 53): its title fills most of its first page in 30 pt, so the page's body size is the
    # title's; the neighbours disagree by one (-9, -10) and both put it on pdf 43; idx 0 then ends on pdf 42
    ({42: "logretta2011/42", 43: "logretta2011/43", 44: "logretta2011/44"}, ("Yfirtaka: Síðari hluti", 9, 0),
     ("Eru ákvarðanir einkarekinna háskóla um réttindi og skyldur nemenda stjórnsýsla eða ákvarðanir á grundvelli "
      "einkaréttar?", 53), ("Heimildir stjórnvalda til að taka afstöðu", 79, 69), 43),
])
def test_a_title_is_found_as_a_heading_past_a_smaller_copy_and_on_a_page_it_fills(keys, before, entry, after, placed):
    b = ArticleEntry(title=before[0], page_start=before[1], pdf_pages=[before[2], after[2] - 1],
                     provenance={"title": "llm"})
    a = ArticleEntry(title=entry[0], page_start=entry[1], provenance={"title": "llm"})
    c = ArticleEntry(title=after[0], page_start=after[1], pdf_pages=[after[2], after[2] + 5],
                     provenance={"title": "llm"})
    tops = _place([b, a, c], Reader(keys))
    assert (a.pdf_pages[0], b.pdf_pages) == (placed, [before[2], placed - 1])
    assert tops == {id(a): None}     # it heads its page (a name or a byline over it): read whole, byline and all


def test_a_placed_entry_reads_its_start_page_from_its_own_heading_down():
    # TL 1973 idx 6 "Skattaréttarnámskeið" (bulk): placed on p. 29, whose heading "RÁÐSTEFNA UM MANNRÉTTINDI" is the
    # item before it; read whole, the page gave a false "another title" deviation
    entry = ArticleEntry(title="Skattaréttarnámskeið", page_start=29, provenance={"title": "llm"})
    y = PR._prints_entry(page("tl1973/34"), entry)
    assert y == next(ln.y for ln in page("tl1973/34").lines if ln.text == "SKATTARÉTTARNÁMSKEIÐ")
    for crop, deviation in [(None, True), (y, False)]:
        a = ArticleEntry(title="Skattaréttarnámskeið", provenance={"title": "llm", "article_type": "llm"})
        PR.read_printed_page(a, PK.below(page("tl1973/34"), crop), None)
        assert bool([n for n in a.analysis_notes if n.startswith("FRÁVIK")]) == deviation
    # TL 1971 idx 8 "Leiðrétting" (bulk): "Páll Líndal." over its heading signs the item before it; the correction's
    # own lines are short, so read whole the page ends it with that signature
    r = Reader({172: "tl1971c/172"})
    entry = ArticleEntry(title="Leiðrétting", page_start=139, provenance={"title": "llm"})
    y = PR._prints_entry(page("tl1971c/172"), entry)
    for crop, signed in [(False, ("Páll Líndal",)), (True, ())]:
        a = ArticleEntry(title="Leiðrétting", page_start=139, pdf_pages=[172, 172], provenance={"title": "llm"})
        ends = PR.end_ranges([a], r, lambda i, lo=None, hi=None: None, tops={id(a): y} if crop else None)
        assert ends.ends[id(a)].signed == signed


@pytest.mark.parametrize("key,t,title,signed", [
    # TL 1953-4 idx 0 (bulk sample): signed "Árni Tryggvason." at the top of p. 9; a wide space, then the next article's
    # "Theodór B. Líndal:" over its title: the space lies between the two items, not inside the article's last lines
    ("tl1953/9", Typography((12.0,), 55.0), "Aðstaða dómara til andsvara við gagnrýni", ("Árni Tryggvason",)),
    # LMBL 2021-3 idx 3 (bulk sample): the report runs on in lower case at the top of p. 13 (its signature "…
    # Hrafnhildur Sæberg Þorsteinsdóttir" ends a line of the two columns, no sign-off line); a wide space, the section's
    # running head "LAGADAGURINN 2021" and "III. ÖRMÁLSTOFUR"
    ("lmbl2021c/12", Typography((10.2,), 55.0), "II. Peningaþvætti og alþjóðleg regluvarsla", ()),
])
def test_a_wide_space_after_the_articles_last_lines_is_the_next_items(key, t, title, signed):
    tail = PK.tail_on_next(page(key), t, title)
    assert tail is not None and tail.signed == signed



# --- fix round 1 (review of 23d) --------------------------------------------------------------------------------------

def test_a_box_on_another_articles_page_does_not_take_that_articles_run_on():
    # LMBL 1999-1 idx 10 "Úrskurðarnefnd" (review): a box half way down p. 10 (pdf 9) of idx 9 "Bótaréttur launþega";
    # p. 11's left column is idx 9 going on down the page beside "Prófnefnd vegna hdl." in the right column
    r = Reader(["lmbl1999/9", "lmbl1999/10"])
    host = ArticleEntry(title="Bótaréttur launþega – er breytinga þörf?", page_start=10, pdf_pages=[0, 0],
                        provenance={"title": "llm"})
    box = ArticleEntry(title="Úrskurðarnefnd", page_start=10, pdf_pages=[0, 0], provenance={"title": "llm"})
    nxt = ArticleEntry(title="Prófnefnd vegna hdl.", page_start=11, pdf_pages=[1, 1], provenance={"title": "llm"})
    PR.end_ranges([host, box, nxt], r, lambda i, lo=None, hi=None: {0: 10, 1: 11}.get(i))
    assert (host.pdf_pages, box.pdf_pages, box.page_end) == ([0, 0], [0, 0], None)
    t = Typography((9.0,), 35.0)
    assert PK.tail_on_next(page("lmbl1999/10"), t, "Úrskurðarnefnd", beside=True) is None
    assert PK.tail_on_next(page("lmbl1999/10"), t, "Úrskurðarnefnd") is not None    # (a lone item: its column goes on)


def test_the_next_items_heading_in_bold_body_type_is_found_by_its_title():
    # TL 1996 idx 8 "Dómaraþing 1995" (review): its report runs on to the top of p. 64 in lower case, signed "Þorgeir
    # Ingi Njálsson", over "DÓMSMÁLAÞING 9.-10. NÓVEMBER 1995", set bold in the body's size like every heading of the
    # volume: no line is set larger than the body, so the next entry's own title marks where it starts
    r = Reader(["tl1996b/68", "tl1996b/69"])
    a = ArticleEntry(title="Dómaraþing 1995", page_start=63, pdf_pages=[0, 0], provenance={"title": "llm"})
    b = ArticleEntry(title="Dómsmálaþing 9.–10. nóvember 1995", page_start=64, pdf_pages=[1, 1],
                     provenance={"title": "llm"})
    ends = PR.end_ranges([a, b], r, lambda i, lo=None, hi=None: {0: 63, 1: 64}.get(i))
    assert (a.pdf_pages, a.page_end, b.pdf_pages) == ([0, 1], 64, [1, 1])
    heading = next(ln.y for ln in page("tl1996b/69").lines if ln.text.startswith("DÓMSMÁLAÞING"))
    assert ends.ends[id(a)].cut == heading and ends.ends[id(a)].signed == ("Þorgeir Ingi Njálsson",)


def test_a_section_label_at_the_top_of_the_page_places_an_entry_named_by_it():
    # _prints_entry's label path: TL 1993 p. 43 (pdf 44) opens with "A VIÐ OG DREIF" (OCR for "Á víð og dreif")
    label = next(ln for ln in page("tl1993/44").lines if ln.text == "A VIÐ OG DREIF")
    for title in ("Á víð og dreif", "Á víð og dreif: Skýrsla stjórnar Dómarafélags Íslands"):
        assert PR._prints_entry(page("tl1993/44"), ArticleEntry(title=title)) == label.y
    assert PR._prints_entry(page("tl1993/43"), ArticleEntry(title="Á víð og dreif")) is None


def test_an_entry_whose_neighbours_offsets_find_its_title_on_two_pages_stays_unlocated():
    # the neighbours disagree (offsets 1 and 5) and each offset finds the title printed: two places, none taken
    a3 = ArticleEntry(title="Samræmd túlkun Luganosamningsins", page_start=33, pdf_pages=[34, 77],
                      provenance={"title": "llm"})
    a4 = ArticleEntry(title="Skýrsla stjórnar Dómarafélags Íslands starfsárið 1991–1992", page_start=43,
                      provenance={"title": "llm"})
    a7 = ArticleEntry(title="Samstarf við samtök lögfræðinga", page_start=73, pdf_pages=[78, 81],
                      provenance={"title": "llm"})
    assert _place([a3, a4, a7], Reader({44: "tl1993/44", 48: "tl1993/44"})) == {}
    assert (a4.pdf_pages, a3.pdf_pages, _notes(a4)) == (None, [34, 77], [])
