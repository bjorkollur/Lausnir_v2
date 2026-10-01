# PostgreSQL á RuleOfLaw-disknum

Frá og með 2026-09-13 er PostgreSQL-gagnagrunnurinn (`lausnir_v2`, ~71 GB) vistaður
beint á ytri disknum, ekki á innri diski tölvunnar:

```
/Volumes/RuleOfLaw/postgresql_data
```

Þetta þýðir að diskurinn ber ekki lengur bara kóðann og PDF/markdown-skrárnar
(`Lausnir_Data`) heldur líka gagnagrunninn sjálfan. Gamla gagnamappan á innri
disknum er varðveitt óbreytt sem öryggisafrit á meðan nýja uppsetningin er
sannreynd:

```
/opt/homebrew/var/postgresql@17.bak-2026-09-13
```

**Eyddu henni ekki fyrr en þú hefur unnið með nýju uppsetninguna í einhvern tíma
og ert visst/viss um að allt sé í lagi.**

## Portið er 5433, ekki 5432

Frá 2026-09-23 hlustar þessi uppsetning á **port 5433**. Ástæðan er að diskurinn
ferðast milli véla og hýsilvélin gæti þegar keyrt sinn eigin Postgres á
sjálfgefna portinu 5432 — þá myndi okkar tilvik einfaldlega ekki ræsast
(`Address already in use`), og `psql` gæti tengst röngum gagnagrunni.

Stillingin er í `postgresql_data/postgresql.conf` (ekki í plist-skránni), svo
hún **fylgir gagnamöppunni** og er rétt á hvaða vél sem er án frekari uppsetningar.

Þetta þýðir:

```bash
# psql þarf -p 5433 — nema PGPORT=5433 sé sett í ~/.zshenv (sjá skref 10 neðar)
psql -U geiri -p 5433 -d lausnir_v2

# .env inniheldur portið
DATABASE_URL=postgresql+asyncpg://geiri@localhost:5433/lausnir_v2
```

Ásamt sérmerkimiðanum (sjá næsta kafla) þýðir þetta að okkar tilvik og Postgres
hýsilvélarinnar geta keyrt samtímis án nokkurra árekstra.

## Þjónustan heitir `is.lausnir.postgres`, ekki `homebrew.mxcl.postgresql@17`

Frá 2026-09-23 keyrir þessi uppsetning undir **eigin launchd-merkimiða**,
`is.lausnir.postgres`, með eigin plist-skrá (`Lausnir/deploy/is.lausnir.postgres.plist`).

Ástæðan: hýsilvél getur keyrt sinn eigin Postgres undir merkimiðanum
`homebrew.mxcl.postgresql@17`, og `brew services` endurskrifar þá plist-skrá úr
sniðmáti Homebrew við hverja einustu skipun — sem myndi beina okkar þjónustu
aftur á innri diskinn. Með sérmerkimiða getur `brew services` **aldrei** snert
okkar skrá, og bæði tilvikin lifa hlið við hlið.

Notaðu samt ekki `brew services` fyrir okkar tilvik — það þekkir hana ekki.
Notaðu `launchctl` (sjá næst) eða `pg_ctl` handvirkt.

Fyrst eftir flutninginn hékk `launchd`-ræstur Postgres þögult þegar hann
reyndi að nálgast ytri diskinn (líklega Full Disk Access/TCC-höft á
bakgrunnsferlum) — handvirk ræsing úr gagnvirkri skel virkaði alltaf fínt.
**Þetta var leyst** með því að veita `postgres`-forritinu Full Disk Access
heimild (Kerfisstillingar → Persónuvernd og öryggi → Full Disk Access). Eftir
það virkar `launchd` fullkomlega eðlilega: sjálfvirk ræsing við innskráningu
(`RunAtLoad`) og sjálfvirk endurræsing ef ferlið deyr (`KeepAlive`), nákvæmlega
eins og áður á innri disknum. **Ef þessu er sett upp á nýrri vél og
`launchctl bootstrap` hangir þögult á sama hátt, er þetta fyrsta atriðið sem
þarf að athuga.**

