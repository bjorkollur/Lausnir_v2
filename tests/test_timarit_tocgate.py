# tests/test_timarit_tocgate.py
"""Task 23b (R47): a contents list the parser read is measured on its own pages (tocgate) and, when it fails, replaced by
Claude's list (analyze). The pages are real ones from the archive (tests/fixtures/timarit/pages/pagekind.json, made by
make_pagekind_fixture.py there); the end-to-end tests build small issues with PyMuPDF."""
import json
from pathlib import Path

import fitz
import pytest

from engine.config.sources import get_config
from engine.timarit import config as C
from engine.timarit import tocgate as TG
from engine.timarit.analyze import (FORCED_REVIEW_NOTES, TOC_GATE_NOTE, AnalyzeDeps, _on_page, _visual_text,
                                    analyze_issue)
from engine.timarit import analyze as A
from engine.timarit.firstpage import Line, Page
from engine.timarit.printed import RegistryValues
from engine.timarit.manifest import ArticleEntry, IssueManifest
from engine.timarit.toc import TocEntry

FIX = json.loads((Path(__file__).parent / "fixtures/timarit/pages/pagekind.json").read_text(encoding="utf-8"))


def page(key: str) -> Page:
    d = FIX[key]
    return Page([Line(*ln) for ln in d["lines"]], d["height"], d["width"])


def _measure(keys: list[str | None], titles: list[str], authors: list[list[str]] | None = None,
             front: int | None = None) -> TG.TocCheck:
    """check() over real pages as if they were the start pages of entries 0, 1, … of one issue, each entry verified
    against its own start page as analyze does (title_on_page over the page's visual text). A key None is an entry
    the list could not locate."""
    pages = [page(k) if k else Page([], 800.0, 600.0) for k in keys]
    pictures = [FIX[k]["picture"] if k else 0.0 for k in keys]
    entries, registry, people = [], {}, {}
    for i, title in enumerate(titles):
        a = ArticleEntry(title=title, pdf_pages=[i, i] if keys[i] else None, provenance={"title": "toc"})
        a.checks = {"title_on_page": bool(keys[i]) and _on_page(title, _visual_text(pages[i]), C.TITLE_ON_PAGE_MIN)}
        entries.append(a)
        registry[id(a)] = title
        people[id(a)] = (authors or [[]] * len(titles))[i]
    return TG.check(entries, registry, pages.__getitem__, pictures.__getitem__, [],
                    lambda title, i: _on_page(title, _visual_text(pages[i]), C.TITLE_ON_PAGE_MIN), len(pages),
                    lambda i: _visual_text(pages[i]), people, front=front)


def test_a_list_whose_rows_are_shifted_by_one_fails_on_its_pages():
    # TL 1991 2. hefti (audit 4): every title stands on the next entry's start page
    check = _measure(["tl1991/2", "tl1991/7", "tl1991/12", "tl1991/53", "tl1991/69"],
                     ["Benedikt Blöndal. In memoriam", "Réttarfar í þinglýsingarmálum og málskotsreglur",
                      "Er þörf á lögum um útboð hér á landi", "Aðalfundur Lögmannafélags Íslands 1991",
                      "Starfsnám við Mannréttindanefnd Evrópu"])
    assert (check.entries, check.found, check.shifted) == (5, 0, 4)
    assert check.failures(0.8) == ["0 af 5 titlum fundust á upphafssíðum sínum",
                                   "4 titlar standa á upphafssíðu greinarinnar á undan eða eftir"]


def test_an_entry_on_an_advert_page_is_counted():
    # TL 2017 2. hefti (audit 4): "Refsiþarfir samfélagsins …" placed on p. 215, the law firms' advert page
    check = _measure(["tl2017/4", "tl2017/94"], ["Bindandi áhrif dóma í einkamálum (Res Judicata)",
                                                 "Refsiþarfir samfélagsins og lagaleg jafnvægislist"])
    assert (check.found, check.on_filler) == (1, 1)
    assert check.failures(0.5) == []                                 # named in the note, decides nothing
    assert check.context() == ["1 grein hefst á síðu sem virðist auglýsing eða auð"]
    assert check.failures(0.8) == ["1 af 2 titlum fundust á upphafssíðum sínum"]


