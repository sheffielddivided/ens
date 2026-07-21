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

| File | Purpose |
|------|---------|
| `landing_page.html` | Landing-page structure for `build_index.py` |
| `monthly_v1.html` | HTML monthly report, early layout |
| `monthly_v2.html` | HTML monthly report, later layout (reordered columns) |
| `monthly.pdf` | PDF monthly report (generated from `make_pdf_fixture.py`) |
| `yearly.xlsx` | Yearly Excel workbook (generated from `make_xlsx_fixture.py`) |

The `.pdf` and `.xlsx` binaries are produced by the small generator scripts in
this directory so the repository stays text-diffable and the exact fixture
content is reviewable. Run them to regenerate:

```bash
python tests/fixtures/make_xlsx_fixture.py
python tests/fixtures/make_pdf_fixture.py
```
