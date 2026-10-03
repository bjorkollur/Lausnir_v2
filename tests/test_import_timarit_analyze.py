# tests/test_import_timarit_analyze.py
"""Task 19: `import_timarit.py analyze`. The brief's fixtures were rebuilt so that they are consistent with the
real TOC finder and page map (ruling R7); every change is listed in the task report."""
import argparse
import logging
import os
import shutil

import fitz
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from engine.timarit.analyze import AnalyzeDeps
from engine.timarit.leitir_articles import LeitirArticle
from engine.timarit.llm_toc import pending_entries, write_answer
from engine.timarit.manifest import ArticleEntry, IssueManifest, load_manifest, save_manifest
from scripts import import_timarit as imp
from scripts.import_timarit import analyze_manifests

_PAD = "Meginmál greinarinnar heldur áfram hér með nægum texta til að síðan hafi textalag."
SRC = "timarit_logfraedinga"
LABEL = "Tímarit lögfræðinga, 60. árg. 1. hefti (2010)"


def _pdf(pages: list[str]) -> bytes:
    d = fitz.open()
    for t in pages:
        page = d.new_page()
        if t:
            page.insert_text((72, 72), t, fontsize=11)
    return d.tobytes()


def _issue_pdf(toc_tail: str = "") -> bytes:
    """An 11-page TL-style issue: cover, TOC (page-first, three entries: a real TOC needs >= 3 plausible numbered
    lines), three articles starting on printed pages 1, 5 and 9; every article page prints its number on its first
    line, so printed = pdf index - 1. (R7: the brief's single-line TOC and 5-line pages are not detectable.)"""
    toc = ("Efnisyfirlit\n1\nJón Jónsson:\nFyrsta greinin\n5\nAnna Pálsdóttir:\nÖnnur greinin um dóma\n"
           "9\nPáll Pálsson:\nRitdómur: Bók um kröfurétt\n" + toc_tail + "1. hefti 60. árgangur\nvor 2010")
    pages = ["Lögfræðingafélag Íslands\nTímarit lögfræðinga", toc,
             f"1\nJón Jónsson\nFyrsta greinin\nTexti fyrstu greinar.\n{_PAD}"]
    pages += [f"{p}\nFramhald fyrstu greinar.\n{_PAD}" for p in (2, 3, 4)]
    pages += [f"5\nAnna Pálsdóttir\nÖnnur greinin um dóma\nTexti.\n{_PAD}"]
    pages += [f"{p}\nFramhald annarrar greinar.\n{_PAD}" for p in (6, 7, 8)]
    pages += [f"9\nPáll Pálsson\nRitdómur: Bók um kröfurétt\nRitdómur.\n{_PAD}"]
    return _pdf(pages)


def _deps(pdf):
    return lambda: AnalyzeDeps(read_bytes=lambda rel: pdf, leitir=None, llm_toc=None, xlsx_rows=[])


def _manifest(issue="1", file="x.pdf", **over):
    base = dict(source=SRC, year=2010, volume=60, issue=issue, label=LABEL, file=file, files=[file])
    base.update(over)
    return IssueManifest(**base)


def _save(tmp_path, m, name=None):
    p = tmp_path / m.source / (name or f"{m.year}_{m.volume}_{m.issue}.yaml")
    save_manifest(m, p)
    return p


# --- the brief's three tests (fixtures per R7) ------------------------------------------------------------

async def test_analyze_writes_articles_and_status_and_keeps_user_edits(tmp_path):
    m = _manifest()
    m.articles = [ArticleEntry(title="Titill frá notanda", page_start=1, provenance={"title": "user"})]
    p = _save(tmp_path, m)
    counts = await analyze_manifests([p], deps_factory=_deps(_issue_pdf()), save=True)
    out = load_manifest(p)
    assert sum(counts.values()) == 1 and out.status in ("approved", "review")
    assert len(out.articles) == 3 and out.pages == 11 and "bin_ratio" in out.page_map
    assert out.articles[0].title == "Titill frá notanda" and out.articles[0].provenance["title"] == "user"
    assert out.articles[1].title == "Önnur greinin um dóma" and out.articles[1].pdf_pages == [6, 9]


async def test_analyze_marks_failed_and_continues(tmp_path):
    good = _save(tmp_path, _manifest("1"))
    bad = _save(tmp_path, _manifest("2", file="missing.pdf"))
    pdf = _issue_pdf()

    def read(rel):
        if rel == "missing.pdf":
            raise FileNotFoundError(rel)
        return pdf
    counts = await analyze_manifests([bad, good], deps_factory=lambda: AnalyzeDeps(read, None, None, []), save=True)
    assert counts.get("failed") == 1 and load_manifest(bad).status == "failed"
    assert "FileNotFoundError" in load_manifest(bad).error
    assert load_manifest(good).status in ("approved", "review") and sum(counts.values()) == 2


