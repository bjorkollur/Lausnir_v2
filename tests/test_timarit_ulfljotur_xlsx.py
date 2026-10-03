# tests/test_timarit_ulfljotur_xlsx.py
import openpyxl
import pytest

from engine.timarit.config import XLSX_MATCH_MARGIN, XLSX_MATCH_MIN
from engine.timarit.ulfljotur_xlsx import (
    XlsxArticle, articles_for, load_xlsx, masthead_issue, masthead_year, match_first_page, rank_first_page,
    title_similarity, volume_for_year, year_for_volume,
)

HEAD = ("Grein", "Höfundur", "Höfundur II", "Höfundur III", "Árgangur", "Tölublað", "Bls")
ROWS = [
    ("Almenn og sérstök skilyrði miskabótaréttar", "Guðmundur Sigurðsson", None, None, "76. árg", "1. tölublað", 3),
    ("Teitur Gissurarson", "Um endurupptöku einkamála", None, None, "76. árg", "1. tölublað", 71),   # swapped
    ("Bótaregla 1. mgr. 51. gr. skipulagslaga", "Arnar Heimir Lárusson", "Jörgen Már Ágústsson", None, "76. árg", "2. tölublað", 119),
    ("Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni", "Ísleifur Árnason", None, None, "1. árg", "3-4. tölublað", 173),
]


def _write(tmp_path, rows):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    p = tmp_path / "efnisyfirlit.xlsx"; wb.save(p)
    return p


@pytest.fixture
def xlsx(tmp_path):
    return _write(tmp_path, ROWS)


def test_load_parses_volume_issue_and_authors(xlsx):
    rows = load_xlsx(xlsx)
    assert rows[0].volume == 76 and rows[0].issue == "1" and rows[0].page_start == 3 and rows[0].row == 2
    assert rows[2].authors == ["Arnar Heimir Lárusson", "Jörgen Már Ágústsson"]
    assert rows[3].issue == "3-4" and rows[3].volume == 1


def test_load_fixes_swapped_title_and_author(xlsx):
    r = load_xlsx(xlsx)[1]
    assert r.title == "Um endurupptöku einkamála" and r.authors == ["Teitur Gissurarson"]


def test_load_keeps_person_named_title_when_author_has_a_family_name(tmp_path):
    # A tribute titled with a person's name, by an author with a family name (-dal, -arr): not a swap.
    p = _write(tmp_path, [
        ("Ólafur Jóhannesson", "Sigurður Líndal", None, None, "50. árg", "1. tölublað", 5),
        ("Þórður Eyjólfsson", "Ármann Snævarr", None, None, "50. árg", "2. tölublað", 9),
    ])
    rows = load_xlsx(p)
    assert (rows[0].title, rows[0].authors) == ("Ólafur Jóhannesson", ["Sigurður Líndal"])
    assert (rows[1].title, rows[1].authors) == ("Þórður Eyjólfsson", ["Ármann Snævarr"])


def test_load_drops_author_cells_that_hold_only_a_title(tmp_path):
    # Real row 415: the second author cell says just "prófessor"; it must not become an empty author.
    p = _write(tmp_path, [
        ("Þróun refsiréttar í ljósi mannréttinda", "Jónatan Þórmundsson", "prófessor", None, "50. árg", "1. tölublað", 5),
    ])
    assert load_xlsx(p)[0].authors == ["Jónatan Þórmundsson"]


def test_load_skips_rows_without_volume_issue_or_page(tmp_path):
    p = _write(tmp_path, [
        ("Grein án árgangs", "Jón Jónsson", None, None, None, "1. tölublað", 3),
        ("Grein án tölublaðs", "Jón Jónsson", None, None, "5. árg", None, 3),
        ("Grein án blaðsíðu", "Jón Jónsson", None, None, "5. árg", "1. tölublað", None),
        (None, None, None, None, None, None, None),
        ("Heil grein", "Jón Jónsson", None, None, "5. árg", "1. tölublað", 7),
    ])
    rows = load_xlsx(p)
    assert [(r.title, r.row) for r in rows] == [("Heil grein", 6)]


def test_articles_for_filters(xlsx):
    rows = load_xlsx(xlsx)
    assert [r.page_start for r in articles_for(rows, volume=76, issue="1")] == [3, 71]
    assert len(articles_for(rows, volume=76)) == 3