# Fix round 1: lists that work, although the registry has their titles otherwise than the page prints them. Each pair:
# the start page, the registry's title, its authors. None of these titles passes title_on_page as it stands.
_WORKING = [
    ("tl2012/2", "Lögfesting barnasáttmála Sameinuðu þjóðanna", []),        # editorial label over an unreadable layer
    ("lmbl2016c/35", "ARNDÍS A.K. GUNNARSDÓTTIR Ný lög um útlendinga", []),  # a byline in capitals in front
    ("lmbl2017d/7", "Jólahugvekja: Fjórða iðnbyltingin", ["Jóhann Alfreð Kristinsson"]),  # parts in another order
    ("lmbl2020b/13", "Viðtal við Markús Sigurbjörnsson. Þægilegt að kveðja eftir 25 ár",    # the interview opens
     ["Daníel Isebarn Ágústsson"]),                                                         # with its subject
    ("lmbl2023/22", "Tekist á um kjaradeilur: Hádegis- og aðalfundur Vinnuréttarfélags Íslands", ["Eyrún Ingadóttir"]),
    ("lmbl2023/23", "VIÐTAL VIÐ JÓNAS HARALDSSON Hef virkilega reynt að vera til vandræða", []),
    ("tl1951/11", "Eggert Clausen. In memoriam", ["Gústaf A. Sveinsson"]),   # the page prints "Eggert Claessen"
    ("logbru2013/154", "Viðtöl við bifrestinga", []),          # an interview under the label "VIÐTAL", no author
]


def test_a_working_list_is_found_on_its_pages_although_its_titles_differ():
    keys, titles, authors = zip(*_WORKING)
    assert not any(_on_page(t, _visual_text(page(k)), C.TITLE_ON_PAGE_MIN) for k, t, _ in _WORKING)
    check = _measure(list(keys), list(titles), list(authors))
    assert (check.entries, check.found) == (8, 8) and check.failures(0.8) == []
    # fix round 2: the entries found only by a person or a label are named, for a plain note on each
    assert dict(check.proxies) == {0: "merki", 3: "viðmælanda", 5: "viðmælanda", 7: "merki"}


def test_what_does_not_make_an_entry_found():
    # TL 1979: "Kjartan Þórðarson" by Högni Jónsson placed on Bjarni Bjarnason's obituary, whose text names "Vogi
    # Jónsson" (an audited failure: the author's name in running text is no evidence)
    check = _measure(["tl1979/112"], ["Kjartan Þórðarson"], [["Högni Jónsson"]])
    assert check.found == 0
    # the editorial's label counts for the list's first entry only; a body is no person
    check = _measure(["lmbl2023/22", "tl2012/2"], ["Nýr starfsmaður á skrifstofu LMFÍ", "Lögfesting barnasáttmála"],
                     [["Lögmannafélag Íslands"], []])
    assert check.found == 0
    # a credit names the author; a name inside running text does not
    assert TG.named_on(["Kristján Örvar Sveinsson"], _visual_text(page("logbru2013/154")))
    assert not TG.named_on(["Högni Jónsson"], _visual_text(page("tl1979/112")))
    # a column rubric is left out of the count, as a section label is; a title without letters is not (fix round 2)
    check = _measure(["lmbl2023/22", "tl1979/112"], ["Frá ritstjórn", "—"])
    assert (check.entries, check.found) == (1, 0)


def test_a_list_the_parser_read_as_dashes_fails():
    # TL 1971 (fix round 2): 8 of the list's rows were read as "—" and none located; they are the parser's failure,
    # counted and not found, so the list goes to Claude (it passed at 2/2 when they were left out)
    check = _measure(["tl1971/284"] + [None] * 8, ["Um örorkumöt"] + ["—"] * 8)
    assert (check.entries, check.found) == (9, 1)
    assert check.failures(0.8) == ["1 af 9 titlum fundust á upphafssíðum sínum"]


