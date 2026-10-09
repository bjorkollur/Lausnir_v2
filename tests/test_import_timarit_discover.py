import argparse
import os
import random

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from engine.timarit.manifest import ArticleEntry, IssueManifest, save_manifest
from scripts import import_timarit as imp
from scripts.import_timarit import plan_discovery, write_unmatched

FILES = {
    "Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 2010_01.pdf": b"A",
    "Tímarit og fræðigreinar/Tímarit Lögfræðinga/TL 1985.pdf": b"B",
    "Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/1041.pdf": b"C",
    "Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1958 11 arg/1041.pdf": b"C",      # byte duplicate
    "Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/1043.pdf": b"D",
    "Tímarit og fræðigreinar/Lögfræðingur UNAK/Önnur Gögn/Hvað-með-föðurinn.pdf": b"E",
    "Tímarit og fræðigreinar/Lögbrú/LÖGFRÆÐINGUR.docx": b"F",
    "MDE Dómareifanir 2005-2021/Domareifanir_2015_1.pdf": b"G",
}


def test_discover_groups_files_into_issues_and_records_rejections(tmp_path):
    manifests, unmatched = plan_discovery(sorted(FILES), lambda rel: FILES[rel])
    keys = {(m.source, m.year, m.volume, m.issue): m for m in manifests}
    assert keys[("timarit_logfraedinga", 2010, None, "1")].file.endswith("TL 2010_01.pdf")
    assert keys[("timarit_logfraedinga", 1985, None, None)].file.endswith("TL 1985.pdf")
    ulf = keys[("ulfljotur", 1957, 11, None)]
    assert ulf.file is None and sorted(ulf.files) == [
        "Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/1041.pdf",
        "Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1957 11 arg/1043.pdf"]
    assert ("ulfljotur", 1958, 11, None) not in keys
    reasons = {u["path"]: u for u in unmatched}
    assert reasons["Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1958 11 arg/1041.pdf"]["reason"] == "duplicate_of"
    assert reasons["Tímarit og fræðigreinar/Úlfljótur/Tímarit/Ulfljotur_1958 11 arg/1041.pdf"]["duplicate_of"].endswith("1957 11 arg/1041.pdf")
    assert reasons["Tímarit og fræðigreinar/Lögfræðingur UNAK/Önnur Gögn/Hvað-með-föðurinn.pdf"]["reason"] == "working_files"
    assert reasons["Tímarit og fræðigreinar/Lögbrú/LÖGFRÆÐINGUR.docx"]["reason"] == "not_pdf"
    assert reasons["MDE Dómareifanir 2005-2021/Domareifanir_2015_1.pdf"]["reason"] == "deferred_mde"
    assert all(m.status == "pending" for m in manifests)


def test_discover_records_byte_duplicates_once(tmp_path):
    manifests, unmatched = plan_discovery(sorted(FILES), lambda rel: FILES[rel])
    all_files = [f for m in manifests for f in (m.files or [m.file])]
    assert len(all_files) == len(set(all_files)) and not any("1958" in f for f in all_files)


def test_first_path_in_sorted_order_wins_whatever_the_input_order():
    manifests, unmatched = plan_discovery(sorted(FILES, reverse=True), lambda rel: FILES[rel])
    dup = next(u for u in unmatched if u["reason"] == "duplicate_of")
    assert dup["path"].endswith("1958 11 arg/1041.pdf") and dup["duplicate_of"].endswith("1957 11 arg/1041.pdf")


def test_nfd_paths_are_normalised_before_matching():
    import unicodedata
    nfd = {unicodedata.normalize("NFD", k): v for k, v in FILES.items()}
    manifests, unmatched = plan_discovery(sorted(nfd), lambda rel: FILES[rel])
    assert any(m.source == "timarit_logfraedinga" and m.year == 2010 for m in manifests)
    assert {u["reason"] for u in unmatched} == {"duplicate_of", "working_files", "not_pdf", "deferred_mde"}


def test_two_container_files_for_one_issue_are_flagged(monkeypatch):
    # No journal pattern lets two different containers share a key today, so the branch is driven by stub identities.
    from engine.timarit.naming import FileIdentity
    ids = {"A.pdf": FileIdentity("logretta", "A.pdf", "issue", year=2020, issue="1"),
           "B.pdf": FileIdentity("logretta", "B.pdf", "issue", year=2020, issue="1")}
    monkeypatch.setattr(imp, "identify", ids.__getitem__)
    monkeypatch.setattr(imp, "reject_reason", lambda rel: None)
    manifests, _ = plan_discovery(["A.pdf", "B.pdf"], lambda rel: rel.encode())
    assert len(manifests) == 1 and "fleiri en eitt heftisskjal" in manifests[0].error


