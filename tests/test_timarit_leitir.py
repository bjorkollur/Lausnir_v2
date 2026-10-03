# tests/test_timarit_leitir.py
import json
from pathlib import Path

import httpx
import pytest

from engine.timarit.leitir_articles import LeitirArticle, best_match, parse_ispartof, search_articles

DOC = {"pnx": {"display": {
    "type": ["article"],
    "title": ["Hvenær er vanhæfi smitandi? : hugleiðingar í kjölfar dóms Hæstaréttar frá 17. desember 2009 í máli nr. 665/2008 "],
    "creator": ["Kjartan Bjarni Björgvinsson 1976- höfundur$$QKjartan Bjarni Björgvinsson"],
    "ispartof": ["2010; 60 (1): bls. 7-47 Tímarit lögfræðinga :$$QTímarit lögfræðinga $$Z991001868109706886"],
}}}


def test_parse_ispartof_full_form():
    d = parse_ispartof(DOC["pnx"]["display"]["ispartof"][0])
    assert d == {"year": 2010, "volume": 60, "issue": "1", "page_start": 7, "page_end": 47,
                 "journal": "Tímarit lögfræðinga", "record_id": "991001868109706886"}


def test_parse_ispartof_without_issue_or_pages():
    d = parse_ispartof("Skírnir : tímarit Hins íslenska bókmenntafélags. 1929; 103: bls. 151-170$$QSkírnir : tímarit Hins íslenska bókmenntafélags.$$Z991001811659706886")
    assert (d["year"], d["volume"], d["issue"], d["page_start"], d["page_end"]) == (1929, 103, None, 151, 170)
    assert parse_ispartof("Eitthvað óþekkt")["year"] is None


async def test_search_encodes_query_caches_and_filters(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"docs": [DOC, {"pnx": {"display": {"type": ["book"], "title": ["Bók"]}}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    res = await search_articles(client, "Hvenær er vanhæfi smitandi", cache_dir=tmp_path, journal_filter="Tímarit lögfræðinga")
    assert len(res) == 1 and res[0].authors == ["Kjartan Bjarni Björgvinsson"] and res[0].page_start == 7
    assert "Hvenær er vanhæfi smitandi" not in str(calls[0].url)      # percent-encoded
    assert "Hven%C3%A6r%20er%20vanh%C3%A6fi" in str(calls[0].url) or "Hven%C3%A6r+er+vanh%C3%A6fi" in str(calls[0].url)
    res2 = await search_articles(client, "Hvenær er vanhæfi smitandi", cache_dir=tmp_path, journal_filter="Tímarit lögfræðinga")
    assert len(calls) == 1 and res2 == res                               # served from cache
    assert len(list(Path(tmp_path).glob("*.json"))) == 1


async def test_search_http_error_returns_empty_and_caches_nothing(tmp_path):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400)))
    assert await search_articles(client, "x", cache_dir=tmp_path) == []
    assert not list(Path(tmp_path).glob("*.json"))


def test_best_match_prefers_title_then_page_then_year():
    a = LeitirArticle("Hvenær er vanhæfi smitandi?", ["K"], "TL", 2010, 60, "1", 7, 47, "1")
    b = LeitirArticle("Eru valdmörk á hreyfingu?", ["D"], "TL", 2010, 60, "1", 49, 70, "2")
    assert best_match([a, b], title="Hvenær er vanhæfi smitandi", page_start=None, year=2010) is a
    assert best_match([a, b], title=None, page_start=49, year=2010) is b
    assert best_match([a, b], title="Alls annað", page_start=None, year=1999) is None


# --- fix round 1 (R26) ------------------------------------------------------------------------
import time

from engine.timarit import leitir_articles as la


SPEC_MIN_INTERVAL = la._MIN_INTERVAL        # read at import, before any fixture patches it


def test_throttle_interval_is_one_second():
    assert SPEC_MIN_INTERVAL == 1.0


@pytest.fixture(autouse=True)
def _fresh_throttle(monkeypatch):
    """The throttle is module state: tests run without waiting; the throttle test sets its own interval."""
    monkeypatch.setattr(la, "_last_call", 0.0)
    monkeypatch.setattr(la, "_MIN_INTERVAL", 0.0)