def test_title_similarity_tolerates_ocr_noise():
    assert title_similarity("Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni",
                            "Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni.") > 0.95
    assert title_similarity("Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni",
                            "Nokkrar hugleidingar um breytingar a erfdalöggjöfinni") > 0.85
    assert title_similarity("Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni", "Viðskiptabréfsreglur um hlutabréf") < 0.4


def test_match_first_page_uses_title_and_printed_page(xlsx):
    rows = articles_for(load_xlsx(xlsx), volume=1)
    page = "ULFLJOTUR Reykjavik, desember 1947, 4. tbl., I. árg. PRÓFESSOR ÍSLEIFUR ARNASON: Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni. T^/Teginreglur…"
    hit, score = match_first_page(page, 173, rows)
    assert hit is rows[0] and score >= 0.9
    miss, score2 = match_first_page("Alls ótengdur texti um sjávarútveg", 500, rows)
    assert miss is None and score2 < 0.5


def test_year_for_volume_follows_the_printed_mastheads():
    # R25: the mastheads read 1. árg. = 1947, 4. = 1950, 6. = 1953, 11. = 1958 ("FEBRUAR 1958, 1. TBL. XI. ÁRG."),
    # 12. = 1959 ... 16. = 1963; the skipped year lies between vol 4 and vol 6 (vol 5 is neither in the
    # archive nor in the registry; 1952 is unverified). The brief's "1958 skipped" mapping was a year off for vols 6-11.
    assert (year_for_volume(1), year_for_volume(4), year_for_volume(6)) == (1947, 1950, 1953)
    assert (year_for_volume(11), year_for_volume(12), year_for_volume(16)) == (1958, 1959, 1963)
    assert year_for_volume(54) == 2001 and year_for_volume(76) == 2023
    assert year_for_volume(5) == 1952      # unverified


# ---------------------------------------------------------------- authors (fix round 1)

def _authors(tmp_path, *cells):
    p = _write(tmp_path, [("Grein", *cells, *([None] * (3 - len(cells))), "5. árg", "1. tölublað", 7)])
    return load_xlsx(p)[0].authors


def test_authors_comma_separated_in_one_cell_are_all_kept(tmp_path):
    # real row 560: "Friðgeir Björnsson, Valtýr Sigurðsson" used to lose Valtýr
    assert _authors(tmp_path, "Friðgeir Björnsson, Valtýr Sigurðsson") == ["Friðgeir Björnsson", "Valtýr Sigurðsson"]
    assert _authors(tmp_path, "Páll Hreinsson og Eiríkur Tómasson") == ["Páll Hreinsson", "Eiríkur Tómasson"]


def test_authors_are_flattened_across_cells_and_deduplicated(tmp_path):
    assert _authors(tmp_path, "Jón Jónsson, Anna Pálsdóttir", "Anna Pálsdóttir", "Ása Karlsdóttir") == [
        "Jón Jónsson", "Anna Pálsdóttir", "Ása Karlsdóttir"]


def test_role_after_a_comma_is_not_an_author(tmp_path):
    assert _authors(tmp_path, "Jónatan Þórmundsson, prófessor") == ["Jónatan Þórmundsson"]


@pytest.mark.parametrize("cell, expected", [
    ("Zahle, Henrik", "Henrik Zahle"),
    ("Christensen, Jens Peter", "Jens Peter Christensen"),
    ("Harper, J. Ross", "J. Ross Harper"),
    ("Hollo, Erkki J.", "Erkki J. Hollo"),
    ("McCormick, Neil", "Neil McCormick"),
    ("Eyben, W.E. von", "W.E. von Eyben"),
    ("Eyben, W.E.v.", "W.E.v. Eyben"),
    ("Konow, Kate von", "Kate von Konow"),
    ("Sundström, G.O. Zacharias", "G.O. Zacharias Sundström"),
])
def test_surname_first_foreign_authors_are_reordered(tmp_path, cell, expected):
    assert _authors(tmp_path, cell) == [expected]


def test_surname_first_reordering_leaves_other_comma_cells_alone(tmp_path):
    # two full names, and a name followed by a role, are not "Surname, Given"
    assert _authors(tmp_path, "Ása Karlsdóttir, Jón Jónsson") == ["Ása Karlsdóttir", "Jón Jónsson"]
    assert _authors(tmp_path, "Jón Jónsson, hrl.") == ["Jón Jónsson"]


