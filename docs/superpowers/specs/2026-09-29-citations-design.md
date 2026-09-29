# Tilvitnanir milli dóma (case-to-case citations) — hönnun

**Dagsetning:** 2026-09-29 (endurskoðuð sama dag eftir gagnrýna yfirferð; 14 atriði löguð, sjá kafla 14)
**Staða:** Innleitt 2026-09-29 á grein feat/citations; niðurstöður í áætluninni. Áætlun: `docs/superpowers/plans/2026-09-29-citations.md` (`## Niðurstöður keyrslu (2026-09-29)` og `## Nákvæmniúttekt` fyrir tölur og nákvæmnimælingu).
**Forsaga:** `docs/2026-09-27-mat-a-adferdafraedi-og-llm-leit.md` nefndi tilvitnanir milli dóma sem næstu rannsóknarvirkni á eftir leit og MCP. `document_links` ber í dag áfrýjunarkeðjuna (`appealed_to`/`appealed_from`) og málskotsbeiðnir (`leyfisbeidni_um`, `leiddi_til_doms`); tilvitnanir í lög eru í `documents.cited_provisions`.

## 1. Markmið

Fyrir hvern dóm á að vera hægt að svara: **í hvaða dóma vitnar hann** og **hvaða dómar vitna í hann**, með setningunni þar sem vitnað er og efnisgreininni sem hún stendur í. Þetta birtist í vefnum, API-inu og MCP-þjóninum og byggist sjálfkrafa fyrir ný skjöl.

**Ekki markmið í þessari útgáfu:** nefndir og stofnanir sem skotmörk (óbyggðanefnd, úrskurðarnefndir, Persónuvernd o.fl.), EFTA-dómstóllinn og Mannréttindadómstóll Evrópu, tilvitnanir í lög (þegar til), merkingarfræðileg flokkun tilvitnana (fylgt/hafnað/aðgreint).

## 2. Ákvarðanir notanda

| Spurning | Val | Afleiðing |
|---|---|---|
| Skotmörk í v1 | **Dómstólarnir sjö undir `domstolar`** | Hæstiréttur, Landsréttur, héraðsdómstólar, Félagsdómur, Landsdómur, Endurupptökudómur, málskotsbeiðnir. Töfluskipanið tekur við nefndum síðar án breytinga. |
| Geymsla | **Leið A: tafla `citations` + afleiddar `cites`-brúnir í `document_links`** | Óleystar tilvitnanir geymast; setning og efnisgrein fylgja; graf-fyrirspurnir nota `document_links` eins og áður. |

## 3. Mæling sem hönnunin byggir á (3.000 skjala úrtak, 29.09.2026)

| | Fjöldi |
|---|---|
| „máli nr. X“ með dómstól í samhengi (220 stafa gluggi) | 1.933 |
| leyst á nákvæmlega eitt skjal | 1.656 (86 %) |
| tvíræð eftir dagsetningar- og dómur/úrskurður-þrengingu | 41 |
| óleysanleg (Hæstiréttur fyrir 1999, óbirtir héraðsdómar, sýslumenn) | 228 |
| án dómstóls í samhengi (mest aðrir úrskurðaraðilar) | 943 |
| aðgreindar leystar tilvitnanir á skjal | 0,42 → ≈37.000 brúnir í 87.000 dómum |

Lærdómar: prósaformið ræður („dómi Hæstaréttar 8. nóvember 2017 í máli nr. 700/2017“); skammstöfunin „Hrd. 700/2017“ kemur nánast ekki fyrir í dómstexta; dagsetning í setningunni leysir endurnotuð Landsréttarnúmer; dagsetning án númers er einkvæm í aðeins 15 % tilvika og er **ekki** notuð ein og sér; lagatilvísanir verða að vera útilokaðar áður en málsnúmer eru lesin; 220 stafa gluggi var of víður (úrskurður óbyggðanefndar rataði á Hæstarétt); afstæðar dagsetningar eru algengar (3.605 í 3.000 skjölum: „sl.“ 2.158, „sama ár“ 767, „s.á.“ 560) og 1.207 þeirra vísa á annað ár en dagsetning skjalsins.