def _art(title="Frá ritstjóra", year=1999, volume=49, issue="1", p1=5, p2=6, journal="TL"):
    return LeitirArticle(title, [], journal, year, volume, issue, p1, p2, "9")


def test_best_match_year_is_a_gate():
    old = _art(year=1999, volume=49)
    assert best_match([old], title="Frá ritstjóra", page_start=5, year=2010) is None
    assert best_match([_art(year=2010, volume=49)], title="Frá ritstjóra", page_start=5, year=2010) is not None


def test_best_match_volume_is_a_gate():
    r = _art(year=2010, volume=49)
    assert best_match([r], title="Frá ritstjóra", page_start=5, year=2010, volume=60) is None
    assert best_match([r], title="Frá ritstjóra", page_start=5, year=2010, volume=49) is r
    assert best_match([r], title="Frá ritstjóra", page_start=5, year=2010) is r      # volume not given: no gate


def test_best_match_unknown_record_year_or_volume_is_not_gated():
    r = _art(year=None, volume=None)
    assert best_match([r], title="Frá ritstjóra", page_start=5, year=2010, volume=60) is r


def test_best_match_gate_picks_the_right_year_among_same_title_records():
    old, new = _art(year=1999, volume=49), _art(year=2010, volume=60)
    assert best_match([old, new], title="Frá ritstjóra", page_start=5, year=2010) is new
    assert best_match([new, old], title="Frá ritstjóra", page_start=5, year=1999) is old


def test_best_match_page_only_needs_page_and_year_to_agree():
    other_year = _art(title="Annað", year=1987, volume=37, p1=7, p2=20)
    assert best_match([other_year], title=None, page_start=7, year=2010) is None       # gate
    unknown_year = _art(title="Annað", year=None, volume=None, p1=7, p2=20)
    assert best_match([unknown_year], title=None, page_start=7, year=2010) is None     # 0.25 < 0.30
    same_year = _art(title="Annað", year=2010, volume=60, p1=7, p2=20)
    assert best_match([same_year], title=None, page_start=7, year=None) is None        # page alone is not enough
    assert best_match([same_year], title=None, page_start=7, year=2010) is same_year
    assert best_match([same_year], title=None, page_start=None, year=2010) is None


def test_best_match_scores_title_against_part_before_subtitle():
    real = LeitirArticle("Hvenær er vanhæfi smitandi? : hugleiðingar í kjölfar dóms Hæstaréttar frá 17. desember 2009 í máli nr. 665/2008",
                         ["Kjartan Bjarni Björgvinsson"], "Tímarit lögfræðinga", 2010, 60, "1", 7, 47, "991001868109706886")
    assert best_match([real], title="Hvenær er vanhæfi smitandi", page_start=None, year=2010) is real
    assert best_match([real], title="Hvenær er vanhæfi smitandi", page_start=None, year=None) is real
    assert best_match([real], title="Allt annað efni", page_start=None, year=2010) is None


@pytest.mark.parametrize("dash", ["-", "–", "—", "−"])
def test_parse_ispartof_page_range_with_any_dash(dash):
    d = parse_ispartof(f"2010; 60 (1): bls. 7{dash}47 Tímarit lögfræðinga")
    assert (d["page_start"], d["page_end"]) == (7, 47)


@pytest.mark.parametrize("pages", ["7 - 47", "7- 47", "7 -47", "7 – 47"])
def test_parse_ispartof_page_range_with_spaces(pages):
    d = parse_ispartof(f"2010; 60 (1): bls. {pages} Tímarit lögfræðinga")
    assert (d["page_start"], d["page_end"]) == (7, 47)


@pytest.mark.parametrize("issue, expected", [("1–2", "1-2"), ("1—2", "1-2"), ("1 – 2", "1-2"), ("3-4", "3-4"),
                                             ("3/4", "3/4"), ("1", "1")])
def test_parse_ispartof_issue_forms(issue, expected):
    d = parse_ispartof(f"2010; 60 ({issue}): bls. 7-47 Tímarit lögfræðinga")
    assert d["issue"] == expected
    assert d["journal"] == "Tímarit lögfræðinga"
    assert (d["year"], d["volume"], d["page_start"], d["page_end"]) == (2010, 60, 7, 47)


