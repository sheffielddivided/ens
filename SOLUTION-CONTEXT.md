# SOLUTION-CONTEXT.md — ENS (Danmark) olje-/gass-/vannproduksjon

Formål: dette dokumentet beskriver hva denne løsningen faktisk gjør, med
tilstrekkelig detalj til at datainnsamlingen kan reimplementeres og
datamodellen forstås **uten å lese kildekoden**. Det er skrevet for å slås
sammen med søsterløsninger for andre land til én felles applikasjon. Alle
påstander er verifisert mot faktisk kode og faktiske data i repoet (ikke mot
README/kommentarer), med filsti og linjenummer. Der noe ikke kunne
verifiseres fullt ut, er det markert `USIKKER:`.

Alle linjenumre er fra HEAD på branch `claude/session-icr465` på
tidspunktet dette ble skrevet (2026-09-13).

---

## 1. Oversikt

Dette er et **selvoppdaterende datasett + statisk nettsted** for produksjon
av olje, gass og vann fra danske offshore-felt i Nordsjøen. Data hentes fra
Energistyrelsens (ENS, det danske energidirektoratet) offentlige
publikasjoner, normaliseres til et felles JSON-skjema, og vises i et
Chart.js-diagram og et Leaflet-kart på GitHub Pages.

Hele løsningen kjører uten server:
- En **GitHub Actions cron-jobb** (`.github/workflows/monthly-update.yml`)
  kjører et Python-pipeline noen ganger i måneden, som crawler ENS' nettside,
  laster ned nye/endrede kilder, parser dem, og committer resultat-JSON
  direkte til git-repoet.
- **GitHub Pages** server statiske filer fra `docs/` og leser JSON-filene med
  `fetch()` i nettleseren. Ingen backend, ingen database, ingen build-steg.
- Et separat, manuelt utløst pipeline (`build_gis.py` /
  `.github/workflows/build-gis.yml`) konverterer ENS' shapefiler til GeoJSON
  for feltkartet.
- Eierskapsdata (`data/ownership.json`) bygges fra en **manuelt vedlikeholdt**
  Excel-fil, ikke fra et automatisert nett-hentesteg (se §4.5).

Nøkkelprinsipp gjennom hele kodebasen: **fail loud, never guess**. Når en
kilde ikke matcher et kjent format, kastes `SourceFormatError`
(`scripts/common.py:107-112`) i stedet for å skrive gjettede/tvilsomme tall.

## 2. Teknologistack

**Backend/pipeline (Python 3.11, ingen webserver):**
- `openpyxl` — lese `.xlsx` (årlig kilde), se `requirements.txt`
- `beautifulsoup4` + `lxml` — parse HTML-månedsrapporter
- `pdfplumber` — parse PDF-månedsrapporter
- `requests` — HTTP-klient (importeres lazy i `common.py:309,349` slik at
  rene parse-/test-kjøringer ikke krever nettverksstakken)
- `pytest` — testrammeverk, kjører kun mot fixtures (`pytest.ini`:
  `testpaths = tests`)
- GIS-pipeline (separat `requirements-gis.txt`, kun for `build_gis.py`):
  `pyshp` (les shapefiler uten GDAL), `pyproj` (reprojisering), `shapely`
  (forenkling av polygoner)

**Frontend (ingen build-steg, ingen rammeverk):**
- Ren HTML/CSS/vanilla JS: `docs/index.html`, `docs/app.js`, `docs/map.js`,
  `docs/style.css`
- `Chart.js` 4.4.1 (bar-diagram), `Leaflet` 1.9.4 (kart), `xlsx.js` (SheetJS)
  0.18.5 (Excel-eksport av grafdata) — alle lastet fra jsDelivr CDN i
  `docs/index.html:94-96,8`, ingen npm/bundler
- `docs/map.js` er pakket i en IIFE (`docs/map.js:19-21`) spesifikt for å
  unngå globale navnekollisjoner med `app.js`, som kjører i samme side uten
  moduler

**Infrastruktur:**
- GitHub Actions (cron + `workflow_dispatch`)
- GitHub Pages, statisk hosting fra `/docs`
- Git selv som datalager — hver kjøring committer endringer i JSON/GeoJSON
  direkte til default branch

**Ingen database.** Alt lagres som JSON-filer i git.

## 3. Datakilder

Alt utgår fra én landingsside:
`https://ens.dk/en/energy-sources/monthly-and-yearly-production`
(`scripts/common.py:46`).

Det finnes to kildetyper, begge lenket fra denne siden, begge i **"SI
Units"**-varianten (ikke "Oil Field Units" — det finnes en parallell tabell
med Oil-Field-Units-lenker på samme side som eksplisitt ekskluderes, se
`tests/test_parsers.py:155-161`):

1. **Årlig Excel-fil** (`.xlsx`), tittel i stil med "Yearly production,
   injection, flare, fuel and export in SI units". Dekker hele historikken
   fra 1972. Dette er **den autoritative kilden** — endelige, ikke-foreløpige
   tall. Reell URL sett i repoet: `https://ens.dk/media/7167/download`
   (`data/sources/index.json`, `yearly.url`).

2. **Månedlige produksjonsrapporter**, én fil per kalendermåned, fra ca.
   januar 2018. To fysiske format brukt over tid:
   - **HTML** (`.htm`) — eldre rapporter (bekreftet reelt eksempel: januar
     2018, se §5).
   - **PDF** — nyere rapporter (bekreftet reelt eksempel: juli 2026, se §5).
   Skillet mellom HTML og PDF **utledes fra URL-endelsen på hver enkelt
   lenke**, ikke fra en antatt overgangsdato — `_monthly_format()` i
   `scripts/build_index.py:71-87` sjekker om URL-en ender på `.pdf`/`.htm`
   eller peker til et generisk `/media/<id>/download`-endepunkt (som
   ENS bruker for nyere filer uavhengig av ekte filtype), og i så fall
   sniffes faktisk bytes-innhold ved nedlasting for å avgjøre formatet
   (`scripts/update.py:44-57`, `_sniff_kind()`, sjekker magic bytes `%PDF` vs.
   HTML-tags). Dette betyr i praksis: **stol aldri på filendelsen alene** —
   sjekk alltid de faktiske bytene.

Alle månedstall er **foreløpige estimater**; årsfilen overstyrer dem alltid
når begge finnes for samme år (se §8.1).

### Hvordan lenkene faktisk finnes på siden

`scripts/build_index.py` crawler landingssiden i to passeringer
(`crawl()`, linje 191-293):

- **Pass A** (`_si_ofu_monthly_anchors`, samme fil): ENS-siden har en tabell
  der hver rad er en måned med to lenker — én for SI Units, én for Oil Field
  Units. Denne passeringen gjenkjenner nettopp denne tabellstrukturen og
  plukker kun SI-lenken.
- **Pass B**: en generisk anker-skanning over hele siden som fallback, for å
  fange opp lenker som ikke sitter i den strukturerte tabellen (f.eks. hvis
  ENS endrer layout).

Begge passeringer filtrerer bort urelaterte PDF-er som ligger på samme side
(f.eks. lenker til `energy_statistics`-rapporter, se
`tests/test_parsers.py:130-133`).

