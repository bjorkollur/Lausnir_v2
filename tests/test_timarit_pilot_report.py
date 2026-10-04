# tests/test_timarit_pilot_report.py
"""Task 22 Step 3: scripts/timarit_pilot_report.py — distributions and what-if tables over analysed manifests.
Pure functions on small synthetic manifests; the CLI on two manifests saved in tmp_path. No DB, no network."""
import pytest
import yaml

from engine.config.sources import get_config
from engine.timarit import analyze as A
from engine.timarit import config as C
from engine.timarit.manifest import ArticleEntry, IssueManifest, load_manifest, save_manifest
from scripts import timarit_pilot_report as rep

ALL = {"title_on_page": True, "printed_page_match": True, "author_on_page": True, "external_record": True}


def _art(conf, checks=None, pdf=(0, 4), title="Grein", start=1, prov=None, notes=()):
    return ArticleEntry(title=title, page_start=start, pdf_pages=list(pdf) if pdf else None, confidence=conf,
                        checks=dict(checks or {}), provenance=dict(prov) if prov is not None else {"title": "toc"},
                        analysis_notes=list(notes))


def _mf(source="logretta", year=2010, issue="1", *, file="x.pdf", pages=10, arts=(), status="review", page_map=None,
        error=None):
    return IssueManifest(source=source, year=year, volume=None, issue=issue, label=f"{source} {year}", file=file,
                         pages=pages, articles=list(arts), status=status, page_map=dict(page_map or {}), error=error)


# --- histogram ------------------------------------------------------------------------------------------

def test_histogram_ten_bins_edges_and_clamping():
    counts = rep.histogram([0.0, 0.05, -0.1, 0.1, 0.7, 0.95, 1.0, 1.2])
    assert counts == [3, 1, 0, 0, 0, 0, 0, 1, 0, 3]
    assert sum(counts) == 8


def test_histogram_other_range_and_empty():
    assert rep.histogram([], bins=4) == [0, 0, 0, 0]
    assert rep.histogram([5, 15, 25, 35], lo=0, hi=40, bins=4) == [1, 1, 1, 1]


def test_format_histogram_labels_lower_edges_and_notes_values_above_the_range():
    line = rep.format_histogram([0.05, 0.95, 1.5])
    assert line.startswith("n=3")
    assert "0.0:1" in line and "0.9:2" in line and "0.5:0" in line
    assert "yfir 1.0: 1" in line
    assert rep.format_histogram([]) == "engin gögn"


def test_format_histogram_says_how_many_lacked_the_value():
    assert rep.format_histogram([0.5, 0.6], missing=4).startswith("n=2, vantar 4  ")
    assert "vantar" not in rep.format_histogram([0.5, 0.6])
    assert rep.format_histogram([], missing=3) == "engin gögn (vantar 3)"


# --- signals --------------------------------------------------------------------------------------------

def test_signal_composition_counts_true_checks_over_all_articles():
    arts = [_art(1.0, ALL), _art(0.5, {"title_on_page": True}), _art(0.0, {}), _art(0.8, {"title_on_page": True, "author_on_page": False})]
    comp = rep.signal_composition(arts)
    assert comp["title_on_page"] == (3, 4)
    assert comp["printed_page_match"] == (1, 4)
    assert comp["author_on_page"] == (1, 4)
    assert comp["external_record"] == (1, 4)
    assert list(comp)[:4] == list(C.WEIGHTS)             # the weighted checks first, in WEIGHTS order


def test_signal_composition_includes_unweighted_checks_that_occur():
    comp = rep.signal_composition([_art(0.5, {"title_on_page": True, "odd_check": True})])
    assert comp["odd_check"] == (1, 1)


def test_title_sources_counts_the_provenance_of_titles():
    arts = [_art(1, prov={"title": "toc"}), _art(1, prov={"title": "toc+leitir"}), _art(1, prov={"title": "toc"}), _art(1, prov={})]
    assert rep.title_sources(arts) == {"toc": 2, "toc+leitir": 1, "(óskráð)": 1}


# --- config override ------------------------------------------------------------------------------------

def test_override_restores_the_config_even_when_the_body_raises():
    before = (C.APPROVE_THRESHOLD, C.COVERAGE_MAX_GAP_PAGES)
    with pytest.raises(RuntimeError):
        with rep._override(APPROVE_THRESHOLD=0.1, COVERAGE_MAX_GAP_PAGES=99):
            assert (C.APPROVE_THRESHOLD, C.COVERAGE_MAX_GAP_PAGES) == (0.1, 99)
            raise RuntimeError
    assert (C.APPROVE_THRESHOLD, C.COVERAGE_MAX_GAP_PAGES) == before