Staðreyndir úr grunninum sem hönnunin verður að virða: Hæstaréttarnúmer eru geymd með forskeyttum núllum („055/2001“) og fjögur með bili („243 /2002“); héraðsdómsskammstafanir eru `Hérd. Rvk.`, `Hérd. Reykn.`, `Hérd. Suðl.`, `Hérd. Norðeyst.`, `Hérd. Vestl.`, `Hérd. Austl.`, `Hérd. Vestfj.`, `Hérd. Norðvest.`; Félagsdómur notar bæði `F-1/2010` og `1/2000`; málskotsbeiðnir eru númeraðar `2023-65` með `verdict_type = 'Ákvörðun'`; `instance_tier` er 1 fyrir `Féld.`, 3 fyrir `Ld.` og `Eud.`; Hæstaréttarsafnið byrjar 1999 en 193 raðir bera málsnúmer frá 1997–1998.

## 4. Gagnalíkan

### 4.1 Tafla `citations` (alembic 0004)

| Dálkur | Tegund | Skýring |
|---|---|---|
| `id` | UUID PK | |
| `from_doc_id` | UUID FK documents ON DELETE CASCADE, NOT NULL | vitnandi skjal |
| `layer` | TEXT NOT NULL | `summary`, `body` eða `lower_body` — textalagið sem tilvitnunin fannst í (sama og `passages.layer`) |
| `char_start`, `char_end` | INT NOT NULL | bil **málsnúmersins** í því lagi; `UNIQUE (from_doc_id, layer, char_start)`. Í upptalningu („nr. 116/1999, 274/1999 og 3/2000“) fær hvert númer eigin röð með eigin `char_start`. |
| `raw_text` | TEXT NOT NULL | textabúturinn frá dómstólsorðinu (eða skammstöfuninni) að enda málsnúmersins, mest 240 stafir; í upptalningu deila raðirnar sama upphafi |
| `target_court` | TEXT NOT NULL | nákvæmlega eins og `documents.court`: `Hrd.`, `Lrd.`, `Hérd. Rvk.` … (taflan í 5.3), `Féld.`, `Ld.`, `Eud.`, `Hrd. málsk.`; `Hérd.` án staðar ef héraðsdómstóll er ónefndur |
| `target_case_number` | TEXT | staðlað (sjá 6.1); null fyrir dómasafnsform |
| `target_date` | DATE | ef nefnd í setningunni samkvæmt 5.5 |
| `target_verdict` | TEXT | `Dómur` / `Úrskurður` / `Ákvörðun` / null |
| `to_doc_id` | UUID FK documents ON DELETE SET NULL | leyst skjal |
| `status` | TEXT NOT NULL | `resolved`, `ambiguous`, `unresolved`, `self`, `pre_coverage` |
| `method` | TEXT | `casenum_date`, `casenum_verdict`, `casenum_unique`, null |
| `confidence` | REAL | 1,0 / 0,9 / 0,8 / null |
| `created_at` | TIMESTAMPTZ default now() | |

Vísar: `ix_cit_from (from_doc_id, layer, char_start)`, `ix_cit_to (to_doc_id) WHERE to_doc_id IS NOT NULL`, `ix_cit_target (target_court, target_case_number)`, `ix_cit_status (status)`.

`target_tier` er **ekki** geymt; það leiðist af `target_court`. **`passage_id` er ekki geymt**: efnisgreinar eru endurbyggðar með eyðingu og innsetningu (`rebuild_passages`), sem myndi núlla dálkinn þögult, og efnisgreinar eru ekki samfelldar (bil milli þeirra). Efnisgreinin finnst við lestur: `SELECT id, ordinal FROM passages WHERE document_id = :id AND layer = :layer AND char_start <= :pos ORDER BY char_start DESC LIMIT 1`.

### 4.2 `documents.citation_hash` (sama flutningur)

Nýr dálkur `citation_hash TEXT NULL` á `documents`: sha256 af `coalesce(summary,'') || '\x1f' || coalesce(body_text,'') || '\x1f' || coalesce(lower_body_text,'')`. Sama mynstur og `passage_hash` (0002). Fjöldatölur (fundnar/leystar) eru reiknaðar úr `citations`, ekki geymdar.

