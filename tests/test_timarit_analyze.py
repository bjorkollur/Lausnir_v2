# tests/test_timarit_analyze.py
"""Task 17: one issue end to end. The brief's fixtures were rebuilt so they are consistent with the
real page-map, TOC and matcher algorithms (ruling R7); every change is listed in the task report."""
import datetime

import fitz
import pytest

from engine.config.sources import get_config
from engine.timarit.analyze import AnalyzeDeps, PdfMeta, analyze_issue, locate_articles, metadata_year, pdf_meta, verify_article
from engine.timarit.leitir_articles import LeitirArticle
from engine.timarit.manifest import ArticleEntry, IssueManifest, merge_preserving_user
from engine.timarit.pagemap import build_page_map
from engine.timarit.toc import TocEntry
from engine.timarit.ulfljotur_xlsx import XlsxArticle

J = "Tímarit og fræðigreinar/"
_PAD = "Meginmál greinarinnar heldur áfram hér með nægum texta til að síðan hafi textalag."


def _pdf(pages: list[str], meta: dict | None = None) -> bytes:
    d = fitz.open()
    if meta is not None:
        d.set_metadata(meta)
    for t in pages:
        page = d.new_page()
        if t:
            page.insert_text((72, 72), t, fontsize=11)
    return d.tobytes()


def _issue_pdf():
    """An 11-page TL-style issue: cover, TOC (page-first, one entry per page mark), three articles starting on
    printed pages 1, 5 and 9. Every article page prints its number on the first line: printed = pdf index - 1."""
    toc = ("Efnisyfirlit\n1\nJón Jónsson:\nFyrsta greinin\n5\nAnna Pálsdóttir:\nÖnnur greinin um dóma\n"
           "9\nPáll Pálsson:\nRitdómur: Bók um kröfurétt\n1. hefti 60. árgangur\nvor 2010")
    pages = ["Lögfræðingafélag Íslands\nTímarit lögfræðinga", toc,
             f"1\nJón Jónsson\nFyrsta greinin\nTexti fyrstu greinar.\n{_PAD}"]
    pages += [f"{p}\nFramhald fyrstu greinar.\n{_PAD}" for p in (2, 3, 4)]
    pages += [f"5\nAnna Pálsdóttir\nÖnnur greinin um dóma\nTexti.\n{_PAD}"]
    pages += [f"{p}\nFramhald annarrar greinar.\n{_PAD}" for p in (6, 7, 8)]
    pages += [f"9\nPáll Pálsson\nRitdómur: Bók um kröfurétt\nRitdómur.\n{_PAD}"]
    return _pdf(pages)


def _deps(pdf, leitir=None, llm=None, xlsx=(), calls=None):
    async def _leitir(q, journal):
        if calls is not None:
            calls.append(("leitir", q))
        return leitir or []

    async def _llm(text):
        if calls is not None:
            calls.append(("llm", text))
        return llm or []
    read = pdf if callable(pdf) else (lambda rel: pdf)
    return AnalyzeDeps(read_bytes=read, leitir=_leitir if leitir is not None else None,
                       llm_toc=_llm if llm is not None else None, xlsx_rows=list(xlsx))


def _notes(a: ArticleEntry) -> str:
    """The machine notes of an article (R33: analyze never writes the user's `note`)."""
    return " | ".join(a.analysis_notes)


def _manifest(**over):
    base = dict(source="timarit_logfraedinga", year=2010, volume=60, issue="1",
                label="Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", file="x/TL 2010_01.pdf", files=["x/TL 2010_01.pdf"])
    base.update(over)
    return IssueManifest(**base)


# --- the brief's tests (fixtures rebuilt per R7) ---------------------------------------------------

async def test_issue_is_split_into_three_located_articles():
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_issue_pdf()))
    assert m.pages == 11 and m.page_map["offset"] == -1
    # Task 22b (fix 2): "Ritdómur:" in front of a title is a section label, evidence for the type, not the title
    assert [a.title for a in m.articles] == ["Fyrsta greinin", "Önnur greinin um dóma", "Bók um kröfurétt"]
    assert [a.page_start for a in m.articles] == [1, 5, 9]
    assert [a.pdf_pages for a in m.articles] == [[2, 5], [6, 9], [10, 10]]
    assert [a.page_end for a in m.articles] == [4, 8, 9]
    assert m.articles[0].authors == ["Jón Jónsson"] and m.articles[2].article_type == "Ritdómur"
    assert all(a.checks["title_on_page"] and a.checks["printed_page_match"] for a in m.articles)
    assert m.articles[0].provenance["title"] == "toc" and m.status in ("approved", "review")
    assert m.sha256 and m.error is None and m.confidence == min(a.confidence for a in m.articles)


async def test_leitir_record_raises_confidence_and_fills_page_end():
    rec = LeitirArticle("Fyrsta greinin", ["Jón Jónsson"], "Tímarit lögfræðinga", 2010, 60, "1", 1, 4, "991")
    plain = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_issue_pdf()))
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_issue_pdf(), leitir=[rec]))
    a = m.articles[0]
    assert a.checks["external_record"] is True and a.leitir_record_id == "991" and a.page_end == 4
    assert a.provenance["pages"].startswith("leitir") and a.confidence > plain.articles[0].confidence
    assert m.articles[1].leitir_record_id is None        # the one record fits only the first article


async def test_llm_fallback_when_toc_unreadable_marks_llm_and_review():
    pdf = _pdf(["Kápa", "Efnisyfirlit sem les ekki ...",
                f"1\nJón Jónsson\nFyrsta greinin\nTexti.\n{_PAD}", f"2\n{_PAD}", f"3\n{_PAD}"])
    llm = [TocEntry("Fyrsta greinin", ["Jón Jónsson"], 1, section="Fræðigrein")]
    calls = []
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(pdf, llm=llm, calls=calls))
    a = m.articles[0]
    assert a.provenance["title"] == "llm" and a.confidence <= 0.70 and m.status == "review"
    assert a.pdf_pages == [2, 4] and a.checks["title_on_page"] and a.article_type == "Fræðigrein"
    assert [c[0] for c in calls] == ["llm"]


async def test_article_with_start_page_beyond_pdf_is_flagged_not_cut():
    entries = [ArticleEntry(title="A", page_start=1), ArticleEntry(title="B", page_start=400)]
    texts = ["1\nA", "2\nx", "3\ny"]
    locate_articles(entries, build_page_map(texts, "default"), texts)
    assert entries[0].pdf_pages == [0, 2] and entries[1].pdf_pages is None and "utan skjals" in _notes(entries[1])
    assert entries[1].note == ""


def test_last_article_of_a_hefti_ends_on_its_last_numbered_page_not_on_the_next_hefti_cover():
    # Task 17a (R32): a TL annual volume, two hefti; the second hefti's cover pages carry no number and fall between
    # the segments of the piecewise map. The first hefti's last article runs up to the next located article, but its
    # page_end is the last numbered page of that range (8), not the unnumbered cover (None).
    texts = [f"{n}\nTexti." if n else "Kápa 2. heftis" for n in [1, 2, 3, 4, 5, 6, 7, 8, None, None, 9, 10, 11, 12]]
    entries = [ArticleEntry(title="A", page_start=1), ArticleEntry(title="B", page_start=5),
               ArticleEntry(title="C", page_start=9)]
    pm = build_page_map(texts, "default")
    assert pm.printed[8:10] == [None, None]
    locate_articles(entries, pm, texts)
    assert [a.pdf_pages for a in entries] == [[0, 3], [4, 9], [10, 13]]
    assert [a.page_end for a in entries] == [4, 8, 12]