**USIKKER:** Det finnes ingen offisiell API eller maskinlesbar feed fra ENS
for disse dataene — alt er HTML-scraping av en side ment for mennesker. Hvis
ENS endrer sidestrukturen vesentlig, vil crawleren kunne slutte å finne nye
måneder stille (den er riktignok bygget for å feile synlig når en enkelt
rapport ikke matcher et kjent tabell-layout, men en *helt* endret landingsside
kan i prinsippet gi et tomt resultat uten feil — se §11).

## 4. Innsamlingsprosess

Pipelinen har seks faser, kjørt av separate skript, alle kjørbare
frittstående med `--help`. `scripts/update.py` orkestrerer fase 3-5 for en
vanlig kjøring.

### 4.1 Fase 1 — bygg kildeindeks (`scripts/build_index.py`)

Crawler landingssiden (se §3) og skriver `data/sources/index.json`:

```json
{
  "yearly": { "url": "https://ens.dk/media/7167/download", "format": "xlsx" },
  "monthly": [
    { "year": 2018, "month": 1, "url": "...", "format": "html", "unit_system": "si",
      "status": "ok", "retrieved_at": "...", "records": 17 },
    ...
  ]
}
```

`build_index()` (`scripts/build_index.py:313-388`) **slår sammen** nyoppdagede
lenker med den forrige indeksen: for URL-er som fortsatt finnes, bevares
`status`/`retrieved_at`/`error`/`records` fra forrige kjøring, slik at
allerede-hentede måneder ikke må hentes/parses på nytt uten grunn. Dette
er nøkkelen til hele pipelinens inkrementalitet.

**Kritisk driftsdetalj:** selve landingssiden hentes via samme
cache-mekanisme som alt annet innhold (`Fetcher`, se §4.6). Hvis landingssiden
caches uten `force=True`, vil crawleren for alltid se den samme, første
nedlastede versjonen av siden og aldri oppdage nye måneder uansett hvor ofte
jobben kjøres på cron. `scripts/update.py:292` kaller derfor eksplisitt
`BI.build_index([C.ENS_PRODUCTION_PAGE], offline=offline, force=True)` —
`force=True` tvinger en fersk nedlasting av selve landingssiden ved hver
kjøring (mens de faktiske måneds-/årsfilene fortsatt caches normalt). Dette
har historisk vært en reell bug i denne kodebasen (se §11).

### 4.2 Fase 2 — årlig kilde (`scripts/ingest_yearly.py`)

Laster ned/leser årsfilen og skriver `data/yearly.json` + `data/fields.json`.

Excel-arket har **ingen fast cellestruktur** ENS kan endre år for år, så
parseren prøver tre auto-detekterte layout-strategier i rekkefølge, og
beholder den som gir flest gyldige rader:

1. **Stacked-block** (`try_parse_stacked_sheet()`,
   `scripts/ingest_yearly.py:370-416`) — **dette er layouten den ekte ENS-
   filen faktisk bruker** (verifisert mot `data/sources/raw/7167_download`).
   Ett regneark, med målinger (olje/gass/vann/injeksjon/fakling/brensel/
   eksport) stablet vertikalt som egne "blokker", hver med sin egen
   år-kolonne-overskriftsrad og en liste av feltrader under. Se §5 for et
   ekte eksempel.
2. **Matrix/tabular**-layout(er) — enklere fallback-format (én rad per
   felt+år, kolonner = mål) — støttet defensivt, men **ikke det virkelige
   formatet**; kun brukt av syntetiske test-fixtures
   (`tests/fixtures/yearly.xlsx`).

`classify_measure()` (testet i `tests/test_parsers.py:190-199`) gjenkjenner
målingstype fra blokkens overskriftstekst (engelsk/dansk vokabular: "Oil
production", "Sales gas production", "Water injection", "Flaring", osv.) —
ikke fra posisjon.

`build_fields()` (`scripts/ingest_yearly.py:512-543`) bygger
`data/fields.json`: for hvert felt som forekommer i minst én rad, samles alle
sett skrivemåter som `aliases`, og `first_year`/`last_year` settes til
min/max årstall feltet **forekommer som rad i kildefilen** — **uavhengig av
om verdiene er null/tomme det året**. Se §11 for en konkret fallgruve dette
skaper.

### 4.3 Fase 3 — månedsrapporter (`scripts/parse_monthly_html.py`,
`scripts/parse_monthly_pdf.py`, delt logikk i `scripts/monthly_common.py`)

Hver månedsrapport parses individuelt til en liste med records. Begge
formatene (HTML og PDF) bruker samme **stacked-block**-layout som årsfilen —
per-mål-blokker (Oil/Gas/Water), hver med egen sub-header-rad, stablet
vertikalt i dokumentet. Dette er det virkelige formatet, verifisert mot
`tests/fixtures/monthly_real_2018_01.htm` og
`tests/fixtures/monthly_real_2024_01.pdf` (ekte nedlastede ENS-dokumenter,
ikke syntetiske).

**HTML** (`scripts/parse_monthly_html.py`):
- `_table_to_grid()` (linje 37-98) ekspanderer `rowspan`/`colspan` til en flat
  grid før parsing, siden ENS' HTML-tabeller bruker begge.
- Prøver **hver** `<table>`-node i dokumentet og beholder den rikeste
  (flest gyldige felt-rader) parsingen — dokumentet kan ha flere tabeller,
  og hvilken som er produksjonstabellen varierer.
- Rå bytes dekodes IKKE med `utf-8`: ENS' `.htm`-filer fra denne perioden er
  faktisk **cp1252**-kodet (Windows-1252), verifisert både i test
  (`tests/test_parsers.py:314-315`, `raw.decode("cp1252")`) og i
  orkestratoren (`scripts/update.py:72-79`, `_decode_html()` prøver
  utf-8 → cp1252 → latin-1 i rekkefølge).

**PDF** (`scripts/parse_monthly_pdf.py`):
- `_STRATEGIES` (linje 33-38): tre forskjellige `pdfplumber`
  tabell-ekstraksjonsstrategier prøves.
- `extract_all_tables()` (linje 47-86) bygger **også** en "merged" kandidat
  som slår sammen alle tabeller/sider fra én strategi til én lang grid — dette
  er nødvendig fordi `pdfplumber` ofte oppdager de stablede blokkene som
  separate tabeller i stedet for én sammenhengende tabell (bekreftet med et
  ekte eksempel: Halfdans vann-tall ligger i en egen tabell adskilt fra resten
  av blokken, og bare den sammenslåtte gridden fanger den — se
  `tests/test_parsers.py:399-411`).
- Den kandidaten (av alle strategier × merged/ikke-merged) med flest gyldige
  rader vinner.

**Delt kolonnelogikk** (`scripts/monthly_common.py`), brukt av begge:
- `parse_stacked_monthly()` (linje 271-317): for hver mål-blokk, finner
  sub-header-raden som inneholder "Monthly" (og "Field"), og tar **verdien i
  "Monthly"-kolonnen** — IKKE "Daily Avg."-kolonnen og IKKE den kumulative
  "This year"/"Year to date"-kolonnen, som begge også finnes i samme blokk.
  Dette er et lett sted å feile hvis man reimplementerer fra bunnen: en rask
  titt på en rapport kan lett få en til å plukke feil kolonne.
