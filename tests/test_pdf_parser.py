from unittest.mock import patch
import subprocess

from engine.processors.pdf_parser import docling_ocr_pdf


def test_docling_ocr_pdf_default_timeout_is_300():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()  # short-circuit, we only inspect the call
        docling_ocr_pdf(b"%PDF-fake")
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 300


def test_docling_ocr_pdf_accepts_custom_timeout():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        docling_ocr_pdf(b"%PDF-fake", timeout=1800)
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 1800


def test_docling_ocr_pdf_returns_none_on_timeout_expired():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="docling", timeout=1800)
        result = docling_ocr_pdf(b"%PDF-fake", timeout=1800)
    assert result is None


def test_docling_ocr_pdf_requests_placeholder_images_not_embedded():
    # Regression: without this flag Docling embeds every figure as a base64
    # data URI in the markdown — one scanned page reached 3.5 MB and failed
    # the insert outright (tsvector caps input at 1 MB in Postgres).
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        docling_ocr_pdf(b"%PDF-fake")
    (cmd,), _ = mock_run.call_args
    assert "--image-export-mode" in cmd
    assert cmd[cmd.index("--image-export-mode") + 1] == "placeholder"


def test_docling_ocr_pdf_strips_any_embedded_image_data_uri():
    """Belt-and-suspenders: even if --image-export-mode is ever ignored or a
    future Docling version behaves differently, no data URI reaches body_text."""
    from pathlib import Path

    def fake_run(cmd, **kwargs):
        output_dir = cmd[cmd.index("--output") + 1]
        (Path(output_dir) / "input.md").write_text(
            "# Titill\n\n![Image](data:image/png;base64,AAAABBBBCCCC==)\n\nAlvöru texti hér."
        )
        return subprocess.CompletedProcess(cmd, 0)

    with patch("subprocess.run", side_effect=fake_run):
        result = docling_ocr_pdf(b"%PDF-fake")
    assert "base64" not in result
    assert "data:image" not in result
    assert "Titill" in result and "Alvöru texti hér." in result


# ── Footnotes ─────────────────────────────────────────────────────────────────

from engine.processors.pdf_parser import _parse_footnote_lines, _render_footnotes


def test_parse_footnote_lines_pairs_number_with_its_text():
    lines = [(127.6, "1 Alþt. 2011-2012, A-deild."), (127.6, "2 Samanber lög nr. 61/2012.")]
    assert _parse_footnote_lines(lines) == ([
        (1, "Alþt. 2011-2012, A-deild."),
        (2, "Samanber lög nr. 61/2012."),
    ], [])


def test_parse_footnote_lines_joins_indented_continuation():
    """A note that wraps is indented — it belongs to the note above, not a new one."""
    lines = [
        (127.6, "4 Hrefna Friðriksdóttir: „Samningur um réttindi barnsins“."),
        (141.9, "Reykjavík 2009, bls. 311."),
    ]
    assert _parse_footnote_lines(lines) == ([
        (4, "Hrefna Friðriksdóttir: „Samningur um réttindi barnsins“. Reykjavík 2009, bls. 311."),
    ], [])


def test_parse_footnote_lines_ignores_bare_page_number():
    lines = [(127.6, "1 Fyrsta athugasemd."), (295.4, "7")]
    assert _parse_footnote_lines(lines) == ([(1, "Fyrsta athugasemd.")], [])


def test_parse_footnote_lines_returns_orphan_continuation_as_leftover():
    """A note running over from the previous page has no number here. It must not
    be attached to a footnote it doesn't belong to — but it must not be thrown
    away either: doing so cost one collection 12% of its text."""
    notes, leftovers = _parse_footnote_lines([(141.9, "framhald af fyrri síðu.")])
    assert notes == []
    assert leftovers == ["framhald af fyrri síðu."]


def test_parse_footnote_lines_returns_empty_for_no_lines():
    assert _parse_footnote_lines([]) == ([], [])


def test_render_footnotes_emits_gfm_definitions_for_referenced_notes():
    out = _render_footnotes([(1, "Fyrsta."), (2, "Önnur.")], "texti[^1] meira[^2]")
    assert "[^1]: Fyrsta." in out
    assert "[^2]: Önnur." in out
    assert "án tilvísunar" not in out


def test_render_footnotes_keeps_unreferenced_notes_as_a_plain_list():
    """remark-gfm drops a definition nothing references, which would delete the
    note's text outright — so unreferenced notes must survive some other way."""
    out = _render_footnotes([(1, "Vísað í."), (2, "Engin tilvísun.")], "texti[^1]")
    assert "[^1]: Vísað í." in out
    assert "[^2]:" not in out                      # not emitted as a dropped definition
    assert "Neðanmálsgreinar án tilvísunar" in out
    assert "2. Engin tilvísun." in out             # text preserved


