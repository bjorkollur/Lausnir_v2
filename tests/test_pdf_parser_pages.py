import fitz
import pytest

from engine.processors import pdf_parser
from engine.processors.pdf_parser import _join_pages_with_offsets, parse_pdf, parse_pdf_pages


def _pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for t in pages:
        p = doc.new_page()
        p.insert_text((72, 72), t, fontsize=11)
    return doc.tobytes()


def test_offsets_point_at_each_pages_first_char():
    body, offs = parse_pdf_pages(_pdf(["Fyrsta síða.", "Önnur síða.", "Þriðja síða."]))
    assert [i for i, _ in offs] == [0, 1, 2]
    for (i, c), word in zip(offs, ("Fyrsta", "Önnur", "Þriðja")):
        assert body[c:].startswith(word), (i, body[c:c + 20])


def test_blank_page_contributes_no_offset():
    body, offs = parse_pdf_pages(_pdf(["Fyrsta.", "", "Þriðja."]))
    assert [i for i, _ in offs] == [0, 2]


def test_page_filter_runs_before_join_and_offsets_follow():
    def drop_first_line(pages):
        return [p.split("\n", 1)[1] if "\n" in p else p for p in pages]

    body, offs = parse_pdf_pages(_pdf(["HAUS\nEfni eitt.", "HAUS\nEfni tvö."]), page_filter=drop_first_line)
    assert "HAUS" not in body and body[offs[1][1]:].startswith("Efni tvö")


def test_parse_pdf_unchanged():
    pdf = _pdf(["Fyrsta síða.", "Önnur síða."])
    assert parse_pdf(pdf) == parse_pdf_pages(pdf)[0]


def test_page_filter_that_blanks_a_page_drops_its_offset():
    def blank_second(pages):
        return [pages[0], "", pages[2]]

    body, offs = parse_pdf_pages(_pdf(["Eitt.", "Tvö.", "Þrjú."]), page_filter=blank_second)
    assert [i for i, _ in offs] == [0, 2]
    assert "Tvö" not in body and body[offs[1][1]:].startswith("Þrjú")


def test_page_filter_must_preserve_page_count():
    with pytest.raises(ValueError):
        parse_pdf_pages(_pdf(["Eitt.", "Tvö."]), page_filter=lambda pages: pages[:1])


def test_join_with_offsets_matches_join_pages_except_for_page_break_hyphen():
    from engine.processors.pdf_parser import _join_pages

    pages = ["Fyrsta síða.", "önnur heldur áfram.", "3. Ný málsgrein hefst.", "Þriðja."]
    joined, starts = _join_pages_with_offsets(pages)
    assert joined == _join_pages(pages)
    assert [joined[s:].startswith(p[:6]) for s, p in zip(starts, pages)] == [True] * 4
    assert _join_pages_with_offsets([]) == ("", [])


def test_word_broken_across_page_break_is_joined():
    joined, starts = _join_pages_with_offsets(["Lögin fjalla um breyting-", "ar á skattalögum."])
    assert joined == "Lögin fjalla um breytingar á skattalögum."
    assert joined[starts[1]:] == "ar á skattalögum."


def test_trailing_hyphen_before_capital_keeps_its_break():
    joined, _ = _join_pages_with_offsets(["Reykjavíkur-", "Borg er höfuðborg."])
    assert joined == "Reykjavíkur-\n\nBorg er höfuðborg."


def test_footnotes_are_appended_after_body(monkeypatch):
    monkeypatch.setattr(
        pdf_parser, "_extract_pages",
        lambda content, **kw: ([(0, "Texti[^1] hér."), (1, "Meiri texti.")], [(1, "Skýring eitt.")]),
    )
    body, offs = pdf_parser.parse_pdf_pages(b"", footnotes=True)
    assert body.endswith("[^1]: Skýring eitt.")
    assert body[offs[1][1]:].startswith("Meiri texti")