- Fuel/Flare/Injection-blokker **hoppes eksplisitt over** i månedsrapporter
  (kommentar `scripts/monthly_common.py:251-252`) — i motsetning til
  årsfilen, er disse tallene **nasjonale aggregater** i månedsrapportene, ikke
  per felt, så de ville forurenset per-felt-dataene hvis de ble tatt med.
- `classify_column()` og `detect_unit()` (testet i
  `tests/test_parsers.py:330-349`) gjenkjenner kolonneoverskrifter på både
  engelsk og dansk ("Water"/"Vand", "Salgsgas"/"Sales gas") og med
  Unicode-supertegn i enheter (`m³`, `Nm³`).
- `plausibility_warnings()` (linje 363-377): logger `WARN` (ikke feil) hvis en
  verdi overstiger et plausibilitetstak: 5000 for olje/gass, 50000 for vann,
  i de dokumenterte SI-enhetene. Samme tak (`_MONTHLY_CAP`) dupliseres i
  `scripts/validate.py:31`.

Begge parserne kaster `SourceFormatError` hvis dokumentet ikke matcher **noen**
kjent layout (testet: `tests/test_parsers.py:302-307`).

### 4.4 Fase 4/5 — orkestrering og sammenslåing (`scripts/update.py`)

`process_months()` (linje 82-130): for hver måned i indeksen som mangler
eller er markert `failed`, hent og parse den. Ved suksess erstattes
posten i `data/monthly.json` og `status` settes til `ok`; ved feil
isoleres feilen til akkurat den måneden (`status: "failed"` +
feilmelding lagres i indeksen) **uten** å stoppe resten av kjøringen — én
korrupt/uventet månedsrapport blokkerer ikke de andre.

`build_combined()` (linje 144-208) er kjernen i sammenslåingslogikken som
produserer `data/combined.json` (se §5 og §8.1 for detaljer om
presedens-reglene).

Til slutt kopieres `combined.json` og `ownership.json` til `docs/data/` slik
at det statiske nettstedet kan lese dem direkte
(`scripts/common.py:34`, `DOCS_DATA_DIR`).

### 4.5 Fase 6 — eierskap (`scripts/ingest_ownership.py`) — IKKE automatisert

I motsetning til alt annet i pipelinen, kjøres dette skriptet **manuelt, ikke
på cron**. Bekreftet ved søk: verken `.github/workflows/*.yml` eller
`scripts/update.py` kaller `ingest_ownership.py` noe sted — kun en kommentar
i `scripts/update.py` nevner det. ENS publiserer ikke lisensiar-andeler som en
nedlastbar fil (siden er et interaktivt oppslagsverktøy), så det finnes
ingenting å automatisk hente. I stedet leses en **manuelt vedlikeholdt
Excel-eksport** (`data/sources/licences/danske_lisenser_komplett.xlsx`) som
må erstattes for hånd når eierskap endrer seg. Se §5 og §8.2 for detaljer om
transformasjonen.

**Reimplementasjonskonsekvens:** en søsterløsning må ikke anta at dette steget
kjøres periodisk av seg selv — det er et frittstående "kjør når kilden er
oppdatert"-skript, ikke del av den daglige jobben.

### 4.6 Delt infrastruktur: `Fetcher`-klassen (`scripts/common.py:288-371`)

All nettverkstrafikk går gjennom én klasse:
- Sender en beskrivende `User-Agent`
  (`scripts/common.py:50-54`, overstyrbar via miljøvariabel
  `ENS_USER_AGENT`) som identifiserer prosjektet og peker til repoet.
- Håndhever **≤ 1 forespørsel/sekund** (`MIN_REQUEST_INTERVAL_S`,
  linje 57, overstyrbar via `ENS_MIN_INTERVAL`) via en `_throttle()`-sjekk
  før hver faktiske nettverkskall.
- **Cacher alt** under `data/sources/raw/`, nøkkelet på et
  filnavn utledet fra URL-en (`safe_filename()`, linje 374-384 — håndterer
  ENS' generiske `/media/<id>/download`-URL-er ved å ta med foreldre-segmentet
  i navnet, siden siste segment ("download") ellers ikke er unikt).
- Ved cache-miss og **ikke** `--offline`: laster ned med **4 forsøk og
  eksponentiell backoff** (2s, 4s, 8s, 16s — `fetch()`, linje 325-368).
- `--offline`-modus (brukt av alle tester og valgfritt av skriptene) kaster
  `SourceFormatError` i stedet for å gjøre noe nettverkskall i det hele tatt
  hvis noe mangler i cachen (linje 344-347) — garanterer at
  `--offline`-kjøringer aldri stille faller tilbake til nettet.

Import av `requests` skjer **lazy** (linje 309, 349) slik at ren
parsing-/testkode kan importere `common.py` uten `requests` installert i det
hele tatt.

## 5. Datamodell

Alle feltnavn normaliseres til en stabil, lokalt genererte **slug** —
`normalize_field()` (`scripts/common.py:191-212`). Det finnes **ingen
ENS-nativ felt-ID**; slug er den eneste kanoniske entitets-ID på tvers av
alle filer. Algoritmen: fjern fotnotemarkører (`*`, `†`, `‡`, supertall) FØR
Unicode-normalisering (ellers ville f.eks. `¹` blitt til literal `1` og limt
inn i navnet), Unicode NFKD-fold + fjern diakritiske tegn (æ/ø/å →
ASCII), lowercase, fjern parentetisk innhold, fjern ordet "field" som eget
ord, erstatt alt ikke-alfanumerisk med `_`. Eksempler (verifisert i
`tests/test_parsers.py:28-43`):

| Rå streng | Slug |
|---|---|
| `Dan` / `DAN` / `Dan Field` | `dan` |
| `Halfdan Field*` | `halfdan` |
| `Tyra South East` | `tyra_south_east` |
| `Nini/Cecilie` | `nini_cecilie` |
| `Syd Arne` | `syd_arne` |
| `Gorm¹` | `gorm` |

`is_total_label()` (`scripts/common.py:223-225`) gjenkjenner aggregat-rader
("Total", "I alt", "Sum", "Denmark"/"Danmark", …) via en fast
`TOTAL_LABELS`-mengde (linje 217-220) slik at de aldri lagres som felt.

### 5.1 Filene

**`data/yearly.json`** og **`data/monthly.json`** — flat liste med records,
identisk struktur bortsett fra at monthly har `month` og `preliminary: true`:

```json
{
  "field": "dan", "year": 2025, "month": 3,
  "oil": 123.4, "gas": 56.7, "water": 890.1,
  "preliminary": true,
  "source_url": "https://ens.dk/...",
  "retrieved_at": "2026-07-21T06:00:00Z"
}
```

`yearly.json`-records har `preliminary: false` og kan i tillegg ha
`gas_injection`, `water_injection`, `flare`, `fuel`, `gas_export`,
`oil_export` (`OPTIONAL_MEASURES`, `scripts/common.py:78-85`) når kilden
tilbyr dem — disse finnes **ikke** i månedsrapportene (se §4.3, hoppes
eksplisitt over). `oil`/`gas`/`water` er minimumsmengden (`CORE_MEASURES`,
linje 77).

Ekte volum (verifisert `data/yearly.json` / `data/monthly.json`, se §10):
1113 årsrecords, 1284 månedsrecords per 2026-09-13.

**`data/fields.json`** — metadata per felt:

```json
{ "fields": {
  "syd_arne": {
    "slug": "syd_arne", "display_name": "Syd Arne",
    "aliases": ["Syd Arne"],
    "first_year": 1972, "last_year": 2024,
    "operator": "Hess"
  }
}}
```

21 felt i produksjon per 2026-09-13 (`data/fields.json`). `operator` er
best-effort fra en hardkodet oppslagstabell (`_KNOWN_OPERATORS`,
`scripts/ingest_yearly.py:502-509`) — `null` når ukjent. **`first_year`/
`last_year` betyr "året feltet først/sist forekommer som rad i kildefilen",
IKKE "året feltet faktisk startet/sluttet produksjon"** — et felt kan ha rader
med verdi 0 i flere år før reell produksjon starter. Konkret eksempel: Gorm
har `first_year: 1972` i `data/fields.json` selv om alle olje-verdier for
1972-1978 er 0 i kildedataene — feltet fantes som rad i regnearket lenge før
det produserte noe.

**`data/ownership.json`** — se §5.3 og §8.2.

**`data/combined.json`** — den avledede, frontend-klare tidsserien.
**Bygges alltid på nytt** fra `yearly.json` + `monthly.json` +
`fields.json` av `build_combined()` (`scripts/update.py:144-208`); ingen
manuell redigering:

```json
{
  "schema_version": 1,
  "unit_definitions": { "oil": "1000 m3", "gas": "mio. Nm3", "water": "1000 m3" },
  "measures": ["oil", "gas", "water"],
  "last_updated": "2026-08-26T05:50:05Z",
  "fields": [ { "slug": "_total", "display_name": "Alle felt" }, { "slug": "dan", "display_name": "Dan" }, ... ],
  "series": {
    "dan": {
      "yearly":  { "oil": [ { "t": "2020", "v": 300.0, "p": false } ], "gas": [...], "water": [...] },
      "monthly": { "oil": [ { "t": "2025-03", "v": 123.4, "p": true } ], ... }
    },
    "_total": { "yearly": {...}, "monthly": {...} }
  }
}
```

- Hvert datapunkt: `t` (`"YYYY"` for år, `"YYYY-MM"` for måned), `v` (verdi),
  `p` (`true` = foreløpig). I dagens `docs/app.js` gir dette **ingen egen
  visuell stil på stolpene selv** (`barDS()`, `docs/app.js:534-536`, har fast
  stil uansett `p`) — foreløpige perioder markeres kun i verktøytipset
  (`docs/app.js:476`) og i en forklarende setning under grafen
  (`docs/app.js:523`). Kun vannlinjen (et overlagt Chart.js `"line"`-datasett,
  `docs/app.js:362-372`) har en stiplet kantlinje, og det er en fast stil for
  hele linjen, ikke betinget av `p`. En reimplementasjon står fritt til å
  bruke `p` til en tydeligere visuell markering (f.eks. stiplet stolpekant)
  enn det denne referanseimplementasjonen faktisk gjør i dag.
- `_total`-serien er summen over alle felt; den plasseres **først** i
  `fields`-listen (`combined["fields"][0]["slug"] == "_total"`, testet i
  `tests/test_parsers.py:462-463`) slik at UI-en kan bruke den som
  standardvalg.
- 22 serier (21 felt + `_total`) per 2026-09-13.
- **Reelt combined.json har ALDRI nøklene `"synthetic"` eller `"note"`** —
  disse finnes kun i demo-filen `docs/data/combined.sample.json`
  (verifisert: `list(json.load(open("data/combined.json")).keys())` gir
  nøyaktig `["schema_version","unit_definitions","measures","last_updated",
  "fields","series"]`). Frontend bruker fraværet av data (ikke et flagg) til
  å avgjøre om ekte data finnes, se §9.

### 5.2 Ekte eksempel: rå kilde → parset record → combined.json

**Rå HTML-kilde** (`data/sources/raw/monthly_2018_01_mp201801si.htm`,
cp1252, Dan-raden i Oil-blokken):

```
<tr><td>Dan</td><td>95,0</td><td>3,1</td><td>3,4</td><td>3,4</td><td>95,0</td></tr>
```
(kolonner: Field | Monthly | Daily Avg. | This month last year | ... —
verdien `95,0` i "Monthly"-kolonnen brukes; `parse_number("95,0")` →
`95.0` via komma-som-desimaltegn-regelen i `scripts/common.py:262-267`.)

**Etter `parse_monthly_html.py`** → post i `data/monthly.json`:
```json
{ "field": "dan", "year": 2018, "month": 1, "oil": 95.0, "gas": 26.4, "water": 809.6,
  "preliminary": true, "source_url": "https://ens.dk/...mp201801si.htm",
  "retrieved_at": "..." }
```

**Rå PDF-kilde** (`data/sources/raw/monthly_2026_07_8696_download`,
pdfplumber-ekstrahert rad, juli 2026):
```
['Dan', '64.2', '2.1', '2.1', '1.9', '431.3', None]
```
(Field | Monthly-olje | Daily Avg | ... — her er kildetallet allerede
punktum-desimal, ingen omregning nødvendig: `64.2`.)

**Rå årlig Excel-kilde** (`data/sources/raw/7167_download`, ark
"Hjemmeside_2025", Dan-raden i Oil-blokken for kolonneår 2018): rå celleverdi
`1123.3872` → lagres uendret som `data/yearly.json`-post
`{ "field": "dan", "year": 2018, "oil": 1123.3872, "preliminary": false, ... }`.

**I `combined.json`** blir 2018 for Dan derfor det **endelige** årstallet
`1123.3872` (fra yearly, `p: false`), ikke summen av de 12 månedsverdiene fra
`monthly.json` — se §8.1 for presedensregelen.

### 5.3 Ekte eksempel: eierskap (rå Excel → `ownership.json`)

Kilderegneark `data/sources/licences/danske_lisenser_komplett.xlsx`, ark
`"Lisenser per blokk"`, kolonner: `Lisensnavn | Block | Område/felt |
Licence granted | Licence expiry date | Operator | Area (km²) |
Delineation by depth (mbmsl) | Selskap | Gruppe | Selskapsandel | Kilde`.

Fire rå rader for lisensområdet `"Contiguous Area"` (default-konsesjonen som
de fleste DUC-felt, inkl. Dan, arver — se §8.2), blokk `5504/7`:

| Selskap | Gruppe | Selskapsandel |
|---|---|---|
| BlueNord Energy Denmark A/S | BlueNord | 0.368 |
| TotalEnergies EP Danmark A/S (Concessionaire) | TotalEnergies | 0.312 |
| TotalEnergies Denmark ASW, Filial af ... | TotalEnergies | 0.12 |
| Nordsøfonden | Nordsøfonden | 0.2 |

**Etter `ingest_ownership.py`** (summert per `Gruppe`, se §8.2) →
`data/ownership.json["fields"]["dan"]`:
```json
{ "BlueNord": 0.368, "TotalEnergies": 0.432, "Nordsøfonden": 0.2 }
```
(`0.312 + 0.12 = 0.432` — de to TotalEnergies-radene, ulike juridiske
enheter i samme "Gruppe", slås sammen til én andel.)

## 6. Enheter og konverteringer

