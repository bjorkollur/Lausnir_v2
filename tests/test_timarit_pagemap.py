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
    # pdf_index_for should return None when the target page has no number
    pm = PageMap(offset=2, printed=[None, None, 3, 4, 5], read=[False, False, True, True, True], quality=0.6, runner_up_share=0.0)
    assert pm.pdf_index_for(3) is None  # idx would be 1, but printed[1] is None
    assert pm.pdf_index_for(4) == 2     # idx is 2, printed[2] is 4, valid
    assert pm.pdf_index_for(0) is None  # printed_page < 1