def test_parse_ispartof_en_dash_issue_with_primo_markers():
    d = parse_ispartof("2010; 60 (1–2): bls. 7–47 Tímarit lögfræðinga :$$QTímarit lögfræðinga $$Z991001868109706886")
    assert d == {"year": 2010, "volume": 60, "issue": "1-2", "page_start": 7, "page_end": 47,
                 "journal": "Tímarit lögfræðinga", "record_id": "991001868109706886"}


@pytest.mark.parametrize("pages, expected", [("107-9", (107, 109)), ("107-19", (107, 119)), ("95-6", (95, 96)),
                                             ("190-9", (190, 199)), ("107-5", (107, None)), ("150-120", (150, None)),
                                             ("47-7", (47, None))])
def test_parse_ispartof_abbreviated_page_end(pages, expected):
    d = parse_ispartof(f"2010; 60 (1): bls. {pages} Tímarit lögfræðinga")
    assert (d["page_start"], d["page_end"]) == expected


def test_parse_ispartof_keeps_other_forms():
    d = parse_ispartof("2010; 60 (1): bls. 12 Tímarit lögfræðinga")
    assert (d["page_start"], d["page_end"]) == (12, 12)
    d = parse_ispartof("2010; bls. 7-47")
    assert (d["year"], d["volume"], d["issue"], d["page_start"], d["page_end"], d["journal"]) == (2010, None, None, 7, 47, None)
    d = parse_ispartof("Tímarit lögfræðinga. 2010; 60 (1): bls. 7-47")
    assert (d["year"], d["volume"], d["issue"], d["page_start"], d["page_end"]) == (2010, 60, "1", 7, 47)
    assert d["journal"].rstrip(". ") == "Tímarit lögfræðinga"
    d = parse_ispartof("2010; 60 (3/4)")
    assert (d["volume"], d["issue"], d["page_start"], d["page_end"]) == (60, "3/4", None, None)