### 4.3 `document_links`

Ný tengslategund `cites`: `from_doc_id` = vitnandi, `to_doc_id` = vitnaður, **einátta**, ein röð á hvert (from, to) par þótt vitnað sé oft; `confidence` = hæsta öryggi meðal tilvitnananna sem mynda brúnina, `method = 'citation'`. Brúnir eru skrifaðar fyrir tilvitnanir úr **`summary` og `body`** (eigin rökstuðningur dómsins), ekki úr `lower_body` (rökstuðningur lægra dómstigs, sem ber sínar eigin tilvitnanir sé hann í safninu). Nýr vísir `ix_link_to_rel (to_doc_id, relation)` fyrir „vitnað í þennan dóm“ og talningar.

**Áfrýjunarpör.** Tilvitnun í dóm sem er í áfrýjunarkeðju sama máls (Hæstiréttur ræðir Landsréttardóminn sem áfrýjað var) er skráð **og** fær `cites`-brún eins og hver önnur; það er einmitt setningin sem notendur vilja sjá. Birtingarlagið merkir hana `also_appeal: true` þegar parið ber líka `appealed_to`/`appealed_from`/`leyfisbeidni_um`/`leiddi_til_doms` í hvorri stefnu sem er, og framendinn forðast tvöfalda birtingu. Málskotsbeiðnir sem vitna í dóminn sem þær snúast um birtast því í `cited_by` með `also_appeal: true`.

**Sama mál.** Dómur sem vitnar í úrskurð með sama málsnúmeri í sama dómstól (67 slík pör í Hérd. Rvk., 9 í Lrd.) leysist á annað skjal og fær venjulega brún; birtingarlagið merkir `same_case: true` þegar `target_case_number` og `target_court` eru þau sömu og vitnandi skjals. `status='self'` gildir aðeins þegar leysta skjalið er vitnandi skjalið sjálft.

`check_link_orientation.py` sleppir `cites` í núverandi þremur athugunum og fær eina nýja, sérstaka fyrir `cites`: engin brún má vísa á skjal með síðari `document_date` en vitnandi skjalið.

## 5. Útdráttur — `engine/processors/citations.py`

Hreint fall, engin gagnagrunnstenging:

```python
@dataclass(frozen=True)
class RawCitation:
    char_start: int; char_end: int      # bil málsnúmersins
    raw_text: str                        # frá dómstólsorði að enda númers (≤ 240)
    target_court: str                    # sjá 5.3
    target_case_number: str | None       # staðlað með norm_case_number
    target_date: date | None
    target_verdict: str | None           # 'Dómur' | 'Úrskurður' | 'Ákvörðun' | None
    form: str                            # 'prose' | 'abbrev' | 'reporter'

def extract_citations(text: str, *, doc_date: date | None) -> list[RawCitation]
```

### 5.1 Lagatilvísanir fyrst
Bil sem passa `\b(?:l(?:ög|aga|ögum|ögunum)|reglugerð\w*|auglýsing\w*|samþykkt\w*)\s+nr\.\s*\d{1,4}/\d{4}` eru merkt og númer innan þeirra hunsuð.

### 5.2 Númer og setningar
Númeramynstur: `<NÚMER> = [A-ZÞÆÖ]{1,2}-\d{1,5}/\d{4} | \d{1,4}/\d{4} | \d{4}-\d{1,3}` (síðasta formið er málskotsbeiðni). Kveikja: `mál(?:i|inu|s|um|unum)?\s+nr\.\s*<NÚMER>(?:\s*,\s*<NÚMER>)*(?:\s+og\s+<NÚMER>)?` → ein `RawCitation` á hvert númer. Að auki, án „mál“, `ákvörðun\w*\s+(?:réttarins\s+)?nr\.\s*\d{4}-\d{1,3}` → málskotsbeiðni.

Setning = textinn frá síðasta setningaskili á undan kveikjunni: `. ` með stórum staf á eftir, `;`, eða línuskil með auðri línu. Ritháttarskammstafanir (`nr.`, `gr.`, `sbr.`, `8. nóvember`) hafa tölustaf eða lágstaf á eftir og rjúfa ekki setningu.