def test_a_list_with_nothing_to_measure_fails():
    # fix round 2: a list of rubrics and section labels only says nothing about its pages; it does not pass
    check = _measure(["lmbl2023/22", "tl1979/112"], ["Frá ritstjórn", "Ávarp"])
    assert check.entries == 0 and check.share == 0.0
    assert check.failures(0.8) == ["enginn titill efnisyfirlitsins var mælanlegur á síðunum"]


def test_a_label_does_not_place_an_entry_whose_title_is_on_the_next_page():
    # LMBL 2016-2 (fix round 2): "Aðalfundur Lögmannafélags Íslands" placed on the LEIÐARI page; its title is printed
    # on the next entry's page, so the label says nothing and the entry stands one off
    check = _measure(["lmbl2016b/3", "lmbl2016b/5"], ["Aðalfundur Lögmannafélags Íslands",
                                                     "Málþing um skyldur lögmanna gagnvart samfélaginu"])
    assert (check.found, check.shifted, check.proxies) == (0, 1, ())


def _measure_pages(pages: list[Page], starts: list[int], front: int | None = 0) -> TG.TocCheck:
    """check() over an issue whose entries start on the pages `starts` (each one page long, title on its page), with
    `front` as the first page after the cover and the contents; the pages in no entry's range are the rest."""
    entries = []
    for s in starts:
        a = ArticleEntry(title=f"Grein á síðu {s}", pdf_pages=[s, s], provenance={"title": "toc"})
        a.checks = {"title_on_page": True}
        entries.append(a)
    return TG.check(entries, {id(a): a.title for a in entries}, pages.__getitem__, lambda i: 0.0, [],
                    lambda t, i: False, len(pages), front=front)


def test_text_pages_before_the_first_entry_are_unclaimed():
    # TL 1971 (fix round 2): its list's first located entry starts on pdf 232; the text pages between the contents and
    # it belong to no entry, and count as unclaimed from the first page after the contents, also when they are set in
    # another type than the issue's longest article (the volume's 1971 issues: 11.3 pt, its 1972 article: 10.1 pt).
    # Task 23c: the front matter's own pages (below) are not counted, so the gap here is longer than they are.
    gap = C.COVERAGE_MAX_GAP_PAGES + 3
    pages = [page("tl1971b/50")] * gap + [page("tl1971/284")]
    assert (_measure_pages(pages, [gap], front=0).unclaimed, _measure_pages(pages, [gap], front=None).unclaimed) == (3, 0)


def test_front_matter_between_the_contents_and_the_first_article_is_not_unclaimed():
    # Task 23c. TL 2024-4 pdf 1 is the inner title and masthead, Úlfljótur 2024-1 pdf 2-4 the contents, the masthead and
    # the editor's note: pages of three long lines or more in their own type, in 108 lists that hold. They lie within
    # the first COVERAGE_MAX_GAP_PAGES pages after the contents, which are the issue's front matter, not its articles.
    tl = _measure_pages([page("tl2024e/1"), page("tl1985/83")], [1])
    ulf = _measure_pages([page("ulf2024/2"), page("ulf2024/3"), page("ulf2024/4"), page("tl1985/83")], [3])
    assert (tl.unclaimed, tl.pages) == (0, 2) and (ulf.unclaimed, ulf.pages) == (0, 4)


def test_the_front_gap_beyond_the_front_matter_is_unclaimed():
    # TL 1989: the list's first entry is on pdf 224 of 304 and the volume's first 220 pages are articles it lacks
    gap = C.COVERAGE_MAX_GAP_PAGES + 40
    pages = [page("tl1985/83")] * gap + [page("tl1985/84")]
    check = _measure_pages(pages, [gap])
    assert (check.unclaimed, check.pages) == (40, gap + 1)