def test_render_footnotes_deduplicates_repeated_numbers():
    out = _render_footnotes([(1, "Fyrsta."), (1, "Tvítekin.")], "texti[^1]")
    assert out.count("[^1]:") == 1
    assert "Fyrsta." in out


def test_render_footnotes_returns_empty_without_notes():
    assert _render_footnotes([], "einhver texti") == ""


from engine.processors.pdf_parser import _numbering_restarts


def test_numbering_restarts_detects_a_collection():
    """A festschrift numbers each contribution from 1, so the same marker recurs
    in unrelated places — linking them document-wide would misdirect the reader."""
    body = " ".join(f"texti[^{n}]" for _ in range(20) for n in range(1, 6))
    assert _numbering_restarts(body) is True


def test_numbering_restarts_false_for_a_single_author_work():
    body = " ".join(f"texti[^{n}]" for n in range(1, 60))
    assert _numbering_restarts(body) is False


def test_numbering_restarts_tolerates_a_few_repeats():
    refs = [str(n) for n in range(1, 60)] + ["3", "7"]
    body = " ".join(f"texti[^{n}]" for n in refs)
    assert _numbering_restarts(body) is False


def test_numbering_restarts_needs_enough_markers_to_judge():
    """Three markers, all the same, is not evidence of anything."""
    assert _numbering_restarts("a[^1] b[^1] c[^1]") is False


def test_numbering_restarts_ignores_definitions():
    body = "\n".join(f"[^{n}]: skýring" for n in range(1, 40))
    assert _numbering_restarts(body) is False


from engine.processors.pdf_parser import _footnote_block_start


def _line(top, text, size):
    """Minimal stand-in for pdfplumber words on one line."""
    return [{"text": t, "size": size, "x0": 100.0 + i * 10, "top": top}
            for i, t in enumerate(text.split())]


def _page(lines):
    line_map = {top: _line(top, text, size) for top, text, size in lines}
    return line_map, sorted(line_map), 11.0


def test_footnote_block_start_finds_the_block_at_the_foot_of_the_page():
    lm, tops, body = _page([
        (100, "venjulegur meginmálstexti hér", 11.0),
        (120, "meira meginmál", 11.0),
        (700, "1 Fyrsta athugasemd", 9.0),
        (712, "2 Önnur athugasemd", 9.0),
    ])
    assert _footnote_block_start(lm, tops, body, 842) == 2


def test_footnote_block_start_leaves_a_block_quotation_in_the_body():
    """Icelandic legal writing sets long quotations a point or two smaller. Those
    sit mid-page with body text after them and must not be taken for notes —
    doing so deleted statute quotations outright."""
    lm, tops, body = _page([
        (100, "meginmál á undan", 11.0),
        (200, "tilvitnun í lagaákvæði sett í minna letri", 9.0),
        (220, "framhald tilvitnunar", 9.0),
        (300, "meginmál á eftir tilvitnun", 11.0),
        (700, "1 Raunveruleg athugasemd", 9.0),
    ])
    # Only the last line is a note; the quotation stays above the cut.
    assert _footnote_block_start(lm, tops, body, 842) == 4


def test_footnote_block_start_returns_none_without_a_note_block():
    lm, tops, body = _page([(100, "bara meginmál", 11.0), (120, "meira", 11.0)])
    assert _footnote_block_start(lm, tops, body, 842) is None


def test_footnote_block_start_ignores_a_trailing_page_number():
    # A realistic page: mostly body, one note, then the folio. With too few body
    # lines the "most of the page is notes" guard declines the page entirely.
    lines = [(100 + i * 20, f"meginmálslína {i}", 11.0) for i in range(10)]
    lines += [(700, "1 Athugasemd", 9.0), (780, "42", 10.0)]
    lm, tops, body = _page(lines)
    assert _footnote_block_start(lm, tops, body, 842) == 10


def test_footnote_block_start_declines_when_most_of_the_page_looks_small():
    """A wholly small-type page (an appendix) is not a page of footnotes."""
    lm, tops, body = _page([
        (100, "smátt letur", 9.0),
        (120, "enn smátt", 9.0),
        (140, "og meira", 9.0),
        (160, "og enn meira", 9.0),
    ])
    assert _footnote_block_start(lm, tops, body, 842) is None


from engine.processors.pdf_parser import _heading_line_indexes


def test_heading_lines_accepts_a_short_run():
    lm, tops, body = _page([
        (100, "Efnisyfirlit", 14.0),
        (130, "venjulegt meginmál", 11.0),
        (150, "meira meginmál", 11.0),
    ])
    assert _heading_line_indexes(lm, tops, body, None) == {0}