def test_space_before_a_period_is_collapsed(tmp_path):
    assert _authors(tmp_path, "Gerður Guðmundsdóttir", "dr . Hafsteinn Dan Kristjánsson") == [
        "Gerður Guðmundsdóttir", "Hafsteinn Dan Kristjánsson"]
    assert _authors(tmp_path, "Hafdís H . Ólafsdóttir") == ["Hafdís H. Ólafsdóttir"]
    assert _authors(tmp_path, "Halldór Kr . Þorsteinsson") == ["Halldór Kr. Þorsteinsson"]


def test_anonymus_is_no_author_but_ritstjorn_stays(tmp_path):
    assert _authors(tmp_path, "Anonymus") == []
    assert _authors(tmp_path, "Ritstjórn") == ["Ritstjórn"]


def test_swap_detection_sees_a_surname_first_author_as_a_person(tmp_path):
    # strict title "Teitur Gissurarson" + a foreign author in "Surname, Given" form: not a swap
    p = _write(tmp_path, [("Teitur Gissurarson", "Zahle, Henrik", None, None, "5. árg", "1. tölublað", 7)])
    r = load_xlsx(p)[0]
    assert (r.title, r.authors) == ("Teitur Gissurarson", ["Henrik Zahle"])


# ---------------------------------------------------------------- volume_for_year (R24)

def test_volume_for_year_is_the_inverse_of_year_for_volume():
    for v in range(1, 90):
        if v != 5:                      # vol 5 is unverified, see year_for_volume
            assert volume_for_year(year_for_volume(v)) == v
    assert (volume_for_year(1947), volume_for_year(1950), volume_for_year(1953)) == (1, 4, 6)
    assert (volume_for_year(1957), volume_for_year(1958), volume_for_year(1959)) == (10, 11, 12)   # masthead years, R25
    assert (volume_for_year(2002), volume_for_year(2010), volume_for_year(2023), volume_for_year(2024)) == (55, 63, 76, 77)


def test_volume_for_year_rejects_years_without_a_known_volume():
    with pytest.raises(ValueError):
        volume_for_year(1951)           # between vol 4 (1950) and vol 6 (1953): no volume known
    with pytest.raises(ValueError):
        volume_for_year(1946)           # before the journal began


# ---------------------------------------------------------------- masthead_issue

@pytest.mark.parametrize("text, expected", [
    ("ULFLJOTUR Reykjavik, desember 1947, 4. tbl., I. árg. PRÓFESSOR ÍSLEIFUR ARNASON:", "4"),
    ("ULFLJOTUR\nREYKJAVIK,\n1. TBL. 1962, \nXV. ÁRG.\nVALDIMAR STEFÁNSSON", "1"),
    ("REYKJAVIK, \n2. TBL. 1963 \nXVI. ÁRG.\nGIZUR BERGSTEINSSON", "2"),
    ("ULFLJOTUR Reykjavik, 3.-4. tbl. 1949, II. árg.", "3-4"),
    ("ULFLJOTUR Reykjavik, 3.\u20134. tbl. 1949, II. árg.", "3-4"),
    ("ULFLJOTUR Reykjavik, 3.\u20144. TBL. 1949, II. árg.", "3-4"),
    ("ULFLJOTUR Reykjavik, 3-4. tbl. 1949", "3-4"),
])
def test_masthead_issue_reads_the_issue_number(text, expected):
    assert masthead_issue(text) == expected


@pytest.mark.parametrize("text", [
    "Flöggun\nÞórir Skarphéðinsson, cand.jur.",                                    # no masthead
    "Athugasemd\nÍ síðasta tölublaði (4. tbl. XIII. árg.) er ýtarleg frásögn af bæjarþingsmáli",  # cites another issue
    "ÞÓRÐUR BJÖRNSSON: Um hjálp vísinda. Í 3. tbl. Úlfljóts 1987 gerir ritstjóri blaðsins að umræðuefni",
    "ULFLJOTUR\nREYKJAVIK,\nÞÓRÐUR BJÖRNSSON,\nfulltrúi sakadómara:",             # masthead without an issue
    "",
])
def test_masthead_issue_ignores_citations_and_pages_without_a_masthead(text):
    assert masthead_issue(text) is None


