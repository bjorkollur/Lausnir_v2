# tests/test_timarit_analyze_ends.py
"""Task 22c: analyze ends articles on their own last text page, reads signatures and the numbers printed on located
pages, and flags significant deviations ("FRÁVIK: ", R42). Typeset PDFs as in test_timarit_analyze_printed; each test
names the audited case (audit 2) it mirrors."""
import pytest

from engine.config.sources import get_config
from engine.timarit import analyze as A
from engine.timarit import firstpage as FP
from engine.timarit import pagekind as PK
from engine.timarit import printed as P
from engine.timarit.analyze import FORCED_REVIEW_NOTES, FRAVIK, analyze_issue
from engine.timarit.manifest import ArticleEntry, _coverage_ok
from engine.timarit.pagemap import PATTERNS, build_page_map
from engine.timarit.printed import read_printed_page
from engine.timarit.toc import TocEntry
from engine.timarit.ulfljotur_xlsx import XlsxArticle
from tests.test_timarit_analyze_printed import _body, _deps, _manifest, _typeset

ADVERT = [(20, 120, "Lögmannsstofan Lex ehf."), (12, 160, "Borgartúni 26 • 105 Reykjavík"),
          (12, 180, "Sími 590 2600 • www.lex.is • lex@lex.is"), (16, 300, "Í fremstu röð í yfir hundrað ár")]


def _fravik(a) -> list[str]:
    return [n for n in a.analysis_notes if n.startswith(FRAVIK)]


def _issue_with_adverts():
    toc = "Efnisyfirlit\n1\nJón Jónsson:\nFyrsta greinin\n7\nAnna Pálsdóttir:\nÖnnur greinin\n9\nPáll Pálsson:\nÞriðja greinin"
    return [[(10, 60 + 14 * k, ln) for k, ln in enumerate(toc.splitlines())],
            [(12.5, 580, "1"), (14, 100, "Jón Jónsson:"), (16, 130, "FYRSTA GREININ")] + _body(170, 25),
            [(12.5, 580, "2")] + _body(60, 35), [(12.5, 580, "3")] + _body(60, 12),
            [(12.5, 580, "4")] + ADVERT, [(12.5, 580, "5")] + ADVERT, [(12.5, 580, "6")],
            [(12.5, 580, "7"), (14, 100, "Anna Pálsdóttir:"), (16, 130, "ÖNNUR GREININ")] + _body(170, 25),
            [(12.5, 580, "8")] + _body(60, 35),
            [(12.5, 580, "9"), (14, 100, "Páll Pálsson:"), (16, 130, "ÞRIÐJA GREININ")] + _body(170, 25)]


async def test_an_article_ends_on_its_last_text_page_and_the_adverts_after_it_are_no_gap():
    # TL 2024 idx 3 / Lögbrú 2013 idx 0: the article ends on p. 3; two pages of lawyers' adverts and a blank page follow
    m = await analyze_issue(_manifest("timarit_logfraedinga", issue="1", file="x/TL 2010_01.pdf"),
                            get_config("timarit_logfraedinga"), _deps(_typeset(_issue_with_adverts())))
    first = m.articles[0]
    assert (first.pdf_pages, first.page_end) == ([1, 3], 3)
    assert m.page_map["non_article"] == [4, 5, 6]
    assert _coverage_ok(m)                         # the advert and blank pages are no gap between the articles


async def test_in_a_page_window_the_adverts_and_the_ranges_are_pages_of_the_whole_file_and_coverage_holds():
    # Task 23e: the same issue as the second volume of a file (pdf 10-19), another volume before it and one after it
    pages = _issue_with_adverts()
    m = await analyze_issue(_manifest("timarit_logfraedinga", issue="1", file="x/TL 2010_01.pdf", pdf_window=[10, 19]),
                            get_config("timarit_logfraedinga"), _deps(_typeset(pages * 3)))
    assert [a.pdf_pages for a in m.articles] == [[11, 13], [17, 18], [19, 19]]
    assert m.page_map["non_article"] == [14, 15, 16]
    assert m.pages == 30 and _coverage_ok(m)
    m.pdf_window = None                            # the same ranges measured against the whole file leave 20 pages out
    assert not _coverage_ok(m)