def test_a_start_page_that_falls_between_segments_is_noted_as_a_gap_not_as_outside_the_document():
    # printed 9 is on no page (the scan skips from 8 to 10 across the second hefti's cover): it is inside the
    # document's numbering, so "utan skjals" (beyond the document) would mislead the reviewer
    texts = [f"{n}\nTexti." if n else "Kápa 2. heftis" for n in [1, 2, 3, 4, 5, 6, 7, 8, None, None, 10, 11, 12, 13]]
    entries = [ArticleEntry(title="A", page_start=1), ArticleEntry(title="B", page_start=9),
               ArticleEntry(title="C", page_start=40)]
    locate_articles(entries, build_page_map(texts, "default"), texts)
    assert entries[1].pdf_pages is None
    assert entries[1].analysis_notes == ["upphafsbls. 9 er á engri síðu skjalsins (eyða í blaðsíðutali)"]
    assert entries[2].analysis_notes == ["upphafsbls. 40 er utan skjals"]


def test_a_start_page_printed_on_two_pages_is_not_located_and_noted_as_unclear():
    # Review IMPORTANT 1: numbered adverts end the first hefti (printed 9-11) and the second hefti starts again at 9.
    # The contested numbers resolve to no page; the article is noted, not placed on an advert page.
    texts = [f"{n}\nTexti." if n else "Kápa 2. heftis" for n in
             [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, None, None, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]]
    entries = [ArticleEntry(title="A", page_start=1), ArticleEntry(title="B", page_start=9),
               ArticleEntry(title="C", page_start=12)]
    pm = build_page_map(texts, "default")
    assert 9 in pm.contested and pm.pdf_index_for(9) is None
    locate_articles(entries, pm, texts)
    assert entries[1].pdf_pages is None
    assert entries[1].analysis_notes == ["upphafsbls. 9 fannst ekki: blaðsíðutalið óljóst (talan stendur á fleiri en einni síðu)"]
    assert entries[2].pdf_pages == [16, 26]


def test_verify_article_checks():
    a = ArticleEntry(title="Fyrsta greinin", authors=["Jón Jónsson"], page_start=1)
    c = verify_article(a, "1\nJón Jónsson\nFyrsta greinin\nTexti", 1)
    assert c == {"title_on_page": True, "printed_page_match": True, "author_on_page": True, "external_record": False}
    c2 = verify_article(a, "Alls annað efni", None)
    assert not any(c2.values())


async def test_ulfljotur_article_pdfs_are_matched_to_the_spreadsheet():
    rows = [XlsxArticle("Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni", ["Ísleifur Árnason"], 1, "4", 173, 2),
            XlsxArticle("Viðskiptabréfsreglur um hlutabréf", ["Ólafur Lárusson"], 1, "1", 211, 3)]
    pdf = _pdf(["ULFLJOTUR desember 1947, 4. tbl., I. árg.\nPRÓFESSOR ÍSLEIFUR ARNASON:\n"
                "Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni.\n173"])
    m = _manifest(source="ulfljotur", year=1947, volume=1, issue=None, label="Úlfljótur, 1. árg. (1947)", file=None,
                  files=["Úlfljótur/Tímarit/Ulfljotur_1947 01 arg/173.pdf"])
    m = await analyze_issue(m, get_config("ulfljotur"), _deps(pdf, xlsx=rows))
    a = m.articles[0]
    assert a.title == rows[0].title and a.xlsx_row == 2 and a.provenance["title"] == "xlsx"
    assert a.source_file.endswith("173.pdf") and a.checks["external_record"] and a.checks["printed_page_match"]
    assert m.issue is None and a.page_start == 173
    assert a.issue == "4" and a.provenance["issue"] == "xlsx"      # R31: the row's issue rides on the article
    assert a.checks["author_on_page"] and a.pdf_pages == [0, 0] and a.page_end == 173 and m.year == 1947 and a.analysis_notes == [] and a.note == ""


# --- toc style by container kind (R22) -------------------------------------------------------------

def _tl_volume_pdf():
    toc = ("Efnisyfirlit\n1. hefti\nFyrsta grein um samninga eftir Jón Jónsson ........ 1\n"
           "Önnur grein um eignarrétt eftir Önnu Pálsdóttur ........ 5\n2. hefti\n"
           "Þriðja grein um refsirétt eftir Pál Pálsson ........ 9")
    pages = ["TÍMARIT LÖGFRÆÐINGA\n35. ÁRGANGUR\n1985", toc,
             f"1\nFyrsta grein um samninga\nJón Jónsson\n{_PAD}"]
    pages += [f"{p}\n{_PAD}" for p in (2, 3, 4)]
    pages += [f"5\nÖnnur grein um eignarrétt\nAnna Pálsdóttir\n{_PAD}", f"6\n{_PAD}", f"7\n{_PAD}", f"8\n{_PAD}",
              f"9\nÞriðja grein um refsirétt\nPáll Pálsson\n{_PAD}", f"10\n{_PAD}"]
    return _pdf(pages)


async def test_tl_annual_volume_uses_the_page_last_layout_and_keeps_the_hefti_per_article():
    m = _manifest(year=1985, volume=None, issue=None, label="Tímarit lögfræðinga (1985)",
                  file=J + "Tímarit Lögfræðinga/TL 1985.pdf", files=[J + "Tímarit Lögfræðinga/TL 1985.pdf"])
    m = await analyze_issue(m, get_config("timarit_logfraedinga"), _deps(_tl_volume_pdf()))
    assert [(a.title, a.authors, a.page_start, a.issue) for a in m.articles] == [
        ("Fyrsta grein um samninga", ["Jón Jónsson"], 1, "1"),
        ("Önnur grein um eignarrétt", ["Anna Pálsdóttir"], 5, "1"),
        ("Þriðja grein um refsirétt", ["Páll Pálsson"], 9, "2")]
    assert [a.pdf_pages for a in m.articles] == [[2, 5], [6, 9], [10, 11]]
    assert m.volume == 35 and m.label == "Tímarit lögfræðinga, 35. árg. (1985)"   # printed on the cover
    assert m.issue is None and all(a.provenance["issue"] == "toc" for a in m.articles)


async def test_tl_issue_pdf_uses_the_page_first_layout_even_without_a_known_path():
    """The brief's manifests carry a file path naming no archive pattern: an issue (m.issue set) reads page-first."""
    m = await analyze_issue(_manifest(file="anything.pdf"), get_config("timarit_logfraedinga"), _deps(_issue_pdf()))
    assert [a.page_start for a in m.articles] == [1, 5, 9]


# --- leitir.is gates (R26) ---------------------------------------------------------------------------

async def test_leitir_record_of_another_year_is_ignored():
    rec = LeitirArticle("Fyrsta greinin", ["Jón Jónsson"], "Tímarit lögfræðinga", 2009, None, "1", 1, 4, "991")
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_issue_pdf(), leitir=[rec]))
    assert m.articles[0].leitir_record_id is None and not m.articles[0].checks["external_record"]


async def test_leitir_year_gate_is_off_for_a_multi_year_volume_and_fills_the_volume():
    rec = LeitirArticle("Fyrsta grein um samninga", ["Jón Jónsson"], "Tímarit lögfræðinga", 1972, 22, "1", 1, 4, "77")
    m = _manifest(year=1971, year_end=1972, volume=None, issue=None, label="x",
                  file=J + "Tímarit Lögfræðinga/TL 1971_1972.pdf", files=[J + "Tímarit Lögfræðinga/TL 1971_1972.pdf"])
    pdf = _pdf(["TÍMARIT LÖGFRÆÐINGA"] + [p for p in _tl_volume_pdf_pages()[1:]])
    m = await analyze_issue(m, get_config("timarit_logfraedinga"), _deps(pdf, leitir=[rec]))
    assert m.articles[0].leitir_record_id == "77" and m.volume == 22


