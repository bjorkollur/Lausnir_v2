"""Hierarchical scope catalog for the search picker (Fons Juris layout).

Four top-level categories, mirroring the reference site:
  - Dómstólar            — courts, with Hæstiréttur / Landsréttur / Héraðsdómar
                           each drilled into Dómar / Úrskurðir (by verdict_type),
                           plus Málskotsbeiðnir under Hæstiréttur.
  - Stjórnsýsla o.fl.    — regulatory agencies + ombudsman.
  - Nefndir o.fl.        — all appeal/ruling committees + ministry rulings.
  - Bækur og fræðiskrif  — academic writing (Skemman theses).

A selection ("scope") is a list of node *keys*. ``resolve_scope`` turns those
into a :class:`ScopeFilter` — a set of SQL clauses combining source IDs with an
optional verdict_type constraint, so a node like "Hæstiréttur – Dómar" filters
``source = haestirettur AND verdict_type = 'Dómur'``.

Every source in ``SOURCE_REGISTRY`` lands in exactly one category;
:func:`validate_catalog` enforces this (used by tests).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from engine.config.sources import SOURCE_REGISTRY, LAGASAFN_KAFLAR as _LAGASAFN_KAFLAR
from engine.database.models import Source

# ── Category → source membership (the partition of all sources) ────────────────
_DOMSTOLAR_SOURCES = [
    "haestirettur", "malskotsbeidnir", "landsrettur", "heradsdomstolar",
    "landsdomar", "felagsdomur", "endurupptokudomur",
]
_STJORNSYSLA_SOURCES = [
    "fjarskiptastofa", "fjolmidlanefnd", "personuvernd", "samgongustofa",
    "samkeppni", "umbodsmadur", "hugverkastofa",
]
_BAEKUR_SOURCES = ["logfraediritgerdir"]
_LAGASAFN_SOURCES = [f"lagasafn_{n:02d}" for n, _ in _LAGASAFN_KAFLAR]

# Nefndir = everything else (self-maintaining: a new tribunal source lands here
# automatically until explicitly reassigned), sorted by display name for the UI.
_ASSIGNED = (
    set(_DOMSTOLAR_SOURCES) | set(_STJORNSYSLA_SOURCES)
    | set(_BAEKUR_SOURCES) | set(_LAGASAFN_SOURCES)
)
_NEFNDIR_SOURCES = sorted(
    (s for s in SOURCE_REGISTRY if s not in _ASSIGNED),
    key=lambda s: SOURCE_REGISTRY[s].display_name,
)


def _label(short_name: str) -> str:
    cfg = SOURCE_REGISTRY.get(short_name)
    return cfg.display_name if cfg else short_name


def _source_leaf(short_name: str) -> dict:
    return {"key": short_name, "label": _label(short_name), "sources": [short_name]}


# ── The scope tree (display + resolution) ──────────────────────────────────────
# Node shape: {key, label, [sources], [verdict_types], [children]}.
# A node with `verdict_types` filters those sources by verdict_type. A node with
# `children` and no explicit `sources` resolves to the union of its descendants.
SCOPE_TREE: list[dict] = [
    {
        "key": "domstolar", "label": "Dómstólar", "children": [
            {"key": "haestirettur", "label": "Hæstiréttur", "children": [
                {"key": "haestirettur_domar", "label": "Dómar",
                 "sources": ["haestirettur"], "verdict_types": ["Dómur"]},
                {"key": "haestirettur_urskurdir", "label": "Úrskurðir",
                 "sources": ["haestirettur"], "verdict_types": ["Úrskurður"]},
                {"key": "malskotsbeidnir", "label": "Málskotsbeiðnir",
                 "sources": ["malskotsbeidnir"]},
            ]},
            {"key": "landsrettur", "label": "Landsréttur", "children": [
                {"key": "landsrettur_domar", "label": "Dómar",
                 "sources": ["landsrettur"], "verdict_types": ["Dómur"]},
                {"key": "landsrettur_urskurdir", "label": "Úrskurðir",
                 "sources": ["landsrettur"], "verdict_types": ["Úrskurður"]},
            ]},
            {"key": "heradsdomar", "label": "Héraðsdómar", "children": [
                {"key": "heradsdomar_domar", "label": "Dómar",
                 "sources": ["heradsdomstolar"], "verdict_types": ["Dómur"]},
                {"key": "heradsdomar_urskurdir", "label": "Úrskurðir",
                 "sources": ["heradsdomstolar"], "verdict_types": ["Úrskurður"]},
            ]},
            {"key": "landsdomar", "label": "Landsdómur", "sources": ["landsdomar"]},
            {"key": "felagsdomur", "label": "Félagsdómur", "sources": ["felagsdomur"]},
            {"key": "endurupptokudomur", "label": "Endurupptökudómur",
             "sources": ["endurupptokudomur"]},
        ],
    },
    {
        "key": "stjornsysla", "label": "Stjórnsýsla o.fl.",
        "children": [_source_leaf(s) for s in _STJORNSYSLA_SOURCES],
    },
    {
        "key": "nefndir", "label": "Nefndir o.fl.",
        "children": [_source_leaf(s) for s in _NEFNDIR_SOURCES],
    },
    {
        "key": "baekur", "label": "Bækur og fræðiskrif",
        "children": [_source_leaf(s) for s in _BAEKUR_SOURCES],
    },
    {
        "key": "lagasafn", "label": "Lagasafn Alþingis",
        "children": [
            {
                "key": f"lagasafn_{n:02d}",
                "label": f"{n}. {label}",
                "sources": [f"lagasafn_{n:02d}"],
            }
            for n, label in _LAGASAFN_KAFLAR
        ],
    },
]


# ── Flatten the tree into key → resolution for O(1) scope lookup ───────────────
def _leaf_sources(node: dict) -> list[str]:
    """All source short_names reachable under a node."""
    if "sources" in node and "children" not in node:
        return list(node["sources"])
    if "sources" in node:
        return list(node["sources"])
    out: list[str] = []
    for c in node.get("children", []):
        out.extend(_leaf_sources(c))
    return out


# key -> ("plain", [short_names]) | ("vt", [short_names], [verdict_types])
_RESOLUTIONS: dict[str, tuple] = {}


def _register(node: dict) -> None:
    if node.get("verdict_types"):
        _RESOLUTIONS[node["key"]] = ("vt", list(node["sources"]), list(node["verdict_types"]))
    else:
        _RESOLUTIONS[node["key"]] = ("plain", _leaf_sources(node))
    for c in node.get("children", []):
        _register(c)


for _cat in SCOPE_TREE:
    _register(_cat)


@dataclass
class ScopeFilter:
    """Resolved scope: OR of clauses, each (source_ids, verdict_types|None)."""
    clauses: list[tuple[list[uuid.UUID], list[str] | None]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.clauses

    def to_sql(self, prefix: str = "scope") -> tuple[str, dict[str, Any]]:
        """Return an SQL boolean fragment (alias ``d``) plus bound params."""
        plain_ids: list[uuid.UUID] = []
        vt_parts: list[str] = []
        params: dict[str, Any] = {}
        for i, (ids, vts) in enumerate(self.clauses):
            if vts is None:
                plain_ids.extend(ids)
            else:
                params[f"{prefix}{i}_ids"] = ids
                params[f"{prefix}{i}_vts"] = vts
                vt_parts.append(
                    f"(d.source_id = ANY(:{prefix}{i}_ids) "
                    f"AND d.verdict_type = ANY(:{prefix}{i}_vts))"
                )
        parts: list[str] = []
        if plain_ids:
            params[f"{prefix}_plain"] = list(dict.fromkeys(plain_ids))
            parts.append(f"d.source_id = ANY(:{prefix}_plain)")
        parts.extend(vt_parts)
        return ("(" + " OR ".join(parts) + ")", params) if parts else ("FALSE", {})


def catalog() -> list[dict]:
    """The scope tree for the UI (display order preserved)."""
    return SCOPE_TREE


def validate_catalog() -> None:
    """Assert the categories partition SOURCE_REGISTRY exactly (test guard)."""
    cats = {
        "domstolar": _DOMSTOLAR_SOURCES,
        "stjornsysla": _STJORNSYSLA_SOURCES,
        "nefndir": _NEFNDIR_SOURCES,
        "baekur": _BAEKUR_SOURCES,
        "lagasafn": _LAGASAFN_SOURCES,
    }
    seen: dict[str, str] = {}
    for cat, shorts in cats.items():
        for s in shorts:
            if s in seen:
                raise ValueError(f"{s!r} in both {seen[s]!r} and {cat!r}")
            seen[s] = cat
            if s not in SOURCE_REGISTRY:
                raise ValueError(f"Category {cat!r} references unknown source {s!r}")
    missing = set(SOURCE_REGISTRY) - set(seen)
    if missing:
        raise ValueError(f"Sources missing from all categories: {sorted(missing)}")


async def _short_name_to_id(session: AsyncSession) -> dict[str, uuid.UUID]:
    rows = (await session.execute(select(Source.short_name, Source.id))).all()
    return {short: sid for short, sid in rows}


async def resolve_scope(
    session: AsyncSession, scope: list[str] | None
) -> ScopeFilter | None:
    """Resolve scope keys to a :class:`ScopeFilter`.

    Returns ``None`` for whole-database search (empty/None scope, or any "all").
    Returns an empty ScopeFilter (``is_empty``) when every key is unknown — so a
    typo matches nothing rather than silently widening to everything.
    """
    if not scope:
        return None
    if any(tok == "all" for tok in scope):
        return None

    name_to_id = await _short_name_to_id(session)
    clauses: list[tuple[list[uuid.UUID], list[str] | None]] = []
    matched = False
    for tok in scope:
        res = _RESOLUTIONS.get(tok)
        if res is None:
            continue
        matched = True
        ids = [name_to_id[s] for s in res[1] if s in name_to_id]
        if not ids:
            continue
        clauses.append((ids, res[2] if res[0] == "vt" else None))
    if not matched:
        return ScopeFilter(clauses=[])
    return ScopeFilter(clauses=clauses)