De dokumenterte SI-enhetene, brukt overalt i rådataene (`UNIT_DEFINITIONS`,
`scripts/common.py:70-74`):

| Mål | Enhet |
|---|---|
| Olje | `1000 m3` |
| Gass | `mio. Nm3` (millioner normal-kubikkmeter) |
| Vann | `1000 m3` |

Dette er de **dokumenterte forventede** enhetene, ikke bare en antakelse:
parserne leser i tillegg kildens egne enhetsstrenger fra
kolonneoverskriftene (`detect_unit()`, `scripts/monthly_common.py`, testet
`tests/test_parsers.py:344-349`) og logger `WARN` hvis de avviker — enhetene
fastsettes altså ikke ved gjetting alene, og et avvik blir ikke stille
ignorert.

### 6.1 Oljeekvivalenter (o.e.) i frontend

Frontend (`docs/app.js`) viser **aldri** rådataene i sine native SI-enheter
for olje/gass — den viser alltid en kombinert olje+gass-rate i tusen fat
oljeekvivalenter per dag ("mboepd"). Konverteringsfaktor:

```js
const BOE = 6.29;   // fat per m³ olje   (docs/app.js:52)
```

`oeRate(v, t)` (`docs/app.js:78-80`) regner `v * BOE / daysForRate(t)` — dvs.
BOE-faktoren appliseres **direkte på både olje- og gasstallet** før de
summeres. Dette er konsistent med ENS' egen SI-konvensjon: olje (`1000 m3`)
og gass (`mio. Nm3`) rapporteres allerede på en skala som gjør dem
sammenlignbare før BOE-faktoren (dvs. koden multipliserer rå
olje-tallet OG rå gass-tallet med samme 6.29-faktor og summerer — den
regner **ikke** om gass til m³ separat med en annen faktor). Enhver
reimplementasjon som ønsker samme visning må bruke akkurat denne — ikke en
"riktigere" fysisk BOE-omregning for gass separat, som ville gitt et annet
tall enn det som vises i dagens UI.

`daysForRate()` (`docs/app.js:60-75`) håndterer ufullstendige perioder (f.eks.
inneværende, ennå ikke avsluttede år) ved å dele på **faktisk antall dager
til og med siste kjente datapunkt**, ikke på 365/366 — unngår at et
delvis år vises som en kunstig lav rate.

Vann vises **valgfritt** på en sekundær akse i sin **native** enhet
(`1000 m3`, ingen BOE-konvertering) — det finnes ingen "vannoljeekvivalent".

### 6.2 Tallformatering fra kilden

`parse_number()` (`scripts/common.py:231-271`) håndterer dansk/europeisk
tallformat robust: gjenkjenner om `.` eller `,` er tusenskille vs.
desimaltegn ved å se på **hvilken som forekommer lengst til høyre** når begge
finnes i samme tall (europeisk: `.` tusenskille, `,` desimal — f.eks.
`"1.234,5"` → `1234.5`; angloamerikansk: motsatt), avviser rene bokstaver
(hindrer at enhetssuffikser som `"Nm3"` mines for sifre), og returnerer
`None` (ikke `0`) for tomme/dash-celler slik at "ingen verdi" kan skilles fra
faktisk `0`.

## 7. Geodata

**Kilde:** ENS' egne shapefiler,
<https://ens.dk/en/energy-sources/oil-and-gas-related-data/shape-files-oil-and-gas-maps>
(lenket fra `README.md:281`, ikke hardkodet URL i skript — brukeren limer inn
zip-URL-er manuelt via `--url`).

**Fem lag** → `docs/data/gis/*.geojson` (ekte feature-antall per
2026-09-13, verifisert direkte):

| Fil | Rolle | Geometri | Features |
|---|---|---|---|
| `fields.geojson` | feltavgrensninger | polygon | 34 |
| `licences.geojson` | tildelte lisensområder | polygon | 20 |
| `blocks.geojson` | blokk-/kvadrantrutenett | polygon | 785 |
| `installations.geojson` | offshore-installasjoner | punkt | 66 |
| `wells.geojson` | lete-/vurderingsbrønner | punkt | 371 |

**34 feltpolygoner vs. 21 produksjonsfelt** (`data/fields.json`) — disse
tallene er **bevisst forskjellige**, ikke en feil: noen polygoner har aldri
hatt registrert produksjon, og minst ett produksjonsfelt (`ravn`, ifølge
`README.md:338`) har produksjon uten registrert avgrensning i kilden. `WARN`
logges for begge retninger av mismatch i stedet for å late som de matcher.

**Projeksjoner — ulike per lag, ikke én global konstant.** Verifisert direkte
mot de rå `.prj`-filene i `data/sources/gis/raw/` med `pyproj`:

| Lag | Kildeprojeksjon | Type |
|---|---|---|
| FieldDelineations, Licenses, OffshoreInstallations | `EPSG:23031` (UTM 31N / ED50) | projisert |
| Blocks | `EPSG:23032` (UTM 32N / ED50) | projisert |
| ExpAppWells | `EPSG:4230` (geografisk ED50, lon/lat) | geografisk |

Alle er ED50-baserte (europeisk datum 1950), **ikke** WGS84. GeoJSON krever
WGS84 (`EPSG:4326`, RFC 7946), så hvert lag reprojiseres individuelt —
kildens egen `.prj`-fil er fasit per lag (`build_gis.py`, fallback til
`EPSG:23032` **kun** hvis en `.prj` mangler). Dette er en reell
datum-transformasjon (ED50→WGS84 forskyver geometrien ~100 m), ikke bare en
enhetsomregning — å anta én felles EPSG-kode for alle lag ville gitt synlig
feilplasserte polygoner.

**Feltnavn-forsoning mot produksjonsslugs** (`reconcile_field_slug()`,
`scripts/build_gis.py:238-242`, testet `tests/test_gis.py:93-104`): kartets
feltnavn normaliseres med samme `normalize_field()`-slug som
produksjonsdataene, PLUSS en ekstra `FIELD_SLUG_ALIASES`-tabell
(`scripts/build_gis.py:84`) for kjente avvik i ENS' egne kartetiketter, bl.a.:

- `"South Arne - eastern/western part"` → slugges normalt til `south_arne`,
  men aliaset mapper eksplisitt til `syd_arne` (feltets faktiske slug i
  produksjonsdataene, som alltid bruker det danske navnet "Syd Arne" — se
  §11 for hvorfor dette aliaset finnes).
- `"Tyra Southeast"` → `tyra_se`.
- Parentetiske tillegg (`"Halfdan (Igor area)"` → `halfdan`) og
  lisensdel-kvalifikatorer (`"Lulita - 1/90 part"` → `lulita`) fjernes.

**Pipeline** (`scripts/build_gis.py`): les `.shp`/`.zip` via `pyshp` →
forenkle polygoner (Douglas-Peucker, standard 50 m i kildens meterenhet, via
`shapely`) → reprojiser til WGS84 → fjern støykolonner (`Shape_Area`, `FID`,
duplikate koordinatfelt) → avrund koordinater til fast presisjon → skriv
**idempotent** (uendret geometri ⇒ byte-for-byte identisk fil, verifisert i
`tests/test_gis.py:138-146`).

Kjøres **kun på forespørsel** (`workflow_dispatch`), ikke på den daglige
cronen — kartgeometri endres sjelden og krever tyngre avhengigheter
(`requirements-gis.txt`, separat fra hoved-`requirements.txt`).