def test_pages_between_and_after_the_entries_are_unclaimed_from_the_first_entry_on():
    pages = [page("tl1985/83")] * 5 + [page("tl1985/84")] * 3
    assert _measure_pages(pages, [0, 4]).unclaimed == 6 and _measure_pages(pages, [0, 4]).pages == 8


def test_pages_whose_text_layer_is_garbled_are_no_unclaimed_text():
    # TL 2009-2012 (Task 23c): a garbled font reads as replacement characters. The ranges there are cut short in the
    # garbled pages (TL 2010-2: one article of 66 pages ends on the 5th); that is no article the list lacks. The
    # unclaimed pages of the lists that do lack articles are readable.
    garbled, readable = page("tl2010/14"), page("tl1985/83")
    assert "\ufffd" in "".join(ln.text for ln in garbled.lines)
    assert _measure_pages([garbled] * 5, [0]).unclaimed == 0
    assert _measure_pages([readable] * 5, [0]).unclaimed == 4


def test_the_forms_of_a_title_the_page_may_print():
    assert TG.variants("VIÐTAL VIÐ JÓNAS HARALDSSON Hef virkilega reynt að vera til vandræða")[1:] == \
        ["Hef virkilega reynt að vera til vandræða"]
    assert TG.variants("Viljum aukna umræðu um kosti þess að fyrirtæki ráði lögfræðinga VIÐTAL")[1:] == \
        ["Viljum aukna umræðu um kosti þess að fyrirtæki ráði lögfræðinga"]
    assert TG.variants("Raddir fólks berast inn til dómstólanna: Viðtal við Ingibjörgu K. Benediktsdóttur")[1:] == \
        ["Raddir fólks berast inn til dómstólanna"]
    assert TG.variants("Nýtt rit um efnisreglur stjórnsýsluréttar og sala sveitarfélaga á byggingarlóðum sínum "
                       "Sindri M. Stephensen og Víðir Smári Petersen:")[1:] == \
        ["Nýtt rit um efnisreglur stjórnsýsluréttar og sala sveitarfélaga á byggingarlóðum sínum"]
    assert TG.variants("RÉTTARRÍKIÐ OG DÓMSTÓLAR") == ["RÉTTARRÍKIÐ OG DÓMSTÓLAR"]   # a title in capitals stays whole


def test_a_list_that_holds_on_its_pages_passes():
    check = TG.TocCheck(entries=10, found=9, shifted=0, on_filler=0, unclaimed=3, pages=100)
    assert check.failures(0.8) == [] and check.as_dict()["share"] == 0.9 and check.as_dict()["pages"] == 100
    assert TG.TocCheck(4, 3, 0, 0, 0).failures(0.8) == ["3 af 4 titlum fundust á upphafssíðum sínum"]
    assert TG.TocCheck(0, 0, 0, 0, 0).failures(0.8) != []           # nothing to measure: no pass (fix round 2)


