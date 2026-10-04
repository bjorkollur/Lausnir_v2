# tests/test_timarit_analyze_printed.py
"""Task 22b: analyze takes the article's metadata from its printed pages (R40). The PDFs are typeset with font sizes
so that the page reader sees headings the way it does in the archive; each test names the audited case it mirrors."""
import fitz

from engine.config.sources import get_config
from engine.timarit.analyze import AnalyzeDeps, analyze_issue
from engine.timarit.manifest import IssueManifest
from engine.timarit.toc import TocEntry
from engine.timarit.ulfljotur_xlsx import XlsxArticle

_BODY = "Meginmál greinarinnar heldur áfram hér með nægum texta svo að síðan hafi textalag og meginmál."


def _typeset(pages: list[list[tuple]]) -> bytes:
    """Pages of (font size, baseline y, text[, left edge x]) lines on an A5 page (x 40 unless given)."""
    d = fitz.open()
    for rows in pages:
        page = d.new_page(width=420, height=600)
        for size, y, text, *x in rows:
            page.insert_text((x[0] if x else 40, y), text, fontsize=size)
    return d.tobytes()


def _body(top: float = 300, n: int = 6) -> list[tuple[float, float, str]]:
    return [(10, top + 14 * k, _BODY) for k in range(n)]


def _deps(pdf, llm=None, xlsx=(), calls=None):
    async def _llm(text):
        if calls is not None:
            calls.append(text)
        return llm or []
    return AnalyzeDeps(read_bytes=pdf if callable(pdf) else (lambda rel: pdf), leitir=None,
                       llm_toc=_llm if llm is not None else None, xlsx_rows=list(xlsx))


def _manifest(source, **over):
    base = dict(source=source, year=2007, volume=8, issue=None, label="x", file="x/issue.pdf", files=["x/issue.pdf"])
    base.update(over)
    return IssueManifest(**base)


def _rannsoknir_pdf() -> bytes:
    """Rannsóknir í félagsvísindum VIII (2007): the TOC lists "Höfundalisti" and the second article one page late
    (9 for its unnumbered title page 8); its numbered pages carry the author or the title as running head."""
    toc = [(14, 60, "Efnisyfirlit"), (10, 90, "Höfundalisti ........................................... 3"),
           (10, 110, "Jón Jónsson: Fyrsta greinin um eignarrétt ....................... 5"),
           (10, 130, "Auður Björg Jónsdóttir: Ákvörðun miskabóta í ærumeiðingamálum ........ 9"),
           (10, 150, "Páll Pálsson: Þriðja greinin um refsirétt ....................... 12")]

    def numbered(n, head):
        return [(10, 40, str(n)), (10, 60, head)] + _body(100, 20)
    pages = [[(20, 100, "RANNSÓKNIR Í FÉLAGSVÍSINDUM VIII")], toc,
             [(10, 40, "3"), (14, 70, "Höfundalisti"), (10, 100, "Jón Jónsson, lektor við lagadeild HÍ")],
             numbered(4, "Formáli"),
             [(10, 40, "5"), (18, 80, "Fyrsta greinin um eignarrétt"), (15, 110, "Jón Jónsson")] + _body(),
             numbered(6, "Jón Jónsson"), numbered(7, "Fyrsta greinin um eignarrétt"),
             [(18, 80, "Ákvörðun miskabóta í"), (18, 100, "ærumeiðingamálum"), (15, 140, "Auður Björg Jónsdóttir")]
             + _body(200, 10),
             numbered(9, "Auður Björg Jónsdóttir"), numbered(10, "Ákvörðun miskabóta í ærumeiðingamálum"),
             numbered(11, "Auður Björg Jónsdóttir"),
             [(10, 40, "12"), (18, 80, "Þriðja greinin um refsirétt"), (15, 110, "Páll Pálsson")] + _body(),
             numbered(13, "Páll Pálsson")]
    return _typeset(pages)


async def test_title_page_before_the_registry_page_and_front_matter():
    # fixes 4 and 7 (Rannsóknir 2007 idx 3 and idx 0): the article starts on its unnumbered title page, one before the
    # TOC's number; "Höfundalisti" is front matter, not an article
    m = await analyze_issue(_manifest("rannsoknir_lagadeild"), get_config("rannsoknir_lagadeild"),
                            _deps(_rannsoknir_pdf()))
    titles = [a.title for a in m.articles]
    assert "Höfundalisti" not in titles and len(titles) == 3
    a = next(x for x in m.articles if x.title == "Ákvörðun miskabóta í ærumeiðingamálum")
    assert a.pdf_pages == [7, 10] and a.page_start == 8 and a.page_end == 11 and a.provenance["pages"] == "page"
    assert "efnisyfirlitið segir bls. 9, prentað bls. 8" in a.analysis_notes    # a title page: a note, not FRÁVIK
    first = next(x for x in m.articles if x.title == "Fyrsta greinin um eignarrétt")
    assert first.pdf_pages == [4, 6] and first.page_start == 5 and first.page_end == 7
    assert first.provenance["pages"] == "toc" and first.analysis_notes == []


