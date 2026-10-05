# tests/test_timarit_pagekind.py
"""Task 22c: what the pages after an article's first page are, where it ends, how it is signed (pagekind). Every page
is a real pilot page as firstpage.read_page returns it (text, size, top, bold, left, right) with the share pictures
cover and its vector drawings, in tests/fixtures/timarit/pages/pagekind.json (made by make_pagekind_fixture.py there,
running text in mid-page replaced by filler of the same length); each test names the audited article it mirrors."""
import importlib.util
import json
from pathlib import Path

import pytest

from engine.timarit import firstpage as FP
from engine.timarit import pagekind as K
from engine.timarit.firstpage import Line, Page
from engine.timarit.pagekind import Kind, Typography

FIX = json.loads((Path(__file__).parent / "fixtures/timarit/pages/pagekind.json").read_text(encoding="utf-8"))


def page(key: str, edit=None) -> Page:
    """A fixture page; `edit(lines)` may change its lines (text, size, top, bold, left, right) first."""
    d = FIX[key]
    lines = [list(ln) for ln in d["lines"]]
    if edit is not None:
        edit(lines)
    return Page([Line(*ln) for ln in lines], d["height"], d["width"])


def end(source: str, pdf: list[int], title: str, authors: tuple[str, ...] = (), edits: dict | None = None) -> K.End:
    """article_end over the given PDF pages of a pilot file, as if they followed each other (index 0 = first page);
    `edits`: {pdf page: edit} for pages to change first."""
    pages = [page(f"{source}/{i}", (edits or {}).get(i)) for i in pdf]
    pictures = [FIX[f"{source}/{i}"]["picture"] for i in pdf]
    drawings = [FIX[f"{source}/{i}"]["drawings"] for i in pdf]
    return K.article_end(pages.__getitem__, 0, len(pages) - 1, title, pictures.__getitem__, list(authors),
                         drawings.__getitem__)


# --- where an article ends ------------------------------------------------------------------------------------------

def test_an_interview_ends_on_its_last_text_page_not_on_the_blank_and_advert_pages_after_it():
    # Úlfljótur 2023-3 idx 3: the interview ends on p. 384 (pdf 121); 385-386 are blank, 387 on adverts
    e = end("ulf2023", [114, 115, 116, 117, 120, 121, 122, 123, 124, 125],
            "Viðtal við Markús Sigurbjörnsson, fyrrverandi hæstaréttardómara")
    assert (e.last, e.uncertain, e.unlisted) == (5, False, None)            # pdf 121
    assert e.outside == (6, 7, 8, 9)                                        # a running head alone, blank, sponsors, advert


def _say(line_index, words):
    """An edit that ends line `line_index` of a page with `words` (fix round 2: ordinary words an advert also prints)."""
    def edit(lines):
        text = lines[line_index][0]
        lines[line_index][0] = f"{text[:-1]} {words}{text[-1]}"
    return edit


def _line(key, needle):
    return next(k for k, ln in enumerate(FIX[key]["lines"]) if needle in ln[0])


@pytest.mark.parametrize("words", ["ég tel það", "Prentsmiðjan Oddi hf. og banki"])
def test_ordinary_words_in_the_next_items_text_change_nothing(words):
    # TL 1985 idx 32: a line of "NÝ STJÓRNSÝSLULÖGGJÖF …" below the signature says "ég tel" or names a firm: the item
    # still starts there and the article still ends on that page, signed by Hjördís Hákonardóttir
    k = _line("tl1985/292", "Hjördís")
    below = next(j for j, ln in enumerate(FIX["tl1985/292"]["lines"]) if j > k + 2 and len(ln[0]) > 40)
    e = end("tl1985", [289, 290, 291, 292, 293, 294], "Amnesty international", edits={292: _say(below, words)})
    assert (e.last, e.unlisted, e.signed) == (3, (3, "NÝ STJÓRNSÝSLULÖGGJÖF í DANMÖRKU"), ("Hjördís Hákonardóttir",))


def test_ordinary_words_on_the_line_above_a_signature_change_nothing():
    # TL 1985 idx 15: its last line ends "… tel ég." right above "Jónatan Þórmundsson"
    k = _line("tl1985/142", "Jónatan")
    e = end("tl1985", [141, 142, 143, 144], "Þankabrot um nýstofnað embætti ríkislögmanns", edits={142: _say(k - 1, "tel ég")})
    assert (e.last, e.signed, e.unlisted) == (1, ("Jónatan Þórmundsson",), (1, "NÝTT RIT UM HLUTAFÉLÖG"))


