# The Dairy Desk

Dairy ingredient market intelligence dashboard. Tracks NFDM CME spot vs the
USDA NDPSR survey (and the basis between them), FMMO Class IV, the NFDM futures
curve with forecast-accuracy backtesting, a milk-protein estimate, seasonality,
supply fundamentals (production & stocks), sugar #11, cocoa, and whey protein
indications (WPC34/WPC80/WPI).

Data is refreshed on weekdays by a GitHub Actions workflow
([`.github/workflows/fetch-data.yml`](.github/workflows/fetch-data.yml)) running
[`scripts/fetch_data.py`](scripts/fetch_data.py), which writes JSON to `data/`.
The static dashboard ([`index.html`](index.html) + [`app.js`](app.js)) reads
those files and is served via GitHub Pages.

## Sources

- **CME spot & NFDM futures** — USDA Market News (MMN/MARS API) and CME GNF via Yahoo Finance
- **NDPSR survey & Class IV** — USDA mandatory price reporting (DPMRP/FMMO)
- **Supply fundamentals** — USDA NASS QuickStats
- **Whey (WPC34/WPC80/WPI)** — USDA Dairy Market News (report 1053)
- **Sugar #11** — ICE SB via Yahoo Finance
- **Cocoa** — ICE CC via Yahoo Finance

Uses USDA data but is not endorsed or certified by USDA.

## Cocoa market notes

Price/curve data (`data/cocoa.json`, `data/cocoa_futures.json`) is fetched
automatically. Narrative commentary is not — `data/cocoa_notes.json` is
intentionally left for an external process (an analyst, an LLM agent, etc.)
to write on its own schedule, using the fetched price data plus outside
context. The dashboard just reads whatever is there; if the file is missing
or malformed the "Market outlook" panel shows an empty-state message instead
of erroring.

Expected shape:

```json
{
  "week_of": "YYYY-MM-DD",
  "summary": "string",
  "weather": "string",
  "products": "string",
  "outlook": "string",
  "booking": ["string", "string", "..."]
}
```

All fields are optional — omit a key and that subsection is skipped. `booking`
renders as a bulleted list; everything else renders as a paragraph.