def test_heading_lines_rejects_a_long_run_of_oversized_text():
    """When a page's dominant size is misread, whole paragraphs look oversized.
    Marking each line a heading turned running text into 1,263 headings in one
    collection — a heading is one or two lines, prose runs on."""
    lines = [(100 + i * 20, f"lína {i} af samfelldum texta", 13.0) for i in range(8)]
    lm, tops, body = _page(lines)
    assert _heading_line_indexes(lm, tops, body, None) == set()


def test_heading_lines_allows_a_two_line_heading():
    lm, tops, body = _page([
        (100, "Fyrri hluti fyrirsagnar", 14.0),
        (120, "seinni hluti fyrirsagnar", 14.0),
        (160, "meginmál á eftir", 11.0),
    ])
    assert _heading_line_indexes(lm, tops, body, None) == {0, 1}


def test_heading_lines_ignores_the_footnote_block():
    lm, tops, body = _page([
        (100, "Fyrirsögn", 14.0),
        (130, "meginmál", 11.0),
        (700, "1 Athugasemd", 9.0),
    ])
    assert _heading_line_indexes(lm, tops, body, 2) == {0}


# ── Scanned-page detection ───────────────────────────────────────────────────────

from unittest.mock import MagicMock

from engine.processors.pdf_parser import _page_has_full_page_image, is_scanned_pdf


class _FakePage:
    """Minimal stand-in for a pdfplumber Page: width/height + a list of image
    dicts, each with the x0/x1/top/bottom keys pdfplumber reports."""

    def __init__(self, width, height, images):
        self.width = width
        self.height = height
        self.images = images


def _image(x0, top, x1, bottom):
    return {"x0": x0, "top": top, "x1": x1, "bottom": bottom}


def test_full_page_image_detects_a_scan():
    page = _FakePage(600, 800, [_image(0, 0, 600, 800)])
    assert _page_has_full_page_image(page) is True


def test_full_page_image_ignores_a_small_figure():
    page = _FakePage(600, 800, [_image(100, 100, 300, 300)])
    assert _page_has_full_page_image(page) is False


def test_full_page_image_ignores_a_page_with_no_images():
    page = _FakePage(600, 800, [])
    assert _page_has_full_page_image(page) is False


def test_full_page_image_true_right_at_the_threshold():
    # 90% of the page area, matching the default threshold exactly.
    page = _FakePage(100, 100, [_image(0, 0, 100, 90)])
    assert _page_has_full_page_image(page, threshold=0.9) is True


def test_full_page_image_false_just_under_the_threshold():
    page = _FakePage(100, 100, [_image(0, 0, 100, 89)])
    assert _page_has_full_page_image(page, threshold=0.9) is False


def test_full_page_image_sums_nothing_across_multiple_small_images():
    # Two half-page images side by side: neither alone is a full-page scan.
    page = _FakePage(600, 800, [_image(0, 0, 300, 800), _image(300, 0, 600, 800)])
    assert _page_has_full_page_image(page) is False


def _fake_pdf(pages):
    ctx = MagicMock()
    ctx.__enter__.return_value.pages = pages
    ctx.__exit__.return_value = False
    return ctx


def test_is_scanned_pdf_true_when_a_sampled_page_is_a_scan():
    pages = [_FakePage(600, 800, []) for _ in range(20)]
    pages[16] = _FakePage(600, 800, [_image(0, 0, 600, 800)])  # a sampled index (step 4, 0..19)
    with patch("pdfplumber.open", return_value=_fake_pdf(pages)):
        assert is_scanned_pdf(b"%PDF-fake") is True


def test_is_scanned_pdf_false_for_an_all_native_document():
    pages = [_FakePage(600, 800, []) for _ in range(20)]
    with patch("pdfplumber.open", return_value=_fake_pdf(pages)):
        assert is_scanned_pdf(b"%PDF-fake") is False


def test_is_scanned_pdf_samples_the_last_page_not_just_the_front():
    # A clean cover followed entirely by scanned pages — sampling only the
    # front would misclassify the whole book as native.
    pages = [_FakePage(600, 800, []) for _ in range(10)]
    pages[-1] = _FakePage(600, 800, [_image(0, 0, 600, 800)])
    with patch("pdfplumber.open", return_value=_fake_pdf(pages)):
        assert is_scanned_pdf(b"%PDF-fake") is True


def test_is_scanned_pdf_false_for_an_empty_document():
    with patch("pdfplumber.open", return_value=_fake_pdf([])):
        assert is_scanned_pdf(b"%PDF-fake") is False