# --- APPROVE_THRESHOLD what-if --------------------------------------------------------------------------

def _approve_set():
    a = _mf(issue="a", arts=[_art(0.9, pdf=(0, 4), start=1), _art(0.9, pdf=(5, 9), start=6)], page_map={"quality": 0.4})
    b = _mf(issue="b", arts=[_art(0.6, pdf=(0, 4), start=1), _art(0.9, pdf=(5, 9), start=6)], page_map={"quality": 0.9})
    c = _mf(issue="c", pages=30, arts=[_art(0.95, pdf=(0, 0))], page_map={"quality": 0.9})      # 29 uncovered pages after the article
    d = _mf(issue="d", file=None, pages=None, arts=[_art(0.7, pdf=(0, 1)), _art(0.8, pdf=(0, 2), start=None)])   # article folder
    e = _mf(issue="e", status="failed", error="boom")
    return [a, b, c, d, e]


def test_approve_what_if_uses_issue_status_semantics():
    before = (C.APPROVE_THRESHOLD, C.COVERAGE_MAX_GAP_PAGES)
    rows = {r.threshold: r for r in rep.approve_what_if(_approve_set())}
    assert sorted(rows) == [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
    r = rows[0.5]
    assert (r.articles_ok, r.articles, r.status_only, r.issues) == (7, 7, 3, 5)   # c is blocked by coverage, e has no articles
    assert (rows[0.65].articles_ok, rows[0.65].status_only) == (6, 2)             # b has a 0.6 article
    assert (rows[0.75].articles_ok, rows[0.75].status_only) == (5, 1)             # d has a 0.7 article
    assert (rows[0.9].articles_ok, rows[0.9].status_only) == (4, 1)               # 0.9 >= 0.90 counts
    assert (rows[0.95].articles_ok, rows[0.95].status_only) == (1, 0)
    assert (C.APPROVE_THRESHOLD, C.COVERAGE_MAX_GAP_PAGES) == before


def test_approve_what_if_headline_is_what_analysis_would_approve():
    with rep._override(PAGE_MAP_MIN_QUALITY=0.5):       # pinned: the pilot is about to change the default
        rows = {r.threshold: r for r in rep.approve_what_if(_approve_set())}
    assert rows[0.5].status_only == 3
    assert rows[0.5].by_analysis == 2                   # a's page map (0.4) is below PAGE_MAP_MIN_QUALITY and forces review
    assert all(r.by_analysis <= r.status_only for r in rows.values())


def test_a_formula_volume_or_year_note_counts_as_forced_review_in_the_headline():
    perfect = {**ALL}
    formula = _mf(issue="f", pages=10, arts=[_art(1.0, perfect, pdf=(0, 9),
                                                  notes=[f"{A.FORMULA_VOLUME_NOTE}: 60. árg. (2010); ekkert prentað árgangsnúmer fannst"])])
    year = _mf(issue="y", pages=10, arts=[_art(1.0, perfect, pdf=(0, 9), notes=[f"{A.FORMULA_YEAR_NOTE} 3. árg.: 2007; ekkert prentað ár fannst"])])
    clean = _mf(issue="c", pages=10, arts=[_art(1.0, perfect, pdf=(0, 9), notes=["prentað 5. árg., mappan 6. árg."])])
    with rep._override(PAGE_MAP_MIN_QUALITY=0.5):
        assert rep.forced_review(formula) and rep.forced_review(year) and not rep.forced_review(clean)
        row = rep.approve_what_if([formula, year, clean], (0.85,))[0]
    assert (row.status_only, row.by_analysis, row.issues) == (3, 1, 3)


def test_the_formula_notes_are_the_ones_analyze_writes():
    m = IssueManifest(source="timarit_logfraedinga", year=2010, volume=None, issue="1", label="x")
    run = A._Run()
    A._fallback_volume(m, get_config("timarit_logfraedinga"), run)
    assert run.review and all(n.startswith(A.FORMULA_VOLUME_NOTE) for n in run.notes) and run.notes
    m = IssueManifest(source="stjornmal_stjornsysla", year=None, volume=3, issue=None, label="x")
    run = A._Run()
    A._sogs_articles([], m, get_config("stjornmal_stjornsysla"), run)
    assert run.review and run.notes and all(n.startswith(A.FORMULA_YEAR_NOTE) for n in run.notes)
    assert run.notes[0].startswith(A.FORCED_REVIEW_NOTES)


def test_forced_review_reasons():
    with rep._override(PAGE_MAP_MIN_QUALITY=0.5):
        assert rep.forced_review(_mf(arts=[_art(1)], page_map={"quality": 0.2}))
        assert rep.forced_review(_mf(arts=[_art(1)], error="ekkert textalag — þarf OCR"))
        assert rep.forced_review(_mf(arts=[_art(0.7, prov={"title": "llm"})]))
        assert rep.forced_review(_mf(arts=[_art(0.7, prov={"title": "toc", "pages": "toc+llm"})]))
        assert rep.forced_review(_mf(file=None, arts=[_art(1, notes=[A.NO_TEXT_LAYER])]))      # an article file without a text layer
        assert not rep.forced_review(_mf(arts=[_art(1)], page_map={"quality": 0.5}))
        assert not rep.forced_review(_mf(arts=[_art(1)], page_map={"quality": float("nan")}))
        assert not rep.forced_review(_mf(file=None, arts=[_art(1)], page_map={"bin_ratio": 0.9}))


def test_a_deviation_forces_review_and_is_listed_with_its_issue_and_counted_per_source():
    # Task 22c (R42): an analysis note starting "FRÁVIK: " sends the issue to review; the report lists every one
    dev = f"{A.FRAVIK}taflan segir bls. 25, prentað bls. 15"
    a = _mf(source="ulfljotur", arts=[_art(1.0, ALL, title="Tillögur þjóðréttarnefndar", notes=[dev, "annað"])])
    b = _mf(source="logretta", arts=[_art(1.0, ALL, title="Ó" * 70,
                                          notes=[f"{A.FRAVIK}skráin gaf enga upphafsblaðsíðu; greinin fannst eftir titli"])])
    c = _mf(source="logretta", issue="2", arts=[_art(1.0, ALL, notes=["leitir.is segir bls. 70–71"])])
    with rep._override(PAGE_MAP_MIN_QUALITY=0.5):
        assert rep.forced_review(a) and rep.forced_review(b) and not rep.forced_review(c)
    loaded = [rep.Loaded("x", m) for m in (a, b, c)]
    devs = rep.deviations(loaded)
    assert [(d.source, d.title, d.note) for d in devs] == [
        ("ulfljotur", "Tillögur þjóðréttarnefndar", "taflan segir bls. 25, prentað bls. 15"),
        ("logretta", "Ó" * 60, "skráin gaf enga upphafsblaðsíðu; greinin fannst eftir titli")]
    text = "\n".join(rep._deviation_lines(loaded))
    assert "== Frávik til yfirferðar: 2 ==" in text and "Eftir heimild: logretta 1, ulfljotur 1" in text
    assert "„Tillögur þjóðréttarnefndar“  — taflan segir bls. 25, prentað bls. 15" in text and "leitir.is" not in text


def test_a_deviation_of_the_whole_issue_is_listed_once():
    # fix round 1: Stjórnmál og stjórnsýsla 10. árg.: one common file-name offset, written on every article
    issue_note = f"{A.FRAVIK}blaðsíðutal skráarnafnanna er 2 síðum hærra en prentað blaðsíðutal í 3 skrám af 3"
    own = f"{A.FRAVIK}skráarnafn segir bls. 613–642, PDF-skjalið bls. 611–636"
    m = _mf(source="stjornmal_stjornsysla", arts=[_art(1.0, ALL, title="A", notes=[issue_note]),
                                                  _art(1.0, ALL, title="B", notes=[issue_note, own]),
                                                  _art(1.0, ALL, title="C", notes=[issue_note])])
    devs = rep.deviations([rep.Loaded("x", m)])
    assert [(d.title, d.note, d.articles) for d in devs] == [(None, issue_note[len(A.FRAVIK):], 3),
                                                             ("B", own[len(A.FRAVIK):], 1)]
    text = "\n".join(rep._deviation_lines([rep.Loaded("x", m)]))
    assert "[allt heftið, 3 greinar]  — blaðsíðutal skráarnafnanna" in text and "„(heftið" not in text


def test_without_external_record_rescales_the_rest_to_the_same_total():
    alt = rep.without_external_record(C.WEIGHTS)
    assert "external_record" not in alt
    assert sum(alt.values()) == pytest.approx(sum(C.WEIGHTS.values()))
    assert alt["title_on_page"] / alt["printed_page_match"] == pytest.approx(C.WEIGHTS["title_on_page"] / C.WEIGHTS["printed_page_match"])


def test_reweighted_recomputes_confidence_from_checks_and_caps_llm_lists():
    no_record = {"title_on_page": True, "printed_page_match": True, "author_on_page": True, "external_record": False}
    m = _mf(pages=10, arts=[_art(0.8, no_record, pdf=(0, 9))])
    alt = rep.without_external_record(C.WEIGHTS)
    assert m.articles[0].confidence == 0.8
    assert rep.reweighted(m, alt).articles[0].confidence == pytest.approx(1.0)
    assert m.articles[0].confidence == 0.8                                     # the input is untouched
    llm = _mf(arts=[_art(0.7, ALL, prov={"title": "llm"})])
    assert rep.reweighted(llm, alt).articles[0].confidence == C.LLM_CONFIDENCE_CAP


def test_approve_what_if_with_weights_where_the_external_record_is_not_required():
    no_record = {"title_on_page": True, "printed_page_match": True, "author_on_page": True, "external_record": False}
    ms = [_mf(pages=10, arts=[_art(0.8, no_record, pdf=(0, 9))])]
    now = {r.threshold: r for r in rep.approve_what_if(ms, (0.85,))}[0.85]
    alt = {r.threshold: r for r in rep.approve_what_if(ms, (0.85,), weights=rep.without_external_record(C.WEIGHTS))}[0.85]
    assert (now.by_analysis, alt.by_analysis) == (0, 1)
    assert (now.articles_ok, alt.articles_ok) == (0, 1)


def test_approve_what_if_with_the_external_record_counted_as_passed_adds_its_weight():
    no_record = {"title_on_page": True, "printed_page_match": True, "author_on_page": True, "external_record": False}
    ms = [_mf(pages=10, arts=[_art(0.8, no_record, pdf=(0, 9))])]
    assumed = rep.approve_what_if(ms, (0.85, 0.95), assume_passed=("external_record",))
    assert [(r.articles_ok, r.by_analysis) for r in assumed] == [(1, 1), (1, 1)]             # 0.8 + 0.2 = 1.0
    title_page = {"title_on_page": True, "printed_page_match": True}
    ms = [_mf(pages=10, arts=[_art(0.65, title_page, pdf=(0, 9))])]
    assert rep.approve_what_if(ms, (0.85,), assume_passed=("external_record",))[0].by_analysis == 1      # 0.65 + 0.20 = 0.85
    assert rep.approve_what_if(ms, (0.85,))[0].by_analysis == 0


# --- the other thresholds -------------------------------------------------------------------------------

def test_non_finite_page_map_values_are_dropped_like_missing_ones():
    ms = [_mf(issue="nan", page_map={"quality": float("nan"), "bin_ratio": float("inf"), "runner_up_share": float("-inf")}),
          _mf(issue="ok", page_map={"quality": 0.9, "bin_ratio": 0.8, "runner_up_share": 0.1}),
          _mf(issue="text", page_map={"quality": "høgh", "bin_ratio": True})]
    assert rep.map_quality_what_if(ms)[0] == (0.3, 0, 1)
    assert rep.bin_ratio_what_if(ms)[0][3] == 1
    assert rep.restart_ratio_what_if(ms)[0][2] == 1
    assert rep._page_map_missing(ms, "quality", containers_only=True) == 2
    assert rep._page_map_missing(_approve_set(), "bin_ratio") == 5


def test_map_quality_what_if_counts_containers_strictly_below():
    ms = [_mf(issue=str(i), page_map={"quality": q}) for i, q in enumerate([0.25, 0.4, 0.5, 0.9])]
    ms.append(_mf(issue="folder", file=None, page_map={"bin_ratio": 0.9}))             # no map: not a container
    ms.append(_mf(issue="nomap"))                                                      # container without a quality
    rows = {t: (below, total) for t, below, total in rep.map_quality_what_if(ms)}
    assert sorted(rows) == [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    assert rows[0.3] == (1, 4)
    assert rows[0.4] == (1, 4)                       # 0.4 is not below 0.4
    assert rows[0.5] == (2, 4)
    assert rows[0.8] == (3, 4)


def test_bin_ratio_what_if_separates_scans_with_no_words():
    ms = [_mf(issue=str(i), file=None if i % 2 else "x.pdf", page_map={"bin_ratio": r}) for i, r in enumerate([0.0, 0.45, 0.6, 0.75, 0.95])]
    ms.append(_mf(issue="none", page_map={}))
    rows = {t: (below, zero, total) for t, below, zero, total in rep.bin_ratio_what_if(ms)}
    assert sorted(rows) == [0.5, 0.6, 0.7, 0.8]
    assert rows[0.5] == (2, 1, 5)
    assert rows[0.6] == (2, 1, 5)                    # 0.6 itself is not below 0.6
    assert rows[0.7] == (3, 1, 5)
    assert rows[0.8] == (4, 1, 5)


def test_coverage_what_if_only_looks_at_containers_with_articles_and_ignores_the_threshold():
    tight = _mf(issue="t", pages=10, arts=[_art(0.2, pdf=(0, 4)), _art(0.2, pdf=(5, 9))])
    gap3 = _mf(issue="g", pages=10, arts=[_art(0.2, pdf=(3, 4)), _art(0.2, pdf=(5, 6))])           # 3 pages before, 3 after
    unlocated = _mf(issue="u", pages=10, arts=[_art(0.9, pdf=None)])
    folder = _mf(issue="f", file=None, pages=None, arts=[_art(0.9, pdf=(0, 1))])
    empty = _mf(issue="e", status="failed")
    rows = {g: (ok, total) for g, ok, total in rep.coverage_what_if([tight, gap3, unlocated, folder, empty], (0, 2, 3, 6, rep.NO_LIMIT))}
    assert rows[0] == (1, 3)
    assert rows[2] == (1, 3)
    assert rows[3] == (2, 3)
    assert rows[6] == (2, 3)
    assert rows[rep.NO_LIMIT] == (2, 3)              # an article without pages blocks coverage whatever the gap


def test_restart_ratio_what_if_counts_shares_at_or_above():
    ms = [_mf(issue=str(i), page_map={"quality": 0.9, "runner_up_share": s}) for i, s in enumerate([0.0, 0.12, 0.25, 0.6])]
    rows = {t: (n, total) for t, n, total in rep.restart_ratio_what_if(ms, (0.1, 0.25, 0.5))}
    assert rows == {0.1: (3, 4), 0.25: (2, 4), 0.5: (1, 4)}


# --- lowest articles, loading ---------------------------------------------------------------------------

def test_lowest_articles_sorted_by_confidence_stable_and_limited():
    l1 = rep.Loaded("1", _mf(arts=[_art(0.5, title="a"), _art(0.9, title="b"), _art(0.5, title="c")]))
    l2 = rep.Loaded("2", _mf(arts=[_art(0.1, title="d")]))
    out = rep.lowest_articles([l1, l2], 3)
    assert [(k, a.title) for k, a in out] == [("2", "d"), ("1", "a"), ("1", "c")]
    assert len(rep.lowest_articles([l1, l2], 10)) == 4


def _save(root, source, stem, m):
    path = root / source / f"{stem}.yaml"
    save_manifest(m, path)
    return path


def test_load_manifests_reads_every_manifest_and_reports_unreadable_ones(tmp_path):
    root = tmp_path / "manifests"
    _save(root, "logretta", "2010_x_1", _mf(arts=[_art(0.9)]))
    bad = root / "logretta" / "2011_x_1.yaml"
    bad.write_text("source: logretta\nyear: 2011\n", encoding="utf-8")                  # required keys missing
    loaded, problems = rep.load_manifests(root)
    assert [ld.key for ld in loaded] == ["2010_x_1"]
    assert len(problems) == 1 and "2011_x_1.yaml" in problems[0]


def test_load_manifests_only_filters_and_names_missing_paths(tmp_path):
    root = tmp_path / "manifests"
    p1 = _save(root, "logretta", "2010_x_1", _mf(arts=[_art(0.9)]))
    _save(root, "logretta", "2011_x_1", _mf(year=2011, arts=[_art(0.9)]))
    paths = tmp_path / "pilot.txt"
    paths.write_text(f"# the pilot\n\n{p1}\n{root}/logretta/1999_x_1.yaml\n", encoding="utf-8")
    only = rep.read_paths_file(paths)
    assert len(only) == 2
    loaded, problems = rep.load_manifests(root, only)
    assert [ld.key for ld in loaded] == ["2010_x_1"]
    assert len(problems) == 1 and "1999_x_1.yaml" in problems[0]


def test_read_paths_file_resolves_relative_paths_against_the_repo(tmp_path):
    f = tmp_path / "p.txt"
    f.write_text("data/timarit/manifests/logretta/2005_x_1.yaml\n", encoding="utf-8")
    assert rep.read_paths_file(f) == [rep.ROOT / "data/timarit/manifests/logretta/2005_x_1.yaml"]


def test_a_malformed_unmatched_file_is_a_clean_value_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- path: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ólæsileg"):
        rep.contained_in(bad)
    mapping = tmp_path / "map.yaml"
    mapping.write_text("path: a.pdf\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ekki listi"):
        rep.contained_in(mapping)
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    assert rep.contained_in(empty) == []


def test_a_missing_paths_file_is_a_clean_value_error(tmp_path):
    with pytest.raises(ValueError, match="tókst ekki að lesa"):
        rep.read_paths_file(tmp_path / "nope.txt")


def test_contained_in_lists_only_those_rejections(tmp_path):
    f = tmp_path / "_unmatched.yaml"
    f.write_text(yaml.safe_dump([
        {"path": "a.pdf", "reason": "deferred_mde"},
        {"path": "TL 2003_02.pdf", "reason": "contained_in", "duplicate_of": "TL 2003.pdf"},
    ], allow_unicode=True), encoding="utf-8")
    assert rep.contained_in(f) == [("TL 2003_02.pdf", "TL 2003.pdf")]
    assert rep.contained_in(tmp_path / "missing.yaml") == []


# --- the CLI --------------------------------------------------------------------------------------------

def _pilot(tmp_path):
    root = tmp_path / "manifests"
    good = _mf(source="logretta", issue="1", pages=10, status="approved",
               arts=[_art(0.95, ALL, pdf=(0, 4), start=1, title="Vanhæfi dómara", prov={"title": "toc+leitir"}),
                     _art(0.90, ALL, pdf=(5, 9), start=6, title="Sönnun", prov={"title": "toc"})],
               page_map={"quality": 0.95, "runner_up_share": 0.0, "bin_ratio": 0.92})
    weak = _mf(source="ulfljotur", year=1975, issue=None, pages=40, status="review", error="enginn greinalisti: ekkert",
               arts=[_art(0.35, {"title_on_page": True}, pdf=(2, 12), start=3, title="Ó" * 80, prov={"title": "page"},
                          notes=["engin röð í töflu passaði (best 0.41)", "second"]),
                     _art(0.65, {"title_on_page": True, "printed_page_match": True}, pdf=None, start=None, title=None)],
               page_map={"quality": 0.3, "runner_up_share": 0.4, "bin_ratio": 0.55})
    failed = _mf(source="ulfljotur", year=1976, issue=None, status="failed", error="Traceback\nboom " + "x" * 200, file="y.pdf")
    pending = _mf(source="ulfljotur", year=1977, issue=None, status="pending", file="z.pdf")
    paths = [_save(root, "logretta", "2010_x_1", good), _save(root, "ulfljotur", "1975_x_x", weak),
             _save(root, "ulfljotur", "1976_x_x", failed), _save(root, "ulfljotur", "1977_x_x", pending)]
    unmatched = tmp_path / "_unmatched.yaml"
    unmatched.write_text(yaml.safe_dump([{"path": "TL 2003_02.pdf", "reason": "contained_in", "duplicate_of": "TL 2003.pdf"}],
                                        allow_unicode=True), encoding="utf-8")
    return root, paths, unmatched


def _tree_state(root):
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_cli_prints_the_report_without_writing_anything(tmp_path, capsys):
    root, _, unmatched = _pilot(tmp_path)
    before = _tree_state(tmp_path)
    cfg = (C.APPROVE_THRESHOLD, C.COVERAGE_MAX_GAP_PAGES, C.PAGE_MAP_MIN_QUALITY)
    assert rep.main(["--manifests", str(root), "--unmatched", str(unmatched)]) == 0
    out = capsys.readouterr().out
    assert _tree_state(tmp_path) == before
    assert (C.APPROVE_THRESHOLD, C.COVERAGE_MAX_GAP_PAGES, C.PAGE_MAP_MIN_QUALITY) == cfg
    assert "== logretta" in out and "== ulfljotur" in out and "== Allar heimildir" in out
    assert "Hefti: 1 greind" in out                                          # logretta
    assert "Hefti: 2 greind af 3" in out                                     # ulfljotur: the pending one is not analysed
    assert "APPROVE_THRESHOLD" in out and "PAGE_MAP_MIN_QUALITY" in out and "OCR_BIN_RATIO_THRESHOLD" in out
    assert "COVERAGE_MAX_GAP_PAGES" in out and "PAGE_MAP_RESTART_RATIO" in out
    assert "0.95" in out and "0.50" in out
    assert "external_record" in out
    assert "samþykkt af greiningu" in out and "issue_status eingöngu" in out and "TILGÁTA" in out
    assert "n=1, vantar 1" in out                                            # ulfljotur: the failed container has no page-map values
    assert "vísbending" in out.lower() and "20 síðum" in out                 # the OCR table is labelled a proxy
    assert "toc+leitir" in out
    assert "(enginn titill)" in out
    assert "engin röð í töflu passaði (best 0.41)" in out and "second" not in out      # first analysis note only
    assert "Ó" * 60 in out and "Ó" * 61 not in out                                   # title cut at 60
    assert "ekkert" in out                                                           # the error of the weak issue
    assert "boom" in out and "x" * 101 not in out                                    # error cut at 100 chars, on one line
    assert "TL 2003_02.pdf" in out and "TL 2003.pdf" in out                          # contained_in
    assert "1977_x_x" not in out                                                     # the pending issue is not listed as review/failed
    assert "== Frávik til yfirferðar: 0 ==" in out and "Eftir heimild: engin" in out


def test_cli_only_restricts_the_report_to_the_listed_manifests(tmp_path, capsys):
    root, paths, unmatched = _pilot(tmp_path)
    pilot = tmp_path / "pilot.txt"
    pilot.write_text(f"{paths[0]}\n", encoding="utf-8")
    assert rep.main(["--manifests", str(root), "--only", str(pilot), "--unmatched", str(unmatched)]) == 0
    out = capsys.readouterr().out
    assert "== logretta" in out and "== ulfljotur" not in out


def test_cli_exits_1_and_says_so_when_a_listed_manifest_is_missing(tmp_path, capsys):
    root, paths, unmatched = _pilot(tmp_path)
    pilot = tmp_path / "pilot.txt"
    pilot.write_text(f"{paths[0]}\n{root}/logretta/1999_x_1.yaml\n", encoding="utf-8")
    assert rep.main(["--manifests", str(root), "--only", str(pilot), "--unmatched", str(unmatched)]) == 1
    out = capsys.readouterr().out
    assert "1999_x_1.yaml" in out and "== logretta" in out


def test_cli_reports_a_missing_only_file_and_a_malformed_unmatched_file_without_a_traceback(tmp_path, capsys):
    root, _, unmatched = _pilot(tmp_path)
    assert rep.main(["--manifests", str(root), "--only", str(tmp_path / "nope.txt"), "--unmatched", str(unmatched)]) == 1
    cap = capsys.readouterr()
    assert "Villa: tókst ekki að lesa" in cap.err and cap.out == ""
    bad = tmp_path / "bad.yaml"
    bad.write_text("- path: [unclosed\n", encoding="utf-8")
    assert rep.main(["--manifests", str(root), "--unmatched", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "== logretta" in out and "== Vandamál: 1" in out and "bad.yaml ólæsileg" in out
    assert "\n" not in out.split("bad.yaml ólæsileg")[1].split("\n")[0]       # the YAML error is collapsed to one line


def test_cli_with_nothing_analysed_says_so(tmp_path, capsys):
    root = tmp_path / "manifests"
    _save(root, "logretta", "2010_x_1", _mf(status="pending"))
    assert rep.main(["--manifests", str(root), "--unmatched", str(tmp_path / "none.yaml")]) == 1
    assert "Engin greind hefti" in capsys.readouterr().out


def test_saved_manifests_round_trip_for_the_fixtures(tmp_path):
    # the CLI tests rely on save_manifest/load_manifest keeping the page map and provenance of the fixtures
    root, paths, _ = _pilot(tmp_path)
    m = load_manifest(paths[1])
    assert m.page_map["bin_ratio"] == 0.55 and m.articles[0].provenance == {"title": "page"} and m.articles[1].title is None
