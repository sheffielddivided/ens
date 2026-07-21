# Prosjekt: Database for dansk olje-, gass- og vannproduksjon

Selvoppdaterende datasett over produksjon av olje, gass og vann fra danske
olje- og gassfelt, basert på offentlige data fra Energistyrelsen (ENS). Alt
kjører på GitHub: data lagres som JSON i repoet, en GitHub Actions-jobb henter
nye månedsdata automatisk, og en statisk webside på GitHub Pages visualiserer
dataene.

## Datakilder
Hovedside: <https://ens.dk/en/energy-sources/monthly-and-yearly-production>

1. **Årlig Excel-fil (1972–):** "Yearly production, injection, flare, fuel and
   export in SI units" — den autoritative historikken.
2. **Månedlige produksjonsrapporter (ca. jan 2018 →):** SI Units-varianten.
   HTML-sider fram til ca. 2023, deretter PDF. Nøyaktig skifte utledes fra
   lenkene, ikke antatt.
3. Månedstall er **foreløpige** estimater; årsfilen overstyrer dem alltid.

## Arkitektur (kort)
- `scripts/build_index.py` — crawler hovedsiden → `data/sources/index.json`.
- `scripts/ingest_yearly.py` — årlig Excel → `data/yearly.json` + `data/fields.json`.
- `scripts/parse_monthly_html.py` / `parse_monthly_pdf.py` — én månedsrapport → records.
- `scripts/update.py` — orkestrator: finn og hent manglende måneder, bygg `combined.json`.
- `scripts/validate.py` — kvalitetskontroll av hele datasettet.
- `scripts/common.py` — delt: HTTP-klient (høflig, cachende), atomisk JSON, feltnormalisering.

Alle skript kjøres fra repo-rot og har `--help`. Se `README.md` for full
dokumentasjon av datamodell, enheter, forbehold og hvordan alt kjøres.

## Arbeidsregler
- Høflig mot kilden: ≤ 1 forespørsel/sekund, beskrivende User-Agent, cache alt.
- Skriv aldri delvis oppdaterte filer (atomisk skriving), sorter records
  deterministisk, aldri skriv tvilsomme data stille.
- Ved uventet kildeformat: feil med en klar melding (`SourceFormatError`),
  ikke gjett.
- Tester kjører mot fixtures i `tests/fixtures/`, aldri mot nettet.