async def test_in_a_page_window_a_note_names_the_pdf_page_of_the_whole_file():
    # Task 23e: unnumbered pages, an entry without a start page found by its title on pdf 5 (the window's second page):
    # the notes name pdf-síðu 6 (1-based), as the whole file's analysis does, not the window's 2
    vol = lambda w: [[(20, 100, f"{w} bindið")],                                                   # noqa: E731
                     [(14, 100, "Jón Jónsson:"), (16, 130, f"GREIN {w.upper()} BINDIS")] + _body(170, 25),
                     _body(60, 35), _body(60, 12)]
    llm = [TocEntry("Grein síðara bindis", ["Jón Jónsson"], None, section="Fræðigrein")]
    notes = []
    for window in ([4, 7], None):
        m = await analyze_issue(_manifest("timarit_logfraedinga", issue="1", file="x/TL 2010_01.pdf",
                                          pdf_window=window),
                                get_config("timarit_logfraedinga"),
                                _deps(_typeset(vol("Fyrra") + vol("Síðara")), llm=llm))
        (a,) = m.articles
        notes.append((a.pdf_pages, a.analysis_notes))
    assert notes[0] == notes[1] and notes[0][0] == [5, 7]
    assert any("pdf-síðu 6" in n for n in notes[0][1]) and not any("pdf-síðu 2" in n for n in notes[0][1])


async def test_a_note_signed_at_its_end_takes_its_author_from_the_signature():
    # TL 1985 idx 9 / 15 / 32: "Á víð og dreif" notes are signed at the end ("Guðrún Erlendsdóttir"), set right
    toc = "Efnisyfirlit\n1\nStjórnvald eða dómstóll\n3\nAnna Pálsdóttir:\nÖnnur greinin\n5\nPáll Pálsson:\nÞriðja greinin"
    pages = [[(10, 60 + 14 * k, ln) for k, ln in enumerate(toc.splitlines())],
             [(12.5, 580, "1"), (16, 130, "STJÓRNVALD EÐA DÓMSTÓLL")] + _body(170, 25),
             [(12.5, 580, "2")] + _body(60, 12) + [(9, 240, "Guðrún Erlendsdóttir", 260)],
             [(12.5, 580, "3"), (14, 100, "Anna Pálsdóttir:"), (16, 130, "ÖNNUR GREININ")] + _body(170, 25),
             [(12.5, 580, "4")] + _body(60, 35),
             [(12.5, 580, "5"), (14, 100, "Páll Pálsson:"), (16, 130, "ÞRIÐJA GREININ")] + _body(170, 25)]
    m = await analyze_issue(_manifest("timarit_logfraedinga", issue="1", file="x/TL 2010_01.pdf"),
                            get_config("timarit_logfraedinga"), _deps(_typeset(pages)))
    a = m.articles[0]
    assert a.authors == ["Guðrún Erlendsdóttir"] and a.provenance["authors"] == "page"