### 5.3 Dómstóll
Leitað **aftur á bak innan setningarinnar, mest 120 stafi**, að síðasta dómstólsorði. Dómstólsorð og vörpun í `target_court`:

| Orð í texta | `target_court` |
|---|---|
| `Hæstaréttar\|Hæstarétti\|Hæstiréttur\|Hæstarétt` | `Hrd.` |
| `Landsréttar\|Landsrétti\|Landsréttur\|Landsrétt` | `Lrd.` |
| `[Hh]éraðsdóm\w*\s+Reykjavíkur` | `Hérd. Rvk.` |
| `… Reykjaness` | `Hérd. Reykn.` |
| `… Suðurlands` | `Hérd. Suðl.` |
| `… Norðurlands\s+eystra` | `Hérd. Norðeyst.` |
| `… Norðurlands\s+vestra` | `Hérd. Norðvest.` |
| `… Vesturlands` | `Hérd. Vestl.` |
| `… Austurlands` | `Hérd. Austl.` |
| `… Vestfjarða` | `Hérd. Vestfj.` |
| `[Hh]éraðsdóm\w*` án staðar | `Hérd.` |
| `Félagsdóm\w*` | `Féld.` |
| `Landsdóm\w*` | `Ld.` |
| `Endurupptökudóm\w*` | `Eud.` |

**Framburður innan setningar:** orðin `réttarins`, `dómstólsins`, `sama dómstóls`, `sama réttar` og fornafnið í „dóma hans“ eftir dómstólsorð erfa síðasta dómstól setningarinnar, svo „dóma réttarins 21. október 1999 í máli nr. 116/1999, 9. desember sama ár í máli nr. 274/1999“ eignar báðum númerum þann dómstól sem nefndur var á undan.

**Annar úrskurðaraðili nær** (`nefnd\w*`, `sýslumann\w*`, `ráðuneyt\w*`, `stofn\w*`, `Persónuvernd`, `stjórn\w*`, `dómstól\w*` sem ekki er í töflunni) → ekki dómstilvitnun; sleppt.

**Forskeyti ræður dómstól þegar það stangast á við orðið:** `F-` er alltaf Félagsdómur; önnur bókstafsforskeyti alltaf héraðsdómstóll (ef orðið segir `Hrd.` eða `Lrd.` en númerið er `E-…` er tilvitnunin merkt `Hérd.` án staðar). Númer af forminu `\d{4}-\d{1,3}` er alltaf `Hrd. málsk.`.

Aðeins leitað aftur á bak; framvísun („mál nr. 3/2026 sem rekið er fyrir Hæstarétti“) er ≈1 % tilvika og framhitt vísar oftar á næstu tilvitnun.

### 5.4 Skammstafanir og dómasafn
`Hrd\.\s*(\d{1,4}/\d{4})` → `Hrd.`; `Lrd\.\s*(\d{1,4}/\d{4})` → `Lrd.`; `Hérd\.\s*(Rvk|Reykn|Suðl|Norðeyst|Norðvest|Vestl|Austl|Vestfj)\.\s*([A-ZÞÆÖ]{1,2}-\d+/\d{4})` → samsvarandi. Dómasafnsform `Hrd\.?\s*(\d{4})[,:/]\s*(?:bls\.\s*)?(\d{1,5})` og `\bH\s?(\d{4}):(\d{1,5})` → `form='reporter'`, `target_case_number=None`, `target_court='Hrd.'`.

### 5.5 Dagsetning og orð
Dagsetning er aðeins tekin (a) **milli dómstólsorðsins og númersins**, eða (b) innan 30 stafa **á eftir** númerinu ef hún er kynnt með `frá`, `dags.` eða `uppkveðn\w*`. Mynstur: `(\d{1,2})\.\s+(janúar|…|desember)\s+(\d{4}|sama\s+ár|s\.á\.|þess\s+árs|sl\.|síðastliðin\w*)`.
- Fast ár → sú dagsetning.
- `sama ár`, `s.á.`, `þess árs` → **næsta fjögurra stafa ártal á undan í sömu setningu**; finnist ekkert → `target_date = null`.
- `sl.`, `síðastliðinn` → ár `doc_date` ef mánuðurinn er ≤ mánuði `doc_date`, annars árið á undan; `doc_date` null → `target_date = null`.
Orð: síðasta `dóm\w*`, `úrskurð\w*` eða `ákvörð\w*` innan 40 stafa á undan dómstólsorðinu → `Dómur`/`Úrskurður`/`Ákvörðun`.