async def test_analyze_never_touches_approved_or_imported(tmp_path):
    imported = _save(tmp_path, _manifest("1", status="imported"))
    approved = _save(tmp_path, _manifest("2", status="approved"))
    before = {p: p.read_bytes() for p in (imported, approved)}
    counts = await analyze_manifests([imported, approved], deps_factory=_deps(_issue_pdf()), save=True)
    assert counts == {"skipped": 2}
    assert {p: p.read_bytes() for p in before} == before                  # not rewritten, not even re-serialised
    assert load_manifest(imported).articles == []


# --- failure isolation -------------------------------------------------------------------------------------

async def test_a_broken_manifest_file_counts_as_failed_stays_untouched_and_the_run_continues(tmp_path):
    broken = tmp_path / SRC / "2010_60_2.yaml"
    broken.parent.mkdir(parents=True)
    broken.write_text("source: [", encoding="utf-8")
    good = _save(tmp_path, _manifest("1"))
    counts = await analyze_manifests([broken, good], deps_factory=_deps(_issue_pdf()), save=True)
    assert counts.get("failed") == 1 and sum(counts.values()) == 2
    assert broken.read_text(encoding="utf-8") == "source: ["
    assert load_manifest(good).status in ("approved", "review")


async def test_the_error_is_the_exceptions_first_line_and_the_users_articles_survive(tmp_path):
    m = _manifest()
    m.articles = [ArticleEntry(title="Mín grein", page_start=3, provenance={"title": "user"}, note="athugasemd")]
    p = _save(tmp_path, m)

    def boom(rel):
        raise RuntimeError("fyrsta lína\nönnur lína\nþriðja")
    await analyze_manifests([p], deps_factory=lambda: AnalyzeDeps(boom, None, None, []), save=True)
    out = load_manifest(p)
    assert out.status == "failed" and out.error == "RuntimeError: fyrsta lína"
    assert [a.title for a in out.articles] == ["Mín grein"] and out.articles[0].note == "athugasemd"


async def test_an_exception_without_a_message_gives_just_its_name(tmp_path):
    p = _save(tmp_path, _manifest())

    def boom(rel):
        raise KeyError()
    await analyze_manifests([p], deps_factory=lambda: AnalyzeDeps(boom, None, None, []), save=True)
    assert load_manifest(p).error == "KeyError"


async def test_an_unknown_source_fails_that_issue_only(tmp_path):
    odd = _save(tmp_path, _manifest("1", source="engin_heimild"), "odd.yaml")
    good = _save(tmp_path, _manifest("2"))
    counts = await analyze_manifests([odd, good], deps_factory=_deps(_issue_pdf()), save=True)
    assert counts.get("failed") == 1 and load_manifest(odd).status == "failed"
    assert load_manifest(good).status in ("approved", "review")


async def test_a_failed_issue_is_analysed_again_and_loses_its_error(tmp_path):
    m = _manifest(status="failed", error="FileNotFoundError: x.pdf")
    p = _save(tmp_path, m)
    counts = await analyze_manifests([p], deps_factory=_deps(_issue_pdf()), save=True)
    out = load_manifest(p)
    assert "failed" not in counts and out.status in ("approved", "review") and out.error is None


# --- re-running analyze ------------------------------------------------------------------------------------

async def test_analysis_notes_are_replaced_and_the_users_note_is_kept_across_two_runs(tmp_path):
    m = _manifest()
    m.articles = [ArticleEntry(title="Fyrsta greinin", page_start=1, note="Notandi: athuga höfund",
                               analysis_notes=["gömul vélathugasemd"])]
    p = _save(tmp_path, m)
    # a TOC line that points beyond the document gives a machine note that must not pile up
    pdf = _issue_pdf(toc_tail="99\nÖssur Össurarson:\nGrein utan skjals\n")
    for _ in range(2):
        await analyze_manifests([p], deps_factory=_deps(pdf), save=True)
        out = load_manifest(p)
        first = next(a for a in out.articles if a.page_start == 1)
        assert first.note == "Notandi: athuga höfund"
        assert "gömul vélathugasemd" not in first.analysis_notes
        outside = [a for a in out.articles if a.page_start == 99]
        assert len(outside) == 1
        assert len(outside[0].analysis_notes) == len(set(outside[0].analysis_notes)) >= 1
    assert sum(1 for a in out.articles if a.page_start == 1) == 1          # merged, not duplicated


# --- discovery's own error survives analysis ----------------------------------------------------------------

DISCOVERY_ERROR = "fleiri en eitt heftisskjal fyrir sama hefti: a.pdf; b.pdf"