async def test_an_unusable_page_map_reads_the_number_printed_on_the_located_page():
    # Lögrétta 2005 idx 0, 1, 3: the file binds articles numbered 23–28 and 3–8; no map fits, Claude read no page
    # numbers; the first page is an article's own (p. 23), not a cover
    first = [[(12.5, 40, "23"), (30, 150, "Annar titill um lög"), (11, 100, "Anna Pálsdóttir")] + _body(200, 25)]
    first += [[(12.5, 40, str(n))] + _body(60, 35) for n in range(24, 29)]
    second = [[(12.5, 40, "3"), (30, 150, "Fyrsti titill um rétt"), (11, 100, "Jón Jónsson")] + _body(200, 25)]
    second += [[(12.5, 40, str(n))] + _body(60, 35) for n in range(4, 9)]
    llm = [TocEntry("Fyrsti titill um rétt", ["Jón Jónsson"], None), TocEntry("Annar titill um lög", ["Anna Pálsdóttir"], None)]
    m = await analyze_issue(_manifest("logretta", year=2005, volume=2, issue="1", file="x/Lögrétta 2005 1.tbl.pdf"),
                            get_config("logretta"), _deps(_typeset(first + second), llm=llm))
    assert m.page_map["offset"] is None                               # the map is unusable
    by = {a.title: a for a in m.articles}
    assert (by["Annar titill um lög"].pdf_pages, by["Annar titill um lög"].page_start) == ([0, 5], 23)
    assert (by["Fyrsti titill um rétt"].pdf_pages, by["Fyrsti titill um rétt"].page_start) == ([6, 11], 3)
    assert (by["Annar titill um lög"].page_end, by["Fyrsti titill um rétt"].page_end) == (28, 8)
    # found by their titles; Claude's list sends the issue to review already: notes, not FRÁVIK (fix round 1)
    assert all(any("fannst eftir titli sínum" in n for n in a.analysis_notes) and not _fravik(a) for a in m.articles)
    assert m.status == "review"


async def test_an_article_file_that_runs_into_the_next_item_ends_before_it():
    # Úlfljótur 1957 187.pdf (idx 4): the scan goes on into "Juristforeningen 50 ára." under "RAGI BJÖRNSSON, stud.
    # jur.:" on its last page
    rows = [XlsxArticle("Nokkrar athugasemdir um skjöl", ["Ólafur Pálsson"], 11, "4", 3, 9)]
    pages = [[(30, 120, "Nokkrar athugasemdir um skjöl"), (14, 170, "Ólafur Pálsson")] + _body(220, 25)
             + [(10, 580, "ÚLFLJÓTUR - 3")]]
    pages += [_body(60, 35) + [(10, 580, f"ÚLFLJÓTUR - {n}")] for n in (4, 5)]
    pages += [[(11, 60, "RAGI BJÖRNSSON, stud. jur.:"), (16, 90, "Juristforeningen 50 ára.")] + _body(130, 30)
              + [(10, 580, "ÚLFLJÓTUR - 6")]]
    m = _manifest("ulfljotur", year=1958, volume=11, file=None, files=["Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/187.pdf"])
    m = await analyze_issue(m, get_config("ulfljotur"), _deps(_typeset(pages), xlsx=rows))
    a = m.articles[0]
    assert (a.pdf_pages, a.page_start, a.page_end) == ([0, 2], 3, 5)
    assert any("Juristforeningen 50 ára" in n for n in _fravik(a)) and m.status == "review"


async def test_a_registry_author_printed_nowhere_is_dropped_and_flagged():
    # Úlfljótur 1975 idx 9: the sheet names "Ritstjórn" for "Skrá yfir lögfræðibækur", which prints no author
    rows = [XlsxArticle("Skrá yfir lögfræðibækur útgefnar 1974 og 1975", ["Ritstjórn"], 28, "x", 241, 24)]
    pages = [[(30, 120, "SKRÁ YFIR LÖGFRÆÐIBÆKUR"), (30, 160, "ÚTGEFNAR 1974 OG 1975")] + _body(220, 25)
             + [(10, 580, "ÚLFLJÓTUR - 241")]]
    m = _manifest("ulfljotur", year=1975, volume=28, file=None, files=["Úlfljótur/Tímarit/Ulfljotur_1975 28 arg/24.pdf"])
    m = await analyze_issue(m, get_config("ulfljotur"), _deps(_typeset(pages), xlsx=rows))
    a = m.articles[0]
    assert a.authors == []
    assert _fravik(a) == ["FRÁVIK: höfundurinn „Ritstjórn“ úr skránni er hvergi prentaður við greinina og var felldur "
                          "niður"]


@pytest.mark.parametrize("n, text", [(1, "1 síðu"), (2, "2 síðum"), (11, "11 síðum"), (21, "21 síðu"), (101, "101 síðu"),
                                     (111, "111 síðum")])