### 5.6 Annað
Sjálfstilvitnun er skilað (staða ákveðin í leysara). Tómur texti → tómur listi. Aldrei undantekning á texta.

## 6. Leysari — `engine/processors/citation_resolver.py`

### 6.1 Stöðlun málsnúmers
`norm_case_number(s)`: fjarlægja öll bil; í tölulegum hluta á undan `/` eru forskeytt núll felld (`055/2001` → `55/2001`, `E-0012/2020` → `E-12/2020`); bókstafir í hástafi. Notað bæði á `documents.case_number` við byggingu vísisins og á lesin númer.

### 6.2 Vísir og frambjóðendur
`CitationIndex` byggður einu sinni úr skjölum heimildanna sjö: lykill `(court, norm_case_number)` → frambjóðendur `(id, document_date, verdict_type, court)`. Frambjóðendur tilvitnunar: **nákvæm** samsvörun á `court = target_court`, nema `target_court == 'Hérd.'` (staður ónefndur) sem tekur öll héruð með `court LIKE 'Hérd. %'`.

### 6.3 Þrenging og staða
1. Frambjóðendur með `document_date > doc_date` vitnandi skjals eru felldir út.
2. Sé `target_date` sett → aðeins þeir með þá `document_date`. **Tæmist hópurinn → `unresolved`** (aldrei fallið aftur á ódagsetta frambjóðendur). Einn eftir → `resolved`, `casenum_date`, 1,0.
3. Annars sé `target_verdict` sett → aðeins þeir með sama `verdict_type`. Einn eftir → `casenum_verdict`, 0,9.
4. Annars einn frambjóðandi → `casenum_unique`, 0,8.
5. Fleiri en einn eftir → `ambiguous`. Enginn → `unresolved`, nema `target_court == 'Hrd.'` og (ár númersins ≤ 1998 eða `form == 'reporter'`) → `pre_coverage`. Leysta skjalið sama og vitnandi → `self`.

## 7. Bygging — `scripts/build_citations.py`

Valkostir: `--all` (sjálfgefið aðeins úrelt skjöl: `citation_hash` vantar eða stemmir ekki), `--force` (líka óúrelt), `--source SHORT_NAME`, `--doc UUID`, `--since YYYY-MM-DD`, `--relink-unresolved` (endurleysa `unresolved`/`ambiguous` raðir án útdráttar, t.d. eftir innflutning), `--dry-run`, `--workers N` (sama `multiprocessing.Pool`-mynstur og `backfill_passages.py`; útdráttur í vinnsluferlum, skrif í aðalferli).

Fyrir hvert skjal í einni færslu: eyða `citations` þess; eyða `document_links WHERE from_doc_id = :id AND relation = 'cites'`; setja inn nýjar raðir; setja inn `cites`-brúnir (`ON CONFLICT ON CONSTRAINT uq_link_from_to_rel DO UPDATE SET confidence = GREATEST(...)`); uppfæra `documents.citation_hash`. Skriftan á aðeins sínar raðir. Lokasamantekt: fjöldi eftir `status`, `layer`, dómstól vitnandi × dómstól vitnaðs, brúnir, keyrslutími, tíu algengustu `raw_text`-form meðal `unresolved`.

Keyrsluröð eftir innflutning (skjalað í `04-innflutningur.md`): `backfill_passages.py` → `build_citations.py --all` → `build_citations.py --relink-unresolved`.

## 8. Birting

### 8.1 API
- `get_document` skilar að auki:
  - `citations_out`: leystar tilvitnanir úr `summary`/`body`, **ein á hvert `to_doc_id`, sú með lægsta `char_start` í `body` (annars `summary`)**; hver: `document_id`, `urlausn`, `layer`, `passage_id`, `anchor`, `raw_text`, `confidence`, `also_appeal`, `same_case`; röðuð eftir `document_date` vitnaðs; mest 50 auk `citations_out_total`.
  - `cited_by`: skjöl sem vitna í þetta (brúnir `cites` með `to_doc_id = :id`), hvert með fyrstu tilvitnun sinni (`layer`, `passage_id`, `anchor`, `raw_text`), `document_date`, `also_appeal`, `same_case`; nýjast fyrst; mest 50 auk `cited_by_total`.
  - `citations_unresolved_total`: fjöldi `unresolved`+`ambiguous`+`pre_coverage` úr `summary`/`body`.