def test_a_list_that_leaves_much_of_the_volume_unclaimed_fails():
    # Task 23c. TL 1989: 6 of its 7 titles stand on their pages, but the list covers only the end of the volume: the
    # first entry is on pdf 224 of 304 and about 20 articles before it are missing. The titles alone pass it.
    check = TG.TocCheck(7, 6, 0, 0, 195, pages=300)
    assert check.share > 0.8
    assert check.failures(0.8) == ["efnisyfirlitið nær ekki yfir 195 síður heftisins"]
    assert check.failures(0.8, 200, 0.25) == []                      # (the page count is a tunable)
    assert check.failures(0.8, 20, 0.7) == []                        # (so is the share)
    # it takes both: a handful of missing articles in a big volume, or most of a short issue's pages are no list failure
    assert TG.TocCheck(10, 10, 0, 0, 30, pages=300).failures(0.8, 20, 0.25) == []     # 10 % of the volume
    assert TG.TocCheck(4, 4, 0, 0, 15, pages=20).failures(0.8, 20, 0.25) == []        # 75 % of an issue, but 15 pages
    assert TG.TocCheck(4, 4, 0, 0, 30, pages=100).failures(0.8, 20, 0.25) == ["efnisyfirlitið nær ekki yfir 30 síður heftisins"]
    # the defaults are the config's; the number agrees with "síða" in Icelandic (21 síðu, 22 síður, 111 síður)
    assert C.TOC_MAX_UNCLAIMED_PAGES > 0 and 0 < C.TOC_MAX_UNCLAIMED_SHARE < 1
    for n, word in ((21, "síðu"), (22, "síður"), (111, "síður"), (121, "síðu")):
        assert TG.TocCheck(4, 4, 0, 0, n, pages=n).failures(0.8, 20, 0.25) == [f"efnisyfirlitið nær ekki yfir {n} {word} heftisins"]
    # it comes after the reasons that concern the titles
    both = TG.TocCheck(7, 2, 0, 0, 195, pages=300).failures(0.8, 20, 0.25)
    assert both == ["2 af 7 titlum fundust á upphafssíðum sínum", "efnisyfirlitið nær ekki yfir 195 síður heftisins"]


# --- end to end: analyze replaces a failing list ------------------------------------------------------------------

_PAD = "Meginmál greinarinnar heldur áfram hér með nægum texta til að síðan hafi textalag."


def _pdf(pages: list[str]) -> bytes:
    d = fitz.open()
    for t in pages:
        d.new_page().insert_text((72, 72), t, fontsize=11)
    return d.tobytes()


def _issue(shifted: bool) -> bytes:
    return _pdf(_issue_pages(shifted))


def _issue_pages(shifted: bool, heads: dict[int, str] | None = None) -> list[str]:
    """A TL-style issue: cover, contents (page first), articles on printed pages 1, 5 and 9 (printed = pdf - 1). With
    `shifted` the pages print the titles one entry late: an editor's note first, every listed title on the next
    listed page. `heads` puts other heads on articles by index."""
    toc = ("Efnisyfirlit\n1\nJón Jónsson:\nFyrsta greinin\n5\nAnna Pálsdóttir:\nÖnnur greinin um dóma\n"
           "9\nPáll Pálsson:\nRitdómur: Bók um kröfurétt\n1. hefti 60. árgangur\nvor 2010")
    titles = (["Formáli ritstjóra", "Fyrsta greinin", "Önnur greinin um dóma"] if shifted else
              ["Fyrsta greinin", "Önnur greinin um dóma", "Ritdómur: Bók um kröfurétt"])
    pages = ["Lögfræðingafélag Íslands\nTímarit lögfræðinga", toc]
    for k, (start, head) in enumerate(zip((1, 5, 9), titles)):
        pages.append(f"{start}\n{(heads or {}).get(k, head)}\nTexti.\n" + "\n".join([_PAD] * 6))
        pages += [f"{p}\nFramhald.\n" + "\n".join([_PAD] * 6) for p in range(start + 1, start + 4)] if start < 9 else []
    return pages


def _issue_pages_missing_articles(unlisted: int) -> list[str]:
    """As _issue_pages(False), but `unlisted` pages of an article the contents does not list stand between the contents
    and its first entry (printed 1..unlisted); the listed articles start on printed unlisted+1, +5 and +9."""
    first = unlisted + 1
    toc = ("Efnisyfirlit\n" + f"{first}\nJón Jónsson:\nFyrsta greinin\n{first + 4}\nAnna Pálsdóttir:\nÖnnur greinin um dóma\n"
           f"{first + 8}\nPáll Pálsson:\nRitdómur: Bók um kröfurétt\n1. hefti 60. árgangur\nvor 2010")
    pages = ["Lögfræðingafélag Íslands\nTímarit lögfræðinga", toc]
    pages += [f"{p}\nÓskráð grein, framhald.\n" + "\n".join([_PAD] * 6) for p in range(1, unlisted + 1)]
    for start, head in zip((first, first + 4, first + 8), ("Fyrsta greinin", "Önnur greinin um dóma", "Ritdómur: Bók um kröfurétt")):
        pages.append(f"{start}\n{head}\nTexti.\n" + "\n".join([_PAD] * 6))
        pages += [f"{p}\nFramhald.\n" + "\n".join([_PAD] * 6) for p in range(start + 1, start + 4)] if start < first + 8 else []
    return pages


