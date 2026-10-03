import pytest

from engine.timarit.pagemap import PATTERNS, PageMap, build_page_map, fit_offset, read_page_numbers


def _pages(nums, header=""):
    return [f"{header}{n}\nTexti síðu.\nMeira." if n is not None else "Kápa án tölu" for n in nums]


def test_read_bare_number_first_or_last_line():
    pages = ["7\nTexti", "Texti\n8", "Enginn\ntala hér", "  9  \nTexti"]
    assert read_page_numbers(pages, PATTERNS["default"]) == [7, 8, None, 9]


def test_fit_offset_takes_the_majority_and_ignores_outliers():
    offset, runner_up = fit_offset([None, 7, 8, 9, 100, 11])
    assert offset == 6          # idx 1→7, 2→8, 3→9, 5→11 all offset 6
    assert fit_offset([None, None, None]) == (None, 0.0)


def test_fit_offset_refuses_when_fewer_than_three_agree():
    assert fit_offset([None, 7, None, 22]) == (None, 0.0)


def test_build_map_infers_missing_pages_and_scores_quality():
    pm = build_page_map(_pages([None, 2, 3, None, 5, 6]), "timarit_logfraedinga")
    assert pm.offset == 1
    assert pm.printed == [1, 2, 3, 4, 5, 6]
    assert pm.read == [False, True, True, False, True, True]
    assert abs(pm.quality - 4 / 6) < 1e-9
    assert pm.pdf_index_for(5) == 4 and pm.pdf_index_for(99) is None


def test_quality_counts_consistent_pages_only():
    pm = build_page_map(_pages([2, 3, 4, 40, 6]), "default")
    assert pm.offset == 2 and abs(pm.quality - 4 / 5) < 1e-9
    assert pm.printed[3] == 5           # outlier replaced by the inferred value


def test_unmappable_document_has_no_offset_and_zero_quality():
    pm = build_page_map(["Kápa", "Auglýsing", "Texti"], "default")
    assert pm.offset is None and pm.quality == 0.0 and pm.printed == [None, None, None]


def test_source_specific_patterns():
    assert read_page_numbers(["Úlfljótur 173\nFrá ritstjóra"], PATTERNS["ulfljotur"]) == [173]
    assert read_page_numbers(["a 9\nÚrræði lánveitanda"], PATTERNS["logretta"]) == [9]
    assert read_page_numbers(["3 / LÖGBRÚ\nGREIN BLS."], PATTERNS["logbru"]) == [3]
    assert read_page_numbers(["81 Samræmi laga og stjórnarskrár\nSvandís"], PATTERNS["stjornmal_stjornsysla"]) == [81]


# IMPORTANT 2: Two-pass strategy — non-LEADING patterns first, then LEADING only if no match
def test_two_pass_strategy_strong_footer_beats_weak_header():
    # Weak header "2010 var lögum breytt" should not match (2010 ≥ 1800, ignored as year)
    # Strong bare footer "45" should be found
    pages = ["2010 var lögum breytt\ntexti\n45"]
    result = read_page_numbers(pages, PATTERNS["timarit_logfraedinga"])
    assert result == [45]


def test_two_pass_strategy_constant_header_bare_footers():
    # Twenty pages with constant header and incrementing bare footers
    pages = [f"2010 Tímarit lögfræðinga\nTexti síðu\n{i+30}" for i in range(20)]
    result = read_page_numbers(pages, PATTERNS["timarit_logfraedinga"])
    expected = list(range(30, 50))
    assert result == expected
    pm = build_page_map(pages, "timarit_logfraedinga")
    assert pm.offset == 30
    assert abs(pm.quality - 1.0) < 1e-9


# IMPORTANT 1: Numbering restart detection via runner-up ambiguity
def test_ambiguous_numbering_restart_12_plus_8():
    # Two articles: first 12 pages (1-12), second 8 pages (1-8)
    # Offsets: indices 0-11 vote for offset 1, indices 12-19 vote for offset -11
    # Winner: offset 1 with 12 votes; runner-up: offset -11 with 8 votes
    # Runner-up share: 8/12 = 0.667 > 0.25 (PAGE_MAP_RESTART_RATIO) → ambiguous
    pages = _pages(list(range(1, 13)) + list(range(1, 9)))
    pm = build_page_map(pages, "default")
    assert pm.offset is None
    assert pm.quality == 0.0
    assert pm.printed == [None] * 20
    assert pm.read == [False] * 20
    assert pm.runner_up_share > 0.25