def test_footnotes_kept_as_plain_list_when_numbering_restarts(monkeypatch):
    page = " ".join("orð[^1] orð[^1]" for _ in range(12))
    monkeypatch.setattr(
        pdf_parser, "_extract_pages",
        lambda content, **kw: ([(0, page)], [(1, "Fyrsta skýring."), (1, "Önnur skýring.")]),
    )
    body, _ = pdf_parser.parse_pdf_pages(b"", footnotes=True)
    assert "Fyrsta skýring." in body and "Önnur skýring." in body


# R18: the cross-page join applies the same hyphen rules as extract.dehyphenate.
_CROSS_PAGE = [
    # (page ends with, next page starts with, joined text, where the next page starts)
    ("Um skyldur ríkis-", "og sveitarfélaga.", "ríkis- og sveitarfélaga.", "og sveitarfélaga."),
    ("Um skyldur lög-", "eða reglugerða.", "lög- eða reglugerða.", "eða reglugerða."),
    ("Um inn-", "og útflutning.", "inn- og útflutning.", "og útflutning."),
    ("Um sveitar-", "né ríki.", "sveitar- né ríki.", "né ríki."),
    ("Hann sagði -", "og fór heim.", "sagði - og fór heim.", "og fór heim."),
    ("Dómur EFTA-", "dómstólsins.", "EFTA-dómstólsins.", "dómstólsins."),
    ("Um EES-", "samninginn.", "EES-samninginn.", "samninginn."),
    ("Um 3ja-", "manna nefnd.", "3ja-manna nefnd.", "manna nefnd."),
    ("Í 10-", "ára fangelsi.", "10-ára fangelsi.", "ára fangelsi."),
    ("Sjá nr. 42-", "manna dóm.", "nr. 42-manna dóm.", "manna dóm."),
    ("Um breyting-", "ar á lögum.", "breytingar á lögum.", "ar á lögum."),
    # (d): normal separators, hyphen kept
    ("Um Reykjavíkur-", "Borg og nágrenni.", "Reykjavíkur-\n\nBorg og nágrenni.", "Borg og nágrenni."),
    ("Sjá nr. 42-", "2000 og síðar.", "nr. 42-\n\n2000 og síðar.", "2000 og síðar."),
    ("Hann sagði -", "fór heim.", "sagði -\nfór heim.", "fór heim."),
]


@pytest.mark.parametrize("end, start, joined, nxt", _CROSS_PAGE)
def test_cross_page_hyphen_rules_through_parse_pdf_pages(end, start, joined, nxt):
    body, offs = parse_pdf_pages(_pdf([end, start]))
    assert joined in body, body
    assert [i for i, _ in offs] == [0, 1]
    assert body[offs[1][1]:].startswith(nxt), (body, offs)


@pytest.mark.parametrize("end, start, joined, nxt", _CROSS_PAGE)
def test_cross_page_hyphen_rules_in_join_with_offsets(end, start, joined, nxt):
    body, starts = _join_pages_with_offsets(["Fyrst. " + end, start])
    assert joined in body
    assert body[starts[1]:].startswith(nxt)


def test_join_pages_used_by_parse_pdf_is_untouched_by_the_hyphen_rules():
    from engine.processors.pdf_parser import _join_pages

    assert _join_pages(["ríkis-", "og sveitarfélaga."]) == "ríkis-\nog sveitarfélaga."
    assert _join_pages(["EFTA-", "dómstóllinn."]) == "EFTA-\ndómstóllinn."
    assert _join_pages(["breyting-", "ar."]) == "breyting-\nar."


# R18 follow-up: footnote mode joins wrapped lines itself (" ".join), so the
# hyphen rules have to be applied at that join, for journals only.
_WRAPPED = "Dómur EFTA-\ndómstólsins og breyting-\nar á lögum um ríkis-\nog sveitarfélaga."