def _manifest() -> IssueManifest:
    return IssueManifest(source="timarit_logfraedinga", year=2010, volume=60, issue="1",
                         label="Tímarit lögfræðinga, 60. árg. 1. hefti (2010)", file="x/TL 2010_01.pdf",
                         files=["x/TL 2010_01.pdf"])


def _deps(pdf: bytes, claude: list[TocEntry] | None, asked: list | None = None, pending: bool = False) -> AnalyzeDeps:
    async def llm(text):
        if asked is not None:
            asked.append(text)
        return claude or []
    return AnalyzeDeps(read_bytes=lambda rel: pdf, leitir=None, llm_toc=llm if claude is not None else None,
                       xlsx_rows=[], llm_pending=(lambda text: pending) if claude is not None else None)


_CLAUDE = [TocEntry("Formáli ritstjóra", [], 1, section="Ritstjórnargrein"),
           TocEntry("Fyrsta greinin", ["Jón Jónsson"], 5, section="Fræðigrein"),
           TocEntry("Önnur greinin um dóma", ["Anna Pálsdóttir"], 9, section="Fræðigrein")]


async def test_a_list_that_holds_is_kept_and_its_measure_recorded():
    asked: list = []
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_issue(False), _CLAUDE, asked))
    assert [a.title for a in m.articles] == ["Fyrsta greinin", "Önnur greinin um dóma", "Bók um kröfurétt"]
    assert m.page_map["toc_check"]["share"] == 1.0 and asked == []
    assert not any(n.startswith(TOC_GATE_NOTE) for a in m.articles for n in a.analysis_notes)


async def test_a_list_that_leaves_much_of_the_volume_unclaimed_is_replaced_by_claudes():
    # Task 23c (TL 1989): every title stands on its page, but 30 pages of an article the contents does not list stand
    # before the first entry. The titles alone pass the list; the pages no entry claims fail it.
    asked: list = []
    pdf = _pdf(_issue_pages_missing_articles(30))
    claude = [TocEntry("Óskráð grein", [], 1, section="Fræðigrein"), *[TocEntry(e.title, e.authors, e.page_start + 30, section=e.section)
                                                                      for e in _CLAUDE[1:]]]
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(pdf, claude, asked))
    check = m.page_map["toc_check"]
    assert check["share"] == 1.0 and check["unclaimed"] == 30 - C.COVERAGE_MAX_GAP_PAGES and len(asked) == 1
    note = (f"{TOC_GATE_NOTE} (efnisyfirlitið nær ekki yfir {check['unclaimed']} síður heftisins) — greinalisti Claude notaður")
    assert all(note in a.analysis_notes for a in m.articles)
    assert [a.provenance["title"] for a in m.articles] == ["llm"] * 3 and m.status == "review"


async def test_a_list_that_leaves_few_pages_unclaimed_is_kept():
    asked: list = []
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"),
                            _deps(_pdf(_issue_pages_missing_articles(3)), _CLAUDE, asked))
    assert m.page_map["toc_check"]["unclaimed"] == 0 and asked == []
    assert not any(n.startswith(TOC_GATE_NOTE) for a in m.articles for n in a.analysis_notes)