def test_ambiguous_numbering_tie_10_plus_10():
    # Perfect tie: 10 pages with offset 0, 10 pages with offset -10
    pages = _pages(list(range(1, 11)) + list(range(1, 11)))
    pm = build_page_map(pages, "default")
    assert pm.offset is None
    assert pm.quality == 0.0


def test_strong_document_with_small_insert():
    # 290 pages (indices 0-289) reading 1-290 (offset 1)
    # 3 pages with bad reads (indices 100-102 reading 1-3, offset -99)
    # Winner: offset 1 with 287 votes; runner-up: offset -99 with 3 votes
    # Runner-up share: 3/287 ≈ 0.010 < 0.25 → keep the map
    read_list = list(range(1, 291))
    read_list[100] = 1
    read_list[101] = 2
    read_list[102] = 3
    offset, runner_up = fit_offset(read_list)
    assert offset == 1
    assert runner_up < 0.25  # 3/287 < 0.25
    pm = build_page_map([f"{n}\nTexti" if n else "Kápa" for n in read_list], "default")
    assert pm.offset == 1
    assert pm.quality > 0.9  # Most pages are consistent


# IMPORTANT 3: pdf_index_for edge cases
def test_pdf_index_for_rejects_printed_page_less_than_one():
    # Any printed page < 1 should return None
    pm = build_page_map([f"{i}\nTexti" for i in range(1, 6)], "default")
    # This creates offset 1 (indices 0-4 reading 1-5)
    assert pm.pdf_index_for(0) is None
    assert pm.pdf_index_for(-1) is None
    assert pm.pdf_index_for(1) == 0


def test_pdf_index_for_rejects_none_pages():
    # pdf_index_for should return None when the target page has no number.
    # Task 17a adapted this test: it was written for the single-offset lookup (idx = printed - offset) with a fixture whose
    # printed list (3 on index 2) disagrees with its offset (2). pdf_index_for now searches the printed list itself (R32),
    # so the fixture is made consistent (offset 1) and the intent kept: a page without a number is never returned.
    pm = PageMap(offset=1, printed=[None, None, 3, 4, 5], read=[False, False, True, True, True], quality=0.6, runner_up_share=0.0)
    assert pm.pdf_index_for(2) is None  # index 1 would carry 2, but printed[1] is None
    assert pm.pdf_index_for(4) == 3     # printed[3] is 4
    assert pm.pdf_index_for(0) is None  # printed_page < 1


# --- Task 17a (R32): piecewise map ---------------------------------------------------------------------------

def _volume(*blocks):
    """Printed numbers per PDF page: an int block is a run of numbered pages, a None block that many unnumbered pages."""
    out = []
    for b in blocks:
        out += [None] * b if isinstance(b, int) else list(b)
    return out


def test_piecewise_map_continues_over_unnumbered_hefti_covers():
    # TL annual volume (TL 1985 shape): every hefti's cover and TOC pages carry no number and are not counted, so the
    # offset steps down at each hefti (-3, -7, -10) while the printed numbers keep increasing over the whole volume.
    # The single-offset fit called this ambiguous (runner-up 20 >= 0.25 x 30) and located nothing.
    nums = _volume(4, range(1, 21), 4, range(21, 51), 3, range(51, 61))
    pm = build_page_map(_pages(nums), "timarit_logfraedinga")
    assert pm.as_dict()["segments"] == [[4, 23, -3], [28, 57, -7], [61, 70, -10]]
    assert pm.offset == -7                                   # the largest segment's offset (as_dict/compat)
    assert pm.printed == _volume(4, range(1, 21), 4, range(21, 51), 3, range(51, 61))  # covers between hefti: None
    assert pm.read == [n is not None for n in nums]
    assert abs(pm.quality - 60 / 71) < 1e-9 and pm.runner_up_share == 0.0
    assert pm.pdf_index_for(1) == 4 and pm.pdf_index_for(20) == 23
    assert pm.pdf_index_for(21) == 28 and pm.pdf_index_for(51) == 61 and pm.pdf_index_for(61) is None


def test_piecewise_map_numbers_unread_pages_inside_a_segment_and_extends_the_outer_ones():
    # an unread page inside a segment (an article's first page) is inferred; so are the pages before the first and
    # after the last segment, as the single-offset map did; the two pages between the segments stay None
    nums = [None, None, 1, 2, None, 4, 5, None, None, 6, None, 8, 9, None]
    pm = build_page_map(_pages(nums), "default")
    assert [(s.first, s.last, s.offset) for s in pm.segments] == [(2, 6, -1), (9, 12, -3)]
    assert pm.printed == [None, None, 1, 2, 3, 4, 5, None, None, 6, 7, 8, 9, 10]
    assert pm.offset == -1 and abs(pm.quality - 7 / 14) < 1e-9