def test_count_word_agrees_in_number(n, text):
    assert A.count_word(n, "síðu", "síðum") == text


def test_fravik_is_one_of_the_notes_that_force_review():
    assert FRAVIK == "FRÁVIK: " and FRAVIK in FORCED_REVIEW_NOTES


# --- fix round 1 ----------------------------------------------------------------------------------------------------

async def test_an_unnumbered_cover_is_still_skipped_when_the_page_map_is_usable():
    # the cover rule asks the page's own number only for an unusable map; a usable map's number for the cover (the
    # first article is on p. 5, so the map gives the cover 4) must not make the cover, which prints the titles in large
    # type, an article's first page
    cover = [(24, 100, "Samningalögin sjötug"), (24, 140, "Leiðbeiningarskylda stjórnvalds")]
    cover += [(10, 300 + 14 * k, "Lögfræðingafélag Íslands gefur út tímaritið fjórum sinnum á ári") for k in range(6)]
    pages = [cover,
             [(10, 580, "5"), (11.5, 100, "Páll Hreinsson:"), (11.5, 118, "LEIÐBEININGARSKYLDA STJÓRNVALDS")] + _body(160),
             [(10, 580, "6")] + _body(60, 20), [(10, 580, "7")] + _body(60, 20),
             [(10, 580, "8"), (11.5, 100, "Matthías G. Pálsson:"), (11.5, 118, "SAMNINGALÖGIN SJÖTUG")] + _body(160),
             [(10, 580, "9")] + _body(60, 20)]
    llm = [TocEntry("Samningalögin sjötug", ["Matthías G. Pálsson"], None, section="Fræðigrein"),
           TocEntry("Leiðbeiningarskylda stjórnvalds", ["Páll Hreinsson"], None, section="Fræðigrein")]
    m = await analyze_issue(_manifest("timarit_logfraedinga", issue="1", file="x/TL 2007_01.pdf"),
                            get_config("timarit_logfraedinga"), _deps(_typeset(pages), llm=llm))
    assert m.page_map["offset"] == 4                                    # the map is usable; it gives the cover 4
    by = {a.title: a for a in m.articles}
    assert by["Samningalögin sjötug"].pdf_pages == [4, 5] and by["Leiðbeiningarskylda stjórnvalds"].pdf_pages == [1, 3]


def test_an_article_found_by_its_title_far_from_the_registry_page_is_flagged():
    # R42: the TOC says p. 15, which the file (pp. 1–6) does not hold; the title is printed on p. 4
    texts = [f"{n}\nTexti." for n in range(1, 7)]
    body = [FP.Line("Meginmál greinarinnar heldur áfram hér með nægum texta svo að síðan hafi meginmál.", 10.0,
                    200 + 14 * k) for k in range(20)]
    pages = [FP.Page(body, 600.0) for _ in texts]
    pages[3] = FP.Page([FP.Line("ÖNNUR GREININ", 16.0, 100)] + body, 600.0)
    first = ArticleEntry(title="Fyrsta greinin", page_start=1, pdf_pages=[0, 2], provenance={"title": "toc", "pages": "toc"})
    second = ArticleEntry(title="Önnur greinin", page_start=15, provenance={"title": "toc", "pages": "toc"})
    P.relocate_by_title([first, second], [None, None], build_page_map(texts, "default"), texts, pages.__getitem__, set())
    assert (second.page_start, second.pdf_pages) == (4, [3, 5])
    assert _fravik(second) == ["FRÁVIK: efnisyfirlitið segir bls. 15; greinin fannst eftir titli sínum á bls. 4"]