- `GET /api/document/{id}/citations?direction=out|in&page=&page_size=` (≤100), sama raðasnið; 400 við rangt `direction`.
- Leitarniðurstöður fá `cited_by_count` með `LEFT JOIN LATERAL (SELECT count(*) FROM document_links l WHERE l.to_doc_id = d.id AND l.relation = 'cites')` á síðuna (≤100 raðir) yfir `ix_link_to_rel`; kostnaður mældur og skráður, viðmið < 5 ms á síðu.

### 8.2 Framendi
- `DocPanel.tsx`: kafli **„Tilvitnanir“**: „Vitnar í (n)“ og „Vitnað í þennan dóm (n)“; lína = `urlausn` sem hlekkur á `/domur/{id}`, undir henni `raw_text` í smáu letri; færslur með `also_appeal` fá merkið „(í áfrýjunarkeðju)“ og eru ekki endurteknar í „Tengd mál“; `same_case` fær „(sama mál)“; „Sýna fleiri“ sækir `/citations`; neðst „N tilvitnanir fundust ekki í safninu“.
- Merkingar „Tengd mál“: `appealed_to` → „Áfrýjað til“, `appealed_from` → „Áfrýjað frá“, `leyfisbeidni_um` → „Málskotsbeiðni um“, `leiddi_til_doms` → „Leiddi til dóms“; óþekkt heiti sýnt óbreytt.
- Leitarlisti sýnir „vitnað í N sinnum“ þegar `cited_by_count > 0`.
- `types.ts`: `CitationRef`, `relation` sem sambandsgerð þekktra heita.

### 8.3 MCP
- `get_document` fær `citations_out_total`, `cited_by_total` og fimm efstu úr hvorum lista.
- Nýtt verkfæri `citations(doc_id, direction="out"|"in", page=1, page_size≤25)`; verkfærin verða níu; `TOOL_NAMES`, prófin og `docs/wiki/10-mcp.md` uppfærð.

## 9. Gæðamörk

- **Nákvæmniúttekt fyrir sameiningu, 200 leystar tilvitnanir lagskiptar eftir `method` × `target_court`.** Fyrir hverja röð keyrir **sjálfstætt skrifaður sannprófunarregex** (annar en útdrátturinn, aðeins yfir `raw_text`) og staðfestir: (a) númerið í `raw_text` = `norm(target.case_number)`; (b) dómstólsorðið í `raw_text` varpast á `target.court`; (c) full dagsetning í `raw_text`, sé hún til, = `target.document_date`; (d) dómsorð, sé það til, = `target.verdict_type`; (e) `target.document_date <= citing.document_date`; (f) `raw_text` endar á `substring(text, char_start, char_end)`. Síðan les endurskoðandi ±250 stafi í kringum hverja röð fyrir tvennt sem vélin sér ekki: er þetta yfirhöfuð tilvísun í úrlausn, og á dómstólsorðið við þetta númer. Viðmið: **≥ 98 % rétt** (≤ 4 villur af 200); taflan eftir lagi fer til notanda með öllum villudæmum; sameining bíður staðfestingar hans náist viðmiðið ekki.
- **Þekja:** endurmæla með reglum kafla 5 (setningargluggi + framburður) á 2.500 skjala úrtaki áður en full keyrsla er samþykkt; viðmið `resolved` ≥ 80 % af (`resolved`+`ambiguous`+`unresolved`).
- **Engin brún á síðari dóm:** nýja athugunin í `check_link_orientation.py` skilar núlli eftir fulla keyrslu.

## 10. Prófun