async def test_a_list_that_fails_on_its_pages_is_replaced_by_claudes():
    asked: list = []
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_issue(True), _CLAUDE, asked))
    assert len(asked) == 1 and "Efnisyfirlit" in asked[0]                 # the contents and the page digest
    assert [(a.title, a.page_start, a.provenance["title"]) for a in m.articles] == [
        ("Formáli ritstjóra", 1, "llm"), ("Fyrsta greinin", 5, "llm"), ("Önnur greinin um dóma", 9, "llm")]
    note = ("efnisyfirlitið stóðst ekki á síðunum (0 af 3 titlum fundust á upphafssíðum sínum; 2 titlar standa á "
            "upphafssíðu greinarinnar á undan eða eftir) — greinalisti Claude notaður")
    assert all(note in a.analysis_notes for a in m.articles)
    assert m.page_map["toc_check"] == {"entries": 3, "found": 0, "share": 0.0, "shifted": 2, "on_filler": 0,
                                       "unclaimed": 0, "pages": 9, "by_proxy": 0}
    assert m.status == "review" and max(a.confidence for a in m.articles) <= C.LLM_CONFIDENCE_CAP


async def test_an_entry_placed_by_its_author_only_gets_a_plain_note():
    # fix round 2: the first article's page prints its author at the top but not the contents' title; the list holds,
    # and the entry says how it was placed (no deviation, no forced review)
    pdf = _pdf(_issue_pages(False, {0: "Jón Jónsson:"}))
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(pdf, _CLAUDE))
    assert m.page_map["toc_check"]["by_proxy"] == 1 and m.page_map["toc_check"]["share"] == 1.0
    note = "titill efnisyfirlitsins fannst ekki á síðunni; greinin staðsett eftir höfundi"
    assert m.articles[0].analysis_notes == [note] and not note.startswith(FORCED_REVIEW_NOTES)
    assert not any(n.startswith("titill efnisyfirlitsins") for a in m.articles[1:] for n in a.analysis_notes)


def test_the_plain_note_names_the_pages_heading_unless_it_is_the_title_or_the_author():
    # fix round 2, from the bulk run: TL 2024-4 idx 1 (the cover's wording in the registry), Lögfræðingur 2012-6 idx 7
    # (a drop cap read "SamMmsömun"), Rannsóknir 2006-7 idx 3 (the author's name set large)
    def note(heading, title, authors):
        return A._proxy_note("höfundi", heading, RegistryValues(title, authors, None, frozenset()))
    assert note("SÉRSTAKT HÆFI Í SVEITARFÉLÖGUM", "Hæfisreglan í sveitarstjórnarlögunum", ["Trausti Fannar Valsson"]) == (
        "titill efnisyfirlitsins fannst ekki á síðunni; greinin staðsett eftir höfundi "
        "(fyrirsögn síðunnar: „SÉRSTAKT HÆFI Í SVEITARFÉLÖGUM“)")
    assert note("SamMmsömun", "Samsömun", ["Þorsteinn Hjaltason"]) is None
    assert note("Elfa Ýr Gylfadóttir", "Stefnumótun og færar leiðir við útfærslu flutningsreglna",
                ["Elfa Ýr Gylfadóttir"]) == "titill efnisyfirlitsins fannst ekki á síðunni; greinin staðsett eftir höfundi"


@pytest.mark.parametrize("claude, pending, tail", [
    ([], True, "greinalisti Claude ekki kominn; efnisyfirlitinu haldið á meðan"),         # queued for the session
    ([], False, "Claude fann engar greinar í heftinu; efnisyfirlitinu haldið til yfirferðar"),   # answered, empty
    (None, False, "greinalisti Claude ekki tiltækur; efnisyfirlitinu haldið til yfirferðar"),    # no Claude at all
])
async def test_without_claudes_list_the_parsed_list_stays_for_review(claude, pending, tail):
    m = await analyze_issue(_manifest(), get_config("timarit_logfraedinga"), _deps(_issue(True), claude, pending=pending))
    assert [a.title for a in m.articles] == ["Fyrsta greinin", "Önnur greinin um dóma", "Bók um kröfurétt"]
    assert all(any(n.startswith(TOC_GATE_NOTE) and n.endswith(tail) for n in a.analysis_notes) for a in m.articles)
    assert m.status == "review"
