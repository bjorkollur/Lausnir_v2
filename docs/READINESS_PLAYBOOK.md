# Lausnir — Readiness Playbook

Lifandi skjal: rekstrar- og neyðarhandbók fyrir Lausnir v2 (íslenskt lagagagnasafn). Uppfært eftir hverja stóra breytingu á innviðum, afritun eða AI-verkflæði.

**Umfang:** Lausnir er einkaverkefni, ekki ætlað til sölu eða fjölnotendareksturs. Handbókin miðast við það — engin krafa um SLA, viðskiptavinastuðning eða formlegt RTO/RPO.

Síðast yfirfarið: 2026-07-23.

---

## 1. Neyðar- og endurheimtaráætlun

### Núverandi staða (staðfest með skoðun á kóða og vél)

**Samþykkt endurheimtarstefna:** Engin gagnagrunnsafritun er tekin. Ef `lausnir_v2` tapast, er gagnagrunnurinn endurbyggður með því að keyra import-skriptin í `scripts/import_*.py` að nýju fyrir hverja heimild (sjá `scripts/update_all.py` án `--new-only` fyrir fulla endurkeyrslu). Þetta er meðvituð ákvörðun, ekki gat — verkefnið er einkaverkefni og öll frumgögn liggja opinberlega hjá upprunalegu heimildunum (island.is, stjornarradid.is o.s.frv.).

| Atriði | Staða |
|---|---|
| Gagnagrunnsafritun | Ekki notuð — endurheimt fer fram með endurinnflutningi frá heimildum (sjá að ofan) |
| Afrit af `Lausnir_Data/` (PDF, markdown) | Sama stefna — `.md`/`urlausn` eru hvort eð er af RENDER-lagi og endurgerð sjálfkrafa úr NORM (`Renderer.rebuild_all`); PDF-bætin má sækja aftur frá heimild |
| Rollback á DB-skema | ✅ Alembic-grunnlína til (`0001_baseline` → `0002_passages` → `0003_drop_document_chunks`), hver migration með `downgrade()`. Sjá `alembic/versions/`. |
| Rollback á kóða/uppfærslum | Handvirkt í gegnum `git revert`/`git reset` — dugar fyrir einkaverkefni |
| `scripts/supervised.sh` | ✅ Ekki lengur nefnt — CLAUDE.md var stytt og vísar á `docs/wiki/` (staðfest 2026-10-01) |
| Import checkpoints | ✅ `checkpoints/*.json` geymir framvindu fyrir hverja heimild — nýtist til að halda áfram eftir crash |

**Þekkt afgangsáhætta** (fylgir af "endurflytja frá heimild"-stefnunni, ekki tilkynnt sem verkefni — bara skráð til vitundar): sumar heimildir gætu breytt eða fjarlægt eldri gögn af sínum vefjum með tímanum (t.d. `stjornarradid.is` niðurstöður sem hverfa), svo full endurheimt endurspeglar það sem heimildin sýnir *þá stundina*, ekki endilega nákvæmlega það sem var í `lausnir_v2` fyrir tjón.

### TODO — Neyðar- og endurheimtaráætlun

- [x] Búa til fyrstu Alembic-grunnlínuna — lokið 2026-09-27 (`0001_baseline`, síðan `0002_passages`, `0003_drop_document_chunks`, hver með raunverulegri `downgrade()`)
- [x] Annaðhvort útbúa `scripts/supervised.sh` eða fjarlægja tilvísunina úr CLAUDE.md — tilvísunin hvarf þegar CLAUDE.md var stytt (staðfest 2026-10-01)

---

## 2. Skjölun á AI-verkflæði (Claude Code skills)

Engin sjálfstæð AI-agentakerfi eru starfandi í Lausnir — engir bakgrunnsbottar, engir sjálfvirkir vinnsluferlar með eigin gagnaaðgang. Allt AI-vinnuflæði fer í gegnum **Claude Code**, stýrt handvirkt af notanda (Geiri) í einu samtali í einu. Eftirfarandi eru skráðu "skills" (leiðbeiningasett) sem hlaðast inn eftir samhengi:

| Skill | Tilgangur | Gagnaaðgangur | Mannlegt samþykki krafist |
|---|---|---|---|
| `lausnir-new-source` | 7-fasa verkflæði til að bæta við nýrri heimild (dómstól/nefnd): uppgötvun → SourceConfig → collector → extractor → import script | Read/Write á `engine/`, `scripts/`; Bash til að keyra próf | Já — við hvern fasa samkvæmt eigin lýsingu skilsins |
| `graphify` | Kortleggur kóðabasann í þekkingargraf (AST + semantic via subagents) | Read á allan kóðabasann; skrifar í `graphify-out/` | Nei sjálfkrafa — keyrt á beiðni |
| `ponytail` | Þvingar einfaldasta virka lausn (YAGNI-agi) fyrir kóðabreytingar | Engin bein gagnaaðgangsáhrif — hegðunarregla fyrir AI-svör | Nei |
| `caveman`, `design-taste-frontend`, `gpt-taste`, `high-end-visual-design`, `minimalist-ui` | Stíl- og hönnunarleiðbeiningar (samskiptaform / framenda-fagurfræði) | Engin gagnaaðgangsáhrif | Nei |
| `commit-commands` (GitHub-tengt) | Commit/PR-gerð | Write í git, push til remote | Já — sbr. almennar reglur: aldrei push án staðfestingar frá notanda |