async def test_entries_without_a_page_are_found_by_their_printed_title_in_any_order():
    # fix 6 (TL 2007 idx 1 and 2, Lögfræðingur 2006): Claude read titles but no numbers from a jumbled cover, in an
    # order that is not the printed one
    pages = [[(14, 80, "EFNI"), (10, 100, "Leiðbeiningarskylda stjórnvalds"), (10, 120, "Samningalögin sjötug")],
             [(10, 580, "1"), (11.5, 100, "Páll Hreinsson:"), (11.5, 118, "LEIÐBEININGARSKYLDA STJÓRNVALDS")] + _body(160),
             [(10, 580, "2")] + _body(60, 20), [(10, 580, "3")] + _body(60, 20),
             [(10, 580, "4"), (11.5, 100, "Matthías G. Pálsson:"), (11.5, 118, "SAMNINGALÖGIN SJÖTUG")] + _body(160),
             [(10, 580, "5")] + _body(60, 20)]
    llm = [TocEntry("Samningalögin sjötug", ["Matthías G. Pálsson"], None, section="Fræðigrein"),
           TocEntry("Leiðbeiningarskylda stjórnvalds", ["Páll Hreinsson"], None, section="Fræðigrein")]
    m = await analyze_issue(_manifest("timarit_logfraedinga", issue="1", file="x/TL 2007_01.pdf"),
                            get_config("timarit_logfraedinga"), _deps(_typeset(pages), llm=llm))
    by = {a.title: a for a in m.articles}
    assert (by["Samningalögin sjötug"].page_start, by["Samningalögin sjötug"].pdf_pages) == (4, [4, 5])
    assert (by["Leiðbeiningarskylda stjórnvalds"].page_start, by["Leiðbeiningarskylda stjórnvalds"].pdf_pages) == (1, [1, 3])
    assert by["Samningalögin sjötug"].provenance["pages"] == "page"


async def test_an_editorial_takes_its_type_from_the_printed_label_and_its_author_from_the_signature():
    # TL 2010-1 idx 0: "RITSTJÓRNARGREIN: Ný löggjöf um skipun dómara", signed "Róbert R. Spanó" at the end
    toc = "Efnisyfirlit\n1\nNý löggjöf um skipun dómara\n3\nAnna Pálsdóttir:\nÖnnur greinin\n5\nPáll Pálsson:\nÞriðja greinin"
    pages = [[(10, 60 + 14 * k, ln) for k, ln in enumerate(toc.splitlines())],
             [(12.5, 580, "1"), (11, 160, "RITSTJÓRNARGREIN: Ný löggjöf um skipun dómara")] + _body(200),
             [(12.5, 580, "2")] + _body(60, 10) + [(10.5, 260, "Róbert R. Spanó", 226)],   # set right, as printed
             [(12.5, 580, "3"), (11, 160, "Anna Pálsdóttir:"), (11, 175, "ÖNNUR GREININ")] + _body(200),
             [(12.5, 580, "4")] + _body(60, 20),
             [(12.5, 580, "5"), (11, 160, "Páll Pálsson:"), (11, 175, "ÞRIÐJA GREININ")] + _body(200)]
    m = await analyze_issue(_manifest("timarit_logfraedinga", issue="1", file="x/TL 2010_01.pdf"),
                            get_config("timarit_logfraedinga"), _deps(_typeset(pages)))
    ed = m.articles[0]
    assert ed.title == "Ný löggjöf um skipun dómara" and ed.article_type == "Ritstjórnargrein"
    assert ed.authors == ["Róbert R. Spanó"] and ed.provenance["authors"] == "page"
    assert ed.provenance["article_type"] == "page"


