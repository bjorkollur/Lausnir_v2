"""Filename and folder patterns of the user's archive → FileIdentity (spec §5.1, §7.1).

Every pattern is anchored on the archive layout surveyed 2026-10-01; a file that
matches nothing is reported, never guessed at.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from engine.timarit.config import JOURNALS_ROOT

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def roman_to_int(s: str) -> int:
    total = 0
    for i, ch in enumerate(s.upper()):
        v = _ROMAN[ch]
        total += -v if i + 1 < len(s) and _ROMAN[s[i + 1].upper()] > v else v
    return total


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


@dataclass(frozen=True)
class FileIdentity:
    source: str
    rel_path: str
    granularity: str                    # article | issue | volume
    year: int | None = None
    year_end: int | None = None
    volume: int | None = None
    issue: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    title_hint: str | None = None
    file_key: str | None = None


J = re.escape(JOURNALS_ROOT)
_TL_TOP = re.compile(J + r"Tímarit Lögfræðinga/TL (\d{4})(?:_(\d{4}))?(?:_(\d{2}))?\.pdf$")
_TL_ART = re.compile(J + r"Tímarit Lögfræðinga/TL (\d{4})_(\d{2})/(\d{4})_(\d+)\.pdf$")
_ULF_DIR = re.compile(J + r"Úlfljótur/Tímarit/Ulfljotur_(\d{4})(?: (\d{2}) arg|_(\d)_tbl)/([^/]+)\.pdf$")
_ULF_ISSUE = re.compile(J + r"Úlfljótur/Tímarit/Ulfljotur_(\d{4})[_-](\d)\.?_?tbl\.pdf$")
_ULF_AFM = re.compile(J + r"Úlfljótur/Tímarit/Ulfljotur_afmaelistimarit_(\d{4})\.pdf$")
_VEFRIT = re.compile(J + r"Úlfljótur/Vefrit/([^/]+)\.pdf$")
_LMBL = re.compile(J + r"Lögmannablaðið/Lögmannablaðið (\d{4}) (\d{2}) ?\.pdf$")
_LOGR = re.compile(J + r"Lögrétta/Lögrétta (\d{4}) (\d)\.tbl\.pdf$")
_LOGF = re.compile(J + r"Lögfræðingur UNAK/Lögfræðingur (\d) arg (\d{4})\.pdf$")
_LOGB = re.compile(J + r"Lögbrú/(\d{4})-(\d{2})-Lögbrú\.pdf$")
_RANN = re.compile(J + r"Rannsóknir í félagsvísingum Lagadeild/Rannsóknir í félagsvísingum Lagadeild (\d{4}) - ([IVXLC]+)(?: - (.+))?\.pdf$")
_SOGS = re.compile(J + r"Stjórnmál og Stjórnsýsla/Vol(\d{2})(?: ([^/]+))?/(\d+)-(\d+)_(.+)\.pdf$")
_YMSAR = re.compile(J + r"Ýmsar fræðigreinar/([^/]+)\.pdf$")
_NUMERIC_KEY = re.compile(r"^\d+(?:-\d+)*$")


def reject_reason(rel_path: str) -> str | None:
    p = nfc(rel_path)
    if not p.lower().endswith(".pdf"):
        return "not_pdf"
    if p.startswith("MDE "):
        return "deferred_mde"
    if "/Önnur Gögn/" in p:
        return "working_files"
    return None if identify(p) is not None else "no_pattern"


def identify(rel_path: str) -> FileIdentity | None:
    p = nfc(rel_path)
    if not p.lower().endswith(".pdf") or p.startswith("MDE ") or "/Önnur Gögn/" in p:
        return None
    if m := _TL_ART.match(p):
        return FileIdentity("timarit_logfraedinga", p, "article", year=int(m[1]), issue=str(int(m[2])), page_start=int(m[4]))
    if m := _TL_TOP.match(p):
        issue = str(int(m[3])) if m[3] else None
        return FileIdentity("timarit_logfraedinga", p, "issue" if issue else "volume",
                            year=int(m[1]), year_end=int(m[2]) if m[2] else None, issue=issue)
    if m := _ULF_DIR.match(p):
        year, vol, iss, stem = int(m[1]), m[2], m[3], m[4]
        if " TU" in stem or stem.lower().startswith("ulfljotur_"):
            return FileIdentity("ulfljotur", p, "issue", year=year, volume=int(vol) if vol else None, issue=iss)
        key = stem if _NUMERIC_KEY.match(stem) else None
        return FileIdentity("ulfljotur", p, "article", year=year, volume=int(vol) if vol else None, issue=iss,
                            title_hint=None if key else stem, file_key=key)
    if m := _ULF_ISSUE.match(p):
        return FileIdentity("ulfljotur", p, "issue", year=int(m[1]), issue=m[2])
    if m := _ULF_AFM.match(p):
        return FileIdentity("ulfljotur", p, "issue", year=int(m[1]), issue="afmaelisrit")
    if m := _VEFRIT.match(p):
        return FileIdentity("ulfljotur_vefrit", p, "article", title_hint=m[1])
    if m := _LMBL.match(p):
        return FileIdentity("logmannabladid", p, "issue", year=int(m[1]), issue=str(int(m[2])))
    if m := _LOGR.match(p):
        return FileIdentity("logretta", p, "issue", year=int(m[1]), issue=m[2])
    if m := _LOGF.match(p):
        return FileIdentity("logfraedingur", p, "volume", year=int(m[2]), volume=int(m[1]))
    if m := _LOGB.match(p):
        return FileIdentity("logbru", p, "issue", year=int(m[1]), issue=str(int(m[2])))
    if m := _RANN.match(p):
        return FileIdentity("rannsoknir_lagadeild", p, "volume", year=int(m[1]), volume=roman_to_int(m[2]),
                            issue=_slug(m[3]) if m[3] else None)
    if m := _SOGS.match(p):
        return FileIdentity("stjornmal_stjornsysla", p, "article", volume=int(m[1]),
                            issue=_slug(m[2]) if m[2] else None, page_start=int(m[3]), page_end=int(m[4]),
                            title_hint=m[5].replace("_", " ").strip())
    if m := _YMSAR.match(p):
        return FileIdentity("fraedigreinar_ymsar", p, "article", title_hint=m[1])
    return None


def issue_key(fid: FileIdentity) -> tuple[str, int | None, int | None, str | None]:
    return (fid.source, fid.year, fid.volume, fid.issue)
