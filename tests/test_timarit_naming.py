# tests/test_timarit_naming.py
import unicodedata
from collections import Counter
from pathlib import Path

import pytest

from engine.timarit.naming import FileIdentity, identify, issue_key, nfc, reject_reason, roman_to_int

FIX = Path(__file__).parent / "fixtures" / "timarit" / "filenames.txt"
ALL = [l for l in FIX.read_text(encoding="utf-8").splitlines() if l]
PDFS = [p for p in ALL if p.lower().endswith(".pdf")]


def test_nfc_normalises_macos_decomposed_names():
    nfd = unicodedata.normalize("NFD", "Lögmannablaðið")
    assert nfd != "Lögmannablaðið" and nfc(nfd) == "Lögmannablaðið"


def test_every_pdf_is_identified_or_rejected_with_a_reason():
    for p in PDFS:
        fid, why = identify(p), reject_reason(p)
        assert (fid is None) != (why is None), p
        assert why in (None, "working_files", "deferred_mde", "no_pattern"), p
    assert not [p for p in PDFS if reject_reason(p) == "no_pattern"]


def test_non_pdfs_are_rejected_as_not_pdf():
    for p in ALL:
        if not p.lower().endswith(".pdf") and not p.startswith("_meta/"):
            assert reject_reason(p) == "not_pdf", p


def test_counts_per_source_match_the_survey():
    c = Counter((f.source, f.granularity) for f in map(identify, PDFS) if f)
    # Úlfljótur: 909 PDFs in all. The 2026-10-01 survey bucketed 790 (every PDF in an issue subfolder) /
    # 77 (the *tbl.pdf files) / 42; by filename identity two of the 790 are issue-wide "TU" compilations
    # and the afmaelistimarit_2007 file is an issue, and the Vefrit folder holds 41 PDFs → 788 / 80 / 41.
    assert c[("ulfljotur", "article")] == 788 and c[("ulfljotur", "issue")] == 80
    assert c[("ulfljotur_vefrit", "article")] == 41
    assert sum(n for (src, _), n in c.items() if src.startswith("ulfljotur")) == 909
    assert c[("stjornmal_stjornsysla", "article")] == 329
    assert c[("timarit_logfraedinga", "article")] == 6
    assert c[("timarit_logfraedinga", "issue")] + c[("timarit_logfraedinga", "volume")] == 173
    assert c[("logmannabladid", "issue")] == 117
    assert c[("logretta", "issue")] == 34
    assert c[("logfraedingur", "volume")] == 6
    assert c[("logbru", "issue")] == 2
    assert c[("rannsoknir_lagadeild", "volume")] == 11
    assert c[("fraedigreinar_ymsar", "article")] == 2
    rej = Counter(reject_reason(p) for p in PDFS if identify(p) is None)
    assert rej["working_files"] == 11 and rej["deferred_mde"] == 63