def test_marks_of_an_advert_are_contacts_and_firms_not_ordinary_words():
    # fix round 2: "ég tel", "í síma", "bankinn", "tryggingar" are running text; "Sími 590 2600", "Tel. 555 1234",
    # "www.lex.is", "lex@lex.is", "Lex ehf." are an advert's
    assert K._marks("Ég tel að bankinn hafi borið ábyrgð á tryggingum sparisjóðsins og hringt í síma.") == 0
    assert K._marks("Lex ehf. • Sími 590 2600 • Tel. 555 1234 • www.lex.is • lex@lex.is") == 8
    assert K._marks("Lögfræðingafélag Íslands • Álftamýri 9 Sími 568 0887 • 108 Reykjavík") == 2   # word and number
    assert not K._STRONG_MARKS.search("Prentsmiðjan Oddi hf. og Landsbanki Íslands hf.")


def test_the_next_issues_cover_is_no_unlisted_item():
    # TL 1985 idx 24: the cover of 4. hefti, with its contents, follows the adverts after the article; TL 2004 4. hefti
    assert K._issue_cover(page("tl1985/224")) and K._issue_cover(page("tl2004/480"))
    e = end("tl1985", [215, 216, 217, 224], "Embættisskipanir og lausnir frá 1. maí 1983 til 1. desember 1985")
    assert (e.last, e.unlisted) == (2, None)


def test_a_running_head_a_citation_or_a_footnote_naming_an_issue_is_no_cover():
    # fix round 3: Úlfljótur 2023-3's running head "3. tbl. 2023 – 76. árg. Úlfljóts"; TL 2024's first-page citation
    # "Tímarit lögfræðinga, 1. hefti 74. árg. 2024, bls. 9–61,"; TL 1985's footnote "1) Fréttabréf L.M.F.Í., 3. tbl. 4.
    # árg. 1985, bls. 13-15."; a bibliography line
    assert not any(K._issue_cover(page(k)) for k in ("ulf2023/115", "tl2024/10", "tl1985/141"))
    body = [Line("meginmál greinarinnar heldur hér áfram með nægum texta í línunni", 10.0, 60 + 14 * k) for k in range(20)]
    biblio = Page(body + [Line("Úlfljótur, 4. tbl. 56. árg. 2003", 10.0, 250)], 600.0)
    assert not K._issue_cover(biblio)


def test_an_inner_cover_is_a_cover_by_its_issue_line_and_its_contents_whatever_the_type_size():
    # Task 23a: TL 1974 pdf 194 is an inner cover with "EFNI:". Its issue line "3. HEFTI 25. ÁRGANGUR" is set at 0.85 of
    # the body size, under the 0.95 a lone issue line needs; the contents line beside it makes the page a cover.
    cover = page("tl1974/194")
    body = FP.body_size(cover)
    assert next(ln.size for ln in cover.lines if ln.text.startswith("3. HEFTI")) < 0.95 * body
    assert K._issue_cover(cover)
    # the article before it ends on its own text: the cover is no unlisted item (it was flagged before)
    e = end("tl1974", [181, 193, 194], "Dómar Alþjóðadómstólsins")
    assert e.unlisted is None


def _edit_cover(**over):
    """An edit of the TL 1974 inner cover: line 2 is the issue line, line 4 the contents line."""
    def edit(lines):
        for k, change in over.items():
            line = int(k[1:])
            if change is None:
                lines[line][0] = "meginmál greinarinnar heldur hér áfram með nægum texta í línunni"
            elif isinstance(change, str):
                lines[line][0] = change
            else:
                lines[line][change[0]] = change[1]
    return edit


@pytest.mark.parametrize("contents", ["EFNI:", "EFNI", "Efnisyfirlit", "EFNISYFIRLIT:"])
def test_a_contents_line_is_efni_or_efnisyfirlit(contents):
    assert K._issue_cover(page("tl1974/194", _edit_cover(l4=contents)))