def test_a_folders_common_file_name_offset_is_one_deviation_and_only_other_offsets_are_flagged_per_file():
    # Stjórnmál og stjórnsýsla 10. árg.: every file name is two pages ahead of the print; one file's end is off by six
    run = A._Run()
    named = [A._Named(ArticleEntry(title=f"G{k}"), (a, b), (a - 2, b - 2), f"skráarnafn segir bls. {a}–{b}, …")
             for k, (a, b) in enumerate([(31, 50), (51, 76), (77, 98)])]
    named.append(A._Named(ArticleEntry(title="G3"), (613, 642), (611, 636), "skráarnafn segir bls. 613–642, …"))
    A._flag_file_names(named, run, files=5)
    assert run.review and len(run.notes) == 1 and run.notes[0].startswith(FRAVIK)
    assert "2 síðum hærra en prentað blaðsíðutal í 4 skrám af 5" in run.notes[0]
    run1 = A._Run()
    A._flag_file_names([A._Named(ArticleEntry(title=f"H{k}"), (a + 1, a + 9), (a, a + 8), "…") for k, a in
                        enumerate((10, 30))], run1, files=2)
    assert "er 1 síðu hærra en prentað" in run1.notes[0]                      # singular
    assert [bool(_fravik(n.entry)) for n in named] == [False, False, False, True]
    assert [n.entry.analysis_notes for n in named[:3]] == [[n.note] for n in named[:3]]      # notes, not FRÁVIK
    assert named[3].entry.analysis_notes == [FRAVIK + named[3].note]
    # no common offset: a single file whose start is off is flagged on its own
    run = A._Run()
    one = [A._Named(ArticleEntry(title="x"), (5, 32), (6, 31), "skráarnafn segir bls. 5–32, PDF-skjalið bls. 6–31")]
    A._flag_file_names(one, run, files=9)
    assert not run.notes and _fravik(one[0].entry) == ["FRÁVIK: skráarnafn segir bls. 5–32, PDF-skjalið bls. 6–31"]


def test_a_signature_that_names_someone_other_than_the_registry_is_flagged():
    # Úlfljótur 1957 idx 14: the sheet names Jóhann J. Ragnarsson; "Frá aðalfundi" is signed "Gaukur Jörundsson"
    a = ArticleEntry(title="Frá aðalfundi", authors=["Jóhann J. Ragnarsson"], provenance={"title": "xlsx", "authors": "xlsx"})
    read_printed_page(a, FP.EMPTY, FP.EMPTY, sign=lambda x: ["Gaukur Jörundsson"])
    assert a.authors == ["Jóhann J. Ragnarsson"]
    assert _fravik(a) == ["FRÁVIK: greinin er undirrituð (Gaukur Jörundsson) en höfundar skrárinnar eru aðrir "
                          "(Jóhann J. Ragnarsson); skránni haldið"]
    a = ArticleEntry(title="Hugleiðingar", authors=["Benedikt Sigurjónsson"], provenance={"title": "toc"})
    read_printed_page(a, FP.EMPTY, FP.EMPTY, sign=lambda x: ["Ben. Sigurjónsson"])      # a given name cut short
    assert _fravik(a) == []


@pytest.mark.parametrize("registry, printed, conflict", [
    (["Katrín Helga Hallgrímsdóttir"], ["Ágúst Þór Árnason"], True),
    (["Sigríður Rut Júlíusdóttir"], ["Alexander Hafþórsson", "Sigríður Rut Júlíusdóttir"], False),   # a co-author
    (["Arnþór Gunnarsson"], ["Arnþór Gunnarsson Sagnfræðingur"], False),                              # a word more
    (["Rachael Lorna Johnstone"], ["I Introduction"], False),                                         # an outline line
    (["Gunnar Hrafn Jónsson"], ["Gunnar Jónsson"], False),
])
def test_conflict(registry, printed, conflict):
    assert P._conflict(registry, printed) is conflict


