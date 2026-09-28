# 06 — API

← [Wiki-forsíða](README.md)

`engine/api/app.py` — FastAPI, JSON, **staðbundið, engin auðkenning**.

```bash
uv run uvicorn engine.api.app:app --reload --port 8077
```

CORS leyfir hvaða `localhost` / `127.0.0.1` / `192.168.x.x` uppruna sem er, aðeins `GET`.
`lifespan` kallar á `init_db()` við ræsingu.

## Endapunktar

### `GET /api/health`
```json
{"status": "ok"}
```

### `GET /api/sources`
Flokkunartréð með skjalatölum + flatur heimildalisti.

```json
{
  "catalog": [{"key": "domstolar", "label": "Dómstólar", "count": 44321,
               "children": [...]}],
  "sources": [{"short_name": "heradsdomstolar", "display_name": "Héraðsdómstólar",
               "abbreviation": "Hérd.", "count": 24192}],
  "regex_fields": ["body_text", "summary", "case_number", "lower_body_text",
                   "parties", "keywords"],
  "total": 91152
}
```

### `GET /api/search`

| Viðfang | Sjálfgefið | Athugasemd |
|---|---|---|
| `q` | `""` | Leitartexti eða regex-mynstur |
| `mode` | `keyword` | `keyword\|exact\|prefix\|substring\|any\|proximity\|regex` |
| `scope` | — | Endurtekið. Hnútalyklar úr flokkunartrénu, `short_name`, eða `all` |
| `date_from` / `date_to` | — | ISO-dagsetning |
| `sort` | `relevance` | `relevance\|newest\|oldest` |
| `page` | 1 | ≥1 |
| `page_size` | 20 | 1–100 |
| `regex_fields` | — | Endurtekið, aðeins í regex/texta-hömum |
| `proximity_n` | 5 | 1–50, aðeins `proximity` |
| `provision` | — | T.d. `"218. gr. 19/1940"` |
| `keyword` | — | Sía eingöngu á `keywords`-dálkinn, hlutastrengur |
| `section_kind` | — | Endurtekið. Þrengir `keyword`/`proximity` við tilteknar efnisgreinategundir: `reifun`, `malsmedferd`, `malsatvik`, `malsastaedur`, `nidurstada`, `domsord`, `annad`. Óþekkt gildi → HTTP 400. |

```json
{
  "total": 1234, "page": 1, "page_size": 20,
  "strict_total": 1234, "relaxed": false,
  "results": [{
    "id": "uuid", "urlausn": "Hrd. 59/2025 10. júní 2026 – Dómur",
    "source": "haestirettur", "source_display": "Hæstiréttur",
    "court": "Hrd.", "case_number": "59/2025", "document_date": "2026-06-10",
    "verdict_type": "Dómur", "keywords": ["Börn"],
    "plaintiffs": [{"name": "A", "lawyer": null}], "defendants": [...],
    "snippet": "…texti með <mark>áherslu</mark>…", "has_appeal_links": true,
    "passage_id": "uuid", "anchor": "12. mgr.", "section_kind": "nidurstada",
    "layer": "body", "match_count": 3, "match_tier": 0
  }]
}
```

Fimm svæðin `passage_id`…`match_count` koma frá efnisgreininni sem gaf besta samsvörun í `keyword`/`proximity` leit (sjá [05-leit](05-leit.md)) — öll `null` fyrir hina hamina, þar sem samsvörunin er á skjalstigi.

