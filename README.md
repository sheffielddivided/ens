# Dansk olje-, gass- og vannproduksjon (ENS)

Selvoppdaterende datasett over produksjon av olje, gass og vann fra danske
olje- og gassfelt i Nordsjøen, basert på offentlige data fra
[Energistyrelsen (ENS)](https://ens.dk/en/energy-sources/monthly-and-yearly-production).
Data lagres som JSON i repoet, en GitHub Actions-jobb henter nye månedsdata
automatisk, og en statisk webside på GitHub Pages visualiserer dataene.

## Innhold
- [Status og datainnhenting](#status-og-datainnhenting)
- [Datakilder](#datakilder)
- [Arkitektur](#arkitektur)
- [Datamodell](#datamodell)
- [Enheter](#enheter)
- [Forbehold](#forbehold)
- [Kjøre lokalt](#kjøre-lokalt)
- [Tester](#tester)
- [GitHub Actions](#github-actions)
- [GitHub Pages](#github-pages)
- [Kart (GIS)](#kart-gis)
- [Eierskap](#eierskap)

## Status og datainnhenting

Selve **datainnhentingen kjører i GitHub Actions**, der `ens.dk` er tilgjengelig.
Kildedata og de avledede JSON-filene under `data/` produseres og committes av
oppdateringsjobben – de sjekkes altså inn av automatikken, ikke for hånd.

Første gang du vil fylle datasettet: kjør workflowen manuelt
(`Actions → Monthly production update → Run workflow`). Den crawler hovedsiden,
laster ned årsfilen og alle månedsrapporter, parser dem, validerer og committer
resultatet.

> **Merk om byggemiljøet:** i enkelte sandkasse-/CI-miljøer er `ens.dk`
> blokkert av nettverkspolicy. Skriptene er derfor skrevet slik at de kan
> kjøres helt uten nett (`--offline`) mot hurtiglageret i `data/sources/raw/`,
> og testene kjører utelukkende mot fixtures. Nettavhengige steg feiler med en
> tydelig melding i stedet for å skrive tvilsomme data.

Websiden faller tilbake til et tydelig merket **syntetisk demodatasett**
(`docs/data/combined.sample.json`) inntil reelle data er hentet, slik at
grafene kan vises umiddelbart.

## Datakilder

Alt lenkes fra hovedsiden
<https://ens.dk/en/energy-sources/monthly-and-yearly-production>:

1. **Årlig Excel-fil (1972–):** «Yearly production, injection, flare, fuel and
   export in SI units». Den autoritative historikken.
2. **Månedlige produksjonsrapporter (ca. jan 2018 →):** SI Units-varianten (ikke
   Oil Field Units). HTML-sider fram til ca. 2023, deretter PDF. Nøyaktig hvor
   skiftet skjer **utledes fra lenkene** (`.pdf`-endelse ⇒ PDF), ikke antatt.
3. Månedstallene er **foreløpige** estimater; årsfilen overstyrer dem alltid.

`build_index.py` bygger en komplett liste over alle kilde-URLer med måned/år og
format i `data/sources/index.json`, slik at senere kjøringer kan sammenligne mot
den og bare hente det som er nytt.

## Arkitektur

```
scripts/
  common.py             # delt: HTTP-klient (høflig, cachende), atomisk/idempotent
                        #   JSON, feltnormalisering, tallparsing
  build_index.py        # crawler hovedsiden → data/sources/index.json
  ingest_yearly.py      # årlig Excel → data/yearly.json + data/fields.json
  monthly_common.py     # delt tabell-logikk for måneds-parserne (kolonnekartlegging)
  parse_monthly_html.py # én HTML-månedsrapport → records
  parse_monthly_pdf.py  # én PDF-månedsrapport → records
  update.py             # orkestrator: hent manglende måneder, bygg combined.json
  validate.py           # kvalitetskontroll av hele datasettet
  build_gis.py          # ENS-shapefiler → reprojisert, forenklet GeoJSON (kart)
  ingest_ownership.py   # manuell lisensiar-eksport (xlsx) → data/ownership.json
data/
  sources/index.json    # alle kjente kilde-URLer + status (pending/ok/failed)
  sources/raw/          # rå nedlastede filer (xlsx/html/pdf), committes for reproduserbarhet
  sources/gis/raw/      # rå ENS-shapefiler (.zip), committes for reproduserbarhet
  sources/licences/     # manuelt vedlikeholdt lisensiar-eksport (xlsx), se ingest_ownership.py
  yearly.json           # alle årsdata (endelige)
  monthly.json          # alle månedsdata (foreløpige)
  fields.json           # metadata per felt (navn, aliaser, operatør, år-spenn)
  combined.json         # sammenslått tidsserie klar for frontend
  ownership.json        # eierskapsandel per felt/selskap (manuelt vedlikeholdt kilde)
docs/                   # GitHub Pages-rot
  index.html app.js     # datautforsker (Chart.js) – alltid olje+gass i 1000 fat o.e./dag
  map.js                # feltkart (Leaflet), innbakt i index.html mellom diagrammene
  data/gis/*.geojson    # avledede kartlag (WGS84), inkl. *.sample.geojson
tests/                  # pytest mot fixtures i tests/fixtures/
.github/workflows/monthly-update.yml   # daglig dataoppdatering
.github/workflows/build-gis.yml        # kartlag på forespørsel (workflow_dispatch)
```

Alle skript kjøres fra repo-rot og har `--help`.

### Robust parsing framfor gjetting

Parserne er **struktur-oppdagende**: de finner kolonner/ark ved å matche
overskrifts-vokabular (olje/olie, gass/gas, vann/vand, feltnavn, enheter) i
stedet for å hardkode posisjoner. Det gjør at samme kode tåler at kolonner
bytter rekkefølge eller at layout endres over årene. Hvis en kilde ikke lar seg
tolke, feiler parseren med `SourceFormatError` og en beskrivende melding –
aldri stille skriving av tvilsomme data.

## Datamodell

Konsekvente feltnavn og SI-enheter overalt. Feltnavn normaliseres til en stabil
slug (f.eks. `Dan`, `DAN`, `Dan Field` → `dan`; `Halfdan*` → `halfdan`), og
aliaser vedlikeholdes i `fields.json`.

**`monthly.json`** – én post per felt per måned:

```json
{
  "schema_version": 1,
  "unit_definitions": { "oil": "1000 m3", "gas": "mio. Nm3", "water": "1000 m3" },
  "last_updated": "2026-07-21T06:00:00Z",
  "records": [
    {
      "field": "dan", "year": 2025, "month": 3,
      "oil": 123.4, "gas": 56.7, "water": 890.1,
      "preliminary": true,
      "source_url": "https://ens.dk/...",
      "retrieved_at": "2026-07-21T06:00:00Z"
    }
  ]
}
```

**`yearly.json`** har samme struktur uten `month` og med `preliminary: false`.
Ekstra kolonner som årsfilen tilbyr (injeksjon, fakling, brensel, eksport) tas
med når de finnes: `gas_injection`, `water_injection`, `flare`, `fuel`,
`gas_export`, `oil_export`. Olje/gass/vann er minimum.

**`fields.json`** – metadata per felt: `slug`, `display_name`, `aliases`,
`first_year`, `last_year`, `operator` (best-effort; `null` når ukjent).

**`combined.json`** – avledet, bygges alltid på nytt av `update.py`:

```json
{
  "schema_version": 1,
  "unit_definitions": { ... },
  "measures": ["oil", "gas", "water"],
  "last_updated": "2026-07-21T06:00:00Z",
  "fields": [ { "slug": "_total", "display_name": "Alle felt" }, { "slug": "dan", ... } ],
  "series": {
    "dan": {
      "yearly":  { "oil": [ { "t": "2020", "v": 300.0, "p": false } ], "gas": [...], "water": [...] },
      "monthly": { "oil": [ { "t": "2025-03", "v": 123.4, "p": true } ], ... }
    },
    "_total": { "yearly": {...}, "monthly": {...} }
  }
}
```

- Hvert punkt har `t` (tidspunkt: `YYYY` eller `YYYY-MM`), `v` (verdi) og `p`
  (`true` = foreløpig, tegnes stiplet i grafen).
- I **års-oppløsning** gjelder endelige årstall fra `yearly.json` foran
  aggregerte måneds-estimater. Et år som bare finnes i månedsdata blir et
  foreløpig, aggregert punkt.
- `_total`-serien er summert over alle felt.

Alle records sorteres deterministisk (felt, år, måned) for lesbare git-differ,
og filer skrives atomisk og **idempotent** (uendret data ⇒ uendret fil ⇒ ingen
commit). Revisjon av en allerede hentet måned overskriver posten og oppdaterer
`retrieved_at`.

## Enheter

| Serie | Enhet            |
|-------|------------------|
| Olje  | `1000 m3`        |
| Gass  | `mio. Nm3`       |
| Vann  | `1000 m3`        |

`unit_definitions` er de **dokumenterte** SI-enhetene for «SI Units»-rapportene.
Parserne leser i tillegg kildens egne enhetsstrenger fra overskriftene, og
logger en `WARN` dersom de avviker fra det forventede – enhetene fastsettes
altså ikke ved gjetting, og avvik overses ikke stille. Bekreft alltid enhetene
mot kildedokumentet før du stoler på nye tall (se WARN-linjer i loggen).

## Forbehold

- **Foreløpige tall:** månedsdata er estimater (`preliminary: true`) og
  overstyres av årsfilen i `combined.json`.
- **Formatskifte HTML→PDF:** utledes fra lenkene per rapport, ikke fra dato.
- **Feltnavn** varierer i skrivemåte over tiår; de normaliseres til slug og
  aliaser samles i `fields.json`.
- **Validering logger, men stopper ikke** på avvik mellom måneds-sum og årstall
  (±10 %) eller på hull i tidsserien; slike ting er WARN, ikke ERROR.
- **Fixtures er syntetiske:** filene i `tests/fixtures/` etterligner *strukturen*
  til de virkelige ENS-dokumentene, men er ikke kopier. Bytt dem ut med ekte
  nedlastinger fra `data/sources/raw/` når pipelinen har kjørt (se
  `tests/fixtures/README.md`).

## Kjøre lokalt

```bash
python -m pip install -r requirements.txt

# 1) Bygg kildeindeksen (crawler hovedsiden)
python scripts/build_index.py

# 2) Hent og parse årsfilen  (--inspect skriver ut arkstrukturen først)
python scripts/ingest_yearly.py
python scripts/ingest_yearly.py --inspect        # kun struktur, skriver ikke

# 3) Hent manglende måneder og bygg combined.json (+ kopi til docs/data/)
python scripts/update.py

# 4) Valider hele datasettet
python scripts/validate.py

# Én enkelt rapport, frittstående:
python scripts/parse_monthly_html.py --url <url> --year 2019 --month 5
python scripts/parse_monthly_pdf.py  --dump --file rapport.pdf   # rå tabeller
```

Nyttige flagg: `--offline` (kun hurtiglager, aldri nett), `--no-crawl` (bruk
eksisterende indeks), `--refresh-yearly`, `--force` (hent alle måneder på nytt
for å fange revisjoner). Alle skript høflig mot kilden: ≤ 1 forespørsel/sekund,
beskrivende User-Agent, og alt som lastes ned caches i `data/sources/raw/`.

## Tester

```bash
python -m pytest
```

Testene kjører **kun mot fixtures**, aldri mot nettet. De binære fixturene
(`yearly.xlsx`, `monthly.pdf`) genereres av små skript i `tests/fixtures/`
(`make_xlsx_fixture.py` krever `openpyxl`, `make_pdf_fixture.py` krever
`reportlab` – kun for regenerering, ikke en kjøretidsavhengighet).

## GitHub Actions

`.github/workflows/monthly-update.yml`:

- **Trigger:** den 5., 15. og 25. hver måned (publiseringsdato varierer) samt
  `workflow_dispatch` for manuell kjøring (med valg for `refresh_yearly` og
  `force`).
- **Steg:** checkout → installer avhengigheter → `pytest` → `update.py` →
  commit & push av `data/` + `docs/` **kun ved endring** → `validate.py`.
- **Idempotent:** null nye rapporter ⇒ ingen diff ⇒ ingen commit.
- **Ved feil** i parsing eller validering: jobben oppretter (eller kommenterer
  på) et GitHub Issue merket `pipeline-failure` med loggutdraget, og feiler
  synlig. Måneder som ikke lar seg parse markeres `"status": "failed"` i
  indeksen og forsøkes på nytt neste kjøring – én feilende måned stopper ikke
  resten.

Workflowen trenger standard `GITHUB_TOKEN` med `contents: write` og
`issues: write` (satt i `permissions:`-blokken).

## GitHub Pages

Aktiver Pages fra `docs/`-mappen:

1. `Settings → Pages`
2. `Source: Deploy from a branch`
3. Velg branch (f.eks. `main`) og mappe `/docs`.

Websiden laster `data/combined.json` og `data/ownership.json` med `fetch`.
Funksjoner: velg felt (eller «Alle felt»), veksle mellom Totalt/Per
felt/Per selskap, veksle måned/år-oppløsning, en valgfri vannproduksjonslinje,
linjediagram med Chart.js der foreløpige tall tegnes stiplet, og «sist
oppdatert» hentet fra dataene. Ren HTML/CSS/JS, ingen byggesteg, norsk UI.

Ved siden av produksjonsgrafen ligger et Leaflet-kart over feltene (bygget av
`map.js`), fargelagt etter akkumulert produksjon, med en alltid synlig
blokkrutenett-bakgrunn og en «Feltnavn»-av/på-knapp i kartpanelets hode.
Kartet deler tema (lyst/mørkt) og databehov med resten av siden.

## Kart (GIS)

Et kart over feltene, bygget etter samme mønster som resten: et Python-skript
gjør om ENS sine shapefiler til GeoJSON som committes, og Leaflet tegner
lagene direkte i datautforskeren (`docs/map.js`). Ingen server, ingen byggesteg.

**Datakilde og projeksjon.** ENS publiserer kartdata som shapefiler:
<https://ens.dk/en/energy-sources/oil-and-gas-related-data/shape-files-oil-and-gas-maps>.
Lagene ligger i **ulike koordinatsystem** – feltavgrensninger, lisenser og
installasjoner i UTM 31 N / ED50 (`EPSG:23031`), blokker i UTM 32 N / ED50
(`EPSG:23032`), og brønner i geografiske ED50-koordinater (`EPSG:4230`). Webkart
krever lengde/breddegrad på WGS84 (`EPSG:4326`, det eneste GeoJSON tillater,
RFC 7946), så hvert lag **reprojiseres** – en reell datumtransformasjon
(ED50→WGS84 forskyver geometrien ~100 m), ikke bare en enhetsendring. Fordi
lagene varierer, er kildens egen `.prj` **fasit** per lag; `pyproj` gjør
transformasjonen (fallback `EPSG:23032` bare hvis en `.prj` mangler).

**Lag.** Fem shapefiler → `docs/data/gis/`:

| Fil | Lag | Geometri |
| --- | --- | --- |
| `fields.geojson`        | feltavgrensninger (fargelagt etter produksjon) | polygon |
| `licences.geojson`      | tildelte lisensområder                          | polygon |
| `blocks.geojson`        | blokk-/kvadrantrutenett                          | polygon |
| `installations.geojson` | offshore-installasjoner                          | punkt   |
| `wells.geojson`         | lete- og vurderingsbrønner                       | punkt   |

**Pipeline.** `scripts/build_gis.py`:

- leser shapefiler (`.zip`/`.shp`) fra `data/sources/gis/raw/` (via `pyshp`),
- forenkler polygoner (Douglas–Peucker, standard 50 m i kildens meter, via
  `shapely`) og reprojiserer til WGS84,
- for feltlaget: normaliserer navnet til **samme slug** som produksjonsdataene
  slik at kartet kan kobles til `combined.json`. Avvik forsones eksplisitt –
  «- N/NN part»-tillegg fjernes, og et par kjente navn aliases (South Arne =
  Syd Arne, Tyra Southeast = Tyra SE),
- fjerner støykolonner (`Shape_Area`, `FID`, dupliserte koordinatfelt),
- runder koordinater og skriver `<lag>.geojson` **idempotent**
  (uendret geometri ⇒ ingen diff).

## Eierskap

«Per selskap»-visningen i datautforskeren fordeler hvert felts olje+gass-
produksjon på eierselskap etter lisensandel. ENS publiserer ikke dette som en
nedlastbar tabell – siden <https://ens.dk/en/energy-sources/danish-licences-and-licensees>
er et interaktivt oppslagsverktøy, ikke en fil – så `scripts/ingest_ownership.py`
leser i stedet en **manuelt vedlikeholdt eksport**
(`data/sources/licences/danske_lisenser_komplett.xlsx`, én rad per
lisens/blokk/selskap) og skriver `data/ownership.json`. Oppdater eksporten og
kjør skriptet på nytt når eierskap endrer seg; det er ikke del av den daglige
automatiske jobben.

To forenklinger er verdt å kjenne til:

- De fleste DUC-feltene (Dan, Gorm, Halfdan, Tyra, Skjold, …) har ingen egen
  lisensrad i eksporten – de dekkes av hovedkonsesjonen «Sole Concession of 8
  July 1962». Alle felt som ikke er nevnt eksplisitt i `FIELD_AREA` i skriptet
  får derfor konsesjonens eierandeler som default.
- Noen felt er relisensiert over tid under mer enn ett lisensnavn med
  forskjellige eierandeler (Hejre, Solsort). Skriptet lar da den **sist
  tildelte** lisensen overstyre – den eldre anses som avløst, ikke tillegg.

Layer-rollen utledes fra filnavnet; feltnavn uten produksjonsmatch og
produksjonsfelt uten polygon logges som `WARN` i stedet for å gjettes (f.eks.
har `ravn` produksjon men ingen avgrensning i kilden).

```bash
pip install -r requirements-gis.txt          # pyshp + pyproj + shapely (ingen GDAL)

# Bygg fra shapefilene i data/sources/gis/raw/:
python scripts/build_gis.py

# …eller last ned først (lim inn zip-URL-ene fra ENS-siden):
python scripts/build_gis.py --url <zip-url> [--url …] --simplify 50
python scripts/build_gis.py --help
```

**Kjøres på forespørsel, ikke månedlig.** Kartgeometri endres sjelden og drar
inn tyngre avhengigheter, så dette ligger i en egen `workflow_dispatch`-jobb
(`Actions → Build GIS layers → Run workflow`) med egen `requirements-gis.txt` –
den daglige dataoppdateringen er urørt. Jobben kan enten laste ned URL-er du
oppgir, eller bygge fra committede filer, og committer resultatet under
`docs/data/gis/`.

**Lisens.** ENS-shapefilene har ingen redistribusjonsvilkår, så både rådataene
(`data/sources/gis/raw/*.zip`) og det avledede GeoJSON-et committes –
reproduserbart på linje med produksjonskildene. Kartet krediterer ENS og
bakgrunnskartets kilder (OpenStreetMap/CARTO).

**Demodata.** Til utvikling finnes `docs/data/gis/fields.sample.geojson`
(generert fra fixture-en `tests/fixtures/gis/fields.zip`). Skulle de ekte lagene
mangle, faller kartet tilbake til denne og viser et tydelig «syntetiske
demodata»-banner – akkurat som utforskeren.
