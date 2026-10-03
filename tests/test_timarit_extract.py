import fitz
import pytest

from engine.timarit.extract import (
    bin_known_ratio, dehyphenate, extract_article, has_text_layer, strip_running_lines,
)


def _pdf(pages):
    doc = fitz.open()
    for t in pages:
        doc.new_page().insert_text((72, 72), t, fontsize=11)
    return doc.tobytes()


# has_text_layer wants a page of >= 50 characters, so fixtures that must count as
# "has a text layer" carry a filler sentence after their first line.
_FILLER = " Hún fjallar um gildi samninga og efndir þeirra."


def test_strip_running_lines_removes_repeated_headers_and_page_numbers():
    pages = [f"Tímarit lögfræðinga\nEfni {i}.\n{i + 6}" for i in range(6)]
    out = strip_running_lines(pages)
    assert all("Tímarit lögfræðinga" not in p for p in out)
    assert all(not p.splitlines()[-1].strip().isdigit() for p in out)
    assert out[2].strip() == "Efni 2."


def test_strip_running_lines_keeps_lines_that_appear_rarely():
    pages = ["Sérstök fyrirsögn\nEfni.", "Önnur\nEfni.", "Þriðja\nEfni.", "Fjórða\nEfni."]
    assert strip_running_lines(pages)[0].startswith("Sérstök fyrirsögn")


def test_strip_running_lines_never_treats_a_whole_short_page_as_edge():
    # Digit folding makes "Efni 0." ... "Efni 5." one shape; on a three-line page
    # the middle line is body text, not a running head.
    pages = [f"Haus\nEfni {i}.\n{i + 6}" for i in range(6)]
    assert strip_running_lines(pages)[3] == "Efni 3."


def test_strip_running_lines_handles_blank_lines_and_a_second_header_line():
    pages = [f"Tímarit\nHöfundur\n\nKafli {i} fjallar um samninga.\nMeira texti {chr(97 + i)}.\nLokaorð {chr(65 + i)}.\n{i + 40}\n" for i in range(5)]
    out = strip_running_lines(pages)
    assert all("Tímarit" not in p and "Höfundur" not in p for p in out)
    assert all(not p.strip().splitlines()[-1].isdigit() for p in out)
    assert all("Kafli" in p for p in out)


def test_strip_running_lines_leaves_short_documents_alone():
    pages = ["Haus\nA.\n1", "Haus\nB.\n2"]
    assert strip_running_lines(pages) == pages


def test_dehyphenate_joins_lowercase_continuations_only():
    assert dehyphenate("breyting-\nar á lögum") == "breytingar á lögum"
    assert dehyphenate("Reykjavíkur-\nBorg") == "Reykjavíkur-\nBorg"        # capital: a real hyphenated name
    assert dehyphenate("nr. 42-\n2000") == "nr. 42-\n2000"


# R18: one rule set shared with the cross-page join (pdf_parser.resolve_hyphen_break).
@pytest.mark.parametrize("raw, expected", [
    # (a) conjunction continuation: suspended hyphen, keep it, one space
    ("ríkis-\nog sveitarfélaga", "ríkis- og sveitarfélaga"),
    ("lög-\neða reglugerða", "lög- eða reglugerða"),
    ("inn-\nog útflutningur", "inn- og útflutningur"),
    ("hvorki sveitar-\nné ríki", "hvorki sveitar- né ríki"),
    ("bóka-\nauk blaða", "bóka- auk blaða"),
    ("Hann sagði -\nog fór.", "Hann sagði - og fór."),       # a dash, not a word break
    # (b) acronym or digit before the hyphen: real hyphen, join without the newline
    ("EFTA-\ndómstóllinn", "EFTA-dómstóllinn"),
    ("EES-\nsamningurinn", "EES-samningurinn"),
    ("ÍSAL-\nsamningurinn", "ÍSAL-samningurinn"),
    ("3ja-\nmanna nefnd", "3ja-manna nefnd"),
    ("10-\nára", "10-ára"),
    ("nr. 42-\nmanna", "nr. 42-manna"),
    # (c) letter before the hyphen, lowercase continuation: drop the hyphen
    ("breyting-\nar á lögum", "breytingar á lögum"),
    ("Reykjavíkur-\nborg", "Reykjavíkurborg"),
    # (d) everything else is left alone
    ("Reykjavíkur-\nBorg", "Reykjavíkur-\nBorg"),
    ("nr. 42-\n2000", "nr. 42-\n2000"),
    ("Hann sagði -\nfór heim", "Hann sagði -\nfór heim"),
    ("kafla)-\nmanna", "kafla)-\nmanna"),
    ("EFTA-\nDómstóllinn", "EFTA-\nDómstóllinn"),
    ("sagði-\n\nNý málsgrein", "sagði-\n\nNý málsgrein"),
    ("lög-\nogni", "lögogni"),                              # "ogni" is not the word "og"
])
def test_dehyphenate_rules(raw, expected):
    assert dehyphenate(raw) == expected