def _tl_volume_pdf_pages():
    with fitz.open(stream=_tl_volume_pdf(), filetype="pdf") as d:
        return [p.get_text() for p in d]


async def test_leitir_page_conflict_keeps_the_toc_page_and_notes_it():
    rec = LeitirArticle("Fyrsta greinin", ["Jón Jónsson"], "Tímarit lögfræðinga", 2010, 60, "1", 3, 4, "991")
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_issue_pdf(), leitir=[rec]))
    a = m.articles[0]
    assert a.leitir_record_id == "991" and a.page_start == 1 and a.provenance["pages"] == "toc" and "leitir.is" in _notes(a)


# --- page map and text layer -----------------------------------------------------------------------

async def test_issue_with_ambiguous_page_map_goes_to_review():
    toc = "Efnisyfirlit\n1\nJón Jónsson:\nFyrsta greinin\n3\nAnna Pálsdóttir:\nÖnnur greinin um dóma\n7\nPáll Pálsson:\nÞriðja greinin"
    body = [f"{p}\n{_PAD}" for p in (1, 2, 3, 4, 1, 2, 3, 4)]          # numbering restarts: R16 ambiguous map
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_pdf(["Kápa", toc] + body)))
    assert m.page_map["offset"] is None and m.page_map["quality"] == 0.0
    assert m.status == "review" and all(a.pdf_pages is None for a in m.articles)


async def test_printed_page_match_needs_a_number_on_the_page_when_the_page_map_is_weak():
    """Location uses the page map, so its inferred number proves nothing unless the map is trustworthy
    (quality >= PAGE_MAP_MIN_QUALITY) or the located page prints the number itself."""
    toc = "Efnisyfirlit\n1\nJón Jónsson:\nFyrsta greinin\n5\nAnna Pálsdóttir:\nÖnnur greinin\n8\nPáll Pálsson:\nÞriðja greinin"
    body = [f"1\nFyrsta greinin\n{_PAD}", _PAD, f"3\n{_PAD}", _PAD, f"Önnur greinin\n{_PAD}", f"6\n{_PAD}", _PAD,
            f"8\nÞriðja greinin\n{_PAD}"]                    # numbers on 4 of 10 pages: fitted, quality 0.4
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_pdf(["Kápa", toc] + body)))
    assert m.page_map["offset"] == -1 and m.page_map["quality"] < 0.5 and m.status == "review"
    assert [a.pdf_pages[0] for a in m.articles] == [2, 6, 9]
    assert [a.checks["printed_page_match"] for a in m.articles] == [True, False, True]


async def test_scanned_issue_without_text_layer_has_no_articles_and_an_ocr_error():
    calls = []
    pdf = _pdf(["", "", ""])
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(pdf, llm=[], calls=calls))
    assert m.articles == [] and m.error == "ekkert textalag — þarf OCR" and m.status == "review" and m.pages == 3
    assert calls == []                                                 # nothing to send to the LLM


async def test_unreadable_toc_without_llm_leaves_the_issue_empty_in_review():
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"),
                            _deps(_pdf(["Kápa", f"Efnisyfirlit án blaðsíðutala\n{_PAD}", f"1\n{_PAD}"])))
    assert m.articles == [] and m.status == "review" and "efnisyfirlit" in m.error


async def test_a_user_note_survives_reanalysis_and_machine_notes_are_replaced():
    rel = _SOGS + "2011-238_Tourists.pdf"           # gets a machine note (name vs print)
    first = f"Ferðamenn og fjármál\nAnna Pálsdóttir, lektor\n1. tbl. 3. árg. 2007\n{_PAD}"
    cfg, deps = get_config("stjornmal_stjornsysla"), _deps(_sogs_pdf(first, (212, 213, 214)))
    old = await analyze_issue(_sogs_manifest([rel]), cfg, deps)
    assert old.articles[0].analysis_notes and old.articles[0].note == ""
    old.articles[0].note = "Athugað 3.10.: blaðsíðutal rétt í prentaða heftinu"
    again = await analyze_issue(old, cfg, deps)
    merged = merge_preserving_user(old, again)
    a = merged.articles[0]
    assert a.note == "Athugað 3.10.: blaðsíðutal rétt í prentaða heftinu"
    assert a.analysis_notes == again.articles[0].analysis_notes == old.articles[0].analysis_notes   # replaced, not doubled


async def test_one_unreadable_pdf_does_not_fail_the_article_folder():
    good = {_SOGS + "5-7_First.pdf": _sogs_pdf(f"5\nFyrri grein\nJón Jónsson, dósent\n1. tbl. 3. árg. 2007\n{_PAD}", (6, 7)),
            _SOGS + "9-11_Third.pdf": _sogs_pdf(f"9\nÞriðja grein\nAnna Pálsdóttir, lektor\n1. tbl. 3. árg. 2007\n{_PAD}", (10, 11))}
    for bad_bytes, kind in ((b"%PDF-1.4 garbage, not a pdf", "FileDataError"), (b"", "EmptyFileError")):
        files = dict(good) | {_SOGS + "8-8_Broken.pdf": bad_bytes}
        m = await analyze_issue(_sogs_manifest(sorted(files)), get_config("stjornmal_stjornsysla"), _deps(files.__getitem__))
        by_file = {a.source_file: a for a in m.articles}
        broken = by_file[_SOGS + "8-8_Broken.pdf"]
        assert len(m.articles) == 3 and m.status == "review" and m.error is None and m.year == 2007
        assert broken.pdf_pages is None and broken.confidence == 0.0 and not any(broken.checks.values())
        assert broken.analysis_notes == [f"skjal ólæsilegt: {kind}"] and broken.note == ""
        assert by_file[_SOGS + "5-7_First.pdf"].title == "Fyrri grein" and by_file[_SOGS + "9-11_Third.pdf"].pdf_pages == [0, 2]


async def test_analyze_does_not_change_the_callers_manifest_and_keeps_an_approved_status():
    original = _manifest(status="approved")
    m = await analyze_issue(original, get_config("timarit_logfraedinga"), _deps(_issue_pdf()))
    assert original.articles == [] and original.pages is None and m.status == "approved" and len(m.articles) == 3


# --- Stjórnmál og stjórnsýsla (R5, Task 7 note) ------------------------------------------------------

_SOGS = J + "Stjórnmál og Stjórnsýsla/Vol03/"


def _sogs_pdf(first_page: str, numbers: tuple[int, ...]) -> bytes:
    return _pdf([first_page] + [f"{p}\n{_PAD}" for p in numbers])


def _sogs_manifest(files):
    return _manifest(source="stjornmal_stjornsysla", year=None, volume=3, issue=None,
                     label="Stjórnmál og stjórnsýsla, 3. árg.", file=None, files=files)