- `tests/test_citations_extract.py`: fastar setningar — prósaform með og án dagsetningar; upptalning með aðgreindum `char_start`; „sama ár“ með ártal fyrr í setningu og án; „sl.“ í mánuði á undan og eftir mánuði skjals; „s.á.“; lagatilvísun sem má ekki verða tilvitnun; óbyggðanefnd/úrskurðarnefnd sem má ekki eigna Hæstarétti; héraðsdómur með og án staðar og öll átta heitin; `réttarins`-framburður; `F-`-númer með orðinu Félagsdómur og með orðinu héraðsdómur; `E-`-númer með orðinu Hæstaréttar; málskotsbeiðni `2023-65`; `Hrd. 1983/1538`; `H 1999:123`; dagsetning eftir númeri með og án „frá“; tvær tilvitnanir í sömu setningu með sitt hvorn dómstól; setningaskil við „. Í“ en ekki við „nr. 700“.
- `tests/test_citation_resolver.py`: `norm_case_number` (núll, bil, hástafir); vísir með endurnotuðu Landsréttarnúmeri (úrskurður + dómur) leyst með orði; tvö héruð með sama númeri → `ambiguous` án staðar, `resolved` með stað; dagsetning sem tæmir hópinn → `unresolved`; síðari dómur útilokaður; pre-1999 og reporter → `pre_coverage`; `Hrd.` passar ekki `Hrd. málsk.`.
- `tests/test_citations_db.py` (skipif): `--doc` á þrjú þekkt skjöl úr mælingunni; `status`-dreifing; endurkeyrsla idempotent; efnisgrein finnst við lestur fyrir hverja röð.
- API-, MCP- og framendapróf fyrir nýju reitina, slóðina og verkfærið; `test_mcp_server.py` uppfært í níu verkfæri.
- Fulltrúaprófun: nákvæmniúttektin og þekjumælingin í kafla 9.

## 11. Skjölun

`docs/wiki/02-gagnagrunnur.md` (nýja taflan, `citation_hash`, **öll** tengslaheiti `document_links` — síðan er úrelt um `appealed_from`, `leyfisbeidni_um`, `leiddi_til_doms`), `04-innflutningur.md` (keyrsluröð), `05-leit.md`/`06-api.md`/`07-framendi.md`, `10-mcp.md` (níunda verkfærið), `09-gildrur.md` (lög á undan málsnúmerum; dómstóll innan setningar; afstæðar dagsetningar; forskeytt núll í Hæstaréttarnúmerum; Landsréttarnúmer endurnotuð; héraðsnúmer ekki einkvæm milli héraða; `F-` er Félagsdómur), `README.md`.

## 12. Samþykktarhlið

Tvær aðgerðir snerta lifandi grunninn og bíða sérstaks „já“ frá notanda, eins og fyrri DB-breytingar: (1) `alembic upgrade head` með flutningi 0004 (ný tafla, nýr dálkur, nýr vísir; engin eldri gögn breytast), (2) fyrsta fulla keyrsla `build_citations.py --all` sem skrifar ≈37.000 `cites`-brúnir. Allt annað (kóði, próf, þurrkeyrslur, mælingar á úrtaki) gengur án hlés.

## 13. Áhætta og mótvægi

- **Rangar tengingar** eru verri en engar: nákvæmlega einn frambjóðandi eða `ambiguous`; dagsetningarþrenging fellur aldrei aftur; síðari dómar útilokaðir; nákvæmniúttekt með sjálfstæðum sannprófara fyrir sameiningu.
- **Mynstur missa af formum:** `unresolved`/`ambiguous` raðir geymast með `raw_text`; samantekt sýnir stærstu flokka; `--relink-unresolved` án útdráttar.
- **Efnisgreinar endurbyggðar:** ekkert geymt `passage_id`; efnisgrein fundin úr `char_start` við lestur.
- **Keyrslutími:** 87.000 skjöl × regex; áætlað 10–20 mín með vinnsluferlum.

## 14. Breytingar eftir gagnrýna yfirferð 2026-09-29