def _docs_client(body, calls):
    def handler(request):
        calls.append(request)
        return httpx.Response(200, **body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_search_200_with_html_body_returns_empty_and_caches_nothing(tmp_path):
    calls = []
    client = _docs_client({"text": "<html><body>Maintenance</body></html>"}, calls)
    assert await search_articles(client, "x", cache_dir=tmp_path) == []
    assert len(calls) == 1 and not list(tmp_path.iterdir())


async def test_search_200_with_json_list_returns_empty_and_caches_nothing(tmp_path):
    calls = []
    client = _docs_client({"json": [1, 2, 3]}, calls)
    assert await search_articles(client, "x", cache_dir=tmp_path) == []
    assert not list(tmp_path.iterdir())


async def test_search_200_with_malformed_docs_is_tolerated(tmp_path):
    calls = []
    client = _docs_client({"json": {"docs": ["nope", 3, None, {"pnx": None}, DOC]}}, calls)
    res = await search_articles(client, "x", cache_dir=tmp_path)
    assert [a.page_start for a in res] == [7]
    client2 = _docs_client({"json": {"docs": "oops"}}, calls)
    assert await search_articles(client2, "y", cache_dir=tmp_path) == []


@pytest.mark.parametrize("garbage", ['{"docs": [', "", "[1, 2]", "null", "\xff\xfe"])
async def test_search_unreadable_or_invalid_cache_file_is_a_miss(tmp_path, garbage):
    calls = []
    client = _docs_client({"json": {"docs": [DOC]}}, calls)
    first = await search_articles(client, "Hvenær er vanhæfi smitandi", cache_dir=tmp_path)
    (cache_file,) = tmp_path.glob("*.json")
    cache_file.write_bytes(garbage.encode("latin-1") if garbage == "\xff\xfe" else garbage.encode("utf-8"))
    again = await search_articles(client, "Hvenær er vanhæfi smitandi", cache_dir=tmp_path)
    assert again == first and len(calls) == 2                                       # refetched, did not raise
    assert isinstance(json.loads(cache_file.read_text(encoding="utf-8")), dict)     # and repaired
    await search_articles(client, "Hvenær er vanhæfi smitandi", cache_dir=tmp_path)
    assert len(calls) == 2                                                          # now served from cache again


async def test_search_cache_is_written_via_temp_file_and_replace(tmp_path, monkeypatch):
    seen = []
    real_replace = la.os.replace

    def spy(src, dst):
        seen.append((Path(src), Path(dst), Path(src).exists()))
        return real_replace(src, dst)

    monkeypatch.setattr(la.os, "replace", spy)
    client = _docs_client({"json": {"docs": [DOC]}}, [])
    await search_articles(client, "x", cache_dir=tmp_path)
    (src, dst, src_existed), = seen
    assert src_existed and src != dst and src.parent == dst.parent and dst.suffix == ".json"
    assert [p.name for p in tmp_path.iterdir()] == [dst.name]                       # no temp file left behind


async def test_search_cache_write_failure_does_not_lose_result_or_leave_temp_files(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(la.os, "replace", boom)
    client = _docs_client({"json": {"docs": [DOC]}}, [])
    res = await search_articles(client, "x", cache_dir=tmp_path)
    assert [a.page_start for a in res] == [7]
    assert not list(tmp_path.iterdir())


async def test_search_throttles_consecutive_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(la, "_MIN_INTERVAL", 0.4)
    stamps = []

    def handler(request: httpx.Request) -> httpx.Response:
        stamps.append(time.monotonic())
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await search_articles(client, "a", cache_dir=tmp_path) == []
    assert await search_articles(client, "b", cache_dir=tmp_path) == []
    assert len(stamps) == 2 and stamps[1] - stamps[0] >= 0.4 - 0.05
    assert not list(tmp_path.iterdir())


# --- fix round 2 ------------------------------------------------------------------------------
from engine.timarit.ulfljotur_xlsx import _fold

RUBRIC_PROBES = [
    ("Dómar", "Dómar : Hæstaréttar Íslands árið 2010"),
    ("Ritdómur", "Ritdómur : Stjórnskipunarréttur eftir Björg Thorarensen"),
    ("Ritdómur", "Ritdómur : Eitthvað allt annað um refsirétt"),
    ("Frá ritstjóra", "Frá ritstjóra : þankar um lögfræði"),
    ("Formáli", "Formáli : að sérhefti"),
]


@pytest.mark.parametrize("toc_title, record_title", RUBRIC_PROBES)
def test_best_match_bare_rubric_with_conflicting_page_is_rejected(toc_title, record_title):
    r = _art(title=record_title, year=2010, volume=60, p1=99, p2=110)
    assert best_match([r], title=toc_title, page_start=7, year=2010) is None
    assert best_match([r], title=toc_title, page_start=7, year=2010, volume=60) is None


@pytest.mark.parametrize("toc_title, record_title", RUBRIC_PROBES)
def test_best_match_bare_rubric_without_a_page_confirmation_is_rejected(toc_title, record_title):
    r = _art(title=record_title, year=2010, volume=60, p1=99, p2=110)
    assert best_match([r], title=toc_title, page_start=None, year=2010) is None


@pytest.mark.parametrize("toc_title, record_title", RUBRIC_PROBES)
def test_best_match_rubric_on_the_same_page_and_year_matches(toc_title, record_title):
    r = _art(title=record_title, year=2010, volume=60, p1=7, p2=12)
    assert best_match([r], title=toc_title, page_start=7, year=2010) is r


def test_best_match_two_rubric_records_on_other_pages_do_not_pick_by_list_order():
    a = _art(title="Ritdómur : Bók A", year=2010, volume=60, p1=100, p2=110)
    b = _art(title="Ritdómur : Bók B", year=2010, volume=60, p1=200, p2=210)
    assert best_match([a, b], title="Ritdómur", page_start=7, year=2010) is None
    assert best_match([b, a], title="Ritdómur", page_start=7, year=2010) is None


def test_best_match_rubric_picks_the_record_on_the_right_page():
    a = _art(title="Ritdómur : Bók A", year=2010, volume=60, p1=100, p2=110)
    b = _art(title="Ritdómur : Bók B", year=2010, volume=60, p1=7, p2=12)
    assert best_match([a, b], title="Ritdómur", page_start=7, year=2010) is b
    assert best_match([b, a], title="Ritdómur", page_start=7, year=2010) is b


def test_best_match_distinctive_head_with_conflicting_page_is_rejected():
    real = LeitirArticle("Hvenær er vanhæfi smitandi? : hugleiðingar í kjölfar dóms Hæstaréttar frá 17. desember 2009 í máli nr. 665/2008",
                         ["K"], "Tímarit lögfræðinga", 2010, 60, "1", 7, 47, "1")
    assert best_match([real], title="Hvenær er vanhæfi smitandi", page_start=9, year=2010) is None
    assert best_match([real], title="Hvenær er vanhæfi smitandi", page_start=7, year=2010) is real
    assert best_match([real], title="Hvenær er vanhæfi smitandi", page_start=None, year=2010) is real


def test_best_match_full_title_match_survives_a_page_conflict():
    r = _art(title="Hvenær er vanhæfi smitandi", year=2010, volume=60, p1=7, p2=47)
    assert best_match([r], title="Hvenær er vanhæfi smitandi", page_start=9, year=2010) is r


def test_distinctive_head_boundaries():
    # three words, or at least 20 folded characters, make a head distinctive; two short words do not
    assert len(_fold("Alþingi Íslendinga")) == 19 and len(_fold("Hæstiréttur Íslands")) == 20
    cases = [("Um vanhæfi dómara", "Um vanhæfi dómara : yfirlit yfir dóma ársins og helstu nýmæli í framkvæmd", True),
             ("Hæstiréttur Íslands", "Hæstiréttur Íslands : yfirlit yfir dóma ársins og helstu nýmæli í framkvæmd", True),
             ("Alþingi Íslendinga", "Alþingi Íslendinga : yfirlit yfir dóma ársins og helstu nýmæli í framkvæmd", False)]
    for toc_title, record_title, distinctive in cases:
        r = _art(title=record_title, year=2010, volume=60, p1=50, p2=60)
        assert (best_match([r], title=toc_title, page_start=None, year=2010) is r) is distinctive, record_title


def test_best_match_tie_between_top_candidates_returns_none():
    a = _art(title="Ritdómur : Bók A", year=2010, volume=60, p1=7, p2=12)
    b = _art(title="Ritdómur : Bók B", year=2010, volume=60, p1=7, p2=15)
    assert best_match([a, b], title="Ritdómur", page_start=7, year=2010) is None
    c = _art(title="Hvenær er vanhæfi smitandi", year=2010, volume=60, p1=7, p2=47)
    d = _art(title="Hvenær er vanhæfi smitandi", year=2010, volume=60, p1=7, p2=47)
    assert best_match([c, d], title="Hvenær er vanhæfi smitandi", page_start=7, year=2010) is None
    assert best_match([c], title="Hvenær er vanhæfi smitandi", page_start=7, year=2010) is c


@pytest.mark.parametrize("body", [{"message": "Too many requests"}, {"docs": None}, {"docs": {}}, {"docs": "oops"}, {}])
async def test_search_200_without_a_docs_list_is_not_cached(tmp_path, body):
    calls = []
    client = _docs_client({"json": body}, calls)
    assert await search_articles(client, "x", cache_dir=tmp_path) == []
    assert not list(tmp_path.iterdir())
    assert await search_articles(client, "x", cache_dir=tmp_path) == []
    assert len(calls) == 2                                                          # asked again, not remembered


async def test_search_empty_docs_list_is_a_real_answer_and_is_cached(tmp_path):
    calls = []
    client = _docs_client({"json": {"docs": []}}, calls)
    assert await search_articles(client, "x", cache_dir=tmp_path) == []
    assert await search_articles(client, "x", cache_dir=tmp_path) == []
    assert len(calls) == 1 and len(list(tmp_path.glob("*.json"))) == 1


async def test_search_cached_response_without_docs_list_is_a_miss(tmp_path):
    calls = []
    client = _docs_client({"json": {"docs": [DOC]}}, calls)
    await search_articles(client, "x", cache_dir=tmp_path)
    (cache_file,) = tmp_path.glob("*.json")
    cache_file.write_text('{"message": "Too many requests"}', encoding="utf-8")      # poisoned by an older run
    res = await search_articles(client, "x", cache_dir=tmp_path)
    assert [a.page_start for a in res] == [7] and len(calls) == 2
