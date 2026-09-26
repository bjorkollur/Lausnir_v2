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

```json
{
  "total": 1234, "page": 1, "page_size": 20,
  "results": [{
    "id": "uuid", "urlausn": "Hrd. 59/2025 10. júní 2026 – Dómur",
    "source": "haestirettur", "source_display": "Hæstiréttur",
    "court": "Hrd.", "case_number": "59/2025", "document_date": "2026-06-10",
    "verdict_type": "Dómur", "keywords": ["Börn"],
    "plaintiffs": [{"name": "A", "lawyer": null}], "defendants": [...],
    "snippet": "…texti með <mark>áherslu</mark>…", "has_appeal_links": true
  }]
}
```

Ógilt regex eða óþekkt svæði → **HTTP 400** með `{"detail": "..."}`.

### `GET /api/facets`
Sömu síuviðföng og `/api/search` (án `scope`, `sort`, `page`). Skilar flokkunartrénu með tölum fyrir virka fyrirspurn — knýr hliðarstikuna.

```json
{"catalog": [...], "total": 1234}
```

### `GET /api/document/{doc_id}`

`?markdown=true` (sjálfgefið) bætir við `markdown` sviði sem er reiknað á staðnum með `renderer.to_markdown()` — RENDER-lagið, aldrei geymt.

Skilar öllum NORM-gildum auk `appeal_links` (úr `document_links`, með `urlausn` tilvitnun hins skjalsins). 404 ef ekki finnst.

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
