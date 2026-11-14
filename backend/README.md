# AGNI API

From the backend folder in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for the available endpoints. To run the synthetic
Parquet integration tests, install `httpx` in the virtual environment and run
`python -m unittest discover -s tests` using that environment.

Historical analytics read `cells.parquet` and `day_status.parquet` from
`AGNI_DATA_DIR`, which defaults to `backend/data`. The required columns are in
`app/local_store.py`. These filenames and the grid index convention (ci is
longitude times ten and cj is latitude times ten, both for the southwest
corner) are provisional until checked against the team's data build.
Historical dates without coverage are returned as missing.

For a Cloudflare-hosted frontend, set `AGNI_CORS_ORIGINS` to its HTTPS origin.
The API exposes all eight contract routes with historical data. To enable
recent FIRMS analytics, set `FIRMS_MAP_KEY` in the backend environment and
restart Uvicorn. The cache defaults to `data/firms.duckdb`; set
`AGNI_FIRMS_CACHE` to override its path. Use one Uvicorn worker with this
local DuckDB cache. All data routes use FIRMS when the query includes recent
dates. Metadata reports the newest cached date and last successful fetch.
Before a demo, stop the API and warm the cache with the same configuration:

```powershell
.\.venv\Scripts\python.exe -m scripts.warm_cache --start 2026-01-01 --end 2026-01-02
```

Omit the date arguments to attempt the full recent range. Cold-cache
downloads are sequential. Low deliveries are marked partial. The integration
tests use mocked FIRMS responses.

## Synthetic VIIRS dataset

Run this from the backend folder to rebuild the deterministic mock:

```powershell
.\.venv\Scripts\python.exe scripts/build_viirs.py --output data/synthetic
$env:AGNI_DATA_DIR = (Resolve-Path data/synthetic).Path
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The synthetic CSV has the official 15-column layout, including zero-padded
UTC acquisition times and string version codes. The builder derives fire-days
using local solar time minus six hours and writes both row-level and aggregated
Parquet. Synthetic coverage is tracked separately, so a covered day with zero
detections differs from an outage. All detections are fabricated, including
rows with version 2. Do not use these values for fire analysis.

## Replacing the mock with the real R2 dataset

Download the raw CSV and authoritative day-coverage CSV from R2 to a machine
with space for the source and generated output. Build into a new directory:

```powershell
.\.venv\Scripts\python.exe scripts/build_viirs.py --csv unified_viirs_format_gapfilled.csv --status day_status.csv --output data/real
```

The builder applies the architecture v2 month/pass weights. The status CSV
needs `fire_date,has_data,daynight_reliable,flag`; hotspot rows alone cannot
distinguish an outage from a covered day with zero detections.

Upload the three Parquet files to a versioned R2 prefix and sync that prefix
to a local backend directory. Validate the new files, then set
`AGNI_DATA_DIR` to that directory and restart the server. Keep the mock
directory available for rollback. The API reads the local copy.

## Container

Build from the backend directory with `docker build -t agni-api .`.
Mount the directory with all three Parquet files at `/data` and a writable
persistent directory at `/cache`. Configure `FIRMS_MAP_KEY` as a server
secret and `AGNI_CORS_ORIGINS` as your frontend's HTTPS origin. Serve port
8000 behind your hosting provider's HTTPS endpoint.