def _records():
    """leitir.is records for all three articles of _issue_pdf(): with them every article scores 1.0."""
    rec = lambda t, au, p1, p2, i: LeitirArticle(t, [au], "Tímarit lögfræðinga", 2010, 60, "1", p1, p2, i)   # noqa: E731
    return [rec("Fyrsta greinin", "Jón Jónsson", 1, 4, "r1"), rec("Önnur greinin um dóma", "Anna Pálsdóttir", 5, 8, "r2"),
            rec("Ritdómur: Bók um kröfurétt", "Páll Pálsson", 9, 9, "r3")]


def _deps_with_records(pdf):
    async def leitir(q, journal):
        return _records()
    return lambda: AnalyzeDeps(read_bytes=lambda rel: pdf, leitir=leitir, llm_toc=None, xlsx_rows=[])


async def test_a_discovery_error_survives_analysis_and_the_issue_goes_to_review(tmp_path):
    pdf = _issue_pdf()
    control = _save(tmp_path, _manifest("1"))
    await analyze_manifests([control], deps_factory=_deps_with_records(pdf), save=True)
    assert load_manifest(control).status == "approved"                     # the issue is approvable without the error
    flagged = _save(tmp_path, _manifest("2", error=DISCOVERY_ERROR))
    for _ in range(2):                                                     # and a second run neither drops nor doubles it
        await analyze_manifests([flagged], deps_factory=_deps_with_records(pdf), save=True)
        out = load_manifest(flagged)
        assert out.status == "review" and out.error == DISCOVERY_ERROR and len(out.articles) == 3


async def test_the_discovery_error_is_kept_next_to_an_analysis_error_and_a_failure(tmp_path):
    scan = _save(tmp_path, _manifest("1", error=DISCOVERY_ERROR))
    for _ in range(2):                                                     # rerunning does not pile the errors up
        await analyze_manifests([scan], deps_factory=_deps(_pdf(["", "", ""])), save=True)
        out = load_manifest(scan)
        assert out.status == "review" and out.error == DISCOVERY_ERROR + " | ekkert textalag — þarf OCR"

    broken = _save(tmp_path, _manifest("2", error=DISCOVERY_ERROR))

    def boom(rel):
        raise FileNotFoundError(rel)
    await analyze_manifests([broken], deps_factory=lambda: AnalyzeDeps(boom, None, None, []), save=True)
    out = load_manifest(broken)
    assert out.status == "failed" and out.error == DISCOVERY_ERROR + " | FileNotFoundError: x.pdf"


async def test_an_error_analysis_wrote_earlier_is_not_carried_over(tmp_path):
    p = _save(tmp_path, _manifest(error="FileNotFoundError: x.pdf"))
    await analyze_manifests([p], deps_factory=_deps_with_records(_issue_pdf()), save=True)
    assert load_manifest(p).error is None and load_manifest(p).status == "approved"


def test_discover_removes_only_its_own_part_of_a_combined_error(tmp_path):
    """The error analyze keeps in front of its own is still discovery's: when discover stops reporting it, what
    analyze appended stays; when it reports it again it replaces the discovery part only."""
    combined = DISCOVERY_ERROR + " | ekkert textalag — þarf OCR"
    p = _save(tmp_path, _manifest(error=combined))
    new = IssueManifest(source=SRC, year=2010, volume=None, issue="1", label="L", file="x.pdf", files=["x.pdf"])
    assert imp.merge_with_existing(p, new).error == "ekkert textalag — þarf OCR"
    new.error = "fleiri en eitt heftisskjal fyrir sama hefti: a.pdf; c.pdf"
    assert imp.merge_with_existing(p, new).error == new.error + " | ekkert textalag — þarf OCR"
    plain = _save(tmp_path, _manifest("2", error=DISCOVERY_ERROR))
    assert imp.merge_with_existing(plain, IssueManifest(source=SRC, year=2010, volume=None, issue="2", label="L",
                                                        file="x.pdf", files=["x.pdf"])).error is None


# --- bin_ratio ---------------------------------------------------------------------------------------------

async def test_bin_ratio_is_stored_for_a_container(tmp_path):
    p = _save(tmp_path, _manifest())
    await analyze_manifests([p], deps_factory=_deps(_issue_pdf()), save=True)
    out = load_manifest(p)
    assert 0.0 < out.page_map["bin_ratio"] <= 1.0
    assert out.page_map["offset"] == -1 and out.page_map["segments"]          # the page map itself is still there


async def test_bin_ratio_is_stored_for_a_container_without_a_text_layer(tmp_path):
    p = _save(tmp_path, _manifest())
    await analyze_manifests([p], deps_factory=_deps(_pdf(["", "", ""])), save=True)
    out = load_manifest(p)
    assert out.page_map["bin_ratio"] == 0.0 and out.error and out.status == "review"