async def test_sogs_year_and_issue_come_from_the_first_page():
    first = ("5\nRéttarvernd minnihluta á\nÍslandi\nJón Jónsson, prófessor við\nHáskóla Íslands\n"
             f"2. tbl. 3. árg. 2007\nFræðigreinar\n{_PAD}")
    rel = _SOGS + "5-7_Minority_protection_in_Iceland.pdf"
    m = await analyze_issue(_sogs_manifest([rel]), get_config("stjornmal_stjornsysla"), _deps(_sogs_pdf(first, (6, 7))))
    a = m.articles[0]
    assert m.year == 2007 and m.label == "Stjórnmál og stjórnsýsla, 3. árg. (2007)"
    assert a.title == "Réttarvernd minnihluta á Íslandi" and a.authors == ["Jón Jónsson"] and a.provenance["title"] == "page"
    assert (a.page_start, a.page_end, a.issue) == (5, 7, "2") and a.provenance["issue"] == "page"
    assert a.checks["printed_page_match"] and a.pdf_pages == [0, 2] and a.analysis_notes == []


async def test_sogs_heading_joins_hyphenated_title_lines_and_drops_footnote_marks():
    first = (f"STJÓRNMÁL\n&\nSTJÓRNSÝSLA\nn  Fræðigreinar\nSameining sveitarfélaga í 70 ár. Rök-\nsemdir og frumundir\xad\n"
             f"búningur lýðræðis1\nGrétar Þór Eyþórsson, prófessor við Háskólann á Akureyri\nÚtdráttur\n{_PAD}")
    m = await analyze_issue(_sogs_manifest([_SOGS + "143-145_Efficiency.pdf"]), get_config("stjornmal_stjornsysla"),
                            _deps(_sogs_pdf(first, (144, 145))))
    a = m.articles[0]
    assert a.title == "Sameining sveitarfélaga í 70 ár. Röksemdir og frumundirbúningur lýðræðis"
    assert a.authors == ["Grétar Þór Eyþórsson"]


def test_sogs_heading_joins_a_soft_hyphen_at_a_line_end():
    """The real text layer keeps U+00AD ('Frumundir\xad' / 'búningur'); a synthetic PDF turns it into '-'."""
    from engine.timarit.firstpage import sogs_heading as _first_page_heading
    title, authors = _first_page_heading("Stjórnmál eða stjórnsýsla? – Frumundir\xad\nbúningur\nJón Jónsson, lektor\nÚtdráttur")
    assert title == "Stjórnmál eða stjórnsýsla? – Frumundirbúningur" and authors == ["Jón Jónsson"]


async def test_sogs_without_a_printed_year_falls_back_to_the_base_year_with_a_note_and_review():
    first = f"5\nRéttarvernd minnihluta\nJón Jónsson, prófessor\nÚtdráttur\n{_PAD}"
    rel = _SOGS + "5-7_Minority_protection.pdf"
    m = await analyze_issue(_sogs_manifest([rel]), get_config("stjornmal_stjornsysla"), _deps(_sogs_pdf(first, (6, 7))))
    assert m.year == 2007 and m.status == "review" and m.error is None
    assert "2007" in _notes(m.articles[0]) and "árg" in _notes(m.articles[0]) and m.articles[0].note == ""


async def test_sogs_printed_page_outranks_a_mistyped_filename_range():
    first = f"STJÓRNMÁL\n&\nSTJÓRNSÝSLA\nn  Fræðigreinar\nFerðamenn og fjármál sveitarfélaga\nAnna Pálsdóttir, lektor\n{_PAD}"
    rel = _SOGS + "2011-238_Tourists_and_municipal_finance.pdf"          # the real typo: 211-238
    m = await analyze_issue(_sogs_manifest([rel]), get_config("stjornmal_stjornsysla"),
                            _deps(_sogs_pdf(first, (212, 213, 214))))
    a = m.articles[0]
    assert (a.page_start, a.page_end) == (211, 214) and a.provenance["pages"] == "page" and "2011" in _notes(a)
    assert not a.checks["printed_page_match"]          # R34: the name says 2011, the page 211: not confirmed
    assert a.title == "Ferðamenn og fjármál sveitarfélaga" and a.authors == ["Anna Pálsdóttir"]


async def test_sogs_printed_page_match_is_the_file_name_start_confirmed_by_the_page():
    """R34: the page's number becomes page_start when the name disagrees, so the check must compare the name."""
    agree = f"17\nGrein\nJón Jónsson, dósent\n1. tbl. 3. árg. 2007\n{_PAD}"
    differ = f"19\nGrein\nJón Jónsson, dósent\n1. tbl. 3. árg. 2007\n{_PAD}"
    ok = await analyze_issue(_sogs_manifest([_SOGS + "17-20_Article.pdf"]), get_config("stjornmal_stjornsysla"),
                             _deps(_sogs_pdf(agree, (18, 19, 20))))
    off = await analyze_issue(_sogs_manifest([_SOGS + "17-20_Article.pdf"]), get_config("stjornmal_stjornsysla"),
                              _deps(_sogs_pdf(differ, (20, 21, 22))))
    assert ok.articles[0].page_start == 17 and ok.articles[0].checks["printed_page_match"]
    assert off.articles[0].page_start == 19 and not off.articles[0].checks["printed_page_match"]
    assert off.articles[0].confidence < ok.articles[0].confidence


async def test_tl_article_file_page_is_confirmed_only_when_the_page_prints_the_file_name_start():
    rel = J + "Tímarit Lögfræðinga/TL 1975_03/1975_98.pdf"
    m = _manifest(year=1975, volume=None, issue="3", label="x", file=None, files=[rel])
    ok = await analyze_issue(m, get_config("timarit_logfraedinga"), _deps(_pdf([f"{p}\n{_PAD}" for p in (98, 99, 100)])))
    off = await analyze_issue(m, get_config("timarit_logfraedinga"), _deps(_pdf([f"{p}\n{_PAD}" for p in (100, 101, 102)])))
    assert (ok.articles[0].page_start, ok.articles[0].page_end, ok.articles[0].checks["printed_page_match"]) == (98, 100, True)
    assert (off.articles[0].page_start, off.articles[0].checks["printed_page_match"]) == (100, False)
    assert "98" in _notes(off.articles[0])


async def test_sogs_filename_end_page_typo_is_replaced_by_the_page_count():
    first = f"189\nOpnun verslunar\nJón Jónsson, dósent\n1. tbl. 3. árg. 2007\n{_PAD}"
    rel = _SOGS + "189-2010_The_opening.pdf"
    m = await analyze_issue(_sogs_manifest([rel]), get_config("stjornmal_stjornsysla"), _deps(_sogs_pdf(first, (190, 191))))
    a = m.articles[0]
    assert (a.page_start, a.page_end) == (189, 191) and "2010" in _notes(a)
    assert a.checks["printed_page_match"]              # the start pages agree; only the end was mistyped


async def test_sogs_number_read_on_the_first_page_outranks_the_map_and_the_map_gives_the_end():
    """Real Vol04 shape: the PDF lacks the blank page after the first, so the article's own map (fitted on the later
    pages) puts the first page one too high; the number printed on the page itself wins, the map gives the end."""
    first = f"181\nKjarasamningar og stéttarfélög\nJón Jónsson, dósent\n2. tbl. 3. árg. 2007\n{_PAD}"
    rel = _SOGS + "181-185_Act_on_Trade_Unions.pdf"
    m = await analyze_issue(_sogs_manifest([rel]), get_config("stjornmal_stjornsysla"),
                            _deps(_sogs_pdf(first, (183, 184, 185))))
    a = m.articles[0]
    assert (a.page_start, a.page_end) == (181, 185) and a.provenance["pages"] == "filename+page" and a.analysis_notes == []
    assert a.checks["printed_page_match"]