@pytest.mark.parametrize("why,edit", [
    ("no contents line", _edit_cover(l4=None)),
    ("a line that only starts with the word", _edit_cover(l4="Efnið er hér rakið í stuttu máli")),
    ("contents in the lower half", _edit_cover(l4=(2, 0.7 * 631.2))),
    ("contents in the top margin band", _edit_cover(l4=(2, 10.0))),
    ("a citation naming the pages", _edit_cover(l2="3. hefti 25. árg. 1975, bls. 98")),
    ("a long line", _edit_cover(l2="3. HEFTI 25. ÁRGANGUR og meira efni sem var sett á línuna sjálfa")),
    ("an issue line that does not start the line", _edit_cover(l2="Sjá 3. HEFTI 25. ÁRGANGUR")),
    ("an issue line in the lower half", _edit_cover(l2=(2, 0.7 * 631.2))),
])
def test_a_small_issue_line_needs_its_contents_line_in_the_upper_half(why, edit):
    # every other condition of Task 22c stays: starts the line, short, no "bls.", below the margin band, upper half
    assert not K._issue_cover(page("tl1974/194", edit)), why


def test_a_contents_line_makes_no_cover_without_an_issue_line():
    # the page of a contents list under another heading ("EFNI:" with entries) is not a cover by itself
    assert not K._issue_cover(page("tl1974/194", _edit_cover(l2=None, l3=None)))


def test_a_footnote_citing_an_issue_keeps_the_unlisted_item_flag():
    # fix round 3: a footnote "1) Sjá Tímarit lögfræðinga, 2. hefti 35. árg. 1985, bls. 101." on TL 1985 p. 275 (idx 32)
    def footnote(lines):
        lines.append(["1) Sjá Tímarit lögfræðinga, 2. hefti 35. árg. 1985, bls. 101.", 8.0, 0.86 * FIX["tl1985/292"]["height"],
                      False, 60.0, 300.0])
    e = end("tl1985", [289, 290, 291, 292, 293, 294], "Amnesty international", edits={292: footnote})
    assert e.unlisted == (3, "NÝ STJÓRNSÝSLULÖGGJÖF í DANMÖRKU")


def test_an_advert_page_right_after_the_text_stays_out_of_the_article():
    # fix round 3: LMBL 2017's notices to members (firms, no phone) and TL 2024's house advert ("Sími 568 0887") right
    # after an article's last text page are adverts, not its last lines
    assert end("lmbl2017", [31, 33], "Jólasnapsmót LMFÍ").last == 0
    t = Typography((11.5, 10.0), 60.0)
    title = "Takmörkun á frádrætti vaxtagjalda - skuldbindingar samkvæmt EES-rétti"
    assert K.classify(page("tl2024/149"), t, title, FIX["tl2024/149"]["picture"], after_text=True) == Kind.ADVERT
    assert end("tl2024", [143, 149], title).last == 0


def test_a_one_page_item_ends_before_an_advert_with_a_picture_and_the_notices_to_members():
    # LMBL 2017-1 idx 11: "Jólasnapsmót LMFÍ" is p. 32 only; then a full-page advert, two pages of notices, an advert
    e = end("lmbl2017", [31, 32, 33, 34, 35], "Jólasnapsmót LMFÍ")
    assert (e.last, e.outside, e.unlisted) == (0, (1, 2, 3, 4), None)


def test_an_item_that_starts_half_way_down_under_the_signature_ends_the_article_there():
    # TL 1985 idx 32 "Amnesty international": signed "Hjördís Hákonardóttir" on p. 275, where "NÝ STJÓRNSÝSLULÖGGJÖF í
    # DANMÖRKU" begins and runs on to p. 276 (not the article's); adverts follow
    e = end("tl1985", [289, 290, 291, 292, 293, 294], "Amnesty international")
    assert e.last == 3 and e.signed == ("Hjördís Hákonardóttir",)
    assert e.unlisted == (3, "NÝ STJÓRNSÝSLULÖGGJÖF í DANMÖRKU")
    # TL 1985 idx 15: pp. 132-133 only, signed "Jónatan Þórmundsson"; p. 134 is the next book notice, adverts follow
    e = end("tl1985", [141, 142, 143, 144], "Þankabrot um nýstofnað embætti ríkislögmanns")
    assert (e.last, e.signed, e.unlisted) == (1, ("Jónatan Þórmundsson",), (1, "NÝTT RIT UM HLUTAFÉLÖG"))


def test_the_next_items_title_page_ends_the_article_byline_over_a_title_in_lower_case():
    # Lögrétta 2014 idx 1: ends on p. 48 (pdf 37, its bibliography); pdf 38 is "Kristín Haraldsdóttir / sérfræðingur"
    # over "áhrif Lissabon- / sáttmálans …", an article Claude's list lacks
    e = end("logretta2014", [12, 13, 14, 15, 36, 37, 38, 39, 40], "Staða dóma Mannréttindadómstóls Evrópu í íslenskum landsrétti",
            ("Davíð Þór Björgvinsson",))
    assert e.last == 5 and e.unlisted[0] == 6
    assert e.unlisted[1].startswith("Áhrif Lissabonsáttmálans á rétt einstaklinga")


