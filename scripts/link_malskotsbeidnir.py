"""Link Hæstiréttur appeal petitions (málskotsbeiðnir) to the decision they contest.

A petition names its target in its own text — "áfrýja dómi Landsréttar 4. júní
sama ár í máli nr. 455/2025" — so the link is read straight out of the body, not
inferred from content similarity like scripts/link_appeals.py has to do.

Two target kinds:
  - Landsréttur (the common case): "máli nr. 455/2025"
  - Héraðsdómur directly, leapfrogging Landsréttur under 1. mgr. 175. gr.
    laga nr. 91/1991: "dómi Héraðsdóms Reykjavíkur í máli nr. E-590/2025"

Only ONE edge row is written, petition → target. The reverse edge that
link_appeals.py writes for appeal chains is deliberately omitted: a petition is
a request *about* a judgment, not a rung in its appeal chain, and the Landsréttur
judgment should not list petitions among its own appeal links.

Also fills documents.appeal_outcome ('veitt' | 'hafnað') for the petition.

A second pass links *granted* petitions onward to the Hæstiréttur judgment they
led to, relation 'leiddi_til_doms'. Hæstiréttur judgments almost never name the
petition, and only ~25 % name their Landsréttur case number, so the judgment is
found through the target instead: the Hrd judgment that reviewed the very
decision the petition contested is by definition the one the leave produced.
It must post-date the petition — the same Landsréttur judgment can also have been
appealed earlier on someone else's leave, and that older Hrd judgment is a
different case.

Usage:
    uv run python scripts/link_malskotsbeidnir.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, text

import engine.database.connection as _db_conn
from engine.database.models import Document, Source

log = logging.getLogger(__name__)

RELATION = "leyfisbeidni_um"
RELATION_JUDGMENT = "leiddi_til_doms"

# "dómi Landsréttar 4. júní sama ár í máli nr. 455/2025" — the verdict word is
# captured because a Landsréttur case number can carry both an úrskurður and a
# dómur, and the petition says which one it contests.
_LRD_RE = re.compile(
    r"\b(dómi|dóms|dómur|úrskurð(?:i|ur|ar)?)\s+Landsréttar\b"
    r".{0,100}?\bmál(?:i|inu|s)?\s+nr\.\s*(\d+/\d{4})",
    re.IGNORECASE | re.DOTALL,
)
# Leapfrog appeals name the district court too — "E-590/2025" alone is not
# unique across the eight district courts.
_HERD_RE = re.compile(
    r"Héraðsdóms\s+([A-ZÁÐÉÍÓÚÝÞÆÖ][a-záðéíóúýþæö]+)\b"
    r".{0,100}?\bmál(?:i|inu|s)?\s+nr\.\s*([A-ZÁÐÉÍÓÚÝÞÆÖ]-\d+/\d{4})",
    re.IGNORECASE | re.DOTALL,
)

_GRANT_RE = re.compile(
    r"tek(?:in|ið|inn|nar)\s+til\s+greina"
    r"|leyfi(?:ð)?\s+(?:er\s+)?(?:því\s+)?veitt"
    r"|veitt\s+er\s+leyfi"
    r"|fallist\s+er\s+á\s+(?:beiðni|umsókn)"
    r"|samþykkt(?:ar|ir)?\b",
    re.IGNORECASE,
)
_DENY_RE = re.compile(
    r"(?:beiðni|umsókn|ósk)\w*\s*(?:\w+\s+){0,6}?(?:er|verður|eru|verða)\s+(?:því\s+)?(?:að\s+)?hafna"
    r"|hafnað\s+er\s+(?:beiðni|umsókn)"
    r"|(?:er|verður|eru|verða)\s+því\s+hafnað"
    r"|hafnað\s*\.",
    re.IGNORECASE,
)

# Petitions often close with a raw landsrettur.is link. It is their own site's
# internal id (not island.is's, which is what our landsrettur rows key on), so
# it is useless as a join key — but it pushes the ruling sentence out of a
# fixed-size tail window, so strip it before reading the outcome.
_TRAILING_URL_RE = re.compile(r"\s*https?://\S+\s*$")

_OUTCOME_WINDOW = 600


def extract_outcome(body: str | None) -> str | None:
    """'veitt' | 'hafnað' | None — how Hæstiréttur ruled on the petition."""
    if not body:
        return None
    tail = _TRAILING_URL_RE.sub("", body.rstrip())[-_OUTCOME_WINDOW:]
    granted = bool(_GRANT_RE.search(tail))
    denied = bool(_DENY_RE.search(tail))
    if granted == denied:          # neither matched, or contradictory — don't guess
        return None
    return "veitt" if granted else "hafnað"


def extract_target(body: str | None) -> tuple[str, str, str | None] | None:
    """Return (kind, case_number, hint) for the decision the petition contests.

    kind 'lrd'  → hint is the verdict word ('dómi'/'úrskurði'/…)
    kind 'herd' → hint is the district-court name ('Reykjavíkur'/…)
    """
    if not body:
        return None
    m = _LRD_RE.search(body)
    if m:
        return "lrd", m.group(2), m.group(1).lower()
    m = _HERD_RE.search(body)
    if m:
        return "herd", m.group(2), m.group(1)
    return None


def pick_lrd(
    candidates: list[tuple[uuid.UUID, str | None, date | None]],
    verdict_word: str | None,
    petition_date: date | None,
) -> uuid.UUID | None:
    """Choose among Landsréttur rows sharing a case number.

    A case number is reused across an interlocutory úrskurður and the final
    dómur, and sometimes across several úrskurðir. The petition names the
    verdict kind; where that still leaves several, the contested decision is
    the latest one preceding the petition.

    A decision handed down *after* the petition cannot be the one it contests —
    the case number then belongs to a later stage of the same case whose earlier
    decision we simply do not hold. Such a candidate is dropped, even when it is
    the only one, rather than linked wrongly.
    """
    pool = [c for c in candidates if not (c[2] and petition_date and c[2] > petition_date)]
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0][0]
    if verdict_word:
        want = "Dómur" if verdict_word.startswith("dóm") else "Úrskurður"
        narrowed = [c for c in pool if c[1] == want]
        if narrowed:
            pool = narrowed
    if len(pool) == 1:
        return pool[0][0]

    if petition_date:
        earlier = [c for c in pool if c[2] and c[2] <= petition_date]
        if earlier:
            return max(earlier, key=lambda c: c[2])[0]
    return None   # genuinely undecidable — leave it unlinked rather than guess


def pick_judgment(
    candidates: list[tuple[uuid.UUID, date | None]],
) -> uuid.UUID | None:
    """Choose the Hæstiréttur judgment a granted petition produced.

    Candidates are already restricted to judgments that reviewed the contested
    decision *after* the petition. Several can qualify — a case may return to
    Hæstiréttur on a later procedural kæra — and the one this leave produced is
    the first of them.
    """
    dated = [c for c in candidates if c[1]]
    if not dated:
        return None
    return min(dated, key=lambda c: c[1])[0]


# Names too generic to identify a case on their own (anonymised initials, the
# prosecution, the State). Left out of the party comparison in pass 3.
_GENERIC_PARTIES = {"ákæruvaldið", "íslenska ríkið", "íslenska ríkinu", "ríkissaksóknari"}
_MONTHS = ["janúar", "febrúar", "mars", "apríl", "maí", "júní",
           "júlí", "ágúst", "september", "október", "nóvember", "desember"]
_PARTY_MATCH_MIN = 0.6        # shared / larger party set
_PARTY_MATCH_YEARS = 3        # a judgment this long after the petition still counts


def party_names(*sides) -> set[str]:
    """Normalised party names from plaintiffs/defendants JSONB, counsel stripped."""
    out: set[str] = set()
    for side in sides:
        for p in side or []:
            raw = p.get("name") if isinstance(p, dict) else str(p)
            raw = re.sub(r"\s*\([^)]*\)", "", raw or "")
            for part in re.split(r",| og ", raw):
                part = part.strip().lower()
                if len(part) > 3 and part not in _GENERIC_PARTIES:
                    out.add(part)
    return out


def pick_judgment_by_parties(
    petition_parties: set[str],
    petition_date: date,
    candidates: list[tuple[uuid.UUID, date, set[str], str]],
) -> uuid.UUID | None:
    """Choose the Hæstiréttur judgment for a granted petition whose contested
    Landsréttur decision is not in the database, so pass 2 cannot reach it.

    Candidates are (id, date, party names, opening text). A judgment qualifies when
    it post-dates the petition (within _PARTY_MATCH_YEARS) and shares at least
    _PARTY_MATCH_MIN of its parties with the petition. Where several qualify, one
    whose opening names the petition's date ("Áfrýjunarleyfi veitt 1. apríl 2022")
    is preferred; if that still leaves more than one the earliest is taken, unless
    two share the earliest date — companion cases with identical parties — in which
    case nothing is linked. A match resting
    on a single shared name is only accepted when the opening names that date.
    """
    if not petition_parties:
        return None
    horizon = petition_date.replace(year=petition_date.year + _PARTY_MATCH_YEARS)
    stamp = f"{petition_date.day}. {_MONTHS[petition_date.month - 1]} {petition_date.year}"
    pool = []
    for hid, hdate, hparties, head in candidates:
        if not (petition_date < hdate <= horizon) or not hparties:
            continue
        shared = petition_parties & hparties
        if len(shared) / max(len(petition_parties), len(hparties)) < _PARTY_MATCH_MIN:
            continue
        # One shared name ("Reykjavíkurborg") identifies a party, not a case —
        # then the judgment must also name the leave date.
        if len(shared) < 2 and stamp not in head:
            continue
        pool.append((hid, head, hdate))
    if len(pool) > 1:
        dated = [c for c in pool if stamp in c[1]]
        pool = dated or pool
    if len(pool) > 1:
        # Later judgments between the same parties are follow-ups (a kæra over
        # recusal after remand, costs, ...). The judgment the leave produced is
        # the first of them — unless two share that date (companion cases).
        by_date = sorted((c for c in pool), key=lambda c: c[2])
        if by_date[0][2] < by_date[1][2]:
            pool = by_date[:1]
    return pool[0][0] if len(pool) == 1 else None


# Hæstiréttur judgments state when leave was granted — "Áfrýjunarleyfi var veitt
# 27. apríl 2020." — and that is the date of the petition's own ákvörðun. A far
# stronger key than the parties, and it works when they differ in spelling
# ("Hrafnista, dvalarheimili aldraðra í Reykjavík" vs "Hrafnista Laugarási").
_DATE_RE = r"(\d{1,2})\. (" + "|".join(_MONTHS) + r") (20\d{2})"
_LEAVE_RES = [
    re.compile(r"leyfi[^.]{0,120}?\bveitt\b[^.]{0,60}?" + _DATE_RE, re.IGNORECASE),
    re.compile(r"\bveitt[^.]{0,20}?(?:áfrýjunar|kæru)leyfi[^.]{0,30}?" + _DATE_RE, re.IGNORECASE),
    re.compile(r"veitti\s+(?:áfrýjunar|kæru)leyfi[^.]{0,30}?" + _DATE_RE, re.IGNORECASE),
    # "Með ákvörðun Hæstaréttar 4. maí 2021 var fallist á að málið gæti haft þýðingu …"
    re.compile(r"ákvörðun Hæstaréttar\s+" + _DATE_RE + r"[^.]{0,80}?(?:\bveitt|\bfallist|tekin til greina)",
               re.IGNORECASE),
]
_PARTY_STOPWORDS = {"lögmaður", "lögmaðurinn", "enginn", "sjálfur", "íslenska", "ríkinu", "ríkið",
                    "ákæruvaldið", "saksóknari", "ríkissaksóknari", "réttargæslumaður"}
_COUNSEL_RE = re.compile(
    r"\(([^)]*?)\s*(?:lögmaður|hrl\.?|hdl\.?|borgarlögmaður|ríkissaksóknari|saksóknari|ríkislögmaður)")


def leave_dates(body: str | None) -> set[date]:
    """Dates a Hæstiréttur judgment says leave to appeal was granted."""
    out: set[date] = set()
    for rx in _LEAVE_RES:
        for m in rx.finditer(body or ""):
            day, month, year = m.groups()[-3:]
            out.add(date(int(year), _MONTHS.index(month.lower()) + 1, int(day)))
    return out


def _side_texts(*sides) -> list[str]:
    return [(p.get("name") if isinstance(p, dict) else str(p)) or ""
            for side in sides for p in side or []]


def party_words(*sides) -> set[str]:
    """Distinctive words (>= 5 letters) from party names, counsel stripped."""
    words: set[str] = set()
    for raw in _side_texts(*sides):
        for w in re.findall(r"[\wÁÐÉÍÓÚÝÞÆÖáðéíóúýþæö-]{5,}", re.sub(r"\([^)]*\)", "", raw).lower()):
            if w not in _PARTY_STOPWORDS and not w.startswith("ehf"):
                words.add(w)
    return words


def counsel_names(*sides) -> set[str]:
    return {m.strip().lower() for raw in _side_texts(*sides) for m in _COUNSEL_RE.findall(raw)}


def pick_judgment_by_leave_date(
    petition: "Document",
    case_ref: str | None,
    by_leave_date: dict[date, list],
    claimed: set[uuid.UUID],
) -> uuid.UUID | None:
    """Choose the Hæstiréttur judgment that names this petition's date as the
    day leave was granted.

    Several petitions are decided on the same day, so the date alone is not
    enough: the judgment must also share a party word, counsel, or the contested
    Landsréttur case number with the petition, and must not already be the
    outcome of another petition. Where several judgments still qualify the best
    scoring one wins if it is unique — companion cases with identical parties and
    counsel stay unlinked.
    """
    words = party_words(petition.plaintiffs, petition.defendants)
    counsel = counsel_names(petition.plaintiffs, petition.defendants)
    scored = []
    for h in by_leave_date.get(petition.document_date, []):
        if h.id in claimed or not h.document_date or h.document_date < petition.document_date:
            continue
        shared_words = len(words & party_words(h.plaintiffs, h.defendants))
        shared_counsel = len(counsel & counsel_names(h.plaintiffs, h.defendants))
        named = bool(case_ref and case_ref in (h.body_text or ""))
        # one shared counsel alone is thin (the same firm acts in many cases),
        # but with a unique same-day candidate it is enough
        if shared_words or shared_counsel >= 2 or named or shared_counsel == 1:
            scored.append((shared_words + shared_counsel + 2 * named, h.id))
    if not scored:
        return None
    scored.sort(key=lambda c: -c[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


async def main(dry_run: bool = False) -> None:
    await _db_conn.init_db()
    async with _db_conn.AsyncSessionLocal() as session:
        async def sid(name: str) -> uuid.UUID:
            return (await session.execute(
                select(Source.id).where(Source.short_name == name))).scalar_one()

        mb_id, lr_id, hd_id = await sid("malskotsbeidnir"), await sid("landsrettur"), await sid("heradsdomstolar")

        lrd_by_case: dict[str, list[tuple[uuid.UUID, str | None, date | None]]] = defaultdict(list)
        for did, cn, vt, dt in (await session.execute(
            select(Document.id, Document.case_number, Document.verdict_type, Document.document_date)
            .where(Document.source_id == lr_id)
        )).all():
            if cn:
                lrd_by_case[cn.strip()].append((did, vt, dt))

        herd_by_case: dict[str, list[tuple[uuid.UUID, str | None, date | None]]] = defaultdict(list)
        for did, cn, court, dt in (await session.execute(
            select(Document.id, Document.case_number, Document.court, Document.document_date)
            .where(Document.source_id == hd_id)
        )).all():
            if cn:
                herd_by_case[cn.strip()].append((did, court, dt))

        petitions = (await session.execute(
            select(Document).where(Document.source_id == mb_id)
        )).scalars().all()

        existing = {
            r[0] for r in (await session.execute(
                text("SELECT from_doc_id FROM document_links WHERE relation = :rel"),
                {"rel": RELATION},
            )).all()
        }

        stats = defaultdict(int)
        for p in petitions:
            outcome = extract_outcome(p.body_text)
            if outcome != p.appeal_outcome:
                p.appeal_outcome = outcome
                stats["outcome_set" if outcome else "outcome_cleared"] += 1

            target = extract_target(p.body_text)
            if not target:
                stats["no_target_ref"] += 1
                continue
            kind, case_number, hint = target

            if kind == "lrd":
                to_id = pick_lrd(lrd_by_case.get(case_number, []), hint, p.document_date)
                if to_id is None:
                    stats["lrd_unresolved" if lrd_by_case.get(case_number) else "lrd_not_in_db"] += 1
                    continue
            else:
                cands = herd_by_case.get(case_number, [])
                if hint and len(cands) > 1:
                    cands = [c for c in cands if hint.lower()[:5] in (c[1] or "").lower()] or cands
                # same rule as pick_lrd: a judgment postdating the petition
                # cannot be the one it contests
                cands = [c for c in cands
                         if not (c[2] and p.document_date and c[2] > p.document_date)]
                if len(cands) != 1:
                    stats["herd_unresolved" if cands else "herd_not_in_db"] += 1
                    continue
                to_id = cands[0][0]

            if p.id in existing:
                stats["already_linked"] += 1
                continue
            if not dry_run:
                await session.execute(
                    text("""
                        INSERT INTO document_links (id, from_doc_id, to_doc_id, relation, confidence, method)
                        VALUES (:id, :f, :t, :rel, 1.0, :method)
                        ON CONFLICT ON CONSTRAINT uq_link_from_to_rel DO NOTHING
                    """),
                    {"id": uuid.uuid4(), "f": p.id, "t": to_id, "rel": RELATION,
                     "method": f"casenum_{kind}"},
                )
            stats[f"linked_{kind}"] += 1

        # ── Pass 2: granted petition → the Hæstiréttur judgment it produced ────
        # Reached through the contested decision: the Hrd judgment that reviewed
        # it, and post-dates the petition. Done in SQL because pass 1's edges are
        # only visible after flush, and the join is the whole of the logic.
        # 'appealed_to' edges once existed in BOTH orientations (backfill_hrd_lrd_links.py
        # and import_haestirettur.py wrote Hrd → lower until 2026-09-26 / 2026-10-01).
        # Every writer now orients lower → Hrd, but matching either way round costs
        # nothing and keeps the chain visible if a writer ever gets it wrong again.
        await session.flush()
        candidates = (await session.execute(text("""
            SELECT p.id AS pid, hrd.id AS hid, hrd.document_date AS hdate
            FROM documents p
            JOIN sources ps ON ps.id = p.source_id AND ps.short_name = 'malskotsbeidnir'
            JOIN document_links dl ON dl.from_doc_id = p.id AND dl.relation = :rel
            JOIN document_links dl2 ON dl2.relation = 'appealed_to'
                 AND dl.to_doc_id IN (dl2.from_doc_id, dl2.to_doc_id)
            JOIN documents hrd ON hrd.id = CASE WHEN dl2.from_doc_id = dl.to_doc_id
                                                THEN dl2.to_doc_id ELSE dl2.from_doc_id END
            JOIN sources hs ON hs.id = hrd.source_id AND hs.short_name = 'haestirettur'
            WHERE p.appeal_outcome = 'veitt'
              AND hrd.document_date > p.document_date
        """), {"rel": RELATION})).mappings().all()

        by_petition: dict[uuid.UUID, list[tuple[uuid.UUID, date | None]]] = defaultdict(list)
        for row in candidates:
            by_petition[row["pid"]].append((row["hid"], row["hdate"]))

        already_j = {
            r[0] for r in (await session.execute(
                text("SELECT from_doc_id FROM document_links WHERE relation = :rel"),
                {"rel": RELATION_JUDGMENT},
            )).all()
        }

        for pid, cands in by_petition.items():
            if pid in already_j:
                stats["judgment_already_linked"] += 1
                continue
            hid = pick_judgment(cands)
            if hid is None:
                stats["judgment_undated"] += 1
                continue
            if len(cands) > 1:
                stats["judgment_multi_candidate"] += 1
            if not dry_run:
                await session.execute(
                    text("""
                        INSERT INTO document_links (id, from_doc_id, to_doc_id, relation, confidence, method)
                        VALUES (:id, :f, :t, :rel, 0.9, 'chain_via_target')
                        ON CONFLICT ON CONSTRAINT uq_link_from_to_rel DO NOTHING
                    """),
                    {"id": uuid.uuid4(), "f": pid, "t": hid, "rel": RELATION_JUDGMENT},
                )
            stats["linked_judgment"] += 1

        # ── Pass 3: the judgment names the day leave was granted ───────────────
        await session.flush()
        done_j = {
            r[0] for r in (await session.execute(
                text("SELECT from_doc_id FROM document_links WHERE relation = :rel"),
                {"rel": RELATION_JUDGMENT},
            )).all()
        }
        claimed = {
            r[0] for r in (await session.execute(
                text("SELECT to_doc_id FROM document_links WHERE relation = :rel"),
                {"rel": RELATION_JUDGMENT},
            )).all()
        }
        hrd_docs = (await session.execute(
            select(Document.id, Document.document_date, Document.plaintiffs,
                   Document.defendants, Document.body_text)
            .join(Source, Source.id == Document.source_id)
            .where(Source.short_name == "haestirettur", Document.body_text.ilike("%leyfi%"))
        )).all()
        by_leave_date: dict[date, list] = defaultdict(list)
        for h in hrd_docs:
            for d in leave_dates(h.body_text):
                by_leave_date[d].append(h)
        targets = {
            r[0]: r[1] for r in (await session.execute(text("""
                SELECT l.from_doc_id, t.case_number FROM document_links l
                JOIN documents t ON t.id = l.to_doc_id WHERE l.relation = :rel"""),
                {"rel": RELATION})).all()
        }
        for p in petitions:
            if p.appeal_outcome != "veitt" or p.id in done_j or not p.document_date:
                continue
            hid = pick_judgment_by_leave_date(p, targets.get(p.id), by_leave_date, claimed)
            if hid is None:
                continue
            if not dry_run:
                await session.execute(
                    text("""
                        INSERT INTO document_links (id, from_doc_id, to_doc_id, relation, confidence, method)
                        VALUES (:id, :f, :t, :rel, 0.9, 'leave_date')
                        ON CONFLICT ON CONSTRAINT uq_link_from_to_rel DO NOTHING
                    """),
                    {"id": uuid.uuid4(), "f": p.id, "t": hid, "rel": RELATION_JUDGMENT},
                )
            claimed.add(hid)
            log.info("leave_date: petition %s -> judgment %s", p.case_number, hid)
            stats["linked_judgment_by_leave_date"] += 1

        # ── Pass 4: granted petitions still unlinked — match on the parties ─────
        # Pass 2 needs the contested Landsréttur decision in the DB. When it is
        # missing (e.g. Lrd. 510/2021 of 11 Feb 2022 — the DB holds only the
        # 2023 decision after remand) the petition has no appeal chain to follow,
        # but the Hæstiréttur judgment itself is often present with the same
        # parties. Match on those, direct from the petition.
        await session.flush()
        done_j = {
            r[0] for r in (await session.execute(
                text("SELECT from_doc_id FROM document_links WHERE relation = :rel"),
                {"rel": RELATION_JUDGMENT},
            )).all()
        }
        hrd_rows = (await session.execute(text("""
            SELECT d.id, d.document_date, d.plaintiffs, d.defendants,
                   substring(d.body_text FROM 1 FOR 3000) AS head
            FROM documents d JOIN sources s ON s.id = d.source_id AND s.short_name = 'haestirettur'
            WHERE d.document_date IS NOT NULL
        """))).all()
        hrd_cands = [(r.id, r.document_date, party_names(r.plaintiffs, r.defendants), r.head or "")
                     for r in hrd_rows]
        for p in petitions:
            if p.appeal_outcome != "veitt" or p.id in done_j or not p.document_date:
                continue
            hid = pick_judgment_by_parties(
                party_names(p.plaintiffs, p.defendants), p.document_date, hrd_cands)
            if hid is None:
                stats["parties_unmatched"] += 1
                continue
            if not dry_run:
                await session.execute(
                    text("""
                        INSERT INTO document_links (id, from_doc_id, to_doc_id, relation, confidence, method)
                        VALUES (:id, :f, :t, :rel, 0.8, 'parties_direct')
                        ON CONFLICT ON CONSTRAINT uq_link_from_to_rel DO NOTHING
                    """),
                    {"id": uuid.uuid4(), "f": p.id, "t": hid, "rel": RELATION_JUDGMENT},
                )
            log.info("parties_direct: petition %s -> judgment %s", p.case_number, hid)
            stats["linked_judgment_by_parties"] += 1

        if dry_run:
            await session.rollback()
        else:
            await session.commit()

    print("DRY RUN — nothing written\n" if dry_run else "")
    for k in sorted(stats):
        print(f"{k:22s} {stats[k]:5d}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(dry_run=a.dry_run))