def test_a_page_heading_that_is_another_title_is_flagged_but_an_obituarys_first_name_is_not():
    body = [FP.Line("Meginmál greinarinnar heldur áfram hér með nægum texta svo að síðan hafi meginmál.", 10.0,
                    200 + 14 * k) for k in range(20)]
    other = FP.Page([FP.Line("Allt önnur grein um skaðabætur", 24.0, 100)] + body, 600.0)
    a = ArticleEntry(title="Réttarstaða sakaðra manna", provenance={"title": "toc"})
    read_printed_page(a, other, other)
    assert _fravik(a) == ["FRÁVIK: fyrirsögnin á fyrstu síðu („Allt önnur grein um skaðabætur“) er annar titill en "
                          "skrárinnar („Réttarstaða sakaðra manna“); titli skrárinnar haldið"]
    obit = FP.Page([FP.Line("BIRGIR ÁSGEIRSSON", 24.0, 100)] + body, 600.0)
    a = ArticleEntry(title="Birgir Ásgeirsson — Páll Pálmason", provenance={"title": "toc"})
    read_printed_page(a, obit, obit)
    assert _fravik(a) == []


def test_end_article_flags_an_uncertain_end_and_names_only_the_registrys_own_end():
    a = ArticleEntry(title="x", pdf_pages=[10, 20], page_start=11, page_end=21)
    P.end_article(a, PK.End(14, uncertain=True, kept=(13, 14)), lambda i: i + 1)
    assert (a.pdf_pages, a.page_end) == ([10, 14], 15)
    assert _fravik(a) == ["FRÁVIK: lok greinar óviss: á eftir texta hennar kemur efni á bls. 14–15 sem er hvorki "
                          "framhald hennar né upphaf annars efnis (mynd, auglýsing eða texti án textalags?); það er "
                          "talið með greininni"]
    assert not any("skráin segir" in n for n in a.analysis_notes)          # 21 was only the provisional range's end
    b = ArticleEntry(title="y", pdf_pages=[10, 20], page_start=11, page_end=21)
    P.end_article(b, PK.End(14), lambda i: i + 1, registry_end=16)
    assert "skráin segir að greinin endi á bls. 16, prentuð lokasíða er bls. 15" in b.analysis_notes and not _fravik(b)


def test_page_numbers_read_on_the_page_and_agreed_by_the_articles_own_pages():
    # Lögrétta 2005: an unusable map; page 1 prints no number, its article's pages 0, 2 and 3 print 23, 25 and 26;
    # page 4 starts another article (numbered 3): its number never comes from the article before
    texts = ["23\nTexti.", "Texti án blaðsíðutals.", "25\nTexti.", "26\nTexti.", "Texti án blaðsíðutals.", "4\nTexti.",
             "5\nTexti.", "6\nTexti."]
    pm = build_page_map(texts, "logretta")
    assert pm.offset is None                                          # two blocks that cannot follow each other
    n = P.page_numbers(pm, texts, PATTERNS["logretta"])
    assert (n.at(0), n.at(1), n.at(1, 0, 3)) == (23, None, 24)        # its own pages agree on 24
    assert (n.at(4), n.at(4, 4, 7)) == (None, 3)                      # the next article's pages, not the one before
    assert (n.evidence(1), n.evidence(2)) == (None, 25)


# --- Task 22d, ruling R43: a contents-page number that cannot be read is a note, not a deviation -------------------

