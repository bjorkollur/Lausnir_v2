"""Cut an article's pages out of an issue PDF (spec §4.5). The pages are copied
unchanged with PyMuPDF; the issue PDF itself is never modified."""
from __future__ import annotations

import hashlib

import fitz


def page_count(pdf_bytes: bytes) -> int:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as d:
        return d.page_count


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def cut_pages(pdf_bytes: bytes, first: int, last: int) -> bytes:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as src:
        n = src.page_count
        if first > last or first < 0 or first >= n:
            raise ValueError(f"bad page range {first}-{last} for a {n}-page PDF")
        last = min(last, n - 1)
        out = fitz.open()
        out.insert_pdf(src, from_page=first, to_page=last)
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
        return data