def test_an_old_layout_prints_the_articles_last_lines_at_the_foot_of_the_next_items_first_page():
    # Úlfljótur 1957 187.pdf (idx 4, its own file): p. 11 starts "RRAGI BJÖRNSSON, stud. jur.:" / "Juristforeningen 50
    # ára."; p. 10 stopped mid-sentence and the article's last lines stand at the foot of p. 11: it ends there
    e = end("ulf1957", [0, 1, 2, 3, 7, 8, 9], "Nokkrar athugasemdir í sambandi við skjöl, sem afhent eru til þinglýsingar",
            ("Ólafur Pálsson",))
    assert e.last == 5 and e.unlisted == (5, "Juristforeningen 50 ára.")


def test_a_notice_at_the_top_of_a_page_of_the_article_is_an_insert():
    # Úlfljótur 1947 211.pdf: "TIL LESENDA" heads p. 2 while the article goes on below it and on the pages after
    e = end("ulf1947", [0, 1, 2, 3, 4], "Viðskiptabréfsreglur um hlutabréf", ("Ólafur Lárusson",))
    assert (e.last, e.unlisted) == (4, None)


def test_a_page_of_the_articles_text_drawn_as_outlines_is_kept_and_the_end_is_uncertain():
    # LMBL 2005-2 idx 13 (fix round 1): p. 31 (pdf 30) carries two columns of the article as 481 vector outlines and
    # no text layer: not a blank page; the range keeps it and the end is flagged
    e = end("lmbl2005", [28, 29, 30], "Athyglisverðir úrskurðir úrskurðarnefndar lögmanna")
    assert (e.last, e.uncertain) == (2, True)
    blank = Page([], 700.0, 500.0)
    assert K.classify(blank, Typography((10.0,), 45.0), "x", drawings=481) == Kind.OTHER
    assert K.classify(blank, Typography((10.0,), 45.0), "x", drawings=4) == Kind.BLANK
    assert K.classify(blank, Typography((10.0,), 45.0), "x", picture=1.0) == Kind.ADVERT      # a full-page advert


def test_a_photo_spread_with_captions_is_no_advert_and_the_end_is_uncertain():
    # LMBL 2005-2 idx 8 (fix round 1): p. 23 is photographs (58% of the page) with 34 caption lines, no advert mark
    e = end("lmbl2005", [21, 22], "Heyrt og séð á hinni umtöluðu árshátíð LMFÍ")
    assert (e.last, e.uncertain) == (1, True)


def test_text_after_the_adverts_must_go_on_with_the_article():
    # TL 2004 idx 3: after the adverts, Lögberg's book notice ("LOGBERG" / "RIT LAGASTOFNUNAR …") is no continuation
    e = end("tl2004", [184, 185, 194], "Um húftryggingar kaupskipa", ("Valgarð Briem",))
    assert e.last == 0
    assert not K.continues(page("tl2004/194"), Typography((10.5,), 75.0), "Um húftryggingar kaupskipa")


def test_tables_and_lists_of_the_article_under_its_running_head_are_its_pages():
    # Úlfljótur 2010-1 idx 0: an appendix of tables ("Tafla I – …", "2. Fjármálamarkaður – …") under the author's name
    t = Typography((10.0, 8.0), 70.0)
    title = "Fjármálamarkaður innan Evrópusambandsins og EES reglur og eftirlit"
    kinds = [K.classify(page(f"ulf2010/{i}"), t, title, authors=["Dóra Guðmundsdóttir"], after_text=True)
             for i in range(34, 42)]
    assert kinds == [Kind.TEXT] * 8


def test_a_section_heading_under_the_running_head_is_no_new_item():
    # Rannsóknir 2011 idx 2: "Breytingar með tilskipun 2008/48/EB" at the top of a page set under the article's title
    t = K.typography([page(f"ranns2011/{i}") for i in (26, 27, 28)])
    assert K.classify(page("ranns2011/29"), t, "Upplýsingaskylda lánveitanda í samningum um neytendalán",
                      authors=["Eyvindur G. Gunnarsson"]) == Kind.TEXT