def _logfraedingur_2008(heading_of_fiskveidi_at: int = 8) -> bytes:
    """Lögfræðingur 2. árg. 2008 (1. tbl.): the contents page as the text layer gives it (the first seven of its eight
    entries), the page numbers of three of them garbled by OCR ("err", "ð", "Ð("); pdf page i is printed page i. The
    heading of the second unreadable entry is printed on `heading_of_fiskveidi_at` (8, as in the issue)."""
    toc = ["Efnisyfirlit", "Hilmar Vilberg Gylfason:", "Frá ritstjóra .............0.0eeeeeeeeessnnneneess", "err",
           "Ð", "Sindri Kristjánsson:", "Ávarp formanns Þemis ...............0eeeneeeeeneneneeenenenereenenenrrrnn", "6",
           "Aðalheiður Ámundadóttir:", "Um íslenska fiskveiðistjórnunarkerfið", "og skyldu íslenska ríkisins til að virða",
           "álit Mannréttindanefndar Sameinuðu þjóðanna ...............000eeeeeeeee.", "ð",
           "Birkir Már Magnússon:", "Skýrslutaka hjá lögreglu .....................0..0000000", "000 eeen", "23",
           "Hreiðar Eiríksson:", "Helgar tilgangurinn meðalið?", "Um lögmæti valdbeitingar við sýnatöku vegna",
           "rannsóknar á öÖlvunarakstri ....c...eeeeeeeeesssarrsssrrrrssssrrsssrr", "40",
           "Höskuldur Þór Þórhallsson hdl.,:", "Hugtakið „kristið siðgæði“", "og Dómur Mannréttindadómstóls Evrópu frá 29. júní 2007,",
           "Folgeró o.fl. gegn Noregi ...........0000000000000ee", "eee eeen", "Ð(",
           "Pétur Dam Leifsson:", "Öryggisráð SÞ frá sjónarhóli þjóðaréttar eee.", "65"]
    starts = {5: ("Hilmar Vilberg Gylfason", ["FRÁ RITSTJÓRA"]),
              6: ("Sindri Kristjánsson", ["ÁVARP FORMANNS ÞEMIS"]),
              heading_of_fiskveidi_at: ("Aðalheiður Ámundadóttir", ["Um íslenska fiskveiðistjórnunarkerfið",
                                                                    "og skyldu íslenska ríkisins til að virða",
                                                                    "álit Mannréttindanefndar Sameinuðu þjóðanna"]),
              23: ("Birkir Már Magnússon", ["SKÝRSLUTAKA HJÁ LÖGREGLU"]),
              40: ("Hreiðar Eiríksson", ["Helgar tilgangurinn meðalið?", "Um lögmæti valdbeitingar við sýnatöku vegna",
                                          "rannsóknar á ölvunarakstri"]),
              57: ("Höskuldur Þór Þórhallsson", ["Hugtakið „kristið siðgæði“", "og Dómur Mannréttindadómstóls Evrópu frá",
                                                  "29. júní 2007, Folgerø o.fl. gegn Noregi"]),
              65: ("Pétur Dam Leifsson", ["Öryggisráð SÞ frá sjónarhóli þjóðaréttar"])}
    pages = [[(24, 200, "Lögfræðingur")], [(14, 200, "Lögfræðingur 1. tölublað - 2. árgangur - 2008")],
             [(10, 100, "Lögfræðingur er tímarit laganema við Háskólann á Akureyri"), (10, 580, "2 Lögfræðingur")],
             [(10, 60 + 14 * k, ln) for k, ln in enumerate(toc)] + [(10, 580, "Lögfræðingur 3")], [(10, 580, "4 Lögfræðingur")]]
    for n in range(5, 74):
        rows = [(10, 580, f"{n} Lögfræðingur" if n % 2 == 0 else f"Lögfræðingur {n}")]
        if n in starts:
            author, heading = starts[n]
            rows += [(12, 90, author)] + [(18, 120 + 24 * k, h) for k, h in enumerate(heading)]
            rows += _body(120 + 24 * len(heading) + 20, 20)
        else:
            rows += _body(60, 35)
        pages.append(rows)
    return _typeset(pages)


async def _analyse_logfraedingur_2008(**kw):
    m = _manifest("logfraedingur", year=2008, volume=2, issue=None, file="x/Lögfræðingur 2 arg 2008.pdf")
    return await analyze_issue(m, get_config("logfraedingur"), _deps(_logfraedingur_2008(**kw)))


async def test_a_contents_number_ocr_garbled_is_a_note_when_the_title_is_found_where_the_contents_order_puts_it():
    # Lögfræðingur 2008 idx 0, 2, 5 (8 such notes in 2008, 2011 and 2012): the contents page lists the entry, its
    # number is "err" / "ð" / "Ð("; the title is printed on p. 5 / 8 / 57, between its neighbours' pages
    m = await _analyse_logfraedingur_2008()
    by = {a.title.split()[0]: a for a in m.articles}
    assert [a.page_start for a in m.articles] == [5, 6, 8, 23, 40, 57, 65]
    for first_word, page in (("Frá", 5), ("Um", 8), ("Hugtakið", 57)):
        a = by[first_word]
        assert a.page_start == page and a.provenance["pages"] == "page", first_word
        assert not _fravik(a), (first_word, a.analysis_notes)
        assert f"blaðsíðutal greinarinnar í efnisyfirlitinu var ólæsilegt; greinin fannst eftir titli sínum á bls. {page}" \
            in a.analysis_notes
    assert not any(_fravik(a) for a in m.articles)