@pytest.mark.parametrize("text, expected", [
    ("ULFLJOTUR Reykjavik, desember 1947, 4. tbl., I. árg. PRÓFESSOR ÍSLEIFUR ARNASON:", 1947),
    ("ULFLJOTUR\nREYKJAVIK,\n1. TBL. 1962, \nXV. ÁRG.\nVALDIMAR STEFÁNSSON", 1962),
    ("ULFLJOTUR REYKJAVIK, APRIL 1958, 2. TBL. XI. ÁRG. ÓLAFUR PÁLSSON, fulltrúi borgarfógeta:", 1958),
    ("ULFLJOTUR\nREYKJAYIK,\nOKTOBER 1949,\n3. TBL., III. ÁRG.\nHÚSVITJANIR ÚLFLJÓTS II.:", 1949),
    ("ÚLFLJÓTUR\nREYKJAVÍK,\nFEBRÚAR 1950.\n1. TBL., IV. ÁRG.\nPRÓFESSOR NÍELS DUNGAL:", 1950),
    ("REYKJAVIK, \n2. TBL. 1963 \nXVI. ÁRG.\nGIZUR BERGSTEINSSON", 1963),
])
def test_masthead_year_reads_the_printed_date(text, expected):
    assert masthead_year(text) == expected


@pytest.mark.parametrize("text", [
    "Flöggun\nÞórir Skarphéðinsson, cand.jur. Hann útskrifaðist úr lagadeild H.í. í júní 2000",
    "Athugasemd\nÍ síðasta tölublaði (4. tbl. XIII. árg.) er ýtarleg frásögn af bæjarþingsmáli frá 1961",
    "ÞÓRÐUR BJÖRNSSON: Um hjálp vísinda. Í 3. tbl. Úlfljóts 1987 gerir ritstjóri blaðsins að umræðuefni",
    "ULFLJOTUR 20 ARA\nNú í febrúar eru liðin tuttugu ár, síðan fyrsta tölublað Úlfljóts kom út 1947.",
    "ULFLJOTUR\nREYKJAVIK,\nÞÓRÐUR BJÖRNSSON,\nfulltrúi sakadómara:",
    "",
])
def test_masthead_year_ignores_citations_and_pages_without_a_masthead(text):
    assert masthead_year(text) is None


# ---------------------------------------------------------------- match_first_page redesign (R23)

def _art(title, page, issue="1", volume=15):
    return XlsxArticle(title, [], volume, issue, page, 0)


FILLER = ("Þau eru og hafa verið aðalsönnunargagnið um eignarrétt manna að búfé, og þá fyrst og fremst að sauðfé, "
          "sem öldum saman var höfuðbústofn okkar Íslendinga. Sauðfjármörkin eru því vissulega merkilegt réttaratriði. ")


def test_long_exact_title_scores_full_marks_at_any_offset():
    title = "Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni"
    cand = [_art(title, 173)]
    for pad in range(0, 70, 3):
        page = FILLER[:pad] + " " + title + ". " + FILLER
        hit, score = match_first_page(page, None, cand)
        assert hit is cand[0] and score >= 0.99, (pad, score)


def test_long_title_exact_hit_must_start_a_word():
    # real 1980 511.pdf: "Um refsiábyrgð lækna" is the heading; "Ábyrgð lækna" is another row, not a part of it
    page = "VALGEIR PÁLSSON\nUM REFSIÁBYRGÐ\nLÆKNA\nEfnisyfirlit\nSkyldur lækna " + FILLER
    cands = [_art("Ábyrgð lækna", 133, "3", 33), _art("Um refsiábyrgð lækna", 83, "3", 33)]
    ranked = rank_first_page(page, None, cands)
    assert ranked[0][0] is cands[1] and ranked[0][1] >= 0.99 and ranked[1][1] < 0.97
    assert match_first_page(page, None, cands)[0] is cands[1]


def test_short_title_is_not_found_inside_longer_words():
    # old behaviour: "Lög" scored 1.0 on "löggjafinn", "Gjafsókn" on "gjafsóknarmál"
    page = "ÓLAFUR JÓHANNESSON, prófessor:\nFáein orð um búfjármörk\nMörk og löggjafinn, lögin og lögmætið. " + FILLER
    assert match_first_page(page, None, [_art("Lög", 128, "3")]) [0] is None
    page2 = "SIGURÐUR LÍNDAL:\nUm gjafsóknarmál og gjafsóknarleyfi\n" + FILLER
    hit, score = match_first_page(page2, None, [_art("Gjafsókn", 40, "2", 47)])
    assert hit is None and score < XLSX_MATCH_MIN