async def test_article_files_are_listed_by_issue_and_page_not_by_path():
    a = (_SOGS + "17-18_Second.pdf", f"17\nSeinni grein\nJón Jónsson, dósent\n1. tbl. 3. árg. 2007\n{_PAD}", (18,))
    b = (_SOGS + "5-6_First.pdf", f"5\nFyrri grein\nAnna Pálsdóttir, lektor\n1. tbl. 3. árg. 2007\n{_PAD}", (6,))
    c = (_SOGS + "1-2_Other_issue.pdf", f"1\nGrein í öðru hefti\nPáll Pálsson, prófessor\n2. tbl. 3. árg. 2007\n{_PAD}", (2,))
    pdfs = {rel: _sogs_pdf(first, nums) for rel, first, nums in (a, b, c)}
    m = await analyze_issue(_sogs_manifest(sorted(pdfs)), get_config("stjornmal_stjornsysla"), _deps(pdfs.__getitem__))
    assert [(x.issue, x.page_start, x.title) for x in m.articles] == [
        ("1", 5, "Fyrri grein"), ("1", 17, "Seinni grein"), ("2", 1, "Grein í öðru hefti")]


# --- Úlfljótur (R23–R25, R31) ---------------------------------------------------------------------------

async def test_ulfljotur_masthead_year_corrects_the_folder_year_with_a_note():
    rows = [XlsxArticle("Um veðrétt í skipum", ["Jón Jónsson"], 6, "4", 17, 9)]
    pdf = _pdf(["ÚLFLJÓTUR Reykjavík, desember 1953, 4. tbl., VI. árg.\nJón Jónsson:\nUm veðrétt í skipum\n17"])
    m = _manifest(source="ulfljotur", year=1952, volume=6, issue=None, label="Úlfljótur, 6. árg. (1952)", file=None,
                  files=["Úlfljótur/Tímarit/Ulfljotur_1952 06 arg/88.pdf"])
    m = await analyze_issue(m, get_config("ulfljotur"), _deps(pdf, xlsx=rows))
    assert m.year == 1953 and m.label == "Úlfljótur, 6. árg. (1953)"
    assert "möppuár 1952 ≠ prentað ár 1953" in _notes(m.articles[0]) and m.articles[0].page_start == 17


async def test_ulfljotur_same_title_twice_is_resolved_by_the_masthead_issue():
    rows = [XlsxArticle("Á vettvangi lagadeildar", ["Ritstjórn"], 1, "3", 21, 40),
            XlsxArticle("Á vettvangi lagadeildar", ["Ritstjórn"], 1, "4", 17, 41)]
    pdf = _pdf(["ULFLJOTUR Reykjavik, desember 1947, 4. tbl., I. árg.\nÁ vettvangi lagadeildar\nFréttir af deildinni."])
    m = _manifest(source="ulfljotur", year=1947, volume=1, issue=None, label="x", file=None,
                  files=["Úlfljótur/Tímarit/Ulfljotur_1947 01 arg/773.pdf"])
    m = await analyze_issue(m, get_config("ulfljotur"), _deps(pdf, xlsx=rows))
    a = m.articles[0]
    assert a.xlsx_row == 41 and a.issue == "4" and a.page_start == 17
    assert not a.checks["printed_page_match"]          # no number on the page: expected, lands in review


async def test_ulfljotur_unmatched_article_takes_the_printed_page_but_it_confirms_nothing():
    pdf = _pdf([f"ULFLJOTUR Reykjavik, desember 1947, 4. tbl., I. árg.\nEfni sem er ekki í töflunni\n{_PAD}\n45"])
    m = _manifest(source="ulfljotur", year=1947, volume=1, issue=None, label="x", file=None,
                  files=["Úlfljótur/Tímarit/Ulfljotur_1947 01 arg/766.pdf"])
    a = (await analyze_issue(m, get_config("ulfljotur"), _deps(pdf, xlsx=[XlsxArticle("Allt annað", ["X Y"], 1, "1", 3, 5)]))).articles[0]
    assert a.page_start == 45 and a.provenance["pages"] == "page" and not a.checks["printed_page_match"]


async def test_ulfljotur_unmatched_article_never_uses_the_file_stem_as_its_page():
    pdf = _pdf([f"ULFLJOTUR Reykjavik, desember 1947, 4. tbl., I. árg.\nEfni sem er ekki í töflunni\n{_PAD}"])
    m = _manifest(source="ulfljotur", year=1947, volume=1, issue=None, label="x", file=None,
                  files=["Úlfljótur/Tímarit/Ulfljotur_1947 01 arg/766.pdf"])
    m = await analyze_issue(m, get_config("ulfljotur"), _deps(pdf, xlsx=[XlsxArticle("Allt annað", ["X Y"], 1, "1", 3, 5)]))
    a = m.articles[0]
    assert a.page_start is None and a.xlsx_row is None and "engin röð" in _notes(a) and m.status == "review"


def _ulf_issue_pdf(cover: str) -> bytes:
    toc = "EFNISYFIRLIT\nFyrsta grein um rétt ........ 3\nÖnnur grein um lög ........ 7\nÞriðja grein um dóma ........ 11"
    pages = [cover, toc, f"3\nFyrsta grein um rétt\nJón Jónsson\n{_PAD}"]
    pages += [f"{p}\n{_PAD}" for p in (4, 5, 6)]
    pages += [f"7\nÖnnur grein um lög\nAnna Pálsdóttir\n{_PAD}"] + [f"{p}\n{_PAD}" for p in (8, 9, 10)]
    pages += [f"11\nÞriðja grein um dóma\nPáll Pálsson\n{_PAD}", f"12\n{_PAD}"]
    return _pdf(pages)


def _ulf_issue_manifest(year=2023, issue="3"):
    rel = J + f"Úlfljótur/Tímarit/Ulfljotur_{year}-{issue}tbl.pdf"
    return _manifest(source="ulfljotur", year=year, volume=None, issue=issue, label="x", file=rel, files=[rel])


async def test_ulfljotur_issue_pdf_takes_its_articles_from_the_sheet_when_it_has_rows():
    rows = [XlsxArticle("Fyrsta grein um rétt", ["Jón Jónsson"], 76, "3", 3, 7),
            XlsxArticle("Önnur grein um lög", ["Anna Pálsdóttir"], 76, "3", 7, 8),
            XlsxArticle("Þriðja grein um dóma", ["Páll Pálsson"], 76, "3-4", 11, 9),
            XlsxArticle("Grein úr öðru hefti", ["X Y"], 76, "2", 3, 6)]
    m = await analyze_issue(_ulf_issue_manifest(), get_config("ulfljotur"),
                            _deps(_ulf_issue_pdf("ÚLFLJÓTUR\n3. TBL. 76. ÁRG. 2023"), xlsx=rows))
    assert m.volume == 76 and m.label == "Úlfljótur, 76. árg. 3. tbl. (2023)"
    assert [(a.title, a.xlsx_row, a.pdf_pages, a.issue) for a in m.articles] == [
        ("Fyrsta grein um rétt", 7, [2, 5], None), ("Önnur grein um lög", 8, [6, 9], None),
        ("Þriðja grein um dóma", 9, [10, 11], "3-4")]       # a row whose issue differs from the file's keeps its own
    assert all(a.provenance["title"] == "xlsx" and a.checks["external_record"] for a in m.articles)