def test_footnote_mode_applies_the_hyphen_rules_to_wrapped_lines():
    body, _ = parse_pdf_pages(_pdf([_WRAPPED]), footnotes=True)
    assert "EFTA-dómstólsins" in body
    assert "breytingar á lögum" in body
    assert "ríkis- og sveitarfélaga." in body


@pytest.mark.parametrize("wrapped, expected", [
    ("Um Reykjavíkur-\nBorg og nágrenni.", "Reykjavíkur- Borg og nágrenni."),    # capital: plain space, as before
    ("Hann sagði -\nfór heim.", "sagði - fór heim."),
    ("Sjá nr. 42-\n2000 og síðar.", "nr. 42- 2000 og síðar."),
    ("Sjá nr. 42-\nmanna dóm.", "nr. 42-manna dóm."),
    ("Um 10-\nára fangelsi.", "10-ára fangelsi."),
    ("Hann sagði -\nog fór heim.", "sagði - og fór heim."),
])
def test_footnote_mode_leaves_other_hyphens_as_a_plain_space(wrapped, expected):
    body, _ = parse_pdf_pages(_pdf([wrapped]), footnotes=True)
    assert expected in body, body


def test_parse_pdf_footnote_mode_is_unchanged_without_the_opt_in():
    # Books and theses use parse_pdf(footnotes=True): their output must not move.
    assert "EFTA- dómstólsins" in parse_pdf(_pdf([_WRAPPED]), footnotes=True)
    assert "breyting- ar" in parse_pdf(_pdf([_WRAPPED]), footnotes=True)


def test_join_wrapped_lines_defaults_to_a_plain_space():
    from engine.processors.pdf_parser import _join_wrapped_lines

    lines = ["Dómur EFTA-", "dómstólsins og breyting-", "ar á lögum um ríkis-", "og sveitarfélaga."]
    assert _join_wrapped_lines(lines) == "Dómur EFTA- dómstólsins og breyting- ar á lögum um ríkis- og sveitarfélaga."
    assert _join_wrapped_lines(lines, True) == "Dómur EFTA-dómstólsins og breytingar á lögum um ríkis- og sveitarfélaga."
    assert _join_wrapped_lines([], True) == "" and _join_wrapped_lines(["Ein."], True) == "Ein."


def test_footnote_text_wrapped_with_a_hyphen_is_joined_on_request():
    from engine.processors.pdf_parser import _parse_footnote_lines

    lines = [(72.0, "1 Sjá EFTA-"), (72.0, "dómstólinn um ríkis-"), (72.0, "og sveitarfélaga."), (72.0, "2 Önnur.")]
    assert _parse_footnote_lines(lines)[0][0] == (1, "Sjá EFTA- dómstólinn um ríkis- og sveitarfélaga.")
    notes, _ = _parse_footnote_lines(lines, join_hyphens=True)
    assert notes == [(1, "Sjá EFTA-dómstólinn um ríkis- og sveitarfélaga."), (2, "Önnur.")]


def test_footnote_markers_and_note_block_survive_hyphen_joining():
    doc = fitz.open()
    page = doc.new_page()
    body_lines = ["Fyrsta lína greinarinnar fjallar um EFTA-", "dómstólinn og breyting-", "ar á lögum.",
                  "Önnur málsgrein heldur áfram hér.", "Þriðja lína er til fyllingar.", "Fjórða lína líka."]
    for k, line in enumerate(body_lines):
        page.insert_text((72, 100 + 14 * k), line, fontsize=11)
    page.insert_text((72 + 200, 100 + 14 * 2 - 4), "1", fontsize=6)            # superscript after "lögum."
    page.insert_text((72, 700), "1 Sjá EFTA-", fontsize=8)
    page.insert_text((72, 710), "dómstólinn nánar.", fontsize=8)
    body, _ = parse_pdf_pages(doc.tobytes(), footnotes=True)
    assert "EFTA-dómstólinn og breytingar á lögum.[^1] Önnur málsgrein" in body
    assert body.endswith("[^1]: Sjá EFTA-dómstólinn nánar.")