def test_write_unmatched_yaml(tmp_path):
    p = tmp_path / "_unmatched.yaml"
    write_unmatched([{"path": "a.docx", "reason": "not_pdf"}], p)
    assert yaml.safe_load(p.read_text(encoding="utf-8")) == [{"path": "a.docx", "reason": "not_pdf"}]


def test_summary_counts_issues_per_source_and_unmatched_per_reason():
    manifests, unmatched = plan_discovery(sorted(FILES), lambda rel: FILES[rel])
    text_ = "\n".join(imp.summarize(manifests, unmatched))
    assert "timarit_logfraedinga" in text_ and "ulfljotur" in text_
    for reason in ("duplicate_of", "working_files", "not_pdf", "deferred_mde"):
        assert reason in text_
    assert f"{len(manifests)} hefti" in text_ and f"{len(unmatched)} skrár" in text_


# --- merging with a manifest that already exists ---------------------------------------------------------

TL = "Tímarit og fræðigreinar/Tímarit Lögfræðinga/"
OLD_PDF = TL + "TL 2010_01.pdf"


def _analysed(path, **kw):
    """A container issue after analyze: volume and label filled in, sha256/pages/page_map from the file."""
    fields = dict(
        source="timarit_logfraedinga", year=2010, volume=60, issue="1", label="Tímarit lögfræðinga, 60. árg. 1. hefti (2010)",
        file=OLD_PDF, files=[OLD_PDF], sha256="abc", pages=40, page_map={"0": 1}, status="approved", confidence=0.9,
        articles=[ArticleEntry(title="Grein", page_start=3, pdf_pages=[2, 5], provenance={"title": "user"}, confidence=0.9)])
    fields.update(kw)
    old = IssueManifest(**fields)
    save_manifest(old, path)
    return old


def _discovered(**kw):
    fields = dict(source="timarit_logfraedinga", year=2010, volume=None, issue="1", label="Tímarit lögfræðinga, 1. hefti (2010)",
                  file=OLD_PDF, files=[OLD_PDF], sha256="abc")
    fields.update(kw)
    return IssueManifest(**fields)


def test_merge_with_existing_keeps_the_analysis_when_the_file_is_unchanged(tmp_path):
    path = tmp_path / "2010_x_1.yaml"
    _analysed(path)
    out = imp.merge_with_existing(path, _discovered())
    assert out.volume == 60 and out.label.endswith("(2010)") and "60. árg." in out.label    # analyze's values survive
    assert out.sha256 == "abc" and out.pages == 40 and out.page_map == {"0": 1}
    assert out.status == "approved" and out.error is None
    assert [a.title for a in out.articles] == ["Grein"] and out.articles[0].provenance == {"title": "user"}
    assert out.articles[0].pdf_pages == [2, 5]


def test_merge_keeps_analysis_of_an_article_file_issue_and_refreshes_its_files(tmp_path):
    path = tmp_path / "x_5_x.yaml"
    _analysed(path, source="stjornmal_stjornsysla", year=2009, volume=5, issue=None, file=None, sha256=None, pages=None,
              page_map={}, label="Stjórnmál og stjórnsýsla, 5. árg. (2009)", files=["a.pdf"])
    new = IssueManifest(source="stjornmal_stjornsysla", year=None, volume=5, issue=None,
                        label="Stjórnmál og stjórnsýsla, 5. árg.", files=["a.pdf"])
    out = imp.merge_with_existing(path, new)
    assert out.year == 2009 and out.label.endswith("(2009)") and out.status == "approved" and out.error is None


def test_merge_without_existing_file_returns_the_new_manifest(tmp_path):
    new = IssueManifest(source="logretta", year=2020, volume=None, issue="1", label="L", files=["a.pdf"])
    assert imp.merge_with_existing(tmp_path / "nope.yaml", new) is new


