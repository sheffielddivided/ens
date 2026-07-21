# Test fixtures

These files exercise the parsers **without touching the network**. Tests must
never depend on ens.dk being reachable.

## Status: synthetic vs. captured

The fixtures currently committed here are **synthetic** — hand-written to mirror
the *structure* of the real ENS documents (same table shapes, header vocabulary,
unit strings and field names), not copies of real ENS files. They exist so the
parser logic and tests are meaningful before the pipeline has run against the
live site.

They should be **replaced with real captures** the first time the pipeline
successfully downloads real documents (the workflow caches every download under
`data/sources/raw/`). To promote a real capture to a fixture:

```bash
cp data/sources/raw/<the-real-file> tests/fixtures/<descriptive-name>
```

then update the expected values in `tests/test_parsers.py` to match the real
numbers and commit both together.

## Files

| File | Real? | Purpose |
|------|-------|---------|
| `landing_page.html` | synthetic | Simple landing-page structure for `build_index.py` |
| `landing_page_real.html` | real-shaped | ENS SI/Oil-Field-Units table + `/media/<id>/download` links |
| `monthly_v1.html` | synthetic | Simple measure-as-columns HTML report |
| `monthly_v2.html` | synthetic | Reordered-columns / colspan HTML report |
| `monthly_real_2018_01.htm` | **real** | Real ENS HTML report (stacked blocks, cp1252) |
| `monthly_real_2024_01.pdf` | **real** | Real ENS PDF report (stacked blocks, split tables) |
| `monthly.pdf` | synthetic | Simple PDF report (`make_pdf_fixture.py`) |
| `yearly.xlsx` | synthetic | Simple per-measure-sheet workbook (`make_xlsx_fixture.py`) |

The `monthly_real_*` files are genuine ENS documents captured by the pipeline
(under `data/sources/raw/`) and used to test the real stacked-block layout with
spot-checked values. The synthetic fixtures additionally exercise the simpler
fallback layouts.

The `.pdf` and `.xlsx` binaries are produced by the small generator scripts in
this directory so the repository stays text-diffable and the exact fixture
content is reviewable. Run them to regenerate:

```bash
python tests/fixtures/make_xlsx_fixture.py
python tests/fixtures/make_pdf_fixture.py
```