def test_is_scanned_pdf_false_when_pdfplumber_cannot_open_the_file():
    # A caller passing genuinely broken bytes gets routed to the normal
    # parse_pdf/OCR fallback chain, not an exception from the classifier itself.
    assert is_scanned_pdf(b"not actually a pdf") is False


# ── Scan resolution normalization ────────────────────────────────────────────

from engine.processors.pdf_parser import _page_image_dpi, downsample_if_high_res


def test_page_image_dpi_computes_from_source_pixel_size():
    # A 600pt-wide image at 600 px source width is a 72 px/inch * (600/600) = 1x
    # scale, i.e. exactly 72 DPI at that width; use a realistic scan instead —
    # a 300pt-wide page region backed by a 1250px image is ~300 DPI.
    page = _FakePage(300, 400, [{"x0": 0, "top": 0, "x1": 300, "bottom": 400, "srcsize": (1250, 1667)}])
    assert round(_page_image_dpi(page)) == 300


def test_page_image_dpi_none_without_images():
    assert _page_image_dpi(_FakePage(300, 400, [])) is None


def test_page_image_dpi_none_when_source_size_missing():
    page = _FakePage(300, 400, [{"x0": 0, "top": 0, "x1": 300, "bottom": 400, "srcsize": (None, None)}])
    assert _page_image_dpi(page) is None


def _pdf_bytes_with_dpi(dpi: float) -> bytes:
    """Not a real PDF — downsample_if_high_res only needs pdfplumber.open to
    succeed, which is mocked per-test; the bytes value itself is never parsed."""
    return f"fake-pdf-at-{dpi}dpi".encode()


def test_downsample_if_high_res_leaves_a_reasonable_scan_untouched():
    page = _FakePage(300, 400, [_image(0, 0, 300, 400)])  # coverage math irrelevant here
    page.images[0]["srcsize"] = (1250, 1667)  # ~300 DPI
    with patch("pdfplumber.open", return_value=_fake_pdf([page])):
        original = _pdf_bytes_with_dpi(300)
        assert downsample_if_high_res(original) is original


def test_downsample_if_high_res_rerenders_an_unnecessarily_high_res_scan():
    page = _FakePage(300, 400, [_image(0, 0, 300, 400)])
    page.images[0]["srcsize"] = (2500, 3333)  # ~600 DPI
    fake_output = b"smaller-pdf-bytes"

    fake_src_page = MagicMock()
    fake_src_page.rect.width, fake_src_page.rect.height = 300, 400
    fake_src = MagicMock()
    fake_src.__iter__.return_value = iter([fake_src_page])
    fake_out = MagicMock()
    fake_out.tobytes.return_value = fake_output
    fake_out.new_page.return_value = MagicMock()

    with patch("pdfplumber.open", return_value=_fake_pdf([page])):
        with patch("fitz.open", side_effect=[fake_src, fake_out]):
            result = downsample_if_high_res(_pdf_bytes_with_dpi(600))
    assert result == fake_output
    fake_out.close.assert_called_once()
    fake_src.close.assert_called_once()


def test_downsample_if_high_res_keeps_original_when_rerender_is_bigger():
    # Regression: a real book scanned with an efficient bilevel codec (common
    # for black-and-white text) went from 18 MB to 322 MB after "downsampling"
    # to RGB JPEG at a lower DPI — the DPI estimate alone can't predict this,
    # only comparing actual output size can.
    page = _FakePage(300, 400, [_image(0, 0, 300, 400)])
    page.images[0]["srcsize"] = (2500, 3333)  # ~600 DPI, triggers the rerender
    original = _pdf_bytes_with_dpi(600)
    bigger_output = original + b"-padded-to-be-larger-than-the-input"

    fake_src_page = MagicMock()
    fake_src_page.rect.width, fake_src_page.rect.height = 300, 400
    fake_src = MagicMock()
    fake_src.__iter__.return_value = iter([fake_src_page])
    fake_out = MagicMock()
    fake_out.tobytes.return_value = bigger_output
    fake_out.new_page.return_value = MagicMock()

    with patch("pdfplumber.open", return_value=_fake_pdf([page])):
        with patch("fitz.open", side_effect=[fake_src, fake_out]):
            result = downsample_if_high_res(original)
    assert result is original


def test_downsample_if_high_res_returns_original_on_render_failure():
    page = _FakePage(300, 400, [_image(0, 0, 300, 400)])
    page.images[0]["srcsize"] = (2500, 3333)
    with patch("pdfplumber.open", return_value=_fake_pdf([page])):
        with patch("fitz.open", side_effect=RuntimeError("boom")):
            original = _pdf_bytes_with_dpi(600)
            assert downsample_if_high_res(original) is original
