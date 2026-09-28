# Slökuð orðaleit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Þegar ströng orðaleit (öll orð) finnur færri skjöl en `RELAX_BELOW`, bæta við skjölum sem innihalda öll nema eitt orð, síðan eitthvert orðanna, í þrepum á eftir ströngu treffunum, með skýrri tilkynningu í svari og viðmóti, og velja mörkin með mælingu á gullsettinu.

**Architecture:** Slökunin er breyting á forsíu `cand` í `build_hits_sql` (víðasta fyrirspurn + `tier`-dálkur) og `h.tier ASC` fremst í röðun; efnisgreinaþrepið (`hits_and`/`hits_or`) er óbreytt. Hreint fyrirspurnasmíðafall í nýrri einingu `engine/search/relaxation.py`; ákvörðunin (`should_relax`) er tekin í `search_documents` og `facet_counts` með sömu reglu. `SearchResults` fær `strict_total`/`relaxed`, niðurstöður fá `match_tier`. Framendi sýnir eina línu og þrepamerki.

**Tech Stack:** Python 3.13, PostgreSQL 17 FTS (`to_tsquery('simple', …)`), SQLAlchemy async, FastAPI, React 19 + Vitest, `scripts/eval_search.py` fyrir mælingar.

**Spec:** `docs/superpowers/specs/2026-09-28-relaxed-keyword-search-design.md`

## Global Constraints

- Keyra með `uv run …`; umhverfi `set -a; . ./.env; set +a` (Postgres port 5433). Próf: `uv run pytest -q`, `cd frontend && npx vitest run && npx tsc -b` (`tsc --noEmit` athugar ekkert).
- Lemmur koma alltaf úr `lemmatize_query` (tákn `[a-záéíóúýðþæö]+`, bil á milli); fyrirspurnastrengir fara í `to_tsquery('simple', :param)` sem bundin gildi — aldrei string-formatting með notandatexta.
- `RELAX_BELOW` bráðabirgða 10; endanlegt gildi valið af notanda eftir `--relax-sweep`. `MAX_NMINUS1_LEMMAS = 8`.
- Óslakað (strangur fjöldi ≥ mörk, eða ein lemma) verður hegðun og SQL **byte-fyrir-byte óbreytt** frá í dag nema `tier`-fastinn 0 og nýju svarsviðin.
- Þrep 0 alltaf á undan 1 á undan 2, óháð röðun (relevance/newest/oldest).
- Einingapróf snerta ekki DB nema merkt `skipif(not DATABASE_URL)` með rollback.
- Commit-skilaboð enda á:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_013Ss2bC6v9WxkEuVAwoeyRr
  ```

## Review Focus

1. **Lemma með mörgum táknum** (`lemmatize_query("e-mál")` → `"e mál"`): `build_keyword_queries` verður að meðhöndla hvert bil-aðskilið tákn sem lemmu; strangi strengurinn `e & mál` jafngildir `plainto_tsquery` — prófað í Task 1.
2. **Jaðar mörkin**: `strict_total == RELAX_BELOW` → engin slökun; `RELAX_BELOW = 0` → aldrei — Task 1.
3. **Facets og leit ósammála**: leit með sviði getur slakað þótt facets (án sviðs) geri það ekki; það er viljandi og skjalfest — Task 3 próf staðfestir að hvor um sig notar sinn strangan fjölda.
4. **`section_kind` með slökun**: `total` verður að koma úr `hits` (bundið þaki) eins og í dag, ekki úr doc-level `any`-talningu — Task 2.
5. **Frambjóðendaþak og þrep**: `ORDER BY tier ASC` í `cand` verður að standa **á undan** `LIMIT`, annars geta þrep-2 skjöl ýtt út þrep-0 skjölum þegar víðasta mengið er stórt — Task 2 SQL-próf athugar röðina.

---

### Task 1: `engine/search/relaxation.py`

**Files:**
- Create: `engine/search/relaxation.py`
- Test: `tests/test_relaxation.py`

**Interfaces:**
- Produces: `MAX_NMINUS1_LEMMAS`, `RELAX_BELOW`, `KeywordQueries(strict, nminus1, any, n)`, `build_keyword_queries(lemmas) -> KeywordQueries`, `should_relax(strict_total, n, threshold=None) -> bool`.

- [ ] **Step 1: Skrifa prófin**

```python
"""Pure query construction and the relaxation decision."""
import pytest
from engine.search import relaxation
from engine.search.relaxation import KeywordQueries, build_keyword_queries, should_relax


