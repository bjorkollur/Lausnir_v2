import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from import_samkeppni import _parse_detail


def test_relative_pdf_link_resolved_to_absolute_url():
    """Site-relative PDF links (e.g. "/media/akvardanir-2021/x.pdf") used to be
    passed straight to httpx.get() with no base_url configured on the client,
    crashing every such fetch with UnsupportedProtocol — silently dropping the
    PDF-derived body text for any case whose detail page linked a PDF this way.
    Confirmed live: 64 cases failed with this exact error in one --new-only run.
    """
    detail = _parse_detail(
        '<html><body><a href="/media/akvardanir-2021/x.pdf">skjal</a></body></html>',
        "Kaup A á B",
        "Ákvörðun",
    )
    assert detail["pdf_url"] == "https://www.samkeppni.is/media/akvardanir-2021/x.pdf"


def test_already_absolute_pdf_link_untouched():
    detail = _parse_detail(
        '<html><body><a href="https://www.samkeppni.is/wp-content/uploads/x.pdf">skjal</a></body></html>',
        "Kaup A á B",
        "Ákvörðun",
    )
    assert detail["pdf_url"] == "https://www.samkeppni.is/wp-content/uploads/x.pdf"