def test_replaced_pdf_resets_file_derived_state_and_sends_a_non_frozen_issue_back_to_pending(tmp_path):
    path = tmp_path / "2010_x_1.yaml"
    _analysed(path, status="review")
    out = imp.merge_with_existing(path, _discovered(sha256="new"))
    assert out.sha256 == "new" and out.pages is None and out.page_map == {}
    assert out.status == "pending"
    assert [a.title for a in out.articles] == ["Grein"]                  # the user's articles are not thrown away


def test_replaced_pdf_of_an_approved_issue_keeps_the_status_and_flags_it(tmp_path):
    path = tmp_path / "2010_x_1.yaml"
    _analysed(path, status="approved")
    out = imp.merge_with_existing(path, _discovered(sha256="new"))
    assert out.status == "approved" and out.sha256 == "new" and out.pages is None and out.page_map == {}
    assert out.error == "frumskjal breyttist eftir samþykkt — yfirfarðu og greindu aftur"
    # the next discover sees the same bytes again and neither resets nor drops the flag
    save_manifest(out, path)
    again = imp.merge_with_existing(path, _discovered(sha256="new"))
    assert again.error == out.error and again.status == "approved"


def test_replaced_pdf_of_an_imported_issue_is_flagged_too(tmp_path):
    path = tmp_path / "2010_x_1.yaml"
    _analysed(path, status="imported")
    out = imp.merge_with_existing(path, _discovered(sha256="new"))
    assert out.status == "imported" and out.error.startswith("frumskjal breyttist")


def test_a_different_container_file_resets_the_state(tmp_path):
    path = tmp_path / "2010_x_1.yaml"
    _analysed(path, status="failed")
    other = TL + "TL 2010_01 (2).pdf"
    out = imp.merge_with_existing(path, _discovered(file=other, files=[other], sha256="abc"))
    assert out.file == other and out.status == "pending" and out.pages is None and out.page_map == {}


def test_a_changed_file_set_of_an_article_file_issue_resets_it(tmp_path):
    path = tmp_path / "x_5_x.yaml"
    _analysed(path, source="stjornmal_stjornsysla", year=2009, volume=5, issue=None, file=None, sha256=None, pages=None,
              page_map={}, label="L", files=["a.pdf"], status="review")
    new = IssueManifest(source="stjornmal_stjornsysla", year=None, volume=5, issue=None, label="L", files=["a.pdf", "b.pdf"])
    out = imp.merge_with_existing(path, new)
    assert out.status == "pending" and out.files == ["a.pdf", "b.pdf"]


def test_a_discovery_error_that_is_no_longer_reported_is_cleared_but_other_errors_are_not(tmp_path):
    path = tmp_path / "2010_x_1.yaml"
    _analysed(path, error="fleiri en eitt heftisskjal fyrir sama hefti: a.pdf; b.pdf")
    assert imp.merge_with_existing(path, _discovered()).error is None
    _analysed(path, error="analyze: textalag vantar")
    assert imp.merge_with_existing(path, _discovered()).error == "analyze: textalag vantar"
    _analysed(path, error=None)
    out = imp.merge_with_existing(path, _discovered(error="fleiri en eitt heftisskjal fyrir sama hefti: a.pdf; b.pdf"))
    assert out.error.startswith("fleiri en eitt heftisskjal")


def test_plan_discovery_stores_the_container_sha256_and_none_for_article_file_groups():
    import hashlib
    manifests, _ = plan_discovery(sorted(FILES), lambda rel: FILES[rel])
    keys = {(m.source, m.year, m.volume, m.issue): m for m in manifests}
    assert keys[("timarit_logfraedinga", 2010, None, "1")].sha256 == hashlib.sha256(b"A").hexdigest()
    assert keys[("ulfljotur", 1957, 11, None)].sha256 is None


# --- Task 23e: one PDF that holds two volumes is two issues --------------------------------------------------

TL7172 = TL + "TL 1971_1972.pdf"
LABEL71, LABEL72 = "Tímarit lögfræðinga, 21. árg. (1971)", "Tímarit lögfræðinga, 22. árg. (1972)"


