# Hæstiréttur — meðhöndlunarreglur

## Um skjölin

Hæstaréttardómar koma með **`richText`** (HTML) í detail-svari frá island.is GraphQL.
`pdfString` er einnig til staðar en er aðeins notað sem varaúrræði.
`resolutionLink` getur innihaldið URL á dóm lægra dómstigsins (Landsréttur eða Héraðsdómur).

---

## Textaútdráttur

**Aðalleið: `richText` (HTML → plain text)**

```python
from bs4 import BeautifulSoup

def _html_to_plain(html: str | None) -> str | None:
    if not html:
        return None
    return BeautifulSoup(html, "html.parser").get_text(separator="\n").strip() or None
```

Engar crop-stillingar þarf — HTML-texti hefur engan haus eða fót.

**Varaúrræði:** Ef `richText` er `None` eða tómur → `raw.get("text")` (aldur fallback).

---

## Textaskipting: Hæstiréttur vs. lægra dómstig

Hæstaréttardómar innihalda **ekki** texta lægra dómstigsins í sama skjali.
Lægra dómstig er aðskilið skjal tengt í gegnum `resolutionLink`.

- `body_text` = allur HTML-texti dómsins
- `lower_body_text` = alltaf `None` (sótt sér ef þarf í gegnum `resolutionLink`)

---

## Fyrirsagnar- og málsgreinareglur

Ekki við á — HTML-texti varðveitir uppbyggingu. BeautifulSoup `get_text()` nær yfir það.

---

## Verdict type greining

`verdict_type` er ekki í list-query. Greint úr `body_text` eftir HTML-strip:

| Merki í texta | `verdict_type` |
|---|---|
| `"Úrskurðarorð"` eða `"úrskurðar"` | `Úrskurður` |
| Annars | `Dómur` |