`char_start` = bil númersins (upptalningar); afstæðar dagsetningar leystar rétt („sama ár“ → fyrra ártal setningar, „sl.“ → ár/ár−1); `target_tier` fellt út og `instance_tier`-fullyrðing leiðrétt; `F-` → Félagsdómur; átta réttar héraðsdómsskammstafanir með vörpunartöflu; málskotsbeiðnir gerðar nothæfar (`2023-65`, `Ákvörðun`); nákvæm `court`-samsvörun; framburður dómstóls innan setningar með endurmælingu; dagsetning aðeins milli dómstóls og númers eða kynnt með „frá“; þrengingarskref 4 fellt út og tæmdur hópur → `unresolved`; `cites`-brún haldið fyrir áfrýjunarpör með `also_appeal`; `passage_id` ekki geymt; `citation_hash` dálkur í stað sértöflu; „fyrsta tilvitnun“ skilgreind og `ix_link_to_rel` bætt við; sjálfstæður sannprófari og lagskipting í úttekt; `summary`-lag tekið með; `same_case`; pre-1999 regla orðuð sem „enginn frambjóðandi og ár ≤ 1998“.

## 15. Viðbót eftir innleiðingu (2026-09-29)

Fimm atriði komu í ljós við innleiðingu og aðlöguðu hönnunina lítillega frá kafla 1–14 hér að ofan — kóðinn er heimildin þar sem þessi skjöl greinir á:

- **Félagsdómur — `F-`-fallleið í vísinum.** §6.2 nefndi ekki að bara-tölu-lykillinn (`13/2001`) og `F-`-forskeytti lykillinn (`F-13/2001`) þyrftu báðir að reynast fyrir `target_court == 'Féld.'`. Án hennar missti bara-lykillinn af öllum Félagsdómsmálum sem birtust eftir 2010-skiptin, af því dómstexti vitnar áfram í bara-tölu-formið óháð ártali skjalsins sjálfs. Sjá `CitationIndex.candidates()` í `citation_resolver.py` og [09-gildrur](../../wiki/09-gildrur.md). Mæld áhrif: +0,87 hlutfallsstig þekju (deterministic A/B).
- **Afstæð ár verða að vera stök tákn.** `_YEAR_RX` í §5.5 var upphaflega `\b(1[89]\d\d|20\d\d)\b`, sem passaði líka við ártalið falið inni í `nr. 91/1991` eða `2020-2021`. Reglan er nú: hvorki tölustafur né `/` né `-` má standa við hlið ártalsins á hvorn veg (sjá `citations.py`).
- **HTTP 422, ekki 400, fyrir ógild `direction`/`page_size` á `/api/document/{id}/citations`.** §8.1 orðaði það sem „400 við rangt `direction`". Útfærslan notar `Query(pattern=...)`/`Query(le=100)`, svo FastAPI hafnar sjálft áður en meðhöndlarinn keyrir — það er 422 (staðfest fyrirspurn ógild), ekki 400 (sem er frátekið fyrir `SearchError` úr rökfræðinni sjálfri, t.d. ógilt skjala-id).
- **Dómsorð-athugunin í nákvæmniúttektinni er skráð en ekki metin til gæðaviðmiðsins.** §9-athugun (d) („dómsorð á undan dómstólsorði = `target.verdict_type`") mælir merkingu heimildarinnar á `verdict_type`, ekki hvort tenglinum sé rétt beint — Hæstiréttur merkir kærumál oft `Úrskurður` í `verdict_type` en orðar tilvitnunina „dómi …". Úttektin heldur `verdict`-dálkinn (talinn sér sem `vmis`-frávik) en fellir hann úr `mech_ok`/`mech_fail`-heildarniðurstöðunni. Sjá `scripts/audit_citations.py`.
- **Sannprófarinn í úttektinni beitir virka (§5.3) dómstólnum, ekki bara orðinu.** Forskeytisregla §5.3 (`F-` alltaf Félagsdómur, annað bókstafsforskeyti alltaf héraðsdómstóll, `YYYY-N` alltaf málskotsbeiðni) á líka við í sjálfstæða sannprófaranum: dómstóllinn sem númerið sjálft gefur til kynna (sé eitthvað) vinnur gegn dómstólsorðinu, annars er orðið notað. Án þessa féllu réttar tilvitnanir í úttektinni á fölskum forsendum þegar númer og orð stönguðust vísvitandi á. Sjá `_court_from_number`/`_last_court` í `scripts/audit_citations.py`.