def test_a_genuine_restart_is_still_ambiguous_in_a_multi_segment_volume():
    # two hefti that continue (1-30, 31-60) and a third block that starts again at 1: printed 1-20 would appear
    # twice, and 20 >= 0.25 x 30 (R16) -> no map
    nums = _volume(range(1, 31), 3, range(31, 61), range(1, 21))
    pm = build_page_map(_pages(nums), "default")
    assert pm.offset is None and pm.quality == 0.0
    assert pm.printed == [None] * len(nums) and pm.read == [False] * len(nums)
    assert abs(pm.runner_up_share - 20 / 30) < 1e-9


def test_numbered_back_matter_that_the_next_hefti_numbers_again_is_dropped():
    # TL 2000: the advertisement pages at the end of 3. hefti are numbered 259-264 and 4. hefti starts again at 259.
    # A short duplicate (6 < 0.25 x 40) is not a restart: the map keeps both hefti and the ad pages get None.
    # Fix round 1 (review IMPORTANT 1): the numbers both blocks print (41-46) are contested and stay unmapped on the
    # next hefti's pages too, so no article is placed on the wrong one of two pages that print the same number.
    # 40 is contested as well: the unread page before the next hefti's first numbered page could be its unnumbered
    # first page (R7) — exactly the case where that hefti's first footer is unread (next test, page 48) — and the
    # numbers alone cannot tell the two apart; an unmapped number sends an article to review, a wrong one does not.
    nums = _volume(range(1, 41), range(41, 47), 2, range(41, 81))
    pm = build_page_map(_pages(nums), "default")
    assert [(s.first, s.last, s.offset) for s in pm.segments] == [(0, 39, 1), (48, 87, -7)]
    assert pm.contested == list(range(40, 47)) and pm.as_dict()["contested"] == list(range(40, 47))
    assert all(pm.pdf_index_for(p) is None for p in range(40, 47))
    assert pm.pdf_index_for(39) == 38 and pm.pdf_index_for(47) == 54
    assert pm.printed[39:54] == [None] * 15
    assert pm.read[39] and abs(pm.quality - 80 / 88) < 1e-9           # reads and quality are not changed
    assert abs(pm.runner_up_share - 6 / 40) < 1e-9


@pytest.mark.parametrize("unread", [48, 49, 51, 53])
def test_a_contested_number_is_not_given_to_the_back_matter_when_a_read_of_the_next_hefti_is_missing(unread):
    # Review IMPORTANT 1: with one footer of the next hefti unread (its first page 48, or a page inside it), that hefti's
    # first pages have fewer reads than the numbered adverts, so the chain keeps the adverts. Without the fix 41 resolved
    # silently to the advert page 40. The hefti's reads on (nearly) consecutive pages are a second block: the numbers it
    # prints, and the one its unnumbered first page would carry, are contested and unmapped.
    nums = _volume(range(1, 41), range(41, 47), 2, range(41, 81))
    nums[unread] = None
    pm = build_page_map(_pages(nums), "default")
    assert all(pm.pdf_index_for(p) is None for p in range(41, 47))
    assert pm.pdf_index_for(47) == 54 and pm.pdf_index_for(80) == 87 and pm.offset is not None


def test_scattered_misreads_that_share_an_offset_do_not_contest_real_pages():
    # TL 1951_01 / 1951_02: three OCR misreads with one offset (e.g. 61, 64, 65 on pages 58, 61, 62 between correct
    # reads; 160, 161 … 168) overlap real page numbers. They are not on consecutive pages, so nothing is unmapped.
    nums = list(range(1, 61))
    nums[10], nums[30], nums[50] = 5, 25, 45            # offset -5 three times, far apart
    pm = build_page_map(_pages(nums), "default")
    assert pm.contested == [] and pm.printed == list(range(1, 61))
    nums = list(range(1, 61))
    nums[20], nums[21], nums[40] = 60, 61, 80           # two adjacent misreads and a third one far away
    pm = build_page_map(_pages(nums), "default")
    assert pm.contested == [] and pm.printed == list(range(1, 61))


def test_an_inserted_block_that_prints_numbers_again_contests_them():
    # three inserted pages printing 1-3 inside a 60-page document: too small to be a restart (R16), but 1-3 are now
    # printed twice, so neither copy is chosen
    nums = list(range(1, 61))
    nums[30:33] = [1, 2, 3]
    pm = build_page_map(_pages(nums), "default")
    assert pm.offset == 1 and pm.contested == [1, 2, 3]
    assert pm.pdf_index_for(1) is None and pm.pdf_index_for(4) == 3 and pm.printed[30:33] == [31, 32, 33]