**Slökuð leit** (sjá [05-leit](05-leit.md) „Slökuð leit"), aðeins `keyword` með ≥2 lemmum:
- `strict_total` — fjöldi skjala sem uppfylla ströngu fyrirspurnina (öll orðin). Óslakað: `strict_total == total`.
- `relaxed` — `true` þegar strangi fjöldinn var undir `RELAX_BELOW` og leitin var víkkuð út. Þegar `relaxed` er satt er `total` fjöldi **raunverulega fáanlegra** niðurstaðna (bundinn af `RELAX_CAND_LIMIT`), ekki doc-level fjöldi skjala sem uppfylla víðustu fyrirspurnina.
- Hver niðurstaða: `match_tier` — `0` (öll orðin), `1` (öll nema eitt) eða `2` (eitthvert orðanna); alltaf `0` fyrir óslakaða leit og fyrir hina hamina.

Ógilt regex eða óþekkt svæði → **HTTP 400** með `{"detail": "..."}`.

### `GET /api/facets`
Sömu síuviðföng og `/api/search` (án `scope`, `sort`, `page`). Skilar flokkunartrénu með tölum fyrir virka fyrirspurn — knýr hliðarstikuna.

```json
{"catalog": [...], "total": 1234}
```

### `GET /api/document/{doc_id}`

`?markdown=true` (sjálfgefið) bætir við `markdown` sviði sem er reiknað á staðnum með `renderer.to_markdown()` — RENDER-lagið, aldrei geymt.

Skilar öllum NORM-gildum auk `appeal_links` (úr `document_links`, með `urlausn` tilvitnun hins skjalsins). 404 ef ekki finnst.

### `GET /api/document/{doc_id}/passages`

Raðaðar efnisgreinar eins skjals — samhengisfrumeining fyrir tilvitnanir og verkfæri (LLM-samhengissókn o.fl.), óháð leit.

| Viðfang | Sjálfgefið | Athugasemd |
|---|---|---|
| `from` | 0 | Upphafs-`ordinal` |
| `to` | `from + 49` | Loka-`ordinal`, að hámarki 200 efnisgreinar í einu (`MAX_PASSAGE_WINDOW`) |
| `section_kind` | — | Endurtekið, sama gildissvið og í `/api/search` |
| `layer` | — | `summary` \| `body` \| `lower_body` |

```json
{
  "document_id": "uuid", "urlausn": "Hrd. 59/2025 10. júní 2026 – Dómur",
  "total": 42,
  "passages": [{
    "id": "uuid", "ordinal": 12, "layer": "body", "anchor": "12. mgr.",
    "section_path": null, "section_kind": "nidurstada",
    "para_from": 12, "para_to": 12, "char_start": 4210, "char_end": 4532,
    "word_count": 61, "text": "…"
  }]
}
```

404 ef skjalið finnst ekki. HTTP 400 ef `to < from`, glugginn er of stór, eða `layer`/`section_kind` er ógilt.

### `GET /api/law/{doc_id}`
Lagasafnsskjal með skipulögðum ákvæðum. 404 ef skjalið er ekki úr `lagasafn_*` heimild.

```json
{
  "id": "uuid", "case_number": "19/1940", "law_name": "Almenn hegningarlög",
  "verdict_type": "Lög", "document_date": "1940-02-12", "url": "...",
  "kafli": 18, "kafli_label": "18. Refsilög, fangelsismál o.fl.",
  "provisions": [{"num": 218, "suffix": null, "text": "...",
                  "sub": [{"num": 1, "text": "..."}]}]
}
```

`_clean_law_name()` strípar neðanmálssvigana frá Alþingi: `"[Lög um ...]1)"` → `"Lög um ..."`.

### `GET /api/provision`
Sækir texta einnar lagagreinar.

```
/api/provision?law=19/1940&gr=218                       → 218. gr. í heild
/api/provision?law=19/1940&gr=218&mgr=1                 → 218. gr. 1. mgr.
/api/provision?law=19/1940&gr=218&gr_suffix=a           → 218. gr. a.
/api/provision?law=19/1940&gr=218&gr_suffix=a&mgr=1     → 218. gr. a. 1. mgr.
```

404 með lýsandi skilaboðum ef lögin, greinin eða málsgreinin finnst ekki (t.d. *„218. gr. not found in law '19/1940' (has 264 articles)"*).