async def test_bin_ratio_separates_icelandic_text_from_noise(tmp_path):
    noise = "\n".join("xqzvk wjhgf zzqpt bvnmx qwrtz kxcvb " * 3 for _ in range(6))
    clean = _save(tmp_path, _manifest("1"))
    dirty = _save(tmp_path, _manifest("2"))
    await analyze_manifests([clean], deps_factory=_deps(_issue_pdf()), save=True)
    await analyze_manifests([dirty], deps_factory=_deps(_pdf(["Kápa", noise, noise, noise])), save=True)
    assert load_manifest(clean).page_map["bin_ratio"] > 0.5 > load_manifest(dirty).page_map["bin_ratio"]


async def test_bin_ratio_is_stored_for_an_article_folder_whose_page_map_is_otherwise_empty(tmp_path):
    files = ["a/ein.pdf", "a/tvo.pdf"]
    m = IssueManifest(source="fraedigreinar_ymsar", year=2020, volume=None, issue=None, label="Fræðigreinar (2020)",
                      files=files)
    p = _save(tmp_path, m, "2020_x_x.yaml")
    pdf = _pdf([f"Grein um dóma\nEftir Jón Jónsson\n{_PAD}\n{_PAD}"])
    await analyze_manifests([p], deps_factory=_deps(pdf), save=True)
    out = load_manifest(p)
    assert out.pages is None and out.sha256 is None
    assert list(out.page_map) == ["bin_ratio"] and 0.0 < out.page_map["bin_ratio"] <= 1.0


async def test_bin_ratio_of_an_article_folder_samples_files_spread_across_the_folder(tmp_path):
    """A 100-file folder must not be judged by its first three files only."""
    files = [f"a/{i:03d}.pdf" for i in range(100)]
    m = IssueManifest(source="fraedigreinar_ymsar", year=2020, volume=None, issue=None, label="L", files=files)
    p = _save(tmp_path, m, "2020_x_x.yaml")
    clean = _pdf([f"Grein um dóma\nEftir Jón Jónsson\n{_PAD}\n{_PAD}"])
    noise = _pdf(["xqzvk wjhgf zzqpt bvnmx qwrtz kxcvb " * 40])
    # only the files from the middle on are noise: the sample must reach them
    await analyze_manifests([p], deps_factory=lambda: AnalyzeDeps(
        lambda rel: clean if int(rel[2:5]) < 50 else noise, None, None, []), save=True)
    ratio = load_manifest(p).page_map["bin_ratio"]
    assert 0.2 < ratio < 0.8


def _words_pdf(line: str, lines: int) -> bytes:
    """One page of about 300 words (50 lines of 6 words), the size of a real article's first page."""
    return _pdf(["\n".join([line] * lines)])


CLEAN_300 = ("Meginmál greinarinnar heldur áfram hér með nægum texta til að síðan hafi textalag", 23)   # 12 words x 23 lines
NOISE_300 = ("xqzvk wjhgf zzqpt bvnmx qwrtz kxcvb", 50)


async def test_bin_ratio_of_a_folder_is_the_mean_of_its_sampled_pages_not_the_first_two_thousand_words(tmp_path):
    """bin_known_ratio stops after 2000 words, i.e. after ~7 pages of 300 words: pooled text would only show the
    first files. 39 files, the first 20 clean and the rest noise, stride 2 reaches both halves: about 0.5."""
    clean, noise = _words_pdf(*CLEAN_300), _words_pdf(*NOISE_300)
    files = [f"a/{i:03d}.pdf" for i in range(39)]
    m = IssueManifest(source="fraedigreinar_ymsar", year=2020, volume=None, issue=None, label="L", files=files)
    p = _save(tmp_path, m, "2020_x_x.yaml")
    await analyze_manifests([p], deps_factory=lambda: AnalyzeDeps(
        lambda rel: clean if int(rel[2:5]) < 20 else noise, None, None, []), save=True)
    ratio = load_manifest(p).page_map["bin_ratio"]
    assert 0.35 < ratio < 0.6, ratio


async def test_bin_ratio_of_a_folder_ignores_pages_without_words(tmp_path):
    clean, blank = _words_pdf(*CLEAN_300), _pdf([""])
    files = [f"a/{i:03d}.pdf" for i in range(10)]
    m = IssueManifest(source="fraedigreinar_ymsar", year=2020, volume=None, issue=None, label="L", files=files)
    p = _save(tmp_path, m, "2020_x_x.yaml")
    await analyze_manifests([p], deps_factory=lambda: AnalyzeDeps(
        lambda rel: clean if int(rel[2:5]) % 2 else blank, None, None, []), save=True)
    assert load_manifest(p).page_map["bin_ratio"] > 0.8                    # scans are not "noise"; only text pages count


