# Héraðsdómstólar — PDF-meðhöndlunarreglur

## Um skjölin

Héraðsdómar koma sem base64-kóðuð PDF í `pdfString` reit detail-svars frá island.is GraphQL.
`richText` er alltaf `None`. `resolutionLink` er alltaf `None`.
Héraðsdómar eru **first-instance** — `lower_body_text` er alltaf `None`.

---

## PDF-uppbygging

```
┌─────────────────────────────────┐
│  [Haus klipptur 65pt]           │  ← header_pt = 65 (nema fyrsta síða)
│                                 │
│  Dómur                          │  ← formáli: dómstóll, dagsetning, málsnr, aðilar
│  Héraðsdómur Reykjavíkur        │    þessi hluti er STRIPAÐUR í build_new_md()
│  ...                            │
│  [Meginmál dómsins]             │
│                                 │
│  [blaðsíðunúmer / fótur]        │  ← footer_pt = 62
└─────────────────────────────────┘
```

**Crop-stillingar:**

| Stilling | Gildi | Ástæða |
|---|---|---|
| `header_pt` | `65` | Klippir dómstólsheiti og málsnúmer úr haus |
| `footer_pt` | `62` | Klippir blaðsíðunúmer |
| `skip_header_on_first` | `True` | Fyrsta síða: engin hausklipping — formálinn er þar |

---

## Fyrirsagnargreining

Héraðsdómar nota **leturstærð** til að greina fyrirsagnir.

| Stærð (pt) | Markdown | Dæmi |
|---|---|---|
| `20.0` | `# ` | Titillinn efst |
| `14.0` | `## ` | `## I. Málsatvik` |

---

## Formálastripping

Formálinn efst í PDF-inu (dómstólsheiti, dagsetning, málsnúmer, nöfn aðila) er **stripaður**
áður en texti er vistaður. Þetta gerir `strip_raw_body_h1()` í `heading_normalizer.py`.

Ástæða: þessar upplýsingar eru geymdar í structured dálkum (`court`, `document_date`,
`case_number`, `plaintiffs`, `defendants`) og eru endurreiknaðar í markdown-hausum við render.

---

## Málsgreinareglur

Sömu reglur og Landsréttur (sjá `landsrettur.md`) — jaðartölur, lagatilvísanir, síðuskil.

---

## Textaskipting

Héraðsdómar eru first-instance — **engin skipting**:
- `body_text` = allur meginmálstexti
- `lower_body_text` = alltaf `None`