def test_misreads_far_apart_across_unread_pages_are_not_a_block():
    # three misreads with one offset (5, 13, 21 on pages 22, 30, 38) with only unread pages between them
    nums = list(range(1, 21)) + [None] * 25 + list(range(46, 61))
    nums[22], nums[30], nums[38] = 5, 13, 21
    pm = build_page_map(_pages(nums), "default")
    assert pm.contested == [] and pm.pdf_index_for(5) == 4 and pm.pdf_index_for(20) == 19


def test_one_page_that_prints_the_first_number_of_the_next_hefti_contests_it():
    # TL 1978: the last page of 2. hefti ("MÁLÞING UM SAMNINGU DÓMA … Ólafur St. Sigurðsson.") prints 97, and 3. hefti
    # starts again at 97 after the covers. One read is enough at a hefti boundary: it follows the first hefti's own
    # offset into the second hefti's numbers, so it is a second copy of the number, not a scattered misread.
    nums = list(range(1, 32)) + [None] * 10 + list(range(31, 61))
    pm = build_page_map(_pages(nums), "default")
    assert [(s.first, s.last, s.offset) for s in pm.segments] == [(0, 29, 1), (41, 70, -10)]
    assert pm.contested == [30, 31]                      # 31 twice; 30: the unread page 40 may be 3. hefti's first (R7)
    assert pm.pdf_index_for(31) is None and pm.pdf_index_for(32) == 42 and pm.pdf_index_for(29) == 28


def test_a_compatible_run_shorter_than_min_agreeing_is_not_a_segment():
    # two pages that would fit a second offset are misreads, not a hefti
    nums = _volume(range(1, 11), 2, [30, 31], 2, range(13, 23))
    pm = build_page_map(_pages(nums), "default")
    assert [(s.first, s.last, s.offset) for s in pm.segments] == [(0, 9, 1), (16, 25, -3)]
    assert pm.printed[12:14] == [None, None]


def test_pdf_index_for_searches_the_per_page_printed_list():
    pm = PageMap(offset=1, printed=[None, None, 3, 4, 10, 11], read=[False, False, True, True, True, True], quality=0.67)
    assert pm.pdf_index_for(4) == 3 and pm.pdf_index_for(10) == 4
    assert pm.pdf_index_for(5) is None                      # a number no page carries (between segments)
    assert pm.pdf_index_for(2) is None and pm.pdf_index_for(0) is None


# --- Task 17a (R32): per-journal patterns, from real lines --------------------------------------------------------

def test_a_year_is_never_a_page_number():
    # Lögmannablaðið 2014 01 p0 prints "1" / "2014" on top; a page whose edge line is only a year has no number
    assert read_page_numbers(["2014\nTexti\n15"], PATTERNS["default"]) == [15]
    assert read_page_numbers(["2014\nTexti\nMeira"], PATTERNS["default"]) == [None]
    assert read_page_numbers(["Lögfræðingur 2010\nTexti"], PATTERNS["logfraedingur"]) == [None]


def test_logfraedingur_running_footer_and_header():
    pat = PATTERNS["logfraedingur"]
    assert read_page_numbers(["Efnisyfirlit \nFylgt úr hlaði \nð2 \nLögfræðingur 3"], pat) == [3]            # 2006
    assert read_page_numbers(["Prentvinnsla: Ásprent \n2 Lögfræðingur"], pat) == [2]                         # 2008
    assert read_page_numbers(["Lögfræðingur 11\ntors may also refuse to extend the existing line of credit"], pat) == [11]  # 2010


def test_logretta_letter_and_number_on_separate_lines():
    pat = PATTERNS["logretta"]
    # Lögrétta 2010 1.tbl p0: the article letter and the page on two lines; the outline at the bottom ends in "21"
    page = "a\n9\nÚrræði lánveitanda við \nvanefndir lántaka I \n3.5. Afleiðingar ólögmætrar gjaldfellingar o.fl.\t\n19\n4. Nokkur lokaorð\t\n21"
    assert read_page_numbers([page], pat) == [9]
    assert read_page_numbers(["f\n113\n147\n149"], pat) == [113]                                            # 2007 2.tbl
    assert read_page_numbers(["TÍMARIT LÖGRÉTTU \n4\n12 Ásgeir Einarsson og Jóna Björk Helgadóttir"], pat) == [4]  # 2019 3.tbl
    assert read_page_numbers(["a 9\nÚrræði lánveitanda"], pat) == [9]                                       # one line, as before