async def test_bin_ratio_of_a_folder_with_no_text_at_all_is_zero(tmp_path):
    m = IssueManifest(source="fraedigreinar_ymsar", year=2020, volume=None, issue=None, label="L", files=["a/1.pdf"])
    p = _save(tmp_path, m, "2020_x_x.yaml")
    await analyze_manifests([p], deps_factory=_deps(_pdf([""])), save=True)
    assert load_manifest(p).page_map == {"bin_ratio": 0.0}


# --- save / dry-run ---------------------------------------------------------------------------------------

async def test_save_false_writes_nothing(tmp_path):
    p = _save(tmp_path, _manifest())
    before = p.read_bytes()
    counts = await analyze_manifests([p], deps_factory=_deps(_issue_pdf()), save=False)
    assert sum(counts.values()) == 1 and p.read_bytes() == before


# --- the session: one commit per manifest, DB failures isolated -------------------------------------------

class _FakeSession:
    def __init__(self, events):
        self.events = events

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_each_manifest_is_upserted_and_committed_before_the_next_is_analysed(tmp_path, monkeypatch):
    events = []

    async def upsert(session, m, rel):
        events.append(f"upsert {m.issue} {m.status}")
    monkeypatch.setattr(imp, "upsert_issue_row", upsert)
    pdf = _issue_pdf()

    def read(rel):
        events.append(f"read {rel}")
        return pdf
    a = _save(tmp_path, _manifest("1", file="one.pdf"))
    b = _save(tmp_path, _manifest("2", file="two.pdf"))
    await analyze_manifests([a, b], deps_factory=lambda: AnalyzeDeps(read, None, None, []), save=True,
                            session=_FakeSession(events))
    kinds = [e.split(" ")[0] for e in events]
    assert kinds == ["read", "upsert", "commit", "read", "upsert", "commit"]


async def test_a_failed_analysis_is_written_to_the_row_too(tmp_path, monkeypatch):
    seen = []

    async def upsert(session, m, rel):
        seen.append((m.status, m.error))
    monkeypatch.setattr(imp, "upsert_issue_row", upsert)

    def boom(rel):
        raise FileNotFoundError(rel)
    p = _save(tmp_path, _manifest())
    await analyze_manifests([p], deps_factory=lambda: AnalyzeDeps(boom, None, None, []), save=True,
                            session=_FakeSession([]))
    assert seen == [("failed", "FileNotFoundError: x.pdf")]


async def test_a_database_error_fails_that_issue_only_and_is_written_to_its_manifest(tmp_path, monkeypatch):
    events = []

    async def upsert(session, m, rel):
        if m.issue == "1":
            raise RuntimeError("duplicate key value violates unique constraint")
    monkeypatch.setattr(imp, "upsert_issue_row", upsert)
    a = _save(tmp_path, _manifest("1"))
    b = _save(tmp_path, _manifest("2"))
    counts = await analyze_manifests([a, b], deps_factory=_deps(_issue_pdf()), save=True, session=_FakeSession(events))
    assert "rollback" in events and counts.get("failed") == 1 and sum(counts.values()) == 2
    out = load_manifest(a)
    assert out.status == "failed" and "gagnagrunnur" in out.error and "duplicate key" in out.error
    assert len(out.articles) == 3                                          # the analysis itself is kept
    assert load_manifest(b).status in ("approved", "review")


async def test_no_session_means_no_database_work_and_a_session_with_save_false_is_not_used(tmp_path, monkeypatch):
    async def upsert(*a):
        raise AssertionError("no database work expected")
    monkeypatch.setattr(imp, "upsert_issue_row", upsert)
    p = _save(tmp_path, _manifest())
    await analyze_manifests([p], deps_factory=_deps(_issue_pdf()), save=True)
    await analyze_manifests([p], deps_factory=_deps(_issue_pdf()), save=False, session=_FakeSession([]))


# --- selection ---------------------------------------------------------------------------------------------

def _repo_with(tmp_path, monkeypatch, specs):
    """specs: [(source, file stem, status)]; returns the paths in sorted order."""
    monkeypatch.setattr(imp, "REPO", tmp_path)
    root = tmp_path / imp.C.MANIFEST_ROOT
    paths = []
    for source, stem, status in specs:
        year, volume, issue = stem.split("_")
        m = IssueManifest(source=source, year=int(year), volume=None if volume == "x" else int(volume),
                          issue=None if issue == "x" else issue, label="L", status=status, files=[])
        p = root / source / f"{stem}.yaml"
        save_manifest(m, p)
        paths.append(p)
    return sorted(paths)


def _args(**kw):
    base = dict(source=None, issue=None, limit=None, dry_run=False, workers=4, command="analyze")
    base.update(kw)
    return argparse.Namespace(**base)


