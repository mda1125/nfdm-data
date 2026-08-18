# The Dairy Desk

Dairy ingredient market intelligence dashboard. Tracks NFDM CME spot vs the
USDA NDPSR survey (and the basis between them), FMMO Class IV, the NFDM futures
curve with forecast-accuracy backtesting, a milk-protein estimate, seasonality,
supply fundamentals (production & stocks), sugar #11, and whey protein
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

Uses USDA data but is not endorsed or certified by USDA.
