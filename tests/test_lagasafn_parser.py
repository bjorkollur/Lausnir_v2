"""Tests for lagasafn HTML parser."""
import hashlib
from pathlib import Path
from engine.processors.lagasafn_parser import parse_law_html, build_chapter_map

# Minimal law HTML fixture (iso-8859-1 encoded)
_STJORNARSKRA_EXCERPT = b"""
<!DOCTYPE html>
<html><head>
<title>1944  nr. 33  17. j\xfan\xed/ Stj\xf3rnarskr\xe1 l\xfd\xf0veldisins \xcdslands</title>
<meta http-equiv='Content-Type' content='text/html; charset=iso-8859-1'>
</head><body>
<h2> Stj\xf3rnarskr\xe1 l\xfd\xf0veldisins \xcdslands </h2>
<p style='text-align:center'><strong>1944  nr. 33  17. j\xfan\xed</strong></p>
<small><b>T\xf3k gildi 17. j\xfan\xed 1944.</b></small><hr>
<span id="G1"></span><IMG SRC="sk.jpg"> <b>1. gr.</b><br>
<IMG SRC="hk.jpg" id="G1M1"> \xcdsland er l\xfd\xf0veldi me\xf0 \xfeing bundinni stj\xf3rn.<br>
<span id="G2"></span><IMG SRC="sk.jpg"> <b>2. gr.</b><br>
<IMG SRC="hk.jpg" id="G2M1"> Al\xfeingi og forseti \xcdslands fara saman me\xf0 l\xf6ggjafarvaldi\xf0.<br>
</body></html>
""".strip()

def test_parse_basic_law():
    result = parse_law_html(_STJORNARSKRA_EXCERPT, "1944033.html")
    assert result["external_id"] == "1944033"
    assert result["case_number"] == "33/1944"
    assert result["court"] == "Alþingi"
    assert result["verdict_type"] == "Lög"
    assert result["document_date"] is not None
    assert result["document_date"].year == 1944

def test_parse_provisions():
    result = parse_law_html(_STJORNARSKRA_EXCERPT, "1944033.html")
    provs = result["provisions"]
    assert len(provs) == 2
    assert provs[0]["num"] == 1
    assert "lýðveldi" in provs[0]["text"]
    assert provs[1]["num"] == 2
    assert "Alþingi" in provs[1]["text"]

def test_body_text_has_article_prefix():
    result = parse_law_html(_STJORNARSKRA_EXCERPT, "1944033.html")
    bt = result["body_text"]
    assert "1. gr." in bt
    assert "2. gr." in bt
    assert "lýðveldi" in bt
    # No HTML tags in body_text
    assert "<" not in bt and ">" not in bt

def test_md5_present():
    result = parse_law_html(_STJORNARSKRA_EXCERPT, "1944033.html")
    expected = hashlib.md5(_STJORNARSKRA_EXCERPT).hexdigest()
    assert result["md5"] == expected

def test_no_provisions_gives_raw_body():
    """A law with no greinar span IDs still gets body_text (the raw stripped text)."""
    html = b"""<html><body>
<h2> Gamalt log </h2><p><strong>1400  nr.   </strong></p>
<br>Texti laganna er h\xe9r.<br>
</body></html>"""
    result = parse_law_html(html, "1400000.html")
    assert result["body_text"].strip() != ""
    # provisions may be empty for ancient laws without span ids
    assert isinstance(result["provisions"], list)