def test_dehyphenate_is_linear_on_long_unbroken_runs():
    run = "a" * 200_000
    assert dehyphenate(run + "-\nb og c") == run + "b og c"


def test_dehyphenate_handles_several_breaks_in_one_text():
    raw = "ríkis-\nog sveitar-\nfélaga, EES-\nsamningurinn og breyting-\nar"
    assert dehyphenate(raw) == "ríkis- og sveitarfélaga, EES-samningurinn og breytingar"


def test_bin_ratio_separates_icelandic_from_noise():
    assert bin_known_ratio("dómstóll kvað upp úrskurð í málinu") > 0.8
    assert bin_known_ratio("T^/Teginreglur xq zzv fjkl") < 0.5


def test_has_text_layer():
    assert has_text_layer(["Texti sem er lengri en fimmtíu stafir á þessari síðu, vel og lengi."])
    assert not has_text_layer(["", "  ", "ab"])


def test_extract_article_builds_page_offsets_with_printed_numbers():
    pdf = _pdf([f"7\nFyrsta síða greinar.{_FILLER}", "8\nÖnnur síða greinar.", "9\nÞriðja síða."])
    ex = extract_article(pdf, source="timarit_logfraedinga", footnotes=False)
    assert ex.ocr_source == "embedded"
    assert [p["printed"] for p in ex.page_offsets] == [7, 8, 9]
    assert ex.body[ex.page_offsets[1]["char"]:].startswith("Önnur síða")
    assert "7\n" not in ex.body          # page numbers stripped as running lines


def test_extract_article_falls_back_to_ocr_when_no_text_layer():
    pdf = _pdf(["", "", ""])
    ex = extract_article(pdf, source="default", footnotes=False, ocr=lambda b: "OCR texti síðu.")
    assert ex.ocr_source == "tesseract-isl" and ex.body == "OCR texti síðu."
    assert ex.page_offsets == []       # OCR output carries no page boundaries


_NOISE = "xq zzv fjkl qwrt zzz qqq xxx vbn mnb kjh gfd sdf wer"


def test_extract_article_reocrs_below_bin_threshold():
    pdf = _pdf([_NOISE, "zzz qqq xxx"])
    ex = extract_article(pdf, source="default", footnotes=False, ocr=lambda b: "góður texti", ocr_bin_threshold=0.5)
    assert ex.ocr_source == "tesseract-isl" and ex.body == "góður texti"
    assert ex.page_offsets == []


def test_extract_article_keeps_embedded_text_when_reocr_yields_nothing():
    pdf = _pdf([_NOISE, "zzz qqq xxx"])
    ex = extract_article(pdf, source="default", footnotes=False, ocr=lambda b: None, ocr_bin_threshold=0.5)
    assert ex.ocr_source == "embedded" and "fjkl" in ex.body and ex.page_offsets