def test_the_heading_a_page_starts_with_not_a_larger_advert_below_it():
    # LMBL 2020-1 idx 8 is followed by "VINNUHÓPUR UM ÁKVÖRÐUN ÞÓKNUNAR …" (27 pt) over an advert's "VELKOMIN" (32.5 pt)
    assert K.new_item_at_top(page("lmbl2020/37"), Typography((10.0,), 56.0), "Jólasnapsmótið í fótbolta") == \
        "Vinnuhópur um ákvörðun þóknunar í verjenda- og réttargæslumálum"


def test_adverts_by_their_marks_but_not_a_bibliography_that_cites_web_pages():
    # TL 1985 pdf 144: lawyers' adverts ("Sími 53033"); Lögrétta 2010 idx 2: its last pages list cited web pages
    assert K.classify(page("tl1985/144"), Typography((9.5,), 75.0), "Þankabrot") == Kind.ADVERT
    assert K.advert_marks(page("logretta2010/76")) == 0
    assert K.classify(page("logretta2010/76"), Typography((11.0, 9.5), 75.0), "Skipting á skattlagningarréttinum") == \
        Kind.TEXT


# --- the article's type ---------------------------------------------------------------------------------------------

def test_typography_takes_the_body_over_footnotes_that_outweigh_it():
    # Lögfræðingur 2011 idx 2 (an OCR'd scan: one body type read as 8.5–9.4 pt) and Lögrétta 2014 idx 1
    t = K.typography([page(f"lf2011/{i}") for i in (8, 9, 10, 11)])
    assert t.body == pytest.approx(8.7, abs=0.2) and any(abs(s - 6.0) < 0.3 for s in t.sizes[1:])
    t = K.typography([page(f"logretta2014/{i}") for i in (12, 13, 14, 15)])
    assert t.body == 9.5 and 7.0 in t.sizes


# --- signatures -----------------------------------------------------------------------------------------------------

def test_signatures_where_the_text_ends():
    # TL 2024 idx 0: "Sindri M. Stephensen, ritstjóri" with footnotes below it
    assert K.signature(page("tl2024/9"), Typography((11.5, 10.0), 61.0), "Flóttamannaréttur") == ["Sindri M. Stephensen"]
    # TL 1985 idx 9 and idx 30, as the OCR reads them: "GuSrún Erlendsdóttir", "Arn|jótur Bjömsson"
    p83, p281 = page("tl1985/83"), page("tl1985/281")
    assert K.signature(p83, K.typography([p83]), "Stjórnvald eða dómstóll í barnaréttarmálum?") == ["Guðrún Erlendsdóttir"]
    assert K.signature(p281, K.typography([p281]), "Frá Lögfræðingafélagi íslands") == ["Arnljótur Björnsson"]


def test_every_signature_of_a_memorial_by_several_contributors():
    # TL 1985 idx 10, two obituaries: "Gunnlaugur Pétursson og" / "Hjörtur Hjartarson", then "Brynjólfur Ingólfsson"
    t = K.typography([page("tl1985/84")])
    title = "Birgir Ásgeirsson — Páll Pálmason"
    assert K.part_signatures(page("tl1985/84"), t, title) == ["Gunnlaugur Pétursson", "Hjörtur Hjartarson"]
    assert K.signature(page("tl1985/88"), t, title) == ["Brynjólfur Ingólfsson"]
    # Úlfljótur 2004-2 idx 4: three contributions, each signed; the caption "Amljótur Bjömsson," is the subject's
    t = K.typography([page(f"ulf2004/{i}") for i in (0, 1, 2, 3)])
    assert [K.part_signatures(page(f"ulf2004/{i}"), t, "Minning: Arnljótur Björnsson") for i in (0, 2, 4, 8)] == \
        [[], ["Eiríkur Tómasson"], ["Hjörleifur B. Kvaran"], ["Markús Sigurbjörnsson"]]


def test_no_signature_in_a_table_a_list_of_people_or_running_text():
    # TL 2004 idx 13: a table of cases names "Ásgeir Logi Ásgeirsson"; Lögfræðingur 2010: Lögfræðingatal; LMBL 2005:
    # "… Ingimar Ingasyni" (dative) in running text
    assert K.signature(page("tl2004/471"), Typography((10.5, 9.5), 75.0), "Um EFTA-dómstólinn") == []
    p = page("lf2010/89")
    assert K.signature(p, K.typography([p]), "Lögfræðingatal") == []
    p = page("lmbl2005/21")
    assert K.signature(p, K.typography([p]), "Heyrt og séð á hinni umtöluðu árshátíð LMFÍ") == []