def test_a_split_file_is_discovered_as_one_issue_per_volume_with_its_page_window():
    files = {TL7172: b"V", TL + "TL 1973.pdf": b"W"}
    manifests, unmatched = plan_discovery(sorted(files), files.__getitem__)
    tl = {m.year: m for m in manifests}
    assert unmatched == [] and sorted(tl) == [1971, 1972, 1973]            # neither part is a byte duplicate of the other
    a, b = tl[1971], tl[1972]
    assert (a.volume, a.year_end, a.issue, a.pdf_window, a.label) == (21, None, None, [0, 203], LABEL71)
    assert (b.volume, b.year_end, b.issue, b.pdf_window, b.label) == (22, None, None, [204, 375], LABEL72)
    assert a.file == b.file == TL7172 and a.files == b.files == [TL7172] and a.sha256 == b.sha256
    assert a.error is None and b.error is None and a.status == b.status == "pending"
    assert tl[1973].pdf_window is None and tl[1973].volume is None


def test_the_parts_of_a_split_file_are_never_compared_for_containment():
    manifests, unmatched = _run({TL7172: _pages("v7172", 12), TL + "TL 1973.pdf": _pages("v73", 6)})
    assert unmatched == [] and sorted(m.year for m in manifests) == [1971, 1972, 1973]


def test_a_part_is_kept_under_its_year_so_the_1971_part_keeps_the_whole_files_manifest(tmp_path):
    files = {TL7172: b"V"}
    manifests, _ = plan_discovery(sorted(files), files.__getitem__)
    assert [imp.discovery_path(tmp_path, m).name for m in manifests] == ["1971_x_x.yaml", "1972_x_x.yaml"]
    # any other issue: the path of its year, volume and issue, as before
    other = imp.manifest_path(tmp_path, "timarit_logfraedinga", 2010, None, "1")
    assert imp.discovery_path(tmp_path, _discovered()) == other
    logf = IssueManifest(source="logfraedingur", year=2010, volume=4, issue=None, label="L")
    assert imp.discovery_path(tmp_path, logf) == tmp_path / "logfraedingur" / "2010_4_x.yaml"


def _part71(**kw):
    fields = dict(year=1971, volume=21, issue=None, label=LABEL71, file=TL7172, files=[TL7172], sha256="abc",
                  pdf_window=[0, 203])
    fields.update(kw)
    return _discovered(**fields)


def test_merge_turns_the_whole_files_manifest_into_its_first_part(tmp_path):
    path = tmp_path / "1971_x_x.yaml"
    _analysed(path, year=1971, volume=22, issue=None, label="Tímarit lögfræðinga, 22. árg. (1971)", file=TL7172,
              files=[TL7172], sha256="abc", pages=376, year_end=1972, status="review")
    out = imp.merge_with_existing(path, _part71())
    assert (out.year, out.volume, out.year_end, out.issue, out.pdf_window, out.label) == (
        1971, 21, None, None, [0, 203], LABEL71)
    # the window is another issue of the file: what analysis found in the whole file is stale
    assert out.status == "pending" and out.pages is None and out.page_map == {}
    assert out.sha256 == "abc" and out.error is None
    assert [a.title for a in out.articles] == ["Grein"]                      # the user's articles are not thrown away
    save_manifest(out, path)
    assert imp.merge_with_existing(path, _part71()) == out                   # the next discover changes nothing


def test_merge_keeps_the_analysis_of_a_part_whose_window_is_unchanged(tmp_path):
    path = tmp_path / "1972_x_x.yaml"
    _analysed(path, year=1972, volume=22, issue=None, label=LABEL72, file=TL7172, files=[TL7172], sha256="abc", pages=376,
              pdf_window=[204, 375], status="review")
    out = imp.merge_with_existing(path, _part71(year=1972, volume=22, label=LABEL72, pdf_window=[204, 375]))
    assert out.status == "review" and out.pages == 376 and out.page_map == {"0": 1} and out.pdf_window == [204, 375]
    assert [a.pdf_pages for a in out.articles] == [[2, 5]]


def test_a_new_window_on_an_approved_issue_keeps_its_status_and_flags_it(tmp_path):
    path = tmp_path / "1971_x_x.yaml"
    _analysed(path, year=1971, volume=22, issue=None, file=TL7172, files=[TL7172], sha256="abc", year_end=1972)
    out = imp.merge_with_existing(path, _part71())
    assert out.status == "approved" and out.error.startswith("frumskjal breyttist") and out.pdf_window == [0, 203]


# --- content duplicates: a file whose text is inside a larger volume file ----------------------------------

def _pages(seed, n, words=110):
    """n pages of nonsense words, each page unlike any page of another seed."""
    out = []
    for i in range(n):
        r = random.Random(f"{seed}-{i}")
        out.append(" ".join("".join(r.choices("abcdefghijklmnopqrstuvwxyzðþæö", k=r.randint(3, 9))) for _ in range(words)))
    return out