def test_extract_article_does_not_reocr_good_text_or_without_a_threshold():
    pdf = _pdf(["Dómstóllinn kvað upp úrskurð í málinu og vísaði kröfunni frá dómi."])
    calls = []

    def ocr(b):
        calls.append(1)
        return "annað"

    assert extract_article(pdf, source="default", footnotes=False, ocr=ocr, ocr_bin_threshold=0.5).ocr_source == "embedded"
    noisy = _pdf([_NOISE])
    assert extract_article(noisy, source="default", footnotes=False, ocr=ocr).ocr_source == "embedded"
    assert calls == []


def test_printed_start_gives_a_short_article_its_page_numbers():
    # Two pages cannot fit a page map (3 agreeing pages needed), but the importer
    # knows where the article starts in the issue (ruling R17).
    pdf = _pdf([f"Fyrri síða greinar.{_FILLER}", f"Seinni síða greinar.{_FILLER}"])
    assert [p["printed"] for p in extract_article(pdf, source="default", footnotes=False).page_offsets] == [None, None]
    ex = extract_article(pdf, source="default", footnotes=False, printed_start=7)
    assert [p["printed"] for p in ex.page_offsets] == [7, 8]
    assert [p["pdf_page"] for p in ex.page_offsets] == [0, 1]


def test_printed_start_skips_pages_that_contributed_no_text():
    pdf = _pdf([f"Fyrsta.{_FILLER}", "", f"Þriðja.{_FILLER}"])
    ex = extract_article(pdf, source="default", footnotes=False, printed_start=20)
    assert [(p["pdf_page"], p["printed"]) for p in ex.page_offsets] == [(0, 20), (2, 22)]


def test_printed_start_overrides_the_article_local_page_map():
    pdf = _pdf([f"7\nFyrsta síða greinar.{_FILLER}", "8\nÖnnur síða greinar.", "9\nÞriðja síða."])
    ex = extract_article(pdf, source="timarit_logfraedinga", footnotes=False, printed_start=100)
    assert [p["printed"] for p in ex.page_offsets] == [100, 101, 102]


def test_extract_article_keeps_suspended_hyphen_across_a_page_break_end_to_end():
    pdf = _pdf(["Um skyldur ríkis-", f"og sveitarfélaga.{_FILLER}"])
    ex = extract_article(pdf, source="default", footnotes=False)
    assert "ríkis- og sveitarfélaga." in ex.body and "ríkisog" not in ex.body
    assert ex.body[ex.page_offsets[1]["char"]:].startswith("og sveitarfélaga.")


@pytest.mark.parametrize("ocr", [None, lambda b: None, lambda b: "  "])
def test_no_text_layer_and_no_ocr_result_is_labelled_none(ocr):
    ex = extract_article(_pdf(["", "", ""]), source="default", footnotes=False, ocr=ocr)
    assert ex.ocr_source == "none" and ex.body == "" and ex.page_offsets == [] and ex.bin_ratio == 0.0


_WRAPPED = "Dómur EFTA-\ndómstólsins og breyting-\nar á lögum um ríkis-\nog sveitarfélaga."


@pytest.mark.parametrize("footnotes", [False, True])
def test_extract_article_resolves_wrapped_hyphens_in_both_modes(footnotes):
    # Every journal source has footnotes on; the rules must hold there too.
    ex = extract_article(_pdf([_WRAPPED]), source="default", footnotes=footnotes)
    assert "EFTA-dómstólsins" in ex.body
    assert "breytingar á lögum" in ex.body
    assert "ríkis- og sveitarfélaga." in ex.body
    assert "EFTA- " not in ex.body and "breyting- " not in ex.body


def test_extract_article_footnote_mode_keeps_a_capitalised_continuation_apart():
    ex = extract_article(_pdf([f"Um Reykjavíkur-\nBorg og nágrenni.{_FILLER}"]), source="default", footnotes=True)
    assert "Reykjavíkur- Borg" in ex.body
