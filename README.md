# Property Project Planner

A Streamlit web app that estimates the feasibility and cost of a residential
project — pool, garage, ADU, addition, shed, or sport court — from just a street
address. It matches the address to the county's authoritative parcel record,
draws the exact lot lines on current satellite imagery, measures the buildable
space, and returns a region-adjusted cost range.

All data comes from free public sources: US Census geocoder, county assessor
ArcGIS services, OpenStreetMap, and Esri World Imagery. Estimates are for
planning only — not a substitute for a stamped survey or a contractor's bid.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501.

## Deploy (Streamlit Community Cloud — free)

1. Push this folder to a GitHub repository.
2. Go to https://share.streamlit.io and sign in with GitHub.
3. Click **Create app → Deploy a public app from GitHub**.
4. Select the repo, set **Main file path** to `app.py`, and click **Deploy**.

Streamlit Cloud reads `requirements.txt` automatically. No secrets or API keys
are required — every data source used here is keyless and public.

## Project layout

| Path | Purpose |
|------|---------|
| `app.py` | Streamlit UI (address → project → report, light/dark toggle) |
| `src/report.py` | End-to-end pipeline orchestration |
| `src/geocode.py` | Address → coordinates (US Census) |
| `src/county_gis.py` | County ArcGIS parcel lookup (live-verified endpoints) |
| `src/osm.py`, `src/nominatim.py` | OpenStreetMap building/parcel fallback |
| `src/imagery.py` | Esri satellite tiles + capture-date metadata |
| `src/geometry.py` | Lot/yard/setback measurement |
| `src/feasibility.py`, `src/costs.py` | Feasibility rules and cost model |
| `data/*.json` | County endpoints, cost baselines, regional multipliers |