def test_single_lemma():
    kq = build_keyword_queries("krafa")
    assert kq == KeywordQueries(strict="krafa", nminus1=None, any="krafa", n=1)


def test_two_lemmas_nminus1_equals_any():
    kq = build_keyword_queries("gæsluvarðhald rannsókn")
    assert kq.strict == "gæsluvarðhald & rannsókn"
    assert kq.nminus1 == "gæsluvarðhald | rannsókn"
    assert kq.any == "gæsluvarðhald | rannsókn"
    assert kq.n == 2


def test_three_lemmas():
    kq = build_keyword_queries("a b c")
    assert kq.strict == "a & b & c"
    assert kq.nminus1 == "(a & b) | (a & c) | (b & c)"
    assert kq.any == "a | b | c"


def test_four_lemmas_has_four_subsets():
    kq = build_keyword_queries("a b c d")
    assert kq.nminus1.count("|") == 3 and kq.nminus1.count("&") == 8


def test_nine_lemmas_skips_nminus1():
    kq = build_keyword_queries(" ".join(f"l{i}" for i in range(9)))
    assert kq.n == 9 and kq.nminus1 is None and kq.any.count("|") == 8


def test_multi_token_lemma_and_extra_whitespace():
    kq = build_keyword_queries("  e mál   krafa ")
    assert kq.strict == "e & mál & krafa" and kq.n == 3


def test_empty_raises():
    with pytest.raises(ValueError):
        build_keyword_queries("   ")


def test_should_relax_boundaries(monkeypatch):
    monkeypatch.setattr(relaxation, "RELAX_BELOW", 10)
    assert should_relax(9, 2) is True
    assert should_relax(10, 2) is False
    assert should_relax(0, 1) is False
    assert should_relax(0, 2, threshold=0) is False
    assert should_relax(3, 3, threshold=5) is True