def _noisy(pages, rate=0.01, seed=1):
    r = random.Random(seed)
    return ["".join(ch if r.random() > rate else "q" for ch in p) for p in pages]


def test_page_containment_counts_judged_and_matching_pages():
    big = _pages("big", 6)
    assert imp.page_containment(big[1:4], big) == (3, 3)
    assert imp.page_containment(_noisy(big[1:4]), big) == (3, 3)                # OCR-ish noise still matches
    assert imp.page_containment(_pages("other", 3), big) == (0, 3)
    assert imp.page_containment(["", "kort", "  "], big) == (0, 0)             # no text layer: nothing judged
    assert imp.page_containment(big[:2] + [""], big) == (2, 2)                  # blank pages are not judged


def test_page_containment_needs_half_of_a_pages_trigrams_on_one_container_page():
    big = _pages("big", 4)
    quarter = big[0][: len(big[0]) // 4] + " " + _pages("other", 1)[0]                        # a quarter + unrelated
    assert imp.page_containment([quarter], big) == (0, 1)
    mostly = big[0][: len(big[0]) * 3 // 4] + " " + _pages("other", 1)[0][: len(big[0]) // 8]
    assert imp.page_containment([mostly], big) == (1, 1)


def test_unrelated_pages_of_the_same_language_are_not_contained():
    """Same vocabulary, different text: character trigrams would overlap by more than half; word trigrams do not."""
    r = random.Random(7)
    vocab = ["".join(r.choices("aáeéiíoóuúyýþæöðrstnlmkgvhdbfjp", k=r.randint(3, 8))) for _ in range(250)]

    def page(seed):
        rr = random.Random(seed)
        return " ".join(rr.choice(vocab) for _ in range(160))
    assert imp.page_containment([page(f"a{i}") for i in range(5)], [page(f"b{i}") for i in range(40)]) == (0, 5)


VOL = TL + "TL 2004.pdf"
ISS2 = TL + "TL 2004_02.pdf"
ISS3 = TL + "TL 2004_03.pdf"
V75 = TL + "TL 1975.pdf"
ART75 = TL + "TL 1975_03/1975_100.pdf"
ART2A = TL + "TL 2004_02/2004_10.pdf"
ART2B = TL + "TL 2004_02/2004_20.pdf"


def _run(texts):
    files = {rel: rel.encode() for rel in texts}
    return plan_discovery(sorted(files), lambda rel: files[rel], page_texts=lambda rel: texts[rel])


def test_a_file_inside_a_volume_file_is_recorded_as_contained_and_dropped():
    vol = _pages("vol", 10)
    texts = {VOL: vol, ISS2: _noisy(vol[2:6]), ISS3: _pages("iss3", 4),
             V75: _pages("v75", 8), ART75: _pages("art75", 2)}
    manifests, unmatched = _run(texts)
    keys = {(m.source, m.year, m.volume, m.issue) for m in manifests}
    assert ("timarit_logfraedinga", 2004, None, "2") not in keys               # left with no file → dropped
    assert {("timarit_logfraedinga", 2004, None, None), ("timarit_logfraedinga", 2004, None, "3"),
            ("timarit_logfraedinga", 1975, None, None), ("timarit_logfraedinga", 1975, None, "3")} <= keys
    assert unmatched == [{"path": ISS2, "reason": "contained_in", "duplicate_of": VOL}]
    assert not any(ISS2 in m.files for m in manifests)


def test_only_the_contained_files_of_a_manifest_are_removed():
    vol = _pages("vol", 10)
    texts = {VOL: vol, ART2A: _noisy(vol[1:3]), ART2B: _pages("own", 3)}
    manifests, unmatched = _run(texts)
    iss2 = next(m for m in manifests if m.issue == "2")
    assert iss2.files == [ART2B] and iss2.file is None
    assert [(u["path"], u["reason"]) for u in unmatched] == [(ART2A, "contained_in")]


def test_a_file_that_is_not_larger_than_the_volume_file_is_never_contained():
    vol = _pages("vol", 5)
    manifests, unmatched = _run({VOL: vol, ISS2: _noisy(vol)})                 # same page count: keep both
    assert unmatched == [] and {m.issue for m in manifests} == {None, "2"}


def test_files_without_a_text_layer_are_kept():
    manifests, unmatched = _run({VOL: _pages("vol", 6), ISS2: ["", ""]})
    assert unmatched == [] and {m.issue for m in manifests} == {None, "2"}


def test_only_volume_files_can_contain_others():
    iss = _pages("iss", 8)
    manifests, unmatched = _run({ISS3: iss, TL + "TL 2004_04.pdf": _noisy(iss[:3])})
    assert unmatched == []


R = "Tímarit og fræðigreinar/Rannsóknir í félagsvísingum Lagadeild/Rannsóknir í félagsvísingum Lagadeild 2010 - "


def test_containment_is_judged_within_year_and_volume_and_resolves_chains():
    big = _pages("big", 12)
    texts = {R + "XI - Ritstyrt.pdf": big,
             R + "XI - Mid.pdf": _noisy(big[2:8]),
             R + "XI - Small.pdf": _noisy(big[3:5], seed=2),
             R + "X - Annad.pdf": _noisy(big[:4], seed=3)}                      # same text but another volume: not compared
    manifests, unmatched = _run(texts)
    assert {u["path"]: u["duplicate_of"] for u in unmatched} == {
        R + "XI - Mid.pdf": R + "XI - Ritstyrt.pdf", R + "XI - Small.pdf": R + "XI - Ritstyrt.pdf"}
    assert sorted((m.volume, m.issue) for m in manifests) == [(10, "annad"), (11, "ritstyrt")]


def test_without_a_page_reader_nothing_is_compared():
    manifests, unmatched = plan_discovery([VOL, ISS2], lambda rel: rel.encode())
    assert unmatched == [] and len(manifests) == 2


def test_pdf_page_texts_of_unreadable_bytes_is_empty(monkeypatch):
    monkeypatch.setattr(imp, "_read_archive", lambda rel: b"not a pdf")
    assert imp._pdf_page_texts("a.pdf") == []


# --- discover --dry-run writes nothing -------------------------------------------------------------------

async def test_dry_run_writes_nothing_and_opens_no_db_session(tmp_path, monkeypatch, capsys):
    drop = tmp_path / "drop"
    repo = tmp_path / "repo"
    for rel, data in FILES.items():
        p = drop / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (drop / ".DS_Store").write_bytes(b"x")
    (drop / "_meta").mkdir()
    (drop / "_meta" / "Ulfljotur_efnisyfirlit.xlsx").write_bytes(b"x")
    repo.mkdir()
    monkeypatch.setattr(imp, "DROP", drop)
    monkeypatch.setattr(imp, "REPO", repo)

    async def no_db(*a, **k):
        raise AssertionError("dry-run must not touch the database")
    monkeypatch.setattr(imp._db, "init_db", no_db)

    await imp.cmd_discover(argparse.Namespace(source=None, dry_run=True))
    assert list(repo.iterdir()) == []                                    # no data/, no manifests, no _unmatched.yaml
    out = capsys.readouterr().out
    assert "timarit_logfraedinga" in out and "deferred_mde" in out and "duplicate_of" in out
    assert "_meta" not in out and ".DS_Store" not in out


async def test_unreadable_archive_file_stops_discover_before_anything_is_written(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(imp, "DROP", tmp_path / "drop")
    monkeypatch.setattr(imp, "REPO", repo)
    monkeypatch.setattr(imp, "_list_archive", lambda: [OLD_PDF])               # listed but not on disk
    with pytest.raises(SystemExit) as e:
        await imp.cmd_discover(argparse.Namespace(source=None, dry_run=False))
    assert "ekki tókst að lesa" in str(e.value) and OLD_PDF in str(e.value)
    assert list(repo.iterdir()) == []


async def test_a_broken_manifest_stops_discover_with_an_icelandic_message(tmp_path, monkeypatch):
    drop, repo = tmp_path / "drop", tmp_path / "repo"
    (drop / TL).mkdir(parents=True)
    (drop / OLD_PDF).write_bytes(b"A")
    monkeypatch.setattr(imp, "DROP", drop)
    monkeypatch.setattr(imp, "REPO", repo)
    bad = imp.manifest_path(repo / imp.C.MANIFEST_ROOT, "timarit_logfraedinga", 2010, None, "1")
    bad.parent.mkdir(parents=True)
    bad.write_text("source: [", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        await imp.cmd_discover(argparse.Namespace(source=None, dry_run=True))
    assert str(e.value).startswith("discover: yfirlitsskrá")


def test_setup_logging_writes_a_dated_log_file_under_the_log_dir(tmp_path, monkeypatch):
    import datetime
    import logging
    monkeypatch.setattr(imp, "LOG_DIR", tmp_path / "logs" / "timarit")
    handlers = imp._setup_logging("discover")
    try:
        logging.getLogger("import_timarit").info("halló")
    finally:
        for h in handlers:
            logging.getLogger().removeHandler(h)
            h.close()
    log_file = tmp_path / "logs" / "timarit" / f"discover_{datetime.date.today():%Y-%m-%d}.log"
    assert "halló" in log_file.read_text(encoding="utf-8")


# --- upsert_issue_row against the real schema (always rolled back) ----------------------------------------

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")


class _Session:
    """upsert_issue_row only needs .execute() and, for an unknown source, .add()/.flush(); a rolled-back
    connection stands in for the session."""
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, *a, **k):
        return await self.conn.execute(*a, **k)


@needs_db
async def test_upsert_issue_row_inserts_updates_and_never_downgrades():
    eng = create_async_engine(os.environ["DATABASE_URL"])
    conn = await eng.connect()
    await conn.begin()
    try:
        # a throw-away source so the test does not depend on what the live DB holds
        await conn.execute(text(
            "INSERT INTO sources (id, short_name, display_name) VALUES (gen_random_uuid(), 'timarit_logfraedinga', 'x') "
            "ON CONFLICT (short_name) DO NOTHING"))
        s = _Session(conn)
        m = IssueManifest(source="timarit_logfraedinga", year=2010, volume=None, issue="1",
                          label="Tímarit lögfræðinga, 1. hefti (2010)", file="a/TL 2010_01.pdf")
        rel = "data/timarit/manifests/timarit_logfraedinga/__test__2010_x_1.yaml"
        await imp.upsert_issue_row(s, m, rel)
        row = (await conn.execute(text(
            "SELECT year, volume, issue, label, file_path, status FROM journal_issues WHERE manifest_path = :p"),
            {"p": rel})).one()
        assert tuple(row) == (2010, None, "1", m.label, "a/TL 2010_01.pdf", "pending")

        # analyze later learns the volume: same manifest → same row, now carrying the volume and label. Volume 960, as
        # in the import tests: uq_journal_issue (source, year, volume, issue) holds in the live database, where the
        # real TL 2010 1. hefti is volume 60, so the test's row must have an identity no real row can have.
        m.volume, m.label, m.status = 960, "Tímarit lögfræðinga, 960. árg. 1. hefti (2010)", "approved"
        await imp.upsert_issue_row(s, m, rel)
        rows = (await conn.execute(text(
            "SELECT volume, label, status FROM journal_issues WHERE manifest_path = :p"), {"p": rel})).all()
        assert [tuple(r) for r in rows] == [(960, m.label, "approved")]

        # an approved row is not demoted by a later review result
        m.status = "review"
        await imp.upsert_issue_row(s, m, rel)
        assert (await conn.execute(text("SELECT status FROM journal_issues WHERE manifest_path = :p"),
                                   {"p": rel})).scalar() == "approved"

        # an imported row keeps its status and the RAW copy path import wrote into file_path
        await conn.execute(text("UPDATE journal_issues SET status='imported', file_path='raw/hefti/x.pdf' "
                                "WHERE manifest_path = :p"), {"p": rel})
        m.status = "pending"
        await imp.upsert_issue_row(s, m, rel)
        row = (await conn.execute(text("SELECT status, file_path FROM journal_issues WHERE manifest_path = :p"),
                                  {"p": rel})).one()
        assert tuple(row) == ("imported", "raw/hefti/x.pdf")

        # year None (S&S before analyze) is stored as the NOT NULL placeholder 0
        m2 = IssueManifest(source="timarit_logfraedinga", year=None, volume=960, issue=None, label="L")
        rel2 = rel.replace("2010_x_1", "x_960_x")
        await imp.upsert_issue_row(s, m2, rel2)
        assert (await conn.execute(text("SELECT year FROM journal_issues WHERE manifest_path = :p"),
                                   {"p": rel2})).scalar() == 0
    finally:
        await conn.rollback()
        await conn.close()
        await eng.dispose()