async def test_ulfljotur_issue_without_sheet_rows_falls_back_to_its_toc():
    rows = [XlsxArticle("Grein í 75. árgangi", ["X Y"], 75, "3", 3, 2)]          # the sheet lacks vol 76 issues 3-4
    m = await analyze_issue(_ulf_issue_manifest(), get_config("ulfljotur"),
                            _deps(_ulf_issue_pdf("ÚLFLJÓTUR\n3. TBL. 76. ÁRG. 2023"), xlsx=rows))
    assert m.volume == 76 and [a.title for a in m.articles] == ["Fyrsta grein um rétt", "Önnur grein um lög", "Þriðja grein um dóma"]
    assert all(a.provenance["title"] == "toc" and a.xlsx_row is None for a in m.articles)
    assert [a.pdf_pages for a in m.articles] == [[2, 5], [6, 9], [10, 11]]


async def test_ulfljotur_issue_without_a_printed_volume_uses_the_year_with_a_note_and_review():
    rows = [XlsxArticle("Fyrsta grein um rétt", ["Jón Jónsson"], 76, "3", 3, 7),
            XlsxArticle("Önnur grein um lög", ["Anna Pálsdóttir"], 76, "3", 7, 8),
            XlsxArticle("Þriðja grein um dóma", ["Páll Pálsson"], 76, "3", 11, 9)]
    m = await analyze_issue(_ulf_issue_manifest(), get_config("ulfljotur"), _deps(_ulf_issue_pdf("ÚLFLJÓTUR"), xlsx=rows))
    assert m.volume == 76 and m.status == "review" and all("76. árg." in _notes(a) for a in m.articles)


# --- vefrit, standalone works --------------------------------------------------------------------------

async def test_vefrit_article_takes_title_from_the_file_and_date_and_authors_from_the_page():
    rel = J + "Úlfljótur/Vefrit/Breytt landslag á greiðsluþjónustumarkaði.pdf"
    page = ("Breytt landslag á greiðsluþjónustumarkaði\nJANÚAR 29, 2018JANÚAR 7, 2022 / ULFLJOTUR\n"
            "Eftir Kolbrúnu Söru Másdóttur\nmeistaranema við lagadeild HÍ\nog Thelmu Christel Kristjánsdóttur\n"
            f"BA-nema við lagadeild HÍ\n{_PAD}\nDómur féll 15. mars 2019.")
    m = _manifest(source="ulfljotur_vefrit", year=None, volume=None, issue=None, label="Úlfljótur vefrit", file=None, files=[rel])
    m = await analyze_issue(m, get_config("ulfljotur_vefrit"), _deps(_pdf([page, _PAD])))
    a = m.articles[0]
    assert a.title == "Breytt landslag á greiðsluþjónustumarkaði" and a.date == "2018-01-29"
    assert a.authors == ["Kolbrún Sara Másdóttir", "Thelma Christel Kristjánsdóttir"]
    assert a.page_start is None and a.pdf_pages == [0, 1] and a.checks["title_on_page"] and a.checks["author_on_page"]
    assert m.year is None and a.provenance == {"title": "filename", "authors": "page", "date": "page"}


async def test_vefrit_birt_date_form():
    rel = J + "Úlfljótur/Vefrit/Loftslagsváin og dómstólar.pdf"
    page = f"VEFRIT\nLoftslagsváin og dómstólar\nBirt 21. janúar 2022\nEftir dr. Jón Jónsson, prófessor\n{_PAD}"
    m = _manifest(source="ulfljotur_vefrit", year=None, volume=None, issue=None, label="x", file=None, files=[rel])
    a = (await analyze_issue(m, get_config("ulfljotur_vefrit"), _deps(_pdf([page])))).articles[0]
    assert a.date == "2022-01-21" and a.authors == ["Jón Jónsson"]


# --- Task 22d: the year of an undated web article comes from the PDF's metadata --------------------------

THIS_YEAR = datetime.date.today().year


@pytest.mark.parametrize("meta, year", [
    ({"creationDate": "D:20201002123456+00'00'"}, 2020),
    ({"creationDate": "D:20201002", "modDate": "D:20230101"}, 2020),               # the creation date comes first
    ({"creationDate": "", "modDate": "D:20211231235959Z"}, 2021),                  # ... else the modification date
    ({"creationDate": "D:20201002123456", "modDate": "D:garbage"}, 2020),
    ({"creationDate": "20201002123456"}, 2020),                                    # no "D:" prefix
    ({"creationDate": "D:19900101"}, 1990), ({"creationDate": "D:19891231"}, None),
    ({"creationDate": f"D:{THIS_YEAR}0101"}, THIS_YEAR), ({"creationDate": f"D:{THIS_YEAR + 1}0101"}, None),
    ({"creationDate": "D:18991231", "modDate": "D:20150403"}, 2015),               # an implausible year is no date
    ({"creationDate": "D:19700101000000Z", "modDate": "D:19700101000000Z"}, None),
    ({"creationDate": "D:00000000"}, None), ({"creationDate": "garbage"}, None),
    ({"creationDate": "D:2020"}, 2020), ({"creationDate": "D:202"}, None),
    ({}, None), (None, None), ({"title": "x"}, None)])
def test_metadata_year(meta, year):
    assert metadata_year(meta) == year


_VEFRIT = J + "Úlfljótur/Vefrit/Loftslagsváin og dómstólar.pdf"


async def _vefrit(page: str, meta: dict | None, source: str = "ulfljotur_vefrit"):
    m = _manifest(source=source, year=None, volume=None, issue=None, label="x", file=None, files=[_VEFRIT])
    return (await analyze_issue(m, get_config(source), _deps(_pdf([page], meta)))).articles[0]


async def test_an_undated_vefrit_article_takes_the_year_from_the_pdf_metadata():
    a = await _vefrit(f"VEFRIT\nLoftslagsváin og dómstólar\nEftir dr. Jón Jónsson, prófessor\n{_PAD}",
                      {"creationDate": "D:20201002123456+00'00'", "modDate": "D:20220101000000Z"})
    assert a.date == "2020-01-01" and a.provenance["date"] == "pdf_metadata"
    assert "birtingardagur fannst ekki á fyrstu síðu; ár úr lýsigögnum PDF-skjalsins: 2020" in a.analysis_notes
    assert not any(n.startswith("FRÁVIK") for n in a.analysis_notes)          # the user chose this rule: a plain note


async def test_a_printed_vefrit_date_outranks_the_metadata():
    a = await _vefrit(f"VEFRIT\nLoftslagsváin og dómstólar\nBirt 21. janúar 2022\nEftir dr. Jón Jónsson\n{_PAD}",
                      {"creationDate": "D:20201002123456"})
    assert a.date == "2022-01-21" and a.provenance["date"] == "page"
    assert not any("birtingardagur" in n for n in a.analysis_notes)


@pytest.mark.parametrize("meta", [None, {}, {"creationDate": "D:19700101000000Z"}, {"creationDate": "D:18500101"}])
async def test_a_vefrit_article_without_usable_metadata_keeps_no_date_and_the_old_note(meta):
    a = await _vefrit(f"VEFRIT\nLoftslagsváin og dómstólar\nEftir dr. Jón Jónsson\n{_PAD}", meta)
    assert a.date is None and "date" not in a.provenance
    assert a.analysis_notes.count("birtingardagur fannst ekki á fyrstu síðu") == 1


async def test_only_web_articles_take_a_year_from_metadata():
    a = await _vefrit(f"Loftslagsváin og dómstólar\nEftir dr. Jón Jónsson\n{_PAD}", {"creationDate": "D:20201002"},
                      source="fraedigreinar_ymsar")
    assert a.date is None and not any("birtingardagur" in n for n in a.analysis_notes)


# --- Task 22d, R45/R45a: a metadata date that comes from a batch re-export or a browser says nothing -----------------

