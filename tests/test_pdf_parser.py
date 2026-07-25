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