## 8. Forretningslogikk

### 8.1 Årlig overstyrer månedlig

Kjerneregelen i `build_combined()` (`scripts/update.py:144-208`, testet
`tests/test_parsers.py:431-448`): for et gitt felt+år,

- Hvis et **endelig årstall** finnes i `yearly.json` for det året → bruk det,
  `p: false` (ikke-foreløpig).
- Hvis **ikke** → aggreger (summer) de tilgjengelige månedsverdiene for det
  året fra `monthly.json` til ett årspunkt, `p: true` (foreløpig).

Måneds-oppløsningens datapunkter er **alltid** `p: true` uansett — kun
års-oppløsningen kan "modnes" fra foreløpig til endelig når årsfilen til
slutt dekker det året.

### 8.2 Eierskapsfordeling (`scripts/ingest_ownership.py:71-132`)

To transformasjonstrinn fra rått regneark til `ownership.json`:

1. **`build_area_shares()`** (linje 71-101): regnearket har én rad per
   (lisens, blokk, selskap) — samme (selskap, andel)-par gjentas for hver
   blokk i en lisens, så radene **dedupliseres per (lisens, Selskap)** før
   andeler summeres **per `Gruppe`** (konsern-nivå, ikke juridisk enhet — se
   Dan-eksempelet i §5.3 der to TotalEnergies-juridiske-enheter slås sammen
   til én andel). Hvis et lisensområde har vært tildelt under **flere
   lisensnavn over tid** (f.eks. Hejre, Solsort), vinner kun den **sist
   tildelte** lisensen (`max(..., key=lambda e: e["granted"])`) — eldre
   lisenser på samme område anses som **avløst, ikke tillegg**.