async def test_an_unreadable_contents_number_stays_a_deviation_when_the_found_page_breaks_the_contents_order():
    # the same issue with the second unreadable entry's heading printed on p. 30, after the entry listed below it
    # (Birkir Már Magnússon, p. 23): the contents order is broken, so the user is asked
    m = await _analyse_logfraedingur_2008(heading_of_fiskveidi_at=30)
    a = next(x for x in m.articles if x.title.startswith("Um íslenska fiskveiðistjórnunarkerfið"))
    assert a.page_start == 30
    assert _fravik(a) == ["FRÁVIK: skráin gaf enga upphafsblaðsíðu; greinin fannst eftir titli sínum á bls. 30"]
    assert m.status == "review"


def _title_search_case(second: ArticleEntry, title_page: int) -> ArticleEntry:
    """Three articles in contents order, pages 1, (none) and 5 of a file of 8 pages; the second's heading on pdf page
    `title_page`."""
    texts = [f"{n}\nTexti." for n in range(1, 9)]
    body = [FP.Line("Meginmál greinarinnar heldur áfram hér með nægum texta svo að síðan hafi meginmál.", 10.0,
                    200 + 14 * k) for k in range(20)]
    pages = [FP.Page(body, 600.0) for _ in texts]
    pages[0] = FP.Page([FP.Line("FYRSTA GREININ", 16.0, 100)] + body, 600.0)
    pages[title_page] = FP.Page([FP.Line("ÖNNUR GREININ", 16.0, 100)] + body, 600.0)
    pages[4] = FP.Page([FP.Line("ÞRIÐJA GREININ", 16.0, 100)] + body, 600.0)
    first = ArticleEntry(title="Fyrsta greinin", page_start=1, pdf_pages=[0, 2], provenance={"title": "toc", "pages": "toc"})
    third = ArticleEntry(title="Þriðja greinin", page_start=5, pdf_pages=[4, 7], provenance={"title": "toc", "pages": "toc"})
    P.relocate_by_title([first, second, third], [None, None, None], build_page_map(texts, "default"), texts,
                        pages.__getitem__, set())
    return second


def test_an_unnumbered_contents_entry_found_between_its_neighbours_is_a_note():
    second = _title_search_case(ArticleEntry(title="Önnur greinin", provenance={"title": "toc"}), 2)
    assert second.page_start == 3 and not _fravik(second)
    assert second.analysis_notes == [
        "blaðsíðutal greinarinnar í efnisyfirlitinu var ólæsilegt; greinin fannst eftir titli sínum á bls. 3"]


def test_an_unnumbered_contents_entry_found_outside_its_neighbours_or_with_no_contents_line_is_flagged():
    wrong_order = _title_search_case(ArticleEntry(title="Önnur greinin", provenance={"title": "toc"}), 6)     # after the third
    assert wrong_order.page_start == 7 and _fravik(wrong_order) == [
        "FRÁVIK: skráin gaf enga upphafsblaðsíðu; greinin fannst eftir titli sínum á bls. 7"]
    no_line = _title_search_case(ArticleEntry(title="Önnur greinin", provenance={"title": "leitir"}), 2)   # not from a contents page
    assert no_line.page_start == 3 and _fravik(no_line) == [
        "FRÁVIK: skráin gaf enga upphafsblaðsíðu; greinin fannst eftir titli sínum á bls. 3"]
    claude = _title_search_case(ArticleEntry(title="Önnur greinin", provenance={"title": "llm"}), 2)         # Claude's list: a note
    assert claude.page_start == 3 and not _fravik(claude)