```

- [ ] **Step 2: Keyra og sjá falla**

Run: `uv run pytest tests/test_relaxation.py -v` — Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Skrifa eininguna**

```python
"""Relaxed keyword search: query construction and the relaxation decision.

Strict = all lemmas (AND). When fewer than RELAX_BELOW documents match strictly,
search widens to documents matching all-but-one lemma (tier 1) and then any lemma
(tier 2), always ranked after the strict matches. Pure: no DB access.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

# Above this many lemmas the all-but-one tier is skipped (C(n, n-1) = n subsets,
# each an n-1 way AND — fine up to 8, pointless beyond).
MAX_NMINUS1_LEMMAS = 8

# Relax when the strict document count is below this. 0 disables relaxation.
# Provisional; the final value is chosen with `scripts/eval_search.py --relax-sweep`.
RELAX_BELOW = 10


@dataclass(frozen=True)
class KeywordQueries:
    strict: str          # to_tsquery source: 'a & b & c'
    nminus1: str | None  # '(a & b) | (a & c) | (b & c)'; None when n < 2 or n > MAX_NMINUS1_LEMMAS
    any: str             # 'a | b | c'
    n: int


def build_keyword_queries(lemmas: str) -> KeywordQueries:
    toks = lemmas.split()
    if not toks:
        raise ValueError("no lemmas")
    n = len(toks)
    strict = " & ".join(toks)
    any_q = " | ".join(toks)
    if 2 <= n <= MAX_NMINUS1_LEMMAS:
        if n == 2:
            nminus1: str | None = any_q
        else:
            nminus1 = " | ".join("(" + " & ".join(c) + ")" for c in combinations(toks, n - 1))
    else:
        nminus1 = None
    return KeywordQueries(strict=strict, nminus1=nminus1, any=any_q, n=n)


def should_relax(strict_total: int, n: int, threshold: int | None = None) -> bool:
    limit = RELAX_BELOW if threshold is None else threshold
    return n >= 2 and limit > 0 and strict_total < limit
```

- [ ] **Step 4: Keyra prófin** — Expected: 8 PASS.
- [ ] **Step 5: Commit** `feat(search): relaxation query builder and decision`

---

### Task 2: `build_hits_sql` / `search_by_passages` með þrepum

**Files:**
- Modify: `engine/search/passage_search.py`
- Modify: `engine/search/queries.py` (`SearchResults` fær `strict_total: int = 0`, `relaxed: bool = False`)
- Test: `tests/test_passage_search.py`

**Interfaces:**
- `build_hits_sql(*, tsq, or_tsq, doc_where, section_filter, sort="relevance", relax: tuple[str, str | None] | None = None) -> str` — `relax=(any_tsq_sql, nminus1_tsq_sql_or_None)`.
- `order_sql(sort, strategy=None)` skilar strengjum sem byrja á `h.tier ASC, `.
- `search_by_passages(..., relax_params: tuple[str, str | None] | None = None, strict_total: int | None = None)`; `relax_params=("q_any", "q_nminus1")` eru bind-nöfn. Skilar `SearchResults(total, page, page_size, results, strict_total, relaxed)`; hver niðurstaða fær `"match_tier"`.

- [ ] **Step 1: Prófin**

```python
def test_hits_sql_unrelaxed_has_constant_tier_and_strict_prefilter():
    sql = build_hits_sql(tsq="to_tsquery('simple', :q_strict)", or_tsq=None, doc_where=[], section_filter=False)
    assert "0 AS tier" in sql and "CASE WHEN d.fts_is" not in sql
    assert "WHERE d.fts_is @@ to_tsquery('simple', :q_strict)" in sql
    assert "max(c.tier) AS tier" in sql


def test_hits_sql_relaxed_uses_any_prefilter_and_tier_case_before_limit():
    sql = build_hits_sql(tsq="to_tsquery('simple', :q_strict)", or_tsq="to_tsquery('simple', :q_any)",
                         doc_where=["d.document_date >= :date_from"], section_filter=False,
                         relax=("to_tsquery('simple', :q_any)", "to_tsquery('simple', :q_nminus1)"))
    assert "WHERE d.fts_is @@ to_tsquery('simple', :q_any) AND d.document_date >= :date_from" in sql
    assert "WHEN d.fts_is @@ to_tsquery('simple', :q_strict) THEN 0" in sql
    assert "WHEN d.fts_is @@ to_tsquery('simple', :q_nminus1) THEN 1" in sql
    cand = sql.split("hits_and")[0]
    assert cand.index("ORDER BY tier ASC") < cand.index("LIMIT :cand_limit")


def test_hits_sql_relaxed_without_nminus1_has_two_tiers():
    sql = build_hits_sql(tsq="T", or_tsq="A", doc_where=[], section_filter=False, relax=("A", None))
    assert "THEN 1" not in sql and "ELSE 2 END AS tier" in sql


@pytest.mark.parametrize("sort", ["relevance", "newest", "oldest"])
def test_order_sql_puts_tier_first(sort):
    assert order_sql(sort).startswith("h.tier ASC, ")
```

- [ ] **Step 2: Keyra og sjá falla.**
- [ ] **Step 3: Útfæra**

Í `build_hits_sql`:
```python
    if relax is None:
        cand_where = f"d.fts_is @@ {tsq}"
        tier_expr = "0"
    else:
        any_tsq, nminus1_tsq = relax
        cand_where = f"d.fts_is @@ {any_tsq}"
        mid = f"WHEN d.fts_is @@ {nminus1_tsq} THEN 1 " if nminus1_tsq else ""
        tier_expr = f"CASE WHEN d.fts_is @@ {tsq} THEN 0 {mid}ELSE 2 END"
    ...
        cand AS (
            SELECT d.id, {rank_fn}(d.fts_is, {tsq}) AS doc_rank, {tier_expr} AS tier
            FROM documents d
            WHERE {cand_where}{doc_where_sql}
            ORDER BY tier ASC, {cand_order}
            LIMIT :cand_limit
        ),
```
og `max(c.tier) AS tier` í `hits_and` og `hits_or` (dálkaröð eins í báðum UNION-greinum). `order_sql`: `"h.tier ASC, " + …` fyrir allar þrjár raðanir. Í `search_by_passages`: byggja `relax`-tuple úr `relax_params` (`to_tsquery('simple', :q_any)` o.s.frv.), `total` = doc-level talning með `any`-fyrirspurninni þegar slakað (annars strict eins og í dag; með `section_kind` áfram `count(*) FROM hits`), `strict_total` = viðfangið ef gefið annars = `total`; `"match_tier": r["tier"]` í niðurstöðum (`h.tier` í SELECT); skila `SearchResults(..., strict_total=strict_total, relaxed=relax_params is not None)`. `SearchResults` í queries.py fær tvö ný svið með sjálfgefnum gildum svo regex-leiðin þurfi enga breytingu; regex-niðurstöður fá `"match_tier": 0`.

- [ ] **Step 4: Keyra `uv run pytest tests/test_passage_search.py tests/test_search_queries.py -q`.**
- [ ] **Step 5: Commit** `feat(search): tiered candidate prefilter for relaxed keyword search`

---

### Task 3: Ákvörðun í `search_documents` og `facet_counts`

**Files:**
- Modify: `engine/search/queries.py`
- Test: `tests/test_search_queries.py`

**Interfaces:**
- Consumes: Task 1 og 2.
- Produces: `search_documents` skilar `strict_total`/`relaxed`/`match_tier`; `facet_counts` slakar með sömu reglu; hjálparfall `async def _strict_doc_count(session, tsq_sql, where, params) -> int`.

- [ ] **Step 1: Prófin** (hrein — nota `FakeSession` sem skráir SQL-strengi og skilar töluröð):

```python
async def test_keyword_dispatch_relaxes_when_strict_count_below_threshold(monkeypatch):
    # FakeSession: first execute → scalar 3 (strict count), then relaxed path is called
    # with relax_params; assert search_by_passages received relax_params=("q_any","q_nminus1")
    # and params contain q_strict/q_any/q_nminus1 built from the lemmas.
    ...

async def test_keyword_dispatch_no_relax_when_strict_count_at_threshold(monkeypatch): ...
async def test_single_lemma_never_relaxes(monkeypatch): ...
async def test_facets_use_any_query_when_relaxed(monkeypatch): ...  # where contains ':q_any' not ':q_strict'
```
(Fylla út með `monkeypatch.setattr(queries, "search_by_passages", fake)` sem fangar viðföngin; `FakeSession.execute` skilar hlut með `.scalar()`.)

- [ ] **Step 2: Keyra og sjá falla.**
- [ ] **Step 3: Útfæra**

Í `keyword`-greininni, í stað `params["lemmas"] = lemmas` + `_or_query_or_none`:
```python
            kq = build_keyword_queries(lemmas)
            params["q_strict"] = kq.strict
            strict_tsq = "to_tsquery('simple', :q_strict)"
            strict_total = await _strict_doc_count(session, strict_tsq, where, params)
            relaxed = should_relax(strict_total, kq.n)
            or_param = None
            relax_params = None
            if kq.n >= 2:
                params["q_any"] = kq.any
                or_param = "q_any"
            if relaxed:
                if kq.nminus1:
                    params["q_nminus1"] = kq.nminus1
                relax_params = ("q_any", "q_nminus1" if kq.nminus1 else None)
            return await search_by_passages(
                session, tsq_fn="to_tsquery", tsq_param="q_strict", where=where, params=params,
                sort=sort, page=page, page_size=page_size, section_kinds=section_kinds,
                or_tsq_param=or_param, relax_params=relax_params, strict_total=strict_total)
```
`_or_query_or_none` fellt (og próf þess). `facet_counts`: sama `kq`, strangi fjöldi með facets-síunum, `should_relax` → `where.append("d.fts_is @@ to_tsquery('simple', :q_any)")` annars `:q_strict`. Athugasemd sem útskýrir að facets og leit geti verið ósammála þegar svið er þrengra en heildin.

- [ ] **Step 4: Keyra `uv run pytest -q`.** Lifandi athugun með `.env`: `search_documents(q="tómlæti verklaun byggingarframkvæmdir gjaldþrot", scope=["domstolar"])` → `relaxed=True`, fyrstu niðurstöður `match_tier` 0/1 í röð; og `q="gæsluvarðhald"` → `relaxed=False`, óbreytt `total`.
- [ ] **Step 5: Commit** `feat(search): relax keyword search below RELAX_BELOW strict hits; facets follow`

---

### Task 4: API-svið

**Files:**
- Modify: `engine/api/app.py` (`search` skilar `strict_total`, `relaxed`)
- Test: `tests/test_api_passages.py`

- [ ] **Step 1: Próf** — `FakeSession` sem lætur `search_documents` skila `SearchResults(total=5, …, strict_total=2, relaxed=True, results=[{…, "match_tier": 1}])` (með `monkeypatch` á `app.search_documents`), og fullyrða að svarið hafi `strict_total == 2`, `relaxed is True`, `results[0]["match_tier"] == 1`.
- [ ] **Step 2–4:** útfæra (`return {"total": …, "strict_total": res.strict_total, "relaxed": res.relaxed, …}`), keyra, commit `feat(api): expose strict_total, relaxed and match_tier`.

---

### Task 5: Framendi

**Files:**
- Modify: `frontend/src/api/types.ts` (`SearchResponse.strict_total: number; relaxed: boolean;` `SearchResult.match_tier: number`)
- Modify: `frontend/src/components/ResultsList.tsx` (tilkynningarlína), `frontend/src/components/ResultCard.tsx` (þrepamerki)
- Test: `ResultsList.test.tsx`, `ResultCard.test.tsx`

- [ ] **Step 1: Próf** — ResultsList: með `relaxed: true, strict_total: 2, total: 9` sést textinn „2 skjöl innihalda öll leitarorðin“ og „7 skjöl“; með `strict_total: 0` sést „Engin skjöl innihalda öll leitarorðin“; með `relaxed: false` sést hvorugt (`queryByTestId("relaxed-notice")` null). ResultCard: `match_tier: 1` → merki „flest orðin“ (`data-testid="match-tier"`), `2` → „sum orðin“, `0` → ekkert.
- [ ] **Step 2: Sjá falla; Step 3: útfæra** (tilkynning: `<p data-testid="relaxed-notice" className="text-sm text-[var(--ink-soft)] mb-2">`; merki með sömu Tailwind-táknum og `passage-anchor`-merkið); MSW-sniðmátin í `useSearch.test.tsx`/`SearchPage.test.tsx` fá `strict_total`/`relaxed`/`match_tier` ef `tsc -b` krefst þess.
- [ ] **Step 4: `npx vitest run && npx tsc -b`.** **Step 5: Commit** `feat(frontend): relaxed-search notice and match-tier chips`.

---

### Task 6: `--relax-sweep`, val á mörkum, wiki

**Files:**
- Modify: `scripts/eval_search.py`, `tests/test_eval_search.py`
- Modify: `engine/search/relaxation.py` (endanlegt `RELAX_BELOW` + athugasemd)
- Modify: `docs/wiki/05-leit.md`, `06-api.md`, `07-framendi.md`, `09-gildrur.md`; þessi áætlun (kafli „Niðurstöður mælinga“)

- [ ] **Step 1:** `--relax-sweep` (sama mynstur og `--rank-sweep`): fyrir `K ∈ (0, 5, 10, 20, 50)` setja `relaxation.RELAX_BELOW = K`, keyra `evaluate`, prenta töflu `K | recall@10 | MRR | hit@1 | 0-hit | relaxed-queries | p50 | p95`, endurstilla í `finally`. `evaluate` telur `relaxed` úr `res.relaxed`. Próf: `filter_set`/`by_style` óbreytt; nýtt hreint próf að sweep-listinn sé `(0,5,10,20,50)`.
- [ ] **Step 2:** Keyra `set -a; . ./.env; set +a; uv run python scripts/eval_search.py --relax-sweep --k 10 --set all | tee /tmp/lausnir-dev/relax_sweep.txt`. Skrá töfluna í þessa áætlun undir `## Niðurstöður mælinga`.
- [ ] **Step 3: NOTANDI VELUR `RELAX_BELOW`** út frá töflunni (gæði á móti p95). Controller ber töfluna undir notanda; þar til svar berst stendur 10.
- [ ] **Step 4:** Festa gildið með athugasemd (dagsetning, tölur), wiki: 05-leit (nýr kafli „Slökuð leit“: þrep, kveikja, `strict_total`), 06-api (nýju sviðin), 07-framendi (tilkynning og merki), 09-gildrur (facets og leit geta verið ósammála um slökun þegar svið er þrengra; kostnaður víðustu fyrirspurnar).
- [ ] **Step 5:** `uv run pytest -q`, `npx vitest run`, `npx tsc -b`; commit `feat(eval): --relax-sweep; set RELAX_BELOW from measurement; docs`.

---

## Niðurstöður mælinga

Mælt 28.09.2026 á 492 spurninga gullsettinu (`--set all`), eftir að `5ce20d6` (perf(search): select relaxed candidates per tier without detoasting the whole any-set) var lent — fyrir þá lagfæringu hafði fyrsta tilraun til `--relax-sweep` verið stöðvuð eftir að einstakar slakaðar fyrirspurnir tóku allt að 30 s hver (upprunalega `cand` afþjappaði `fts_is` fyrir hvert skjal í víðasta menginu áður en það var raðað/þreps-merkt).

`uv run python scripts/eval_search.py --relax-sweep --k 10 --set all`:

```
   K  recall@10    MRR  hit@1 0-hit relaxed  p50 ms  p95 ms
   0      0.327  0.210  0.161    78       0      27     299
   5      0.396  0.243  0.181     0     136      77    4463
  10      0.402  0.244  0.181     0     183     130    8660
  20      0.402  0.244  0.181     0     238     307   14644
  50      0.402  0.244  0.181     0     309     860   20401
```

`uv run python scripts/eval_search.py --k 10 --set all` (núverandi sjálfgefið `RELAX_BELOW = 10`):

```
  n  recall@10    MRR  hit@1  p50 ms  p95 ms 0-hit
492      0.402  0.244  0.181     178   10454     0

medium           255      0.408  0.255
short            176      0.420  0.260
term              61      0.328  0.154

Coverage: 92,926/92,926 documents with text have passages (100.0%)
```

**Gæði á móti seinkun.** Nánast allur gæðaábatinn kemur strax við `K=5`: núll-treff fara úr 78 í 0, recall@10 hækkar úr 0,327 í 0,396, og hit@1/MRR hækka sambærilega. Frá `K=5` upp í `K=50` er gæðataflan **algjörlega flöt** (recall/MRR/hit@1 óbreytt frá og með `K=10`) — hærri þröskuldur bætir engu við gæðin á þessu gullsetti, hann fjölgar bara fyrirspurnum sem fara í slökuðu leiðina (136 → 183 → 238 → 309 af 492). Á móti vex `p95` nánast línulega með `K`: 0,3 s óslakað, svo 4,5 s / 8,7 s / 14,6 s / 20,4 s fyrir `K = 5/10/20/50`. Þetta er í samræmi við viðvörunina í specinu um að víðasta þrepið („eitthvert orðanna") geti passað við tugþúsundir skjala fyrir algengt orð AND-að sjaldgæfu orði — fyrri útgáfan af `cand` gerði þetta miklu verra (allt að 30 s á staka fyrirspurn) og `5ce20d6` minnkaði það verulega, en versta tilfellið er samt langt yfir specsins ~1,5 s viðmiðunarmörk við hærri `K`-gildi. `p50` er hóflegt á öllum stigum (27–860 ms) — það er `p95`-halinn sem stækkar, drifinn af fáum en mjög dýrum orðasamsetningum, ekki dæmigerðri fyrirspurn. Sjálfgefna keyrslan (`RELAX_BELOW=10`, engin `--relax-sweep`-stýring) staðfestir sömu tölur óháð sópinu: `recall@10 0,402`, `p95 10.454 ms` — aðeins hærra en `K=10`-röðin í sópinu, sennilega vegna kaldari skyndiminnis í sjálfstæðri keyrslu á eftir sópinu heldur en röð sem naut upphitunar frá `K=0`.

**Sjálfgefið gildi: valið af notanda eftir þessa töflu (bíður).**