### Ræsa (staðlað — hleður og virkjar `launchd`-starfið strax, án innskráningar)

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/is.lausnir.postgres.plist
```

### Stöðva

```bash
launchctl bootout gui/$(id -u)/is.lausnir.postgres
```

### Athuga stöðu

```bash
launchctl list | grep is.lausnir.postgres
```
(Annar dálkur er síðasti útgöngukóði: `0` = í lagi. `78` þýðir að `launchd` gat
ekki opnað logskrána — hún verður að vera á innri disknum, sjá skýringu í
plist-skránni sjálfri.)

### Handvirk ræsing (til bráðabirgða, t.d. við villuleit)

```bash
/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D /Volumes/RuleOfLaw/postgresql_data \
  -l /opt/homebrew/var/log/lausnir-postgres.log start
# stöðva:
/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D /Volumes/RuleOfLaw/postgresql_data stop -m smart
```

### Ef ræsing mistekst með "database system is starting up" / lás-villu

Ef Postgres var ekki stöðvað hreint (t.d. tölvan slökkt án þess að stöðva
þjónustuna fyrst), getur staðin `postmaster.pid`-skrá í gagnamöppunni komið í
veg fyrir ræsingu. Postgres keyrir sjálft WAL-endurheimt (crash recovery) við
næstu ræsingu — það er eðlilegt og öruggt, engin gögn tapast. Ef ræsing samt
neitar að byrja:

```bash
rm -f /Volumes/RuleOfLaw/postgresql_data/postmaster.pid
# reyndu svo pg_ctl start aftur
```

**Ekki gera þetta ef Postgres er í raun og veru þegar í gangi** (athugaðu með
`ps aux | grep postgres` fyrst) — skránni er eingöngu óhætt að eyða þegar
ferlið er örugglega ekki keyrandi lengur.

## Að setja upp á nýrri Mac-tölvu

Forsenda: diskurinn heitir áfram „RuleOfLaw" (macOS tengir hann þá sjálfkrafa
undir `/Volumes/RuleOfLaw`).

### Skref 0: athugaðu hýsilvélina fyrst

Ef vélin keyrir þegar Postgres fyrir annað verkefni getur það rekist á okkar.
Keyrðu þessa skriftu áður en lengra er haldið — hún breytir engu, bara skoðar:

```bash
bash /Volumes/RuleOfLaw/Lausnir/deploy/check-host-postgres.sh
```

Hún athugar hvort `postgresql@17` sé uppsett, hvort aðkominn Postgres haldi
porti 5433 eða launchd-merkimiðanum okkar, og — það sem mestu skiptir — hvort
**aðkominn** `postgresql@17` sé keyrður undir `brew services`. Hún greinir okkar
eigið tilvik frá aðkomnu, svo hana má líka keyra eftir uppsetningu sem
heilsufarsathugun. Skilar `0` ef allt er í lagi.

Postgres hýsilvélarinnar á porti 5432 er í lagi — við notum 5433.

1. **Homebrew** — ef ekki þegar uppsett: <https://brew.sh>

2. **Sama PostgreSQL-útgáfa (17.x)** — verður að vera sama major-útgáfa og
   gagnamappan var búin til með:
   ```bash
   brew install postgresql@17
   ```
   Ekki keyra `brew services start postgresql@17` strax á eftir — það myndi
   reyna að frumstilla (`initdb`) nýjan, tóman gagnagrunn á sjálfgefna
   staðnum. Tengdu diskinn og notaðu `pg_ctl` beint eins og lýst er hér ofar.

3. **Poppler** (PDF-þáttun):
   ```bash
   brew install poppler
   ```

4. **uv** (Python pakkastjóri):
   ```bash
   brew install uv
   ```

5. **Node.js** (fyrir framendann):
   ```bash
   brew install node
   ```

6. Tengdu RuleOfLaw-diskinn, staðfestu að hann sé undir `/Volumes/RuleOfLaw`.

7. **Settu upp launchd-þjónustuskrána.** Hún býr í heimamöppunni, ekki á
   disknum, svo ný vél hefur hana ekki:
   ```bash
   mkdir -p ~/Library/LaunchAgents
   cp /Volumes/RuleOfLaw/Lausnir/deploy/is.lausnir.postgres.plist \
      ~/Library/LaunchAgents/
   ```
   Skráin notar sérmerkimiðann `is.lausnir.postgres`, svo hún rekst hvorki á
   Postgres hýsilvélarinnar né `brew services`.

8. **Veittu Full Disk Access** (Kerfisstillingar → Persónuvernd og öryggi →
   Full Disk Access) fyrir `/opt/homebrew/opt/postgresql@17/bin/postgres`.
   Án þessa hangir `launchctl bootstrap` þögult — engin villa, ekkert
   hlustunar-socket (sjá skýringu ofar). Handvirk `pg_ctl`-ræsing virkar samt,
   svo þetta er auðvelt að misgreina.

9. **Ræstu Postgres** (sjá skipun ofar undir „Ræsa").

10. Staðfestu tengingu og gagnaheilleika (**athugið `-p 5433`**):
   ```bash
   psql -U geiri -p 5433 -d lausnir_v2 -c "SELECT count(*) FROM documents;"
   ```
   (Auðkenning er stillt á `trust` fyrir localhost, svo `-U geiri` virkar óháð
   því hvað notandanafnið á nýju tölvunni heitir.)

   Til að `psql` rati sjálfkrafa á 5433, bættu við `~/.zshenv` (vélarsértækt, fylgir
   ekki disknum):
   ```bash
   export PGPORT=5433
   ```
   Á vél sem keyrir líka eigin Postgres á 5432 þarf þá að gefa því `-p 5432` sérstaklega.
   Skriftur lesa portið úr `DATABASE_URL` í `.env` og eru háðar þessu hvorugu —
   þær neita að keyra ef `DATABASE_URL` vantar (`uv run --env-file .env …`).

11. **Python-umhverfi** — `.venv` möppan á disknum er ekki endilega flytjanleg
   milli véla (bundin við tiltekna Python-uppsetningu). Endurgerðu hana á
   nýju vélinni:
   ```bash
   cd /Volumes/RuleOfLaw/Lausnir
   uv sync
   ```

12. **Playwright-vafri** (notaður af `import_stjornarradid.py` o.fl.) þarf að
    sækja á hverja nýja vél sérstaklega:
    ```bash
    uv run playwright install chromium
    ```

13. **Framendinn** (ef á að keyra hann):
    ```bash
    cd /Volumes/RuleOfLaw/Lausnir/frontend
    npm install
    npm run dev
    ```

14. **Tengdu Claude-minnið** (valfrjálst, en gagnlegt). Minnisskrárnar búa á
    disknum í `/Volumes/RuleOfLaw/claude-memory/`, en `~/.claude` er
    per-vél og hefur ekki tenginguna:
    ```bash
    mkdir -p ~/.claude/projects/-Volumes-RuleOfLaw-Lausnir
    ln -s /Volumes/RuleOfLaw/claude-memory \
          ~/.claude/projects/-Volumes-RuleOfLaw-Lausnir/memory
    ```
    Þannig fylgir samhengið um verkefnið disknum milli véla í stað þess að
    tvö sjálfstæð eintök reki í sundur.

## Sjálfvirk ræsing við innskráningu

Þetta virkar núna sjálfkrafa (staðfest 2026-09-13): `RunAtLoad` í plist-skránni
sér um að macOS ræsi Postgres sjálfkrafa við hverja innskráningu, að því gefnu
að (a) diskurinn sé þegar tengdur og (b) `postgres`-forritið hafi Full Disk
Access heimild (sjá að ofan). Ef ræsing við innskráningu hættir að virka á
nýrri vél er þetta fyrsta atriðið til að athuga.