@pytest.mark.parametrize("path,expect", [
    ("Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 2010_01.pdf",
     dict(source="timarit_logfraedinga", granularity="issue", year=2010, issue="1", volume=None)),
    ("Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 1985.pdf",
     dict(source="timarit_logfraedinga", granularity="volume", year=1985, issue=None)),
    ("Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 1971_1972.pdf",
     dict(granularity="volume", year=1971, year_end=1972)),
    ("Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 1975_03/1975_98.pdf",
     dict(granularity="article", year=1975, issue="3", page_start=98)),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1947 01 arg/173.pdf",
     dict(source="ulfljotur", granularity="article", year=1947, volume=1, file_key="173")),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2002_1_tbl/3076.pdf",
     dict(granularity="article", year=2002, issue="1", volume=None, file_key="3076")),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2002_1_tbl/Ulfljotur_2002_1_tbl TU.pdf",
     dict(granularity="issue", year=2002, issue="1")),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1998 51 arg/Lagadeild á tímamótum.pdf",
     dict(granularity="article", year=1998, volume=51, title_hint="Lagadeild á tímamótum", file_key=None)),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2012_4._tbl.pdf", dict(granularity="issue", year=2012, issue="4")),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2021-1tbl.pdf", dict(granularity="issue", year=2021, issue="1")),
    ("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_afmaelistimarit_2007.pdf", dict(granularity="issue", year=2007, issue="afmaelisrit")),
    ("Tímarit og fræðigreinar/Úlfljótur/Vefrit/Loftslagsváin og dómstólar.pdf",
     dict(source="ulfljotur_vefrit", granularity="article", title_hint="Loftslagsváin og dómstólar")),
    ("Tímarit og fræðigreinar/Lögmannablaðið/Lögmannablaðið 2011 03 .pdf",
     dict(source="logmannabladid", granularity="issue", year=2011, issue="3")),
    ("Tímarit og fræðigreinar/Lögrétta/Lögrétta 2010 1.tbl.pdf", dict(source="logretta", year=2010, issue="1")),
    ("Tímarit og fræðigreinar/Lögfræðingur UNAK/Lögfræðingur 4 arg 2010.pdf",
     dict(source="logfraedingur", granularity="volume", volume=4, year=2010)),
    ("Tímarit og fræðigreinar/Lögbrú/2013-01-Lögbrú.pdf", dict(source="logbru", year=2013, issue="1")),
    ("Tímarit og fræðigreinar/Rannsóknir í félagsvísingum Lagadeild/Rannsóknir í félagsvísingum Lagadeild 2007 - VIII.pdf",
     dict(source="rannsoknir_lagadeild", granularity="volume", year=2007, volume=8, issue=None)),
    ("Tímarit og fræðigreinar/Rannsóknir í félagsvísingum Lagadeild/Rannsóknir í félagsvísingum Lagadeild 2010 - XI - Trausti Fannar.pdf",
     dict(year=2010, volume=11, issue="trausti-fannar")),
    ("Tímarit og fræðigreinar/Stjórnmál og Stjórnsýsla/Vol01/81-106_The_Icelandic_Supreme_Court_and_the_Cons.pdf",
     dict(source="stjornmal_stjornsysla", granularity="article", volume=1, year=None, page_start=81, page_end=106,
          title_hint="The Icelandic Supreme Court and the Cons")),
    ("Tímarit og fræðigreinar/Stjórnmál og Stjórnsýsla/Vol14 Power and democracy in Iceland/1-34_The_Icelandic_power_structure_revisited.pdf",
     dict(volume=14, issue="power-and-democracy-in-iceland", page_start=1, page_end=34)),
    ("Tímarit og fræðigreinar/Ýmsar fræðigreinar/Þjóðareign - þýðing og áhrif stjórnarskrárákvæðis.pdf",
     dict(source="fraedigreinar_ymsar", granularity="article")),
])
def test_identify_examples(path, expect):
    fid = identify(path)
    assert fid is not None, path
    for k, v in expect.items():
        assert getattr(fid, k) == v, (k, getattr(fid, k))


def test_identify_sands_keeps_filename_pages_as_hint_only():
    fid = identify("Tímarit og fræðigreinar/Stjórnmál og Stjórnsýsla/Vol13/189-2010_The_opening_of_Costco_in_Iceland.pdf")
    assert (fid.page_start, fid.page_end) == (189, 2010)   # verified against the PDF later, not trusted here


def test_rejections():
    assert reject_reason("Tímarit og fræðigreinar/Lögfræðingur UNAK/Önnur Gögn/Hvað-með-föðurinn.pdf") == "working_files"
    assert reject_reason("MDE Dómareifanir 2005-2021/Domareifanir_2015_1.pdf") == "deferred_mde"
    assert reject_reason("Tímarit og fræðigreinar/Lögbrú/notes.docx") == "not_pdf"
    assert reject_reason("Tímarit og fræðigreinar/Lögbrú/2013-01-Lögbrú.pdf") is None


def test_issue_key_groups_articles_of_one_issue():
    a = identify("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2002_1_tbl/3076.pdf")
    b = identify("Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_2002_1_tbl/3077.pdf")
    assert issue_key(a) == issue_key(b) == ("ulfljotur", 2002, None, "1")


def test_roman():
    assert [roman_to_int(s) for s in ("IV", "VIII", "IX", "XII")] == [4, 8, 9, 12]