**Réttindalíkan:** Það er ekkert formlegt aðgreint les/skrif-réttindakerfi milli skilja — Claude Code keyrir með fullum aðgangi að skráakerfinu hverju sinni sem notandi samþykkir tólakall, og getur keyrt `psql` gegn `postgresql://geiri@localhost/lausnir_v2` með fullum skrifaðgangi eins og hver önnur skel-skipun. Frá 2026-09-29 er **enginn `mcp-postgres`-þjónn stilltur á þessari vél**; eina MCP-gagnagrunnsleiðin er `engine/mcp` sem tengist með aðgreinda lesaðgangshlutverkinu `lausnir_ro` (`DATABASE_URL_READONLY`, `default_transaction_read_only = on`) — sjá `docs/wiki/10-mcp.md`. Sjálfgefið keyrir AI í "auto mode" — heldur áfram án stöðvunar á afturkræfum aðgerðum, en stöðvar og biður um staðfestingu fyrir:
- Eyðingu skráa/greina utan vinnusvæðis
- `git push`, PR-gerð, force-push
- Hvers kyns óafturkræfa aðgerð utan git-repósins sjálfs

**Loggun:** Engin miðlæg loggun á AI-aðgerðir er til í dag. Sagan er eingöngu varðveitt í:
- Samtalssögu innan Claude Code (staðbundin, ekki miðlæg)
- Git-commitasögu (fyrir kóðabreytingar)
- `$DATA_DIR/logs/update/*.log` (fyrir import-keyrslur; hver keyrsla skrifar yfir log fyrri keyrslu fyrir sömu heimild)

### TODO — AI-verkflæði

- [x] Ákveða hvort `mcp-postgres` MCP-þjónninn ætti að hafa read-only aðgang að framleiðslugagnagrunninum í stað fulls read/write — leyst 2026-09-29: sjá `docs/wiki/10-mcp.md`
- [x] Íhuga hvort import-loggar ættu að varðveitast lengur en `/tmp` leyfir — leyst 2026-10-01: `update_all.py` skrifar í `$DATA_DIR/logs/update/`

---

## 3. Tæknilegur grunnur og öryggi

### Gagnagrunnsuppbygging (DB schema)

Full skjölun í [`CLAUDE.md`](../CLAUDE.md) — `sources` og `documents` töflur, þriggja-laga arkitektúr (RAW → NORM → RENDER). Ekki endurtekið hér til að forðast tvöfalda heimild sem úreldist — sjá CLAUDE.md fyrir nákvæmar dálkaskilgreiningar.

### Aðgangsstýring á gagnagrunni (RLS)

❌ **Engin Row-Level Security er til staðar.** Staðfest með leit að `ENABLE ROW LEVEL SECURITY` og `CREATE POLICY` í kóða og migrations — ekkert fannst. Gagnagrunnurinn er staðbundinn (`geiri@localhost`), einn notandi, engin fjölnotendaaðgangsstýring er til staðar eða þörf í núverandi mynd (local-only kerfi án auth-lags).

**Athugasemd:** Ef Lausnir fer einhvern tímann í fjölnotendarekstur eða er hýst utan `localhost`, er RLS-leysi hér áhættuatriði sem þarf að taka upp aftur.

### API-lyklar og umhverfisbreytur

✅ **Staðfest rétt uppsett.** `.env.example` sýnir:
```
DATABASE_URL=postgresql+asyncpg://geiri@localhost/lausnir_v2
DATA_DIR=/Volumes/RuleOfLaw/Lausnir_Data
ANTHROPIC_API_KEY=sk-ant-...
```
Engir hart-kóðaðir lyklar fundust í grófri leit yfir kóðabasann. `engine/config/sources.py` les `DATA_DIR` í gegnum `os.environ.get(...)` með sjálfgefnu gildi.

**Athugasemd:** Claude Code sjálft geymir líka `GITHUB_PERSONAL_ACCESS_TOKEN` í `~/.claude/settings.json` (utan þessa repós) — ekki hluti af Lausnir-kóðanum sjálfum, en vert að muna að sá tókin er í notendastillingum, ekki í verkefninu.

### CORS

`engine/api/app.py` (línur 47–52):
```python
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+)(:\d+)?",
    allow_methods=["GET"],
    allow_headers=["*"],
)
```
Leyfir eingöngu staðarnetsuppruna (`localhost`, `127.0.0.1`, `192.168.x.x`), aðeins `GET`-aðferð. Ekkert auðkenningarlag (auth) er á API-inu — meðvituð ákvörðun skv. hönnunarskjali (`docs/superpowers/specs/...leitar-frontend-design.md`): "local-only, no auth" fyrir v1.

### TODO — Tæknilegur grunnur og öryggi

- [x] Staðfesta að `.env` sé í `.gitignore` — staðfest 2026-07-23 (`.gitignore:5`, `frontend/.gitignore:13` fyrir `.env.local`)
- [ ] Ef API fer nokkurn tímann út fyrir staðarnet: bæta við auðkenningarlagi áður en `allow_origin_regex` er rýmkað
- [ ] Yfirfara MCP-tenginguna (nú `engine/mcp` á `lausnir_ro`) reglulega — endurtekið eftirlit, ekki eitt verk sem lokast: staðfesta að enginn MCP-þjónn með skrifaðgang hafi bæst við og að `lausnir_ro` haldi réttindum sínum (`tests/test_mcp_readonly_role_db.py`). Síðast yfirfarið 2026-10-01: `claude mcp list` sýnir engan staðbundinn MCP-þjón (hvorki `mcp-postgres` né `lausnir`), og `tests/test_mcp_readonly_role_db.py` stenst (4/4); sjá `docs/wiki/10-mcp.md`

---

## Ekki innifalið í þessari handbók

Að beiðni notanda er **stofnanaþekking um verðlagningu, viðskiptavinasögu eða viðskiptaákvarðanir ("The Lily Rule")** ekki hluti af þessu skjali — Lausnir er rannsóknartæki fyrir einn notanda, ekki söluvara með viðskiptavinum.