def test_names_that_sign_nothing():
    # fix round 1: an advert box "Ingi Tryggvason hrl." over "Lína slf." (LMBL 2017-1 p. 21); a bibliography entry
    # "Páll Sigurðsson, Kauparéttur. Reykjavík 1988." (Rannsóknir 2003 idx 12); a table row "Jóhannes Elíasson …."
    # (Úlfljótur 1947 idx 7)
    t = Typography((10.0, 9.0), 55.0)
    assert K.signature(page("lmbl2017/20"), t, "Hinn nýi Landsréttur") == []
    assert K.item_below_signature(page("lmbl2017/20"), t, "Hinn nýi Landsréttur") is None
    assert K.signature(page("ranns2003/211"), Typography((11.0, 9.0), 75.0), "Ný lög um þjónustukaup") == []
    t = K.typography([page("ulf1947b/0"), page("ulf1947b/1")])
    assert K.signature(page("ulf1947b/9"), t, "Mót norrænna laganema og ungra kandidata") == []
    # the next item's "Name:" byline half way down a page is no signature of the article above it
    body = [Line("meginmál greinarinnar heldur hér áfram með nægum texta í línunni", 10.0, 60 + 14 * k, False, 40, 380)
            for k in range(12)]
    p = Page(body + [Line("Páll Pálsson:", 10.0, 240, False, 40, 120), Line("ÞRIÐJA GREININ", 16.0, 260, False, 40, 200)]
             + [Line("meginmál þriðju greinar heldur áfram með nægum texta í línunni", 10.0, 290 + 14 * k, False, 40,
                     380) for k in range(8)], 600.0, 420.0)
    assert K.item_below_signature(p, Typography((10.0,), 65.0), "Fyrsta greinin") is None


def test_the_signature_over_the_next_item_on_the_same_page():
    # Úlfljótur 1957 idx 14 "Frá aðalfundi" (1071.pdf): signed "Gaukur Jörundsson." over "Til laganema.", which is
    # signed "Jóhann J. Ragnarsson." at the foot of the page (the name the spreadsheet gives the first)
    e = end("ulf1957b", [0, 1], "Frá aðalfundi")
    assert (e.last, e.signed, e.unlisted) == (1, ("Gaukur Jörundsson",), (1, "Til laganema."))


def test_an_advert_with_a_web_address_is_no_unlisted_item():
    # LMBL 2020-1 idx 0: HR's "Endurmenntun fyrir lögfræðinga" follows; Lögbrú 2015 idx 2: Bifröst's "BS í
    # viðskiptalögfræði" (fix round 1: an item needs running text in the body size and no advert mark)
    e = end("lmbl2020", [5, 6], "Af vettvangi félagsins", ("Berglind Svavarsdóttir",))
    assert (e.last, e.unlisted) == (0, None)
    assert not K._has_text(page("logbru2015/44"), Typography((10.0, 7.0), 60.0))


def test_the_fixture_is_what_its_generator_makes():
    # fix round 3: the committed fixture must be the generator's output (the classifier's word lists decide what is
    # filled); needs the user's archive
    spec = importlib.util.spec_from_file_location("make_pagekind_fixture",
                                                  Path(__file__).parent / "fixtures/timarit/pages/make_pagekind_fixture.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    if not mk.ARCHIVE.exists():
        pytest.skip("the journal archive is not mounted")
    assert mk.build() == (Path(__file__).parent / "fixtures/timarit/pages/pagekind.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("printed, name", [
    ("GuSrún Erlendsdóttir", "Guðrún Erlendsdóttir"), ("Arn|jótur Bjömsson", "Arnljótur Björnsson"),
    ("Hjörleifur B. Kvaran", "Hjörleifur B. Kvaran"), ("Bjarni Benediktsson", "Bjarni Benediktsson"),
])
def test_repair_name_reads_back_the_ocrs_misreadings(printed, name):
    assert K.repair_name(printed) == name


def test_part_of_title():
    assert K.part_of_title("PÁLLPÁLMASSON", "Birgir Ásgeirsson — Páll Pálmason")
    assert K.part_of_title("RÉTTARREGLUR UM GALLA í", "Réttarreglur um galla í lausafjárkaupum")
    assert not K.part_of_title("Juristforeningen 50 ára.", "Nokkrar athugasemdir í sambandi við skjöl")
