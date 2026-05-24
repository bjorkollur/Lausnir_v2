# Landsréttur — PDF-meðhöndlunarreglur

## Um skjölin

Landsréttarskjöl koma sem base64-kóðuð PDF í `pdfString` reit detail-svars frá island.is GraphQL.
`richText` er alltaf `None` — PDF er eina uppspretta meginmálstexta.
`resolutionLink` er alltaf `None` — héraðsdómstexti er felldur inn í enda skjalsins, ekki tengdur sér.

---

## PDF-uppbygging

```
┌─────────────────────────────────┐
│  [Engin klipping efst]          │  header_pt = 0
│                                 │
│  Landsréttur                    │  ← titill og málsupplýsingar á fyrstu síðu
│  Mál nr. 123/2024               │
│  Dómur 5. maí 2024              │
│                                 │
│  [Meginmál dómsins]             │
│                                 │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─   │  ← skil á milli dómstiganna
│                                 │
│  [Héraðsdómstexti innfelldur]   │
│                                 │
│  [blaðsíðunúmer / fótur]        │  ← klippt 65pt frá botni
└─────────────────────────────────┘  footer_pt = 65
```

**Crop-stillingar:**

| Stilling | Gildi | Ástæða |
|---|---|---|
| `header_pt` | `0` | Engin haus á efri jaðri — titillinn er hluti af meginmáli |
| `footer_pt` | `65` | Klippir blaðsíðunúmer og fótlínu |
| `skip_header_on_first` | `False` | Fyrsta síða fær sömu meðhöndlun og aðrar |

---

## Fyrirsagnargreining

Landsréttur notar **leturgerð** til að merkja fyrirsagnir — ekki leturstærð.
Allar fyrirsagnir eru í sama stærð og meginmálið.

| Leturgerð (fontname) | Markdown | Dæmi |
|---|---|---|
| inniheldur `Bold` (ekki `Ital`) | `## ` | `## I. Málsatvik` |

**Gamlar skrár (2018–2024):** nota `Times New Roman,Bold` eða `Times New Roman Bold,Bol`.  
**Nýjar skrár (2024–):** nota `TimesNewRomanPS-BoldMT`.  
Báðar innihalda `Bold` án `Ital` — þær eru þar af leiðandi skilgreindar í `heading_fonts={"Bold": "## "}`.

**Undantekningar:**
- Skáletraðar feitlættar leturgerðir (`BoldItal`, `BoldItalic`) eru slepptar.
- Lína í feitlættu letri sem er eingöngu tala (t.d. `"5"`) er **ekki** fyrirsögn — hún er jaðartala, og sameinast næstu línu.

Stýrist af `_heading_marker()` í `extractor.py`.

---

## Málsgreinareglur

### Tölusett atriði vs. lagatilvísanir

Mynstur sem telst ný málsgrein:
```
^\s*\d+\.\s+[A-ZÁÉÍÓÚÝÞÆÖ„\"«]
```
Krefst **stórs stafbókstafs** á eftir — þannig að:

| Texti | Túlkun |
|---|---|
| `10. Stefnandi krefst þess...` | ✓ Ný málsgrein |
| `1. mgr. laga nr. 91/1991` | ✗ Lagatilvísun (lágstafur) |
| `3. gr. samningsins` | ✗ Lagatilvísun (lágstafur) |

### Jaðartölur (margin numbers)

Landsréttarskjöl hafa oft málsgreinanúmer prentað **við vinstri jaðar** í minna letri en meginmálið — PDF-þáttarinn lítur á þau sem sjálfstæðar línur.

Þegar lína er eingöngu tala (`^\d{1,3}$`) í minna letri en meginmálið:
→ Sameinist næstu línu sem `"N. [texti]"`

Dæmi úr PDF:
```
4          Stefndi mótmælir öllum kröfum...
```
Niðurstaða: `4. Stefndi mótmælir öllum kröfum...`

### Tala ein á línu (`5.`)

Þegar lína er eingöngu `N.` (tala + punktur, engin annar texti):
→ Aldrei fyrirsögn
→ Sameinist næstu línu sem `"N. [texti]"`

---

## Síðuskilareglur

Þegar tvær síður eru skeyttar saman ræður **fyrsti stafur næstu síðu:**

| Fyrsti stafur næstu síðu | Túlkun | Línubil |
|---|---|---|
| Lágstafur | Framhald málsgreinar | `\n` (eitt) |
| Stór stafur | Ný málsgrein | `\n\n` (tvö) |
| Tölusett málsgrein (`N. Stór...`) | Ný málsgrein | `\n\n` (tvö) |
| Fyrirsögn (`#`) | Ný blokk | `\n\n` (tvö) |

---

## Textaskipting: Landsréttur vs. Héraðsdómur

Landsréttarskjöl innihalda oft texta **beggja dómstiganna** í einu PDF-skjali.
Héraðsdómstextinn byrjar á þekktu mynstri neðst í skjalinu.

**`body_text`** = texti Landsréttar (frá upphafi að skiltákninu)
**`lower_body_text`** = innfelldur héraðsdómstexti (frá skiltákninu til enda)

Þetta split er gert af `split_court_texts()` eftir að PDF-texti er dreginn út.
Ef ekkert skiltákn finnst → allt fer í `body_text`, `lower_body_text = None`.
