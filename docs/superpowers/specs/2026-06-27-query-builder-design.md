# Lausnir v2 — Query Builder (Leitargluggi v1) — Hönnun

**Dagsetning:** 2026-06-27  
**Staða:** Samþykkt hönnun, tilbúin í implementation-plan.

## Samhengi & tilgangur

Núverandi leit hefur tvær stillingar: `keyword` (BÍN FTS) og `regex` (raw POSIX). Notendur þurfa meira:  
exact word, hluti af orði, byrjar á, OR-leit, AND-leit, og proximity (orð nálægt hvor öðru).  
„Regex"-rofinn verður modal dropdown með 7 stillingum.

## Ákvörðun: Approach B (samþykkt)

Mode dropdown, einn reitur, engar staflægar raðir. Bakendinn þýðir hverja stillingu  
yfir í besta SQL-fyrirspurn. Ekki þarf að gera JavaScript-þýðingu á flóknum regex-mynstrum.

## UI-breytingar

### ModeDropdown.tsx (kemur í stað RegexToggle.tsx)

Compact `<select>` eða Radix-popover við hliðina á leitarreitunum, sömu staðsetning og „Regex"-rofinn.

**7 stillingar:**

| Íslenska (UI) | key | Merking |
|---|---|---|
| Orðaleit | `keyword` | BÍN-lemmísering + FTS AND (núverandi) |
| Heilt orð | `exact` | Orðamörk: `\mword\M`, AND per orð |
| Hluti af orði | `substring` | Undirstrengur: `word`, AND per orð |
| Byrjar á | `prefix` | Forskeyti: `\mword`, AND per orð |
| Eitthvað af | `any` | OR: `(w1|w2|...)` |
| Nálægt | `proximity` | FTS `lemma1 <N> lemma2` |
| Regex | `regex` | Raw POSIX (núverandi) |

### Proximity sub-input

Þegar `mode=proximity` er valið: inline chip birtist hægra megin við dropdown:  
`innan [5 ↕] orða` — talnainntaksreitur (1–50, sjálfg. 5).

Þetta verður `proximity_n` í URL og query param.

### Toolbar-breytingar

- **„Reitir"-hnappur** (regex_fields): sýnilegur við `exact`, `prefix`, `substring`, `any`, `regex`. Falinn við `keyword` og `proximity` (nota FTS, ekki regex-dálka).
- **Röðun**: `keyword` og `proximity` geta notað `relevance` (FTS rank fáanlegur). Allar aðrar stillingar skipta sjálfkrafa yfir í `newest` þegar þær eru valdar (ekkert rank-signal).

### URL-state-breytingar

`mode` gildi stækkar úr `keyword|regex` í:  
`keyword | exact | prefix | substring | any | proximity | regex`

Nýr URL param: `proximity_n` (heiltala, sjálfg. 5, aðeins notaður þegar `mode=proximity`).

**Skrár sem breytast:**
- `frontend/src/lib/searchState.ts` — Mode type + proximity_n í SearchState, DEFAULT_STATE
- `frontend/src/api/types.ts` — Mode type uppfærð
- `frontend/src/api/client.ts` — proximity_n sent í querystring
- `frontend/src/components/RegexToggle.tsx` → `ModeDropdown.tsx` (ný skrá, gamla eytt)
- `frontend/src/components/Toolbar.tsx` — Reitir-sýnileiki skilyrtur, sort-auto-switch

## Bakenda-breytingar

### Nýjar mode-gerðir í `engine/search/queries.py`

Helper: `_build_text_filter(mode, words, fields, proximity_n) -> (list[str], dict)`  
þar sem `words = q.split()`.

| mode | SQL sem myndast |
|---|---|
| `exact` | `body_text ~* '\mw1\M' AND body_text ~* '\mw2\M' ...` |
| `prefix` | `body_text ~* '\mw1' AND ...` |
| `substring` | `body_text ~* 'w1' AND ...` |
| `any` | `body_text ~* '(w1|w2|...)'` |
| `proximity` | `fts_is @@ to_tsquery('simple', 'lemma1 <N> lemma2')` |

`regex_fields` gildir fyrir `exact/prefix/substring/any/regex` (eins og núna fyrir regex).  
`proximity` lemmatíserar hvert orð með `lemmatize_word()` áður en `<N>` query er byggt.

**Snippet-myndun:**
- `exact/prefix/substring/any` → Python `_regex_snippet()` (sama og regex-mode)
- `proximity` → `ts_headline('simple', body_text, to_tsquery('simple', 'w1 <N> w2'), ...)` — hápunktar bæði orð

`facet_counts()` fær sömu breytingu (sama where-myndun, bara án pagination).

### `engine/api/app.py`

- `mode` pattern uppfærð: `^(keyword|exact|prefix|substring|any|proximity|regex)$`
- Nýr param: `proximity_n: int = Query(5, ge=1, le=50)`
- Sent áfram til `search_documents()` og `facet_counts()`

## Prófanir (Vitest)

- `ModeDropdown.test.tsx` — 7 stillingar í dropdown, proximity_n input birtist/hverfur
- `searchState.test.ts` — proximity_n í URL encode/decode
- `queries_test.py` (pytest) — each mode generates correct SQL fragment

## Utan umfangs (v1)

- Boolean operators í einum reit (`AND`, `OR` inline í leitarstreng)
- Phrase search (orð í röð, beint)
- Wildcard (`*`) í keyword mode
- Multi-row Finder-style stöplun