SPECS = [("timarit_logfraedinga", "2010_x_1", "pending"), ("timarit_logfraedinga", "2010_x_2", "approved"),
         ("timarit_logfraedinga", "2011_x_1", "review"), ("logretta", "2020_x_1", "pending"),
         ("logretta", "2020_x_10", "imported"), ("logretta", "2020_x_2", "failed")]


def test_select_manifests_by_source_issue_and_limit(tmp_path, monkeypatch):
    paths = _repo_with(tmp_path, monkeypatch, SPECS)
    stems = lambda sel: [(p.parent.name, p.stem) for p in sel]            # noqa: E731
    assert len(imp._select_manifests(_args())) == 6
    assert {s for s, _ in stems(imp._select_manifests(_args(source="logretta")))} == {"logretta"}
    # --issue 2010 matches 2010_*; --issue 2010_x_1 only that stem (and not 2010_x_10 style prefixes)
    assert stems(imp._select_manifests(_args(issue="2020_x_1"))) == [("logretta", "2020_x_1")]
    assert [s for _, s in stems(imp._select_manifests(_args(issue="2010")))] == ["2010_x_1", "2010_x_2"]
    assert len(paths) == 6


def test_limit_counts_only_manifests_that_analyze_will_actually_run(tmp_path, monkeypatch):
    _repo_with(tmp_path, monkeypatch, SPECS)
    sel = imp._select_manifests(_args(source="timarit_logfraedinga", limit=2))
    # 2010_x_2 is approved: skipping it must not eat into the limit
    assert [p.stem for p in sel] == ["2010_x_1", "2011_x_1"]
    sel = imp._select_manifests(_args(source="logretta", limit=3))
    assert [p.stem for p in sel] == ["2020_x_1", "2020_x_2"]               # failed ones are retried, imported are not


def test_a_broken_manifest_stays_selected_so_that_analyze_reports_it(tmp_path, monkeypatch):
    paths = _repo_with(tmp_path, monkeypatch, SPECS[:1])
    bad = paths[0].parent / "2010_x_9.yaml"
    bad.write_text("source: [", encoding="utf-8")
    assert [p.stem for p in imp._select_manifests(_args(limit=5))] == ["2010_x_1", "2010_x_9"]


# --- cmd_analyze -------------------------------------------------------------------------------------------