def test_logmannabladid_running_header_with_the_issue_number():
    pat = PATTERNS["logmannabladid"]
    assert read_page_numbers(["LÖGMANNABLAÐIÐ TBL  01/20    13\nFRÁ HÁTÍÐARSAMKOMU SEM HALDIN VAR  "], pat) == [13]
    assert read_page_numbers(["10\t LÖGMANNABLAÐIÐ TBL  01/20\nÁ árunum 1930–1933 flutti Jónas"], pat) == [10]
    assert read_page_numbers(["LÖGMANNABLAÐIÐ TBL 01/22 9\nstarfandi lögmanna lækkað"], pat) == [9]
    assert read_page_numbers(["LÖGMANNABLAÐIÐ TBL 01/14\t\n15\nJóna Björk Helgadóttir varaformaður "], pat) == [15]     # 2014
    assert read_page_numbers(["LÖGMANNABLAÐIÐ – 2 / 2006 >\n5\nÞar sem tryggingar \nsnúast um fólk"], pat) == [5]  # 2006
    assert read_page_numbers(["kannski aðeins oftar\nábyrgðina svona.\n12\n< LÖGMANNABLAÐIÐ – 2 / 2006"], pat) == [12]
    assert read_page_numbers(["2       <    LÖGMANNABLAÐIÐ – 4 / 2008\nEfnisyfirlit "], pat) == [2]
    assert read_page_numbers(["Bækurnar voru í boði\nLÖGMANNABLAÐIÐ – 4 / 2008 > 7"], pat) == [7]
    # the issue number in the header is not a page; the cover's "1. TBL. 26. ÁRG. 2020" has none
    assert read_page_numbers(["LÖGMANNABLAÐIÐ TBL 01/14\numfjöllun"], pat) == [None]
    assert read_page_numbers(["< LÖGMANNABLAÐIÐ – 2 / 2006\nTexti"], pat) == [None]
    assert read_page_numbers(["VIÐTAL VIÐ MARKÚS \nSIGURBJÖRNSSON\nÚTGEFIÐ AF LÖGMANNAFÉLAGI ÍSLANDS\t\n1. TBL. 26. ÁRG. 2020"], pat) == [None]


def test_ulfljotur_footer_with_a_dash_and_ocr_variants_of_the_name():
    pat = PATTERNS["ulfljotur"]
    assert read_page_numbers(["Umboðsmenn \nhenta í Danmörku\nÚLFLJÓTUR — 97"], pat) == [97]                # 1966
    assert read_page_numbers(["ræða ágrip um meginatriði.\n98 — ÚLFLJÓTUR"], pat) == [98]
    assert read_page_numbers(["Norsku hegningarlögin hafa ávallt\nÚLFLJÓTUR ---- 167"], pat) == [167]      # 1975
    assert read_page_numbers(["Vilhjálmur H. Vilhjálmsson.\n168----ÚLFUÓTUR"], pat) == [168]
    assert read_page_numbers(["bann við framsali manna\n148 — ULFUOTUR"], pat) == [148]
    assert read_page_numbers(["Moskvu.\nÚLFLJÓTUR - 185"], pat) == [185]                                     # 1977
    assert read_page_numbers([" Úlfljótur    257\nÚtgefandi: ORATOR"], pat) == [257]              # 2005
    assert read_page_numbers(["Úlfljótur 173\nFrá ritstjóra"], pat) == [173]                                 # as before
    # a sentence that mentions the journal is not a footer
    assert read_page_numbers(["Texti\nÚLFLJÓTUR — 13 og fleira"], pat) == [None]


def test_tl_old_issues_number_next_to_the_running_header():
    pat = PATTERNS["timarit_logfraedinga"]
    # TL 1960_01: number, then the running header, at the foot of the page
    assert read_page_numbers(["að missa tímaritið úr höndum félagsins\nþó í meirihluta.\n2\nTímarit lögfrœöinga"], pat) == [2]
    assert read_page_numbers(["fyrirsögn\ntexti\nTímarit lögfræðinga\n13"], pat) == [13]
    # TL 1953_02: the article's running title, then the number, at the top
    assert read_page_numbers(["Okur og slcylcl brot.\n67\nið ógildui', ef bersýnilegt ósamræmi"], pat) == [67]


def test_rannsoknir_running_title_then_number():
    pat = PATTERNS["rannsoknir_lagadeild"]
    assert read_page_numbers(["Nokkur orð um eignarhugtakið í Landabrigðisþætti Grágásar \n11 \nkomið til. "], pat) == [11]
    assert read_page_numbers(["12 \nAagot V. Óskarsdóttir  \nSigurðsson: Aflvaki Jónsbókar."], pat) == [12]