async def test_the_printed_page_number_outranks_the_spreadsheet_for_an_article_file():
    # fix 3 (Úlfljótur 1957 idx 3): footers "ÚLFLJÓTUR - 15" … the sheet says Bls 25
    rows = [XlsxArticle("Tillögur þjóðréttarnefndar Sameinuðu þjóðanna um landhelgi ríkja", ["Jón Thors"], 11, "4", 25, 7)]
    pages = [[(30, 120, "Tillögur þjóðréttarnefndar Sameinuðu"), (30, 160, "þjóðanna um landhelgi ríkja"),
              (14, 200, "Jón Thors")] + _body(260) + [(10, 580, f"ÚLFLJÓTUR - {15}")]]
    pages += [_body(60, 20) + [(10, 580, f"ÚLFLJÓTUR - {n}")] for n in (16, 17, 18)]
    m = _manifest("ulfljotur", year=1958, volume=11, file=None, files=["Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/1043.pdf"])
    m = await analyze_issue(m, get_config("ulfljotur"), _deps(_typeset(pages), xlsx=rows))
    a = m.articles[0]
    assert (a.page_start, a.page_end, a.provenance["pages"]) == (15, 18, "page")
    assert "FRÁVIK: taflan segir bls. 25, prentað bls. 15" in a.analysis_notes        # Task 22c: a deviation
    assert a.xlsx_row == 7 and not a.checks["printed_page_match"]      # the sheet and the print disagree: review


async def test_a_title_the_spreadsheet_has_in_two_issues_is_still_the_articles_title():
    # Úlfljótur 1947 idx 0 and 1: "Á vettvangi lagadeildar" is in 3. and 4. tbl. of the sheet; the file says neither
    rows = [XlsxArticle("Á vettvangi lagadeildar", ["Ármann Snævarr"], 1, "4", 17, 1258),
            XlsxArticle("Á vettvangi lagadeildar", ["Ármann Snævarr"], 1, "3", 21, 1254)]
    pages = [[(29, 100, "ÁRMANN SNÆVARR,"), (24, 130, "CAND. JUR.:"), (34, 200, "A vettvangi lagadeildar.")]
             + _body(300) + [(10, 580, "11")]]
    m = _manifest("ulfljotur", year=1947, volume=1, file=None, files=["Úlfljótur/Tímarit/Ulfljotur_1947 01 arg/766.pdf"])
    m = await analyze_issue(m, get_config("ulfljotur"), _deps(_typeset(pages), xlsx=rows))
    a = m.articles[0]
    assert a.title == "Á vettvangi lagadeildar" and a.authors == ["Ármann Snævarr"] and a.page_start == 11
    assert a.provenance["title"] == "xlsx" and a.xlsx_row is None and a.issue is None
    assert any("sami titill í fleiri röðum" in n for n in a.analysis_notes)


async def test_no_toc_issue_sends_claude_the_page_heads_of_the_whole_issue():
    # fix 8 (Lögrétta 2014): the file starts with its first article on page 9; a body page full of footnote numbers
    # is no TOC; Claude gets one line per page with the printed number and the page's heading
    def art(first, title, author):
        return [[(10, 30, "a"), (10, 45, str(first)), (11, 80, author), (20, 120, title)] + _body(200)] + \
               [[(10, 30, "a"), (10, 45, str(first + k))] + [(9, 60 + 12 * j, f"Neðanmálsgrein {j} bls. {10 + j}")
                                                              for j in range(8)] for k in (1, 2, 3)]
    pages = art(9, "Nokkrir farvegir fyrir hafréttardeilur", "Bjarni Már Magnússon") + \
        art(13, "Forgangsáhrif réttilega innleiddra EES-reglna", "Margrét Einarsdóttir")
    sent: list[str] = []
    m = await analyze_issue(_manifest("logretta", year=2014, volume=11, issue="1", file="x/Lögrétta 2014 1.tbl.pdf"),
                            get_config("logretta"), _deps(_typeset(pages), llm=[], calls=sent))
    assert m.error and len(sent) == 1
    lines = sent[0].splitlines()
    assert len(lines) == len(pages)
    assert lines[0].startswith("[0] p.9: ") and "Nokkrir farvegir fyrir hafréttardeilur" in lines[0]
    assert lines[4].startswith("[4] p.13: ") and "Forgangsáhrif" in lines[4]


# --- review round 1: the checks compare the registry with the page (R41); a dropped entry forces review ---------------

async def test_checks_compare_the_registry_values_with_the_page():
    # title page before the TOC's number: the registry said 9, the article starts on printed 8: no page match; the
    # registry title is printed there
    m = await analyze_issue(_manifest("rannsoknir_lagadeild"), get_config("rannsoknir_lagadeild"),
                            _deps(_rannsoknir_pdf()))
    a = next(x for x in m.articles if x.title == "Ákvörðun miskabóta í ærumeiðingamálum")
    assert a.checks["title_on_page"] and not a.checks["printed_page_match"]
    first = next(x for x in m.articles if x.title == "Fyrsta greinin um eignarrétt")
    assert first.checks["printed_page_match"] and first.checks["title_on_page"]