def _archive(tmp_path, monkeypatch, pdf):
    """A repo with one pending manifest, an archive with its PDF, caches and the spreadsheet pointed at tmp_path,
    no network (leitir stubbed), no Claude."""
    drop, repo = tmp_path / "drop", tmp_path / "repo"
    (drop / "j").mkdir(parents=True)
    (drop / "j" / "x.pdf").write_bytes(pdf)
    monkeypatch.setattr(imp, "DROP", drop)
    monkeypatch.setattr(imp, "REPO", repo)
    monkeypatch.setattr(imp.C, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(imp.C, "XLSX_PATH", tmp_path / "none.xlsx")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def no_search(*a, **k):
        return []
    monkeypatch.setattr(imp, "search_articles", no_search)
    p = imp.manifest_path(repo / imp.C.MANIFEST_ROOT, SRC, 2010, 60, "1")
    save_manifest(_manifest(file="j/x.pdf"), p)
    return p, repo


async def test_dry_run_writes_nothing_and_opens_no_database(tmp_path, monkeypatch, capsys):
    p, repo = _archive(tmp_path, monkeypatch, _issue_pdf())
    before = {q: q.read_bytes() for q in repo.rglob("*") if q.is_file()}

    async def no_db(*a, **k):
        raise AssertionError("dry-run must not touch the database")
    monkeypatch.setattr(imp._db, "init_db", no_db)
    await imp.cmd_analyze(_args(dry_run=True))
    assert {q: q.read_bytes() for q in repo.rglob("*") if q.is_file()} == before     # no manifest, no new file
    assert "Þurrkeyrsla" in capsys.readouterr().out
    assert not (tmp_path / "cache").exists() or not any((tmp_path / "cache").rglob("*.json"))


async def test_cmd_analyze_saves_the_manifest_and_commits_once_per_manifest(tmp_path, monkeypatch, capsys):
    p, repo = _archive(tmp_path, monkeypatch, _issue_pdf())
    events = []

    async def init_db(*a, **k):
        events.append("init")

    async def dispose_db():
        events.append("dispose")

    async def upsert(session, m, rel):
        events.append(f"upsert {rel} {m.status}")
    monkeypatch.setattr(imp._db, "init_db", init_db)
    monkeypatch.setattr(imp._db, "dispose_db", dispose_db)
    monkeypatch.setattr(imp._db, "AsyncSessionLocal", lambda: _FakeSession(events))
    monkeypatch.setattr(imp, "upsert_issue_row", upsert)
    await imp.cmd_analyze(_args())
    out = load_manifest(p)
    assert out.status in ("approved", "review") and len(out.articles) == 3
    rel = "data/timarit/manifests/timarit_logfraedinga/2010_60_1.yaml"
    assert events == ["init", f"upsert {rel} {out.status}", "commit", "dispose"]
    assert out.status in capsys.readouterr().out                                    # the summary names the result


async def test_cmd_analyze_with_nothing_selected_says_so_and_opens_no_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(imp, "REPO", tmp_path)

    async def no_db(*a, **k):
        raise AssertionError("nothing to do: no database")
    monkeypatch.setattr(imp._db, "init_db", no_db)
    await imp.cmd_analyze(_args())
    assert "discover" in capsys.readouterr().out


# --- the real dependencies -----------------------------------------------------------------------------------

async def test_real_deps_read_the_archive_and_wrap_leitir_with_the_shared_client_and_cache(tmp_path, monkeypatch):
    (tmp_path / "drop" / "j").mkdir(parents=True)
    (tmp_path / "drop" / "j" / "a.pdf").write_bytes(b"bytes")
    monkeypatch.setattr(imp, "DROP", tmp_path / "drop")
    monkeypatch.setattr(imp.C, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(imp.C, "XLSX_PATH", tmp_path / "none.xlsx")
    calls = []

    async def search(client, q, **kw):
        calls.append((client, q, kw))
        return []
    monkeypatch.setattr(imp, "search_articles", search)
    client = object()
    deps = imp._real_deps_factory(client)()
    assert deps.read_bytes("j/a.pdf") == b"bytes" and deps.xlsx_rows == []
    assert deps.llm_toc is not None                       # no Claude client: the offline wrapper, never None (Task 22a)
    await deps.leitir("titill", "Tímarit lögfræðinga")
    assert calls == [(client, "titill", {"cache_dir": tmp_path / "cache" / "leitir", "journal_filter": "Tímarit lögfræðinga"})]


async def test_real_deps_use_the_shared_claude_client_only_when_given(tmp_path, monkeypatch):
    monkeypatch.setattr(imp.C, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(imp.C, "XLSX_PATH", tmp_path / "none.xlsx")
    calls = []

    async def toc(text_, **kw):
        calls.append((text_, kw))
        return []
    monkeypatch.setattr(imp, "toc_via_llm", toc)
    llm = object()
    deps = imp._real_deps_factory(object(), llm)()
    assert await deps.llm_toc("efnisyfirlit") == []
    assert calls == [("efnisyfirlit", {"cache_dir": tmp_path / "cache" / "llm", "client": llm})]   # unchanged with a key


async def test_real_deps_without_a_claude_client_use_the_offline_wrapper(tmp_path, monkeypatch):
    """Task 22a: no ANTHROPIC_API_KEY → toc_via_llm only reads its cache and queues, under the issue's identity."""
    monkeypatch.setattr(imp.C, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(imp.C, "XLSX_PATH", tmp_path / "none.xlsx")
    calls = []

    async def toc(text_, **kw):
        calls.append((text_, kw))
        return []
    monkeypatch.setattr(imp, "toc_via_llm", toc)
    deps = imp._real_deps_factory(object(), None)()
    assert await deps.llm_toc("efnisyfirlit") == []
    assert calls == [("efnisyfirlit", {"cache_dir": tmp_path / "cache" / "llm", "offline": True, "context": None})]


# --- Task 22a: the offline Claude fallback through analyze ----------------------------------------------------

def _unreadable_toc_pdf():
    return _pdf(["Kápa", "Efnisyfirlit sem les ekki ...", f"1\nJón Jónsson\nFyrsta greinin\nTexti.\n{_PAD}",
                 f"2\n{_PAD}", f"3\n{_PAD}"])


def _anthropic_must_not_be_built(monkeypatch):
    import anthropic

    def boom(*a, **k):
        raise AssertionError("no Claude client may be constructed without an API key")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", boom)


async def test_an_unreadable_toc_is_queued_under_the_issue_identity_and_the_written_answer_is_used_next_run(tmp_path, monkeypatch):
    p, repo = _archive(tmp_path, monkeypatch, _unreadable_toc_pdf())
    _anthropic_must_not_be_built(monkeypatch)
    llm_dir = tmp_path / "cache" / "llm"
    factory = imp._real_deps_factory(object())
    counts = await analyze_manifests([p], deps_factory=factory, save=True)
    first = load_manifest(p)
    assert counts == {"review": 1} and first.articles == [] and "efnisyfirlit" in first.error   # as without a key
    (entry,) = pending_entries(llm_dir)
    assert entry["context"] == {"manifest": "data/timarit/manifests/timarit_logfraedinga/2010_60_1.yaml",
                                "source": SRC, "year": 2010, "volume": 60, "issue": "1", "label": LABEL}
    assert entry["body_path"].read_text(encoding="utf-8").startswith("Kápa")
    assert factory.queued == {entry["key"]}
    assert imp._ISSUE_CONTEXT.get() is None                                    # the context does not leak out of the run

    write_answer(llm_dir, entry["key"], [{"title": "Fyrsta greinin", "authors": ["Jón Jónsson"], "page_start": 1,
                                          "article_type": "Fræðigrein"}])
    again = imp._real_deps_factory(object())
    await analyze_manifests([p], deps_factory=again, save=True)
    out = load_manifest(p)
    (a,) = out.articles
    assert a.title == "Fyrsta greinin" and a.provenance["title"] == "llm" and a.confidence <= 0.70
    assert out.status == "review" and a.article_type == "Fræðigrein" and a.pdf_pages == [2, 4]
    assert pending_entries(llm_dir) == [] and again.queued == set()


async def test_the_same_toc_text_in_two_issues_is_queued_once_and_counted_once(tmp_path, monkeypatch):
    p, repo = _archive(tmp_path, monkeypatch, _unreadable_toc_pdf())
    p2 = imp.manifest_path(repo / imp.C.MANIFEST_ROOT, SRC, 2010, 60, "2")
    save_manifest(_manifest(issue="2", file="j/x.pdf"), p2)
    factory = imp._real_deps_factory(object())
    await analyze_manifests([p, p2], deps_factory=factory, save=True)
    (entry,) = pending_entries(tmp_path / "cache" / "llm")
    assert len(factory.queued) == 1 and entry["context"]["issue"] == "1"


async def test_cmd_analyze_without_a_key_says_in_icelandic_how_many_tocs_wait_and_where(tmp_path, monkeypatch, capsys, caplog):
    p, repo = _archive(tmp_path, monkeypatch, _unreadable_toc_pdf())
    _anthropic_must_not_be_built(monkeypatch)
    caplog.set_level(logging.INFO, logger="import_timarit")
    await imp.cmd_analyze(_args(dry_run=True))
    out = capsys.readouterr().out
    pending = tmp_path / "cache" / "llm" / "pending"
    assert "1 efnisyfirlit bíður svars Claude" in out and out.count(str(pending)) == 1
    assert "timarit_llm_queue.py list" in out
    assert len(pending_entries(tmp_path / "cache" / "llm")) == 1
    assert [r.getMessage() for r in caplog.records if "queued" in r.getMessage()] == [
        f"1 TOC text(s) queued for the Claude Code session in {pending}"]


async def test_cmd_analyze_prints_no_queue_line_when_every_toc_was_readable(tmp_path, monkeypatch, capsys):
    _archive(tmp_path, monkeypatch, _issue_pdf())
    await imp.cmd_analyze(_args(dry_run=True))
    assert "bíð" not in capsys.readouterr().out and not (tmp_path / "cache" / "llm").exists()


# --- journal_issues against the real schema (always rolled back) ---------------------------------------------

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")


class _ConnSession:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, *a, **k):
        return await self.conn.execute(*a, **k)

    async def commit(self):       # analyze_manifests commits after every manifest; this test never commits
        pass

    async def rollback(self):
        pass


@needs_db
async def test_analyze_updates_status_confidence_sha256_page_count_and_error_in_journal_issues(tmp_path):
    eng = create_async_engine(os.environ["DATABASE_URL"])
    conn = await eng.connect()
    await conn.begin()
    try:
        await conn.execute(text(
            "INSERT INTO sources (id, short_name, display_name) VALUES (gen_random_uuid(), :s, 'x') "
            "ON CONFLICT (short_name) DO NOTHING"), {"s": SRC})
        s = _ConnSession(conn)
        p = _save(tmp_path, _manifest("7"), "__test__2010_60_7.yaml")
        pdf = _issue_pdf()
        await analyze_manifests([p], deps_factory=_deps(pdf), save=True, session=s)
        out = load_manifest(p)
        rel = str(p.relative_to(imp.REPO)) if p.is_relative_to(imp.REPO) else str(p)
        row = (await conn.execute(text(
            "SELECT status, confidence, sha256, page_count, error FROM journal_issues WHERE manifest_path = :p"),
            {"p": rel})).one()
        assert row.status == out.status and abs(row.confidence - out.confidence) < 1e-9
        assert row.sha256 == out.sha256 and row.page_count == 11 and row.error is None

        def boom(rel_):
            raise FileNotFoundError(rel_)
        await analyze_manifests([p], deps_factory=lambda: AnalyzeDeps(boom, None, None, []), save=True, session=s)
        row = (await conn.execute(text("SELECT status, error FROM journal_issues WHERE manifest_path = :p"),
                                  {"p": rel})).one()
        assert tuple(row) == ("failed", "FileNotFoundError: x.pdf")
    finally:
        await conn.rollback()
        await conn.close()
        await eng.dispose()
