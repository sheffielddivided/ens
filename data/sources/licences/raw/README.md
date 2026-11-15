# Kilder til eierhistorikk for danske lisenser

Rå, uendrede kopier av Energistyrelsens (ENS) sider, lagret for
reproduserbarhet (hentet 2026-11-15):

| Fil | Kilde | Innhold |
| --- | --- | --- |
| `licences_and_licensees.html` | <https://ens.dk/en/energy-sources/danish-licences-and-licensees> | Dagens lisenser med operatør og eierandeler per selskap (HTML, ingen nedlastbar tabell) |
| `amendments.html` | <https://ens.dk/en/energy-sources/amendments> | Endringer i lisensoversikten 2014–2026: fritekst ordnet etter dato |
| `earlier_amendments.pdf` | <https://ens.dk/media/5083/download> | Endringer februar 1999 – desember 2013: fritekst ordnet etter dato (37 sider) |

Begge endringskildene er fritekst, ikke tabeller: overføringer, tildelinger,
tilbakeleveringer, navnebytter og forlengelser i samme strøm. Eierhistorikken
bygges derfor fra en kuratert fil (`../ownership_history_curated.json`) med
ordrette sitater fra disse kildene; `scripts/ingest_ownership_history.py`
kontrollerer at hvert sitat faktisk står her.