async def test_a_title_or_start_page_found_on_the_page_does_not_confirm_itself():
    # Claude gave no numbers: entries located by their printed title have no registry page to match
    pages = [[(14, 80, "EFNI"), (10, 100, "Leiðbeiningarskylda stjórnvalds"), (10, 120, "Samningalögin sjötug")],
             [(10, 580, "1"), (11.5, 100, "Páll Hreinsson:"), (11.5, 118, "LEIÐBEININGARSKYLDA STJÓRNVALDS")] + _body(160),
             [(10, 580, "2")] + _body(60, 20), [(10, 580, "3")] + _body(60, 20)]
    llm = [TocEntry("Leiðbeiningarskylda stjórnvalds", ["Páll Hreinsson"], None, section="Fræðigrein")]
    m = await analyze_issue(_manifest("timarit_logfraedinga", issue="1", file="x/TL 2007_01.pdf"),
                            get_config("timarit_logfraedinga"), _deps(_typeset(pages), llm=llm))
    a = m.articles[0]
    assert a.page_start == 1 and not a.checks["printed_page_match"]
    assert a.checks["title_on_page"] and a.checks["author_on_page"]       # Claude's title and author are printed there


async def test_a_label_title_and_a_signature_do_not_confirm_themselves():
    # TL 2010 idx 0 (signature) and a TOC entry that is a section label: the page supplies title / author, the checks
    # do not count them
    toc = "Efnisyfirlit\n1\nLeiðari\n3\nAnna Pálsdóttir:\nÖnnur greinin\n5\nPáll Pálsson:\nÞriðja greinin"
    pages = [[(10, 60 + 14 * k, ln) for k, ln in enumerate(toc.splitlines())],
             [(12.5, 580, "1"), (24, 120, "Ný löggjöf um skipun dómara")] + _body(200),
             [(12.5, 580, "2")] + _body(60, 10) + [(10.5, 260, "Róbert R. Spanó", 226)],   # set right, as printed
             [(12.5, 580, "3"), (11, 160, "Anna Pálsdóttir:"), (11, 175, "ÖNNUR GREININ")] + _body(200),
             [(12.5, 580, "4")] + _body(60, 20),
             [(12.5, 580, "5"), (11, 160, "Páll Pálsson:"), (11, 175, "ÞRIÐJA GREININ")] + _body(200)]
    m = await analyze_issue(_manifest("timarit_logfraedinga", issue="1", file="x/TL 2010_01.pdf"),
                            get_config("timarit_logfraedinga"), _deps(_typeset(pages)))
    ed = m.articles[0]
    assert ed.title == "Ný löggjöf um skipun dómara" and ed.provenance["title"] == "page"
    assert ed.article_type == "Ritstjórnargrein" and ed.authors == ["Róbert R. Spanó"]
    assert not ed.checks["title_on_page"] and not ed.checks["author_on_page"] and ed.checks["printed_page_match"]


async def test_entries_not_found_in_the_file_send_the_issue_to_review():
    # an entry past the last page number read, its title nowhere: left out, and the issue is reviewed
    toc = "Efnisyfirlit\n1\nJón Jónsson:\nFyrsta greinin\n3\nAnna Pálsdóttir:\nÖnnur greinin\n90\nPáll Pálsson:\nTýnda greinin"
    pages = [[(10, 60 + 14 * k, ln) for k, ln in enumerate(toc.splitlines())],
             [(12.5, 580, "1"), (11, 160, "Jón Jónsson:"), (11, 175, "FYRSTA GREININ")] + _body(200),
             [(12.5, 580, "2")] + _body(60, 20),
             [(12.5, 580, "3"), (11, 160, "Anna Pálsdóttir:"), (11, 175, "ÖNNUR GREININ")] + _body(200),
             [(12.5, 580, "4")] + _body(60, 20)]
    m = await analyze_issue(_manifest("timarit_logfraedinga", issue="1", file="x/TL 2010_01.pdf"),
                            get_config("timarit_logfraedinga"), _deps(_typeset(pages)))
    from engine.timarit.analyze import FORCED_REVIEW_NOTES
    assert [a.title for a in m.articles] == ["Fyrsta greinin", "Önnur greinin"] and m.status == "review"
    note = next(n for n in m.articles[0].analysis_notes if n.startswith(FORCED_REVIEW_NOTES))
    assert note.startswith("greinar fundust ekki í skjalinu: 1 (bls. 90; síðasta lesna blaðsíðutal 4)")