WORD = {"creator": "Microsoft® Word 2016", "producer": "Microsoft® Word 2016"}
SAFARI = {"creator": "Safari", "producer": "macOS Version 14.2.1 (Build 23C71) Quartz PDFContext"}
REEXPORT_NOTE = ("birtingardagur fannst ekki á fyrstu síðu; dagsetning lýsigagnanna er frá endurútflutningi eða "
                 "vafraprentun og var ekki notuð")
OLD_NOTE = "birtingardagur fannst ekki á fyrstu síðu"


def _stamp(hour: int, minute: int, second: int = 0, offset: str = "Z00'00'", day: str = "20240301") -> str:
    return f"D:{day}{hour:02d}{minute:02d}{second:02d}{offset}"


def test_pdf_meta_reads_the_year_the_utc_time_and_whether_a_browser_made_the_pdf():
    at = datetime.datetime
    assert pdf_meta({"creationDate": "D:20240301165216Z00'00'", **SAFARI}) == PdfMeta(2024, at(2024, 3, 1, 16, 52, 16), True)
    assert pdf_meta({"creationDate": "D:20181221121023+00'00'", **WORD}) == PdfMeta(2018, at(2018, 12, 21, 12, 10, 23), False)
    # the offset is normalised: +01'00' is an hour ahead of UTC, -05'00' five behind; "+0100" and "+01" read the same
    assert pdf_meta({"creationDate": "D:20240301165216+01'00'"}).when == at(2024, 3, 1, 15, 52, 16)
    assert pdf_meta({"creationDate": "D:20240301105500-05'00'"}).when == at(2024, 3, 1, 15, 55, 0)
    assert pdf_meta({"creationDate": "D:20240301165216+0100"}).when == at(2024, 3, 1, 15, 52, 16)
    assert pdf_meta({"creationDate": "D:20240301165216+01"}).when == at(2024, 3, 1, 15, 52, 16)
    assert pdf_meta({"creationDate": "D:20240301165216"}).when == at(2024, 3, 1, 16, 52, 16)       # no offset: as written
    # without an hour there is a year but no time: no batch key
    assert pdf_meta({"creationDate": "D:2020", **WORD}) == PdfMeta(2020, None, False)
    assert pdf_meta({"creationDate": "D:20201002"}) == PdfMeta(2020, None, False)
    assert pdf_meta({"creationDate": "D:19700101000000Z", **SAFARI}) == PdfMeta(None, None, True)
    assert pdf_meta(None) == PdfMeta(None, None, False)


@pytest.mark.parametrize("bad", ["D:20241340120000Z", "D:20240230120000Z", "D:20240301246000Z", "D:20240301126100Z",
                                 "D:20240301120060Z", "D:20240301120000+25'00'", "D:20240301120000+01'75'", "D:20240001120000Z",
                                 "D:00010101000000+01'00'", "D:99991231235959-05'00'"])
def test_a_date_out_of_range_is_unparseable_not_a_crash(bad):
    assert pdf_meta({"creationDate": bad}) == PdfMeta(None, None, False)
    # ... and the modification date is tried instead
    assert pdf_meta({"creationDate": bad, "modDate": "D:20230101120000Z"}) == PdfMeta(2023, datetime.datetime(2023, 1, 1, 12), False)


def test_metadata_year_is_the_year_of_the_date_pdf_meta_reads():
    assert metadata_year({"creationDate": "D:20201002123456+00'00'"}) == pdf_meta({"creationDate": "D:20201002123456+00'00'"}).year == 2020
    assert metadata_year({"creationDate": "D:20241340120000Z"}) is None


@pytest.mark.parametrize("creator, producer, browser", [
    ("Safari", "macOS Version 14.2.1 (Build 23C71) Quartz PDFContext", True),
    ("Chromium", "Skia/PDF m120", True), ("", "Skia/PDF m118", True), ("Google Chrome", "", True),
    ("Mozilla Firefox 115.0", "cairo 1.17.6", True), ("Microsoft Edge", "", True), ("EDGE", "", True),
    ("Microsoft® Word 2016", "Microsoft® Word 2016", False), ("Word", "macOS Version 12.4 Quartz PDFContext", False),
    ("Microsoft Word", "", False), ("", "", False), ("Knowledge Base Editor", "Acrobat Distiller", False)])
def test_a_web_browser_among_creator_and_producer(creator, producer, browser):
    assert pdf_meta({"creationDate": "D:20200101120000", "creator": creator, "producer": producer}).browser is browser


async def _vefrit_folder(metas: dict[str, dict | None]):
    """An analysed folder of vefrit articles, one PDF per entry (file name -> metadata), none printing a date."""
    rels = {f"{J}Úlfljótur/Vefrit/{name}.pdf": meta for name, meta in metas.items()}
    pdfs = {rel: _pdf([f"VEFRIT\n{rel.rsplit('/', 1)[1][:-4]}\nEftir dr. Jón Jónsson, prófessor\n{_PAD}"], meta)
            for rel, meta in rels.items()}
    m = _manifest(source="ulfljotur_vefrit", year=None, volume=None, issue=None, label="x", file=None, files=list(rels))
    m = await analyze_issue(m, get_config("ulfljotur_vefrit"), _deps(lambda rel: pdfs[rel]))
    return {a.source_file.rsplit("/", 1)[1][:-4]: a for a in m.articles}


def _word(stamp: str) -> dict:
    return {"creationDate": stamp, **WORD}


def _distrusted(art) -> bool:
    return art.date is None and "date" not in art.provenance and art.analysis_notes.count(REEXPORT_NOTE) == 1 \
        and not any(n.startswith("FRÁVIK") for n in art.analysis_notes)


def _trusted(art, year: int) -> bool:
    return art.date == f"{year}-01-01" and art.provenance["date"] == "pdf_metadata" and REEXPORT_NOTE not in art.analysis_notes


async def test_three_pdfs_created_within_an_hour_are_a_batch_whose_date_is_not_used():
    # three Word files re-exported together on 2024-03-01 (16:52:16, 16:52:50, 16:55:22); a lone original of 2019 beside them
    arts = await _vefrit_folder({"A": _word(_stamp(16, 52, 16)), "B": _word(_stamp(16, 52, 50)),
                                 "C": _word(_stamp(16, 55, 22)), "Lone": _word(_stamp(15, 37, 25, "+00'00'", "20190223"))})
    assert all(_distrusted(arts[n]) for n in "ABC")
    assert _trusted(arts["Lone"], 2019)
    assert "birtingardagur fannst ekki á fyrstu síðu; ár úr lýsigögnum PDF-skjalsins: 2019" in arts["Lone"].analysis_notes


async def test_three_pdfs_within_sixty_minutes_are_a_batch_across_a_clock_hour():
    # 18:09:18, 18:44:01 and 19:00:01 lie in two clock hours but within 51 minutes
    arts = await _vefrit_folder({"A": _word(_stamp(18, 9, 18, day="20200201")), "B": _word(_stamp(18, 44, 1, day="20200201")),
                                 "C": _word(_stamp(19, 0, 1, day="20200201"))})
    assert all(_distrusted(a) for a in arts.values())


async def test_two_files_on_each_side_of_an_hour_boundary_are_a_batch():
    # 18:30, 18:45 | 19:05, 19:20: no clock hour holds three, but all four lie within 50 minutes
    arts = await _vefrit_folder({"A": _word(_stamp(18, 30)), "B": _word(_stamp(18, 45)),
                                 "C": _word(_stamp(19, 5)), "D": _word(_stamp(19, 20))})
    assert all(_distrusted(a) for a in arts.values())


