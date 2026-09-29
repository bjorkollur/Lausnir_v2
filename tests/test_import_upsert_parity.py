"""Every column the extractor fills must reach the database.

``case_type`` went missing exactly this way: the extractor has produced it ever
since the column was added, but the three court importers build their upsert
dict by hand and left it out, so the column was only ever filled by the one-off
``backfill_case_type.py``.  island.is now classifies Hæstaréttar verdicts from
2016 on only, so for ~10.000 older rows that backfill can never be repeated —
see docs/snapshots/README.md.

This reads the source rather than running an import: the point is to fail when
somebody adds a field to the extractor and forgets the upsert, which no
behavioural test would catch without a live API.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

SOURCES = [
    ("haestirettur", "_extract_haestirettur"),
    ("landsrettur", "_extract_landsrettur"),
    ("heradsdomstolar", "_extract_heradsdomstolar"),
]


def _string_keys(node: ast.Dict) -> set[str]:
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _function(path: pathlib.Path, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}")


def _extractor_fields(fn_name: str) -> set[str]:
    """Column names in the dict the extractor returns."""
    fn = _function(REPO / "engine/processors/extractor.py", fn_name)
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            return _string_keys(node.value)
    raise AssertionError(f"{fn_name} has no returned dict literal")


def _upsert_columns(short_name: str) -> set[str]:
    """Column names in the ``values`` dict the importer upserts."""
    fn = _function(REPO / f"scripts/import_{short_name}.py", "_upsert_doc")
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Dict):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "values" for t in targets):
                return _string_keys(node.value)
    raise AssertionError(f"import_{short_name}.py::_upsert_doc has no `values` dict")


@pytest.mark.parametrize("short_name,extract_fn", SOURCES)
def test_upsert_writes_every_extracted_field(short_name: str, extract_fn: str):
    missing = _extractor_fields(extract_fn) - _upsert_columns(short_name)
    assert not missing, (
        f"import_{short_name}.py drops {sorted(missing)} — the extractor fills "
        f"these but the upsert never writes them, so they only ever reach the DB "
        f"through a backfill"
    )


@pytest.mark.parametrize("short_name", [s for s, _ in SOURCES])
def test_case_type_is_filled_but_never_overwritten(short_name: str):
    """A re-import may fill case_type; it may not replace what is already there.

    The stored value came from the court's own site.  The extractor's value is
    an inference from keywords and parties (`_infer_hrd_lrd_case_type`), good to
    ~97% for Hæstiréttur and ~70% for Landsréttur — never good enough to
    overwrite the real thing.
    """
    source = (REPO / f"scripts/import_{short_name}.py").read_text(encoding="utf-8")
    fn = _function(REPO / f"scripts/import_{short_name}.py", "_upsert_doc")
    body = ast.get_source_segment(source, fn) or ""
    assert "coalesce" in body.lower() and "case_type" in body, (
        f"import_{short_name}.py must guard case_type with coalesce() in its "
        f"on-conflict update, or a re-import will overwrite the court's own "
        f"classification with the inferred fallback"
    )


def _calls(fn_name: str) -> set[str]:
    fn = _function(REPO / "engine/processors/extractor.py", fn_name)
    return {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


@pytest.mark.parametrize("extract_fn", ["_extract_haestirettur", "_extract_landsrettur"])
def test_court_case_type_is_left_for_the_source_to_decide(extract_fn: str):
    """Hæstiréttur and Landsréttur must import with case_type unset.

    Their classification is readable only from the island.is caseTypes filter,
    which backfill_case_type.py --missing-only reads *after* the import.  An
    inferred value written at import would leave the row non-NULL, and
    --missing-only would skip it — the court would never get its say.  The
    keyword/party fallback belongs after the source, not before it.

    Héraðsdómstólar are the exception: there the case-number prefix is the
    court's own answer, so _extract_heradsdomstolar fills it at import.
    """
    assert "_infer_hrd_lrd_case_type" not in _calls(extract_fn)


def test_haestirettur_extract_leaves_case_type_none():
    from engine.config.sources import get_config
    from engine.processors.extractor import Extractor

    raw = {
        "id": "x", "title": "Jón Jónsson gegn Sigríður Sigurðardóttir",
        "caseNumber": "1/2026", "verdictDate": "2026-09-28T00:00:00Z",
        "keywords": ["Skaðabætur"], "court": "Hæstiréttur",
        "richText": "<p>Dómur Hæstaréttar.</p><p>Dómsorð: Stefndi greiði.</p>",
    }
    assert Extractor(get_config("haestirettur")).extract(raw)["case_type"] is None