def test_short_title_is_found_as_a_whole_word_heading():
    page = "Flöggun\nÞórir Skarphéðinsson, cand.jur. Hann útskrifaðist úr lagadeild H.í. í júní 2000\nEFNISYFIRLIT\n1. Inngangur"
    cands = [_art("Fjármálaeftirlitið", 417, "4", 53), _art("Flöggun", 460, "4", 53), _art("Innherjaviðskipti", 430, "4", 53)]
    hit, score = match_first_page(page, None, cands)
    assert hit is cands[1] and score >= 0.95


def test_short_title_never_beats_the_real_long_title():
    page = "ÓLAFUR JÓHANNESSON, prófessor:\nFáein orð um búfjármörk\nBúfjármörk gegna mikilvægu hlutverki. Íslenzk lög og lagaboð. " + FILLER
    cands = [_art("Lög", 128, "3"), _art("Fáein orð um búfjármörk", 189, "2")]
    hit, score = match_first_page(page, None, cands)
    assert hit is cands[1] and score >= 0.99


def test_short_title_as_a_whole_word_in_body_text_is_not_a_heading():
    # modelled on 1962 666.pdf (a balance sheet) and 281.pdf: "lög" as a word, but far below the heading
    page = ("EFNAHAGSREIKNINGUR.\nEignir:\n10 stk. skikkjur ..... 1.203.69\nEignir samkvæmt eignaskrá ..... 4.450.00\n"
            "Útistandandi skuldir ..... 2.492.00\nÓseld félagsmerki ..... 1.150.00\nPeningar í sjóði ..... 6.347.96\n"
            "Séreign Úlfljóts ..... 23.921.57\nHagnaður á árinu ..... 31.970.65\nLög. Það er öllum kunnugt")
    assert match_first_page(page, None, [_art("Lög", 128, "3")])[0] is None
    # the same word in the heading line does count
    assert match_first_page("Lög\nPáll Vídalín:\nUm lögin. " + FILLER, None, [_art("Lög", 128, "3")])[0] is not None


def test_short_title_heading_beats_a_long_title_mentioned_further_down():
    # modelled on 2000 694.pdf: "Flöggun" is the heading, "Fjármálaeftirlitið" turns up in the contents list
    page = ("Flöggun\nÞórir Skarphéðinsson, cand.jur. Hann útskrifaðist úr lagadeild H.í. í júní 2000\nEFNISYFIRLIT\n"
            "3.2 Flöggun gagnvart verðbréfaþingi\n3.3 Flöggun gagnvart Fjármálaeftirlitið\n4. Niðurstöður")
    cands = [_art("Fjármálaeftirlitið", 415, "3", 53), _art("Flöggun", 460, "3", 53)]
    assert [round(s, 2) for _, s in rank_first_page(page, None, cands)] == [1.0, 1.0]
    assert match_first_page(page, None, cands)[0] is cands[1]


def test_long_title_heading_beats_a_short_word_in_the_first_lines_of_text():
    # modelled on 1976 502.pdf: the long title comes first, "líknardrápi" is the first body sentence
    page = ("ÖRN BJARNASON, skólayfirlæknir:\nEuthanasia frá sjónarhóli læknis\nÍ því sem hér fer á eftir mun ég "
            "leitast við að lýsa hugtakinu Líknardráp eins og það er skilið. " + FILLER)
    cands = [_art("Líknardráp", 501, "1", 29), _art("Euthanasia frá sjónarhóli læknis", 502, "1", 29)]
    assert [round(s, 2) for _, s in rank_first_page(page, None, cands)] == [1.0, 1.0]
    assert match_first_page(page, None, cands)[0] is cands[1]


def test_unrelated_page_stays_below_the_cutoff_even_with_a_matching_printed_page():
    page = "EFNAHAGSREIKNINGUR.\nEignir:\n10 stk. skikkjur ..... 1.203.69\nEignir samkvæmt eignaskrá ..... 4.450.00\nPeningar í sjóði ... 6.347.96"
    cands = [_art("Lagasmíð", 519, "3", 48), _art("Um ákæruvaldið", 548, "1")]
    hit, score = match_first_page(page, 519, cands)
    assert hit is None and score < XLSX_MATCH_MIN
    hit, score = match_first_page(page, 548, cands)
    assert hit is None and score < XLSX_MATCH_MIN