@pytest.mark.parametrize("third, batch", [((19, 0, 0), True),          # exactly 60 minutes after the first: inside the window
                                          ((19, 0, 1), False), ((19, 1, 0), False), ((19, 31, 0), False)])
async def test_the_window_is_sixty_minutes_and_two_files_do_not_make_a_batch(third, batch):
    # 18:00 and 18:30 are two files within an hour; a third counts only if some 60-minute window holds all three
    arts = await _vefrit_folder({"A": _word(_stamp(18, 0)), "B": _word(_stamp(18, 30)), "C": _word(_stamp(*third))})
    assert all(_distrusted(a) for a in arts.values()) if batch else all(_trusted(a, 2024) for a in arts.values())


async def test_two_pdfs_made_within_the_hour_are_no_batch():
    arts = await _vefrit_folder({"A": _word(_stamp(18, 9, 18, day="20200201")), "B": _word(_stamp(18, 44, 1, day="20200201"))})
    assert all(_trusted(a, 2020) for a in arts.values())


async def test_creation_times_are_compared_in_utc():
    # the local clocks span six hours (16:52+01:00, 10:55-05:00, 15:30Z) but all three are 15:30-15:55 UTC: a batch
    arts = await _vefrit_folder({"A": _word(_stamp(16, 52, 16, "+01'00'")), "B": _word(_stamp(10, 55, 0, "-05'00'")),
                                 "C": _word(_stamp(15, 30, 0, "Z00'00'"))})
    assert all(_distrusted(a) for a in arts.values())
    # the same local clock, offsets two hours apart (10:00Z, 12:00Z, 14:00Z): no batch
    arts = await _vefrit_folder({"A": _word(_stamp(12, 0, 0, "+02'00'")), "B": _word(_stamp(12, 0, 0, "+00'00'")),
                                 "C": _word(_stamp(12, 0, 0, "-02'00'"))})
    assert all(_trusted(a, 2024) for a in arts.values())


async def test_a_date_out_of_range_is_no_crash_and_falls_back_to_the_modification_date():
    arts = await _vefrit_folder({"Bad": {"creationDate": "D:20241340120000Z", "modDate": "D:20230101120000Z", **WORD},
                                 "Worse": {"creationDate": "D:20241340120000Z", **WORD}})
    assert _trusted(arts["Bad"], 2023)
    assert arts["Worse"].date is None and arts["Worse"].analysis_notes.count(OLD_NOTE) == 1


async def test_a_batch_is_counted_per_folder():
    # three PDFs created in the same minute, but each in a folder of its own (a manifest may hold subfolders): no batch
    metas = {f"{J}Úlfljótur/Vefrit/{y}/F{y}.pdf": _word(_stamp(16, 52, 16)) for y in (1, 2, 3)}
    pdfs = {rel: _pdf([f"VEFRIT\nF{rel[-5]}\nEftir dr. Jón Jónsson\n{_PAD}"], meta) for rel, meta in metas.items()}
    m = _manifest(source="ulfljotur_vefrit", year=None, volume=None, issue=None, label="x", file=None, files=list(metas))
    m = await analyze_issue(m, get_config("ulfljotur_vefrit"), _deps(lambda rel: pdfs[rel]))
    assert {a.date for a in m.articles} == {"2024-01-01"}


@pytest.mark.parametrize("agent", [SAFARI, {"creator": "Chromium", "producer": "Skia/PDF m120"},
                                   {"creator": "Mozilla Firefox 115.0", "producer": "cairo 1.17.6"},
                                   {"creator": "Microsoft Edge", "producer": ""}])
async def test_a_pdf_printed_from_a_web_browser_has_no_usable_date(agent):
    arts = await _vefrit_folder({"Web": {"creationDate": _stamp(16, 52, 16), **agent},
                                 "Word": _word(_stamp(15, 37, 25, "+00'00'", "20190223"))})
    assert _distrusted(arts["Web"]) and _trusted(arts["Word"], 2019)


async def test_a_browser_pdf_without_a_plausible_year_keeps_the_plain_old_note():
    arts = await _vefrit_folder({"Web": {"creationDate": "D:19700101000000Z", **SAFARI}})
    assert arts["Web"].date is None and arts["Web"].analysis_notes.count(OLD_NOTE) == 1
    assert REEXPORT_NOTE not in arts["Web"].analysis_notes


async def test_a_printed_date_outranks_a_distrusted_metadata_date():
    page = f"VEFRIT\nLoftslagsváin og dómstólar\nBirt 21. janúar 2022\nEftir dr. Jón Jónsson\n{_PAD}"
    m = _manifest(source="ulfljotur_vefrit", year=None, volume=None, issue=None, label="x", file=None, files=[_VEFRIT])
    a = (await analyze_issue(m, get_config("ulfljotur_vefrit"),
                             _deps(_pdf([page], {"creationDate": _stamp(16, 52, 16), **SAFARI})))).articles[0]
    assert a.date == "2022-01-21" and a.provenance["date"] == "page" and REEXPORT_NOTE not in a.analysis_notes


def test_a_contents_heading_that_names_an_article_type_types_its_rows():
    """Task 22f: a column heading of a page-last contents page passes the type it stands for ("FRÉTTABÁLKUR" → Frétt,
    "MINNINGARBÁLKUR" → Annað), as Claude's list does; no heading leaves the type to the title."""
    from engine.timarit.analyze import _toc_entries
    m = IssueManifest(source="ulfljotur", year=2017, volume=70, issue="1", label="L", file="u.pdf")
    toc = [TocEntry("Vefrit Úlfljóts", [], 126, section="Frétt"), TocEntry("Arnljótur Björnsson", [], 130, section="Annað"),
           TocEntry("Viðtal við Kristínu Edwald", [], 120, section=None), TocEntry("Um dóma", [], 5, section="GREINAR"),
           TocEntry("Endurskoðun stjórnarskrárinnar", [], 107, section=None)]
    got = [(a.article_type, a.provenance["article_type"]) for a in _toc_entries(toc, m, get_config("ulfljotur"), "toc")]
    assert got == [("Frétt", "toc"), ("Annað", "toc"), ("Viðtal", "toc"), ("Fræðigrein", "toc"), ("Fræðigrein", "toc")]


# --- Task 23d fix round 1, R51: an S&S file's blank last page counts only when the printed range includes it ----------

@pytest.mark.parametrize("cited,pages,end", [
    ("(133-135)", [0, 2], 135),            # the range ends before the blank page (Vol08 133-152_…: "(133-151)")
    ("(133-136)", [0, 3], 136),            # its range includes it: kept (the earlier ruling)
    ("", [0, 3], 136),                     # no range printed: kept, as before
])
async def test_sogs_blank_last_page_counts_only_when_the_printed_range_includes_it(cited, pages, end):
    first = (f"133\nNýskipan í ríkisrekstri\nEva Marín Hlynsdóttir, doktorsnemi\n"
             f"Stjórnmál & stjórnsýsla 1. tbl., 8. árg. 2012 {cited}\n{_PAD}")
    rel = _SOGS + "133-136_New_Public_Management.pdf"
    m = await analyze_issue(_sogs_manifest([rel]), get_config("stjornmal_stjornsysla"),
                            _deps(_pdf([first, f"134\n{_PAD}", f"135\n{_PAD}", ""])))
    a = m.articles[0]
    assert (a.pdf_pages, a.page_start, a.page_end) == (pages, 133, end)
    assert any("auð lokasíða" in n for n in a.analysis_notes) == (pages == [0, 2])