2. **`build_ownership()`** (linje 104-132): for hvert felt i `fields.json`,
   slå opp riktig lisensområde. De fleste felt bruker default
   `DUC_AREA = "Contiguous Area"` (den historiske "Sole Concession of 8 July
   1962"-hovedkonsesjonen). Et lite, hardkodet unntak,
   `FIELD_AREA` (linje 41-48), lister felt med **eget** lisensområde:
   `cecilie`, `lulita`, `nini`, `siri`, `solsort`, `syd_arne`. Valideres at
   andelene for hvert felt summerer til ~1.0 (±0.01) — ellers
   `SourceFormatError`.

Output-skjema:
```json
{ "schema_version": 1, "source": "https://ens.dk/en/energy-sources/danish-licences-and-licensees",
  "companies": ["BlueNord", "TotalEnergies", ...],
  "fields": { "dan": {"BlueNord": 0.368, "TotalEnergies": 0.432, "Nordsøfonden": 0.2}, ... } }
```

### 8.3 Idempotens som gjennomgående prinsipp

`write_json_stable()` (`scripts/common.py:160-174`) skriver **kun** filen på
disk hvis innholdet faktisk er endret (sammenlignet etter å ha fjernet
oppgitte `volatile_keys`, typisk et tidsstempel). Dette gjelder alle
genererte filer (`yearly.json`, `monthly.json`, `fields.json`,
`combined.json`, `ownership.json`, alle `*.geojson`). Konsekvens: en
pipeline-kjøring som ikke finner noe nytt, produserer **null git-diff og
ingen commit** — CI-jobben (`.github/workflows/monthly-update.yml`) bruker
nettopp dette til å committe kun ved faktisk endring.

### 8.4 Valideringsregler (`scripts/validate.py`)

Kjøres som siste steg i CI, etter at `combined.json` er bygget. Skiller
mellom `ERROR` (feiler jobben, exit-kode ≠ 0) og `WARN` (logges, stopper
ikke):

- **ERROR:** negativ måleverdi; duplikat `(felt, år[, måned])`-nøkkel;
  record uten `source_url`.
- **WARN:** hull i et felts månedlige tidsserie; avvik > 10 % mellom summen
  av 12 månedsverdier og det tilsvarende årstallet (testet
  `tests/test_parsers.py:493-508`); fysisk usannsynlige magnituder (samme tak
  som §4.3: olje/gass 5000, vann 50000, `scripts/validate.py:31`).

## 9. Frontend

To JS-filer, ingen rammeverk, ingen bundling, lastet direkte som
`<script>`-tagger fra `docs/index.html:94-98`.

### 9.1 `docs/app.js` — tidsseriegraf

- Laster `data/combined.json` og `data/ownership.json` via `fetch()`
  (`load()`, linje 128-145). **Fallback-kjede:** hvis `combined.json` mangler
  eller har en tom `series`, faller den tilbake til
  `data/combined.sample.json` (**syntetisk demodata**) og viser banneret
  `#sample-banner` (`docs/index.html:25-27`, fjernet fra `hidden` i
  `docs/app.js:137`). Ownership følger samme mønster uavhengig
  (`docs/app.js:130-138`): ekte `ownership.json` brukes hvis den finnes OG har
  et `fields`-objekt, ellers `ownership.sample.json` — merk at ekte
  `combined.json` + manglende ekte `ownership.json` er en gyldig, håndtert
  kombinasjon (sample-ownership brukes uten å utløse hele
  sample-databanneret).
- Chart-type: `"bar"` (`docs/app.js:461`, stolpediagram, ikke linjediagram
  til tross for hva eldre README-tekst antyder — se §11).
- Visninger: Totalt / Per felt / Per selskap, år/måned-oppløsning, valgfri
  vannlinje på sekundærakse. Per-selskap-visningen bruker
  `ownership.json`-andelene til å fordele hvert felts olje+gass-produksjon
  proporsjonalt på eierselskap.
- Eksport til Excel av de **viste** tallene (ikke hele datasettet) via
  `xlsx.js`, ingen server involvert.
- Ingen bygge-/kompileringssteg: filen er den samme koden som kjører i
  nettleseren, uendret.

### 9.2 `docs/map.js` — feltkart (Leaflet)

- Pakket i en IIFE (linje 19-21) for å unngå globale navnekollisjoner med
  `app.js` i samme side.
- Basiskart: **OpenStreetMap** (ikke en nøkkel-krevende tjeneste) — mørkt
  tema simuleres med et **CSS-filter** på flisene
  (`invert(1) hue-rotate(180deg) brightness(0.95) contrast(0.9)
  saturate(0.7)`, `docs/style.css:153`) siden OSM bare tilbyr én (lys)
  flisestil, ikke ved å bytte til en andre, nøkkelgated leverandør.
- `indexProduction()` (linje 113-168) beregner per-felt `oilShare`,
  kumulativ o.e. (`oeCumMBbl`), o.e.-rate (`oeRateMboepd`), og 12-måneders
  o.e.-snitt (`oeAvg12Mboepd`) direkte fra `combined.json` — samme
  BOE-faktor og logikk som i `app.js` (duplisert, ikke delt modul, siden
  filene bevisst ikke deler moduler for å unngå en byggeprosess).
- **Enkelt fast fyllfarge** for alle produserende felt (CSS-variabelen
  `--oil`) og én fast grå (`--c-other`) for felt uten data —
  `fieldStyle()` (linje 175-192). Grensefarge er alltid fast hvit
  (`FIELD_BORDER = "#fff"`, linje 174) uansett feltets produksjonstall — det
  er **ikke** en gradient/heatmap basert på produksjonsvolum.
- `ownershipRow(slug)` (linje 196-203): slår opp `OWN.fields[slug]`, sorterer
  synkende på andel, formaterer som "Selskap X.X %"-liste i popup-boksen; hvis
  feltet ikke har eierskapsdata, returneres en tom streng og raden utelates
  helt fra popup-en (ingen "ukjent"-placeholder).
- Bootrekkefølge i `main()` (linje 302-370): opprett Leaflet-kart → sett
  flislag → koble `ResizeObserver` → last `combined.json` → **last
  `ownership.json` FØR feltlaget bygges** (kritisk: `bindPopup()` rendrer
  popup-HTML-en **eagerly én gang** når laget bygges, ikke lat ved klikk —
  usortert rekkefølge her ville gitt popups uten eierskapsdata selv om
  dataen fantes) → last `fields.geojson` og bygg feltlag med popups → last
  `blocks.geojson` (alltid synlig grunnrutenett) → bygg
  feltnavn-etikett-laget.
- Deler tema (lyst/mørkt) og data med resten av siden via samme
  `docs/style.css`-variabler og samme `combined.json`.

### 9.3 Layout og tema

`docs/style.css` definerer et lyst/mørkt fargesett via CSS custom
properties, styrt av `prefers-color-scheme` **og** en manuell
`data-theme`-override (toggle-knapp i header). Ren CSS grid/flexbox-layout,
ingen CSS-rammeverk.

## 10. Datavolum

Alle tall verifisert direkte mot repoet 2026-09-13:

| Størrelse | Verdi |
|---|---|
| `data/`-katalog totalt | 11 MB |
| `docs/`-katalog totalt | 2.3 MB |
| Rå kildefiler cachet (`data/sources/raw/`) | 106 filer |
| `data/yearly.json` records | 1113 |
| `data/monthly.json` records | 1284 |
| Felt i `data/fields.json` | 21 |
| Serier i `data/combined.json` | 22 (21 felt + `_total`) |
| Kjente månedlige kilde-URL-er i `data/sources/index.json` | 103 |
| GeoJSON-features (fields/blocks/licences/installations/wells) | 34/785/20/66/371 |
| Git-commits totalt | 49 |
| Repohistorikk | 2026-07-21 → 2026-09-10 (siste commit) |

Vekstrate: månedlig cron legger til ≤ 1 ny månedsrapport-fil (typisk noen
titalls KB rå + noen KB delta i JSON) per kjøring som finner noe nytt;
årsfilen erstattes i sin helhet årlig. Ingen historisk sletting — alle rå
kilder beholdes for reproduserbarhet (`data/sources/raw/` og
`data/sources/gis/raw/` committes i sin helhet, ikke `.gitignore`-et).

## 11. Fallgruver og kjente problemer

Dette er det viktigste avsnittet for en reimplementasjon — det forklarer
**hvorfor** koden er som den er der det ikke er opplagt fra strukturen alene.

**1. Landingssiden må force-refreshes, ellers stopper hele pipelinen
stille.** `scripts/update.py:292` kaller
`BI.build_index(..., force=True)` spesifikt for landingssiden. Uten dette
ville `Fetcher`s normale cache-oppførsel (hent én gang, gjenbruk for alltid)
gjøre at crawleren for alltid ser den første nedlastede versjonen av
listesiden og aldri oppdager nye måneder — **uavhengig av hvor ofte
cron-jobben trigges**. Dette var en reell bug i denne kodebasen tidligere i
prosjektets levetid (bekreftet via flere påfølgende GitHub Actions-kjøringer
som alle rapporterte identisk `"102 reports, last=2026-06"` før fiksen, og et
annet tall umiddelbart etter). En reimplementasjon MÅ skille mellom
"cache innholdet til en spesifikk kildefil" (ønskelig — den filen endres
aldri i ettertid) og "cache listen over hvilke kildefiler som finnes"
(må friskes opp hver kjøring).

**2. Kolonnevalg i stablede blokker: "Monthly" er riktig, "Daily Avg." og
kumulative kolonner er feil, og de ligner hverandre.** Se §4.3. Enhver ny
implementasjon som "gjenoppfinner" kolonneparsingen fra en rask titt på et
ekte dokument risikerer å plukke feil kolonne, siden alle fire
(Monthly/Daily Avg/This month last year/kumulativ) er tall i samme
størrelsesorden og sitter tett i samme blokk.

**3. Formatgjetting fra filendelse er utilstrekkelig; faktisk
byte-sniffing er nødvendig.** ENS bruker generiske `/media/<id>/download`-
URL-er for nyere filer der endelsen ikke framgår av URL-en i det hele tatt.
`_sniff_kind()` (`scripts/update.py:44-57`) sjekker derfor de faktiske
nedlastede bytene (magic bytes / HTML-tag-tilstedeværelse), ikke bare
`build_index.py`s URL-baserte gjetning — de to kan komme til forskjellig
konklusjon, og byte-sniffingen vinner.

**4. HTML-rapportene er cp1252-kodet, ikke UTF-8 eller ISO-8859-1 (selv om
sistnevnte ofte antas for danske dokumenter).** `_decode_html()`
(`scripts/update.py:72-79`) prøver utf-8 → cp1252 → latin-1 i den
rekkefølgen. Feil rekkefølge/manglende cp1252-forsøk gir ikke en synlig feil
— det gir stille korrupte tegn i feltnavn med æ/ø/å, som deretter kan
normaliseres til feil eller tomme slugs.

**5. `first_year`/`last_year` i `fields.json` er kildetilstedeværelse, ikke
produksjonsstart/-slutt.** Se konkret Gorm-eksempel i §5.1. En
reimplementasjon som bruker disse feltene til å f.eks. filtrere en tidslinje
til "år med faktisk produksjon" vil få feil resultat uten en egen sjekk mot
faktiske verdier > 0.

**6. [FIKSET] `south_arne` var en død operatør-nøkkel.**
`_KNOWN_OPERATORS` i `scripts/ingest_yearly.py` inneholdt tidligere både
`"south_arne": "Hess"` og `"syd_arne": "Hess"`. Verifisert: den reelle
årsfilen bruker konsekvent det danske navnet "Syd Arne", som
`normalize_field()` alltid slugger til `syd_arne` — aldri `south_arne`
(bekreftet: `data/fields.json` inneholder `syd_arne` med eneste alias
`"Syd Arne"`; `south_arne` fantes ikke som nøkkel i produksjonsdataene i det
hele tatt). Nøkkelen kunne derfor aldri treffes fra den ekte årsfilen — i
motsetning til den analoge `FIELD_SLUG_ALIASES`-oppføringen i
`scripts/build_gis.py:85`, som **er** aktivt i bruk, fordi ENS' *kart*-lag
faktisk bruker det engelske navnet "South Arne" i sine feltetiketter (i
motsetning til årsfilen). Konsekvensen var ufarlig (samme operatør-verdi
ville blitt satt uansett via `syd_arne`-nøkkelen), men den døde nøkkelen er
nå fjernet fra `_KNOWN_OPERATORS`. De to separate normaliserings-/
alias-tabellene (én for årsfil-operatører, én for kart-feltnavn) er
fortsatt ikke utledet fra samme kilde og kan divergere igjen over tid — det
opprinnelige poenget står ved lag som en generell fallgruve, selv om dette
konkrete symptomet er ryddet opp.

**7. [FIKSET] Eierskap manglet et friskhets-signal.** Se §4.5. Skriptet
kjøres fortsatt manuelt, ikke på cron, men `ingest_ownership.py` skriver nå
et `generated_at`-tidsstempel (UTC, samme mønster som `build_gis.py` bruker
for kartgeometri) inn i `data/ownership.json`, deklarert som en `volatile_key`
i `write_json_stable()`-kallet slik at det ikke i seg selv trigger en commit
når de underliggende andelene er uendret. Frontend viser nå denne datoen:
`docs/app.js` (i "Per selskap"-visningens bildetekst) og `docs/map.js` (i
kartpanelets `#map-note`, sammen med geometri-datoen). Et utdatert
eierskapsdatasett er dermed synlig i UI-en i stedet for stille.

**8. Måneds- vs. årstall kan avvike opptil 10 % uten at pipelinen stopper.**
Dette er en bevisst designbeslutning (`scripts/validate.py`, WARN ikke
ERROR) fordi foreløpige månedsestimater **forventes** å avvike noe fra
endelige årstall — men betyr at en reimplementasjon som ønsker strengere
konsistensgarantier må legge til egen logikk.

**9. GIS-feltantall (34) og produksjonsfelt-antall (21) er bevisst
forskjellige og skal ikke tvinges til å matche.** Se §7. `ravn` er et kjent
eksempel på et produksjonsfelt uten kartpolygon.

**10. Fixtures for parserne er hovedsakelig syntetiske, ikke ekte
nedlastinger** (`tests/fixtures/README.md`) — bortsett fra
`monthly_real_2018_01.htm` og `monthly_real_2024_01.pdf`, som er ekte
fanget ENS-dokumenter. De syntetiske fixturene tester de enklere
fallback-layoutene (matrise/tabell-orientering) som i praksis **ikke**
forekommer i den virkelige kilden per i dag — det virkelige formatet er
alltid "stacked block" (§4.2, §4.3). En reimplementasjon bør prioritere å
støtte stacked-block-layouten korrekt over de andre, siden det er det eneste
formatet som faktisk observeres i produksjon.

**11. [FIKSET] `docs/app.js` viser alltid stolpediagram ("bar"), aldri
linje** — `README.md` beskrev det tidligere som et "linjediagram med
Chart.js" og som "fargelagt etter akkumulert produksjon" for kartet; begge
formuleringene var **ikke lenger** i tråd med koden
(`docs/app.js:461`, `type: "bar"`; `docs/map.js:174-192`, fast fyllfarge, se
§9.2) og er nå rettet i README. Dette var et konkret eksempel på nettopp den
typen README/kode-avvik denne dokumentasjonen er skrevet for å unngå å
videreføre. Ved samme anledning ble det oppdaget at `p`
(foreløpig-flagget)/"stiplet"-beskrivelsen i §5.1 hadde samme feilkilde — den
er også rettet der, med korrekt referanse til hvordan `p` faktisk brukes
(kun tooltip + bildetekst, ikke en egen stolpestil). **[FIKSET]** kartets
fargeforklaring i README («fargelagt etter akkumulert produksjon») hadde
samme feilkilde og er nå også rettet til å beskrive den faktiske, faste
to-fargestilen (§9.2).

**12. Ingen ekte maskinlesbar kilde-API finnes.** Alt er HTML-scraping av en
side laget for mennesker (se §3). Enhver strukturendring på ENS' side kan i
verste fall kreve kodeendringer i `build_index.py`s ankermønstre.

**USIKKER:** Jeg har ikke funnet et scenario i koden som eksplisitt håndterer
at ENS *fjerner* en tidligere publisert månedsrapport eller endrer en
allerede committet fil sin URL uten å endre innholdet — `build_index()`
bevarer status for URL-er "som fortsatt finnes" (§4.1), men det er ikke
verifisert hva som skjer med en post i indeksen hvis URL-en forsvinner helt
fra landingssiden (bevares den for alltid, eller fjernes den ved neste
crawl?). Dette bør testes eksplisitt før en søsterløsning stoler på samme
oppførsel.

## 12. Lisens og attribusjon

- **Kildedata:** Energistyrelsen (ENS), offentlige danske data. Nettstedet
  krediterer ENS eksplisitt i footer (`docs/index.html:90`) og i
  sideoverskriften (`docs/index.html:16-18`).
- **[FIKSET] `LICENSE`-fil finnes nå i repo-roten.** Den gjengir ENS' egne
  opphavsrettsvilkår for ens.dk verbatim (hentet fra siden «Om ens.dk»,
  seksjonen «Ophavsret», <https://ens.dk/om-os/om-ensdk>, bekreftet direkte
  mot den live siden i denne gjennomgangen): materiale kan kopieres
  vederlagsfritt med kildehenvisning til Energistyrelsen, innholdet må ikke
  endres/forvanskes, og bilder/figurer/illustrasjoner/logo er unntatt og
  krever egen tillatelse. `LICENSE`-filen presiserer eksplisitt at dette
  dekker **kildedataene** (`data/`, `docs/data/`, inkl. shapefil-rådata og
  eierskapstall), ikke prosjektets egen kildekode — kildekodens lisens er
  fortsatt et **åpent, uavklart spørsmål** som bør besluttes eksplisitt før
  sammenslåing med søsterløsningene (spesielt hvis de har ulike
  kodelisenser).
- **[FIKSET] GIS-shapefiler:** README hevdet tidligere at ENS-shapefilene
  «ingen redistribusjonsvilkår har» — dette var en uverifisert påstand.
  README (`README.md:358-361`) og `LICENSE` sier nå i stedet at ENS ikke
  publiserer egne, separate vilkår for shape-fil-siden spesifikt, og at de
  generelle ens.dk-vilkårene (attribusjon + uendret innhold, se over) derfor
  legges til grunn også for `data/sources/gis/raw/*.zip` og det avledede
  GeoJSON-et. Dette er fortsatt en **tolkning** (ENS har ikke eksplisitt
  bekreftet at shape-fil-siden er dekket av nøyaktig den samme teksten), men
  er nå basert på en faktisk, bekreftet kildetekst i stedet for en
  ubegrunnet påstand.
- **[FIKSET] Kartbakgrunn:** README nevnte tidligere CARTO ved siden av
  OpenStreetMap i denne "Lisens"-paragrafen; CARTO ble erstattet av
  OpenStreetMap tidligere i prosjektets historie (§9.2 beskriver den
  faktiske, gjeldende implementasjonen), og README er nå rettet til å bare
  nevne OpenStreetMap.
- **Eierskapsdata:** avledet fra en manuelt vedlikeholdt eksport av ENS'
  offentlige "Danish Licences and Licensees"-oppslagsverktøy
  (`https://ens.dk/en/energy-sources/danish-licences-and-licensees`) — samme
  offentlige-data-status som produksjonstallene, og dekket av samme
  `LICENSE`-vilkår. **[FIKSET, se §11 punkt 7]** filen har nå et
  `generated_at`-tidsstempel og vises i UI, så alderen på eierskapsdataene
  ikke lenger er usporbar slik den var da dette dokumentet først ble skrevet.