def test_printed_page_bonus_only_counts_when_the_title_already_clears_the_cutoff():
    title = "Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni"
    cand = [_art(title, 173)]
    near = "Nokkrar hugleidmgar um brevtmgar á erfðalöggjöfmni. " + FILLER      # OCR-noisy (i->m, y->v), clears the cutoff
    s_plain = match_first_page(near, None, cand)[1]
    s_bonus = match_first_page(near, 173, cand)[1]
    assert XLSX_MATCH_MIN <= s_plain < 0.97 and s_plain < s_bonus <= 1.0


def test_only_the_heading_zone_counts():
    title = "Nokkrar hugleiðingar um breytingar á erfðalöggjöfinni"
    page = FILLER * 6 + title            # the title sits far below the heading
    assert match_first_page(page, None, [_art(title, 173)])[0] is None


def test_masthead_does_not_count_as_heading_text():
    # the title is the masthead's own words; only the text after the masthead is searched
    page = "ULFLJOTUR Reykjavik, desember 1947, 4. tbl., I. árg. " + FILLER
    assert match_first_page(page, None, [_art("Ulfljotur Reykjavik desember 1947", 3, "4", 1)])[0] is None


def test_equal_scores_prefer_the_longer_title_and_that_is_not_an_ambiguity():
    page = "MIKE MARLIES:\nLagamál. Slagorð\n" + FILLER
    cands = [_art("Lagamál", 620, "2", 40), _art("Lagamál. Slagorð", 672, "3", 40)]
    ranked = rank_first_page(page, None, cands)
    assert [a.title for a, _ in ranked][:2] == ["Lagamál. Slagorð", "Lagamál"]
    hit, score = match_first_page(page, None, cands)
    assert hit is cands[1] and score >= 0.99


def test_the_shorter_title_wins_when_the_page_carries_only_that_title():
    page = "ÓLAFUR LÁRUSSON:\nLagamál\n" + FILLER
    cands = [_art("Lagamál", 620, "2", 40), _art("Lagamál. Slagorð", 672, "3", 40)]
    assert match_first_page(page, None, cands)[0] is cands[0]


def test_same_title_twice_is_ambiguous_unless_the_printed_page_decides():
    page = "ÞORSTEINN HJALTASON:\nAðfararorð\n" + FILLER
    a, b = _art("Aðfararorð", 3, "1", 40), _art("Aðfararorð", 3, "2", 40)
    c = _art("Aðfararorð", 55, "3", 40)
    hit, score = match_first_page(page, None, [a, c])
    assert hit is None and score >= 0.99
    # printed page 55 belongs to exactly one of the two, so that one is the answer
    assert match_first_page(page, 55, [a, c])[0] is c
    # both rows claim the printed page: still ambiguous
    assert match_first_page(page, 3, [a, b])[0] is None


def test_a_runner_up_within_the_margin_makes_the_match_ambiguous():
    assert 0 < XLSX_MATCH_MARGIN < 0.1
    page = "Meðferð persónuupplýsinga hjá hinu opinbera\n" + FILLER
    cands = [_art("Meðferð persónuupplýsinga hjá hinu opinbera", 10), _art("Meðferð persónuupplýsinga hjá hinu opinbera.", 20)]
    assert match_first_page(page, None, cands)[0] is None


def test_series_running_head_does_not_fool_the_matcher():
    # modelled on 1997 392.pdf: "Rökstólar" is the series name, the title follows
    page = ("Rökstólar\nMeðferð upplýsinga í fjölmiðlum\nÍslensku tölvulögin eiga sér hliðstæðu í sambærilegri löggjöf "
            "margra annarra vestrænna ríkja. Hvatinn að setningu laga um skráningu og meðferð persónuupplýsinga")
    cands = [_art("Um meðferð persónuupplýsinga", 300, "2", 50), _art("Meðferð upplýsinga í fjölmiðlum", 392, "3", 50)]
    hit, score = match_first_page(page, None, cands)
    assert hit is cands[1] and score >= 0.99


def test_rank_first_page_is_sorted_best_first_and_lists_scored_candidates():
    page = "Flöggun\nÞórir Skarphéðinsson, cand.jur.\n" + FILLER
    cands = [_art("Fjármálaeftirlitið", 417), _art("Flöggun", 460)]
    ranked = rank_first_page(page, None, cands)
    assert ranked[0][0] is cands[1] and ranked[0][1] >= 0.95
    assert [s for _, s in ranked] == sorted((s for _, s in ranked), reverse=True)
    assert all(s > 0 for _, s in ranked)
