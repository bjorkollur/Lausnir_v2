# 03 — Heimildir

← [Wiki-forsíða](README.md)

## `SourceConfig` — ein færsla per heimild

`engine/config/sources.py`. Meginreglan: **bættu við `SourceConfig` færslu í stað `if source_short_name == "X":` greina í pípunni.**

```python
@dataclass
class SourceConfig:
    short_name: str              # 'haestirettur' — lykill, aðeins a-z0-9_-
    display_name: str            # 'Hæstiréttur'
    abbreviation: str            # 'Hrd.' → fer í documents.court
    instance_tier: int           # 1=hérað, 2=Landsréttur, 3=Hæstiréttur
    has_lower_court: bool        # má meginmál innihalda texta lægra dómstigs?
    parse_parties: str           # 'gegn' | 'role_based' | 'none'
    verdict_type_default: str    # fallback ef ekki greint úr texta
    verdict_types_allowed: list[str]
    case_number_prefix: str      # t.d. 'E-', 'S-', '' — notað við staðfestingu
    pdf_crop: PdfCrop | None
    h1_use_display_name: bool    # H1 notar fullt heiti í stað skammstöfunar
    case_number_is_title: bool   # case_number geymir frjálsan titil (ritgerðir/bækur)
    stjornarradid_source: bool   # heimildin er á stjornarradid.is
```

Hjálparföll: `cfg.markdown_path(vf)` → `{DATA_DIR}/markdown/{short_name}/{vf}.md`, `cfg.pdf_path(vf)` → `{DATA_DIR}/raw/{short_name}/{vf}.pdf`.

### `PdfCrop` — PDF-sértækar stillingar

```python
@dataclass
class PdfCrop:
    header_pt: float             # klippa af haus
    footer_pt: float             # klippa af fæti
    skip_header_on_first: bool
    heading_sizes: dict[float, str]   # stærðarbyggð fyrirsagnaskynjun {12.0: "## "}
    heading_fonts: dict[str, str]     # leturbyggð {"Bold": "## "}
```

Landsréttur og Héraðsdómstólar nota `heading_fonts={"Bold": "## "}` því fyrirsagnir eru sama stærð og meginmál — aðeins feitletrun aðgreinir þær.

## Flokkunartréð (leitarsvið)

`engine/config/source_groups.py`. Fjórir yfirflokkar sem spegla Fons Juris:

| Flokkur | Innihald |
|---|---|
| **Dómstólar** | Hæstiréttur (Dómar / Úrskurðir / Málskotsbeiðnir), Landsréttur (Dómar / Úrskurðir), Héraðsdómstólar, Landsdómur, Félagsdómur, Endurupptökudómur |
| **Stjórnsýsla o.fl.** | Fjarskiptastofa, Fjölmiðlanefnd, Persónuvernd, Samgöngustofa, Samkeppniseftirlitið, Umboðsmaður Alþingis, Hugverkastofa |
| **Nefndir o.fl.** | Allt annað — kærunefndir, úrskurðarnefndir, ráðuneytaúrskurðir |
| **Bækur og fræðiskrif** | `logfraediritgerdir` (Skemman), `logfraedibaekur` |
| **Lagasafn** | 48 kaflar Alþingis-lagasafnsins |

**Sjálfviðhaldandi:** „Nefndir" er skilgreint sem *allt sem er ekki í hinum flokkunum* — ný nefndarheimild lendir þar sjálfkrafa. `validate_catalog()` (prófað í `tests/test_source_groups.py`) tryggir að **hver heimild lendi í nákvæmlega einum flokki**.

**Hnútaform:** `{key, label, [sources], [verdict_types], [children]}`. Hnútur með `verdict_types` síar heimildina líka á tegund — þannig verður „Hæstiréttur – Dómar" að `source = haestirettur AND verdict_type = 'Dómur'`.

Eftir `verdict_type`-lagfæringuna 29.09.2026 (sjá [09-gildrur](09-gildrur.md)) er `haestirettur_urskurdir` **0** — heimildin birtir aðeins dóma, líka í kærumálum. Hnúturinn stendur enn sem scope-lykill í API-inu og telur 0 í flokkunartrénu.

`resolve_scope()` breytir lista af hnútalyklum í `ScopeFilter` með SQL-skilyrðum.

## Heimildaskrá — 20 stærstu

| Heimild | `short_name` | Skjöl |
|---|---|---|
| Héraðsdómstólar | `heradsdomstolar` | 24.192 |
| Hæstiréttur | `haestirettur` | 12.210 |
| Landsréttur | `landsrettur` | 6.223 |
| Umboðsmaður Alþingis | `umbodsmadur` | 5.325 |
| Kærunefnd útlendingamála | `kaeruna_utlend` | 4.450 |
| Lögfræðiritgerðir (Skemman) | `logfraediritgerdir` | 4.311 |
| Yfirskattanefnd | `yfirskattanefnd` | 4.170 |
| Úrskn. velferðarmála – Almannatr. | `urvel` | 3.405 |
| Úrskn. umhverfis- og auðlindamála | `uua` | 2.981 |
| Kærunefnd húsamála | `knhus` | 2.016 |
| Úrskn. velferðarmála – Atvinnuleysistr. | `urvel_atv` | 1.864 |
| Mannanafnanefnd | `mannanafnanefnd` | 1.803 |
| Samkeppniseftirlitið | `samkeppni` | 1.650 |
| Kærunefnd útboðsmála | `kaeruna_utbod` | 1.412 |
| Málskotsbeiðnir Hæstaréttar | `malskotsbeidnir` | 1.284 |
| Persónuvernd | `personuvernd` | 1.240 |
| Úrskn. um upplýsingamál | `urnefnd_uppl` | 1.152 |
| Hugverkastofa | `hugverkastofa` | 1.108 |
| Úrskn. velferðarmála – Félagsþjónusta | `urvel_felag` | 993 |
| Innviðaráðuneyti | `innvida` | 838 |

Fullan lista má sækja beint:
```bash
psql "postgresql://geiri@localhost/lausnir_v2" -c "
select s.short_name, s.display_name, count(d.id) docs
from sources s left join documents d on d.source_id = s.id
group by 1,2 order by docs desc;"
```

`sources_catalogue.md` í rót geymir ítarlegri lýsingu á API-um hverrar heimildar — **en er úrelt** (segir ~86.614 skjöl / 59 heimildir; raunin er 91.152 / 111). Sjá [09-gildrur](09-gildrur.md).

## Að bæta við nýrri heimild

Gátlisti úr `CLAUDE.md`:

```
[ ] SourceConfig færsla í engine/config/sources.py
[ ] COURT_ABBR ef þarf (fyrir PDF-útdrátt á dómstólsheiti)
[ ] Extractor-fall í engine/processors/extractor.py + skráð í _EXTRACTORS
[ ] Import-skripta: scripts/import_{short_name}.py
[ ] Prófa 3 skjöl í DB: case_number, verdict_type, aðilar, body_text ekki tóm
[ ] Staðfesta að ≥90% skjala séu án validation_errors
[ ] sources_catalogue.md uppfært
```

Til er sérsniðið Claude-skill, `lausnir-new-source`, sem keyrir 7-fasa verkflæði með staðfestingu notanda í hverjum fasa.
