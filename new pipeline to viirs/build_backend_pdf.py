"""Render the backend architecture document (v2) as a PDF.

    python build_backend_pdf.py   ->  ../BACKEND_ARCHITECTURE_v2.pdf
"""
from __future__ import annotations

import json

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

import config
from build_pdf import INK2, NAVY, P, callout, code, section, table

OUT_PDF = config.ROOT / "BACKEND_ARCHITECTURE_v2.pdf"
ADJ = json.loads(config.TRAIN_REPORT_FILE.read_text())["unreachable_adjust"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def h(t):
    return P(t, "h2")


def gap(n=6):
    return Spacer(1, n)


def build():
    E = []
    E += [Paragraph("<b>Backend Architecture: Harmonized Burning Activity Calendar</b>",
                    ParagraphStyle("t", fontName="DVB", fontSize=19, leading=24, textColor=NAVY)), gap(4),
          P("<font color='#4a4a4a'>NASA Space Apps Challenge 2026 · Harmonization of MODIS and VIIRS Hot Spots · "
            "Region: Australia · <b>Version 2</b></font>"), gap(8),
          P("The backend serves harmonized fire hotspot data to the web app. Given <b>two lat/lon corners</b> and a "
            "<b>date or date range</b>, it returns the hotspots in that box as rows, daily counts, a monthly calendar, "
            "an anomaly comparison or a CSV download. This document describes its parts, how a request flows through "
            "it, how FIRMS data is fetched and cached, and the build order."), gap(8)]

    E += section(0, "What changed from version 1")
    E += [table([["#", "Change", "Why"],
                 ["1", "<b>Counts are weighted.</b> Every row gets a <i>weight</i>; all counts are SUM(weight), not COUNT(*).",
                  "Model rows for 2000–2011 leave out VIIRS fires far from any MODIS detection. Counting rows makes "
                  "those years 8–12% too low, which biases the calendar median and every anomaly."],
                 ["2", "<b>Low-count FIRMS days are flagged, not cached as quiet.</b>",
                  "Australia never has an empty day: the median day has 2,517 detections, 99% of days have more than "
                  "169. A day with fewer than 100 rows is an outage or partial delivery."],
                 ["3", "<b>Australia box extended to −9° latitude.</b>", "Real VIIRS data reaches −9.38° (Torres Strait)."],
                 ["4", "<b>Uncertainty and small-box warnings</b> in responses.",
                  "Model rows are placed statistically, and accuracy differs by period (±11% to ±16% daily)."],
                 ["5", "<b>Storage section added:</b> CSV is the master copy, Parquet is what the API reads.",
                  "A 3 GB CSV has no index, so every query would scan all 30.9 M rows."],
                 ["6", "<b>Hackathon MVP path</b> separated from the full design.",
                  "Locks, rate limiter and the SP-upgrade job can wait until after the demo."],
                 ["7", "<b>Rebuild step</b> for the unified CSV added to the build order.",
                  "The 3 GB row files are not currently on disk."]],
                [0.04, 0.44, 0.52])]

    # ------------------------------------------------------------ 1
    E += [PageBreak()] + section(1, "Goal and scope")
    E += [table([["Requested dates", "Data source", "FIRMS calls"],
                 ["2000-11-01 → 2025-12-31", "Local harmonized record (Parquet built from "
                  "<i>unified_viirs_format_gapfilled.csv</i>): real VIIRS S-NPP 2012–2025, model rows 2000–2011, "
                  "gap-fill rows on VIIRS outage days", "None"],
                 ["2026-01-01 → today", "NASA FIRMS API, VIIRS S-NPP, cached locally", "Only for days not yet cached"],
                 ["Range crossing the boundary", "Both, merged into one response", "Only for the 2026 part"]],
                [0.24, 0.58, 0.18]), gap(),
          P("<b>Region.</b> The local record covers Australia only. Requests whose box lies outside the Australia box "
            "<font name='DVM'>112, −44, 154, −9</font> (min lon, min lat, max lon, max lat) return 400.")]

    E += section(2, "Architecture overview")
    E += [code("""
                          +-------------------------------+
  Web app (map, --------> |  FastAPI  (api/routes.py)     |
  calendar, CSV)          |  validation, routing, CSV     |
                          +---------------+---------------+
                                          |
                          +---------------v---------------+
                          |  Query service (service.py)   |
                          |  split at 2025-12-31, merge,  |
                          |  weights, quality flags       |
                          +-------+---------------+-------+
                     <= 2025-12-31|               |>= 2026-01-01
                +-----------------v---+       +---v-----------------------+
                | Local store         |       | FIRMS cache               |
                | (local_store.py)    |       | (firms_cache.py)          |
                | DuckDB over Parquet |       | valid? -> fetch -> save   |
                | year=2000..2025     |       +---+-------------------+---+
                | + daily_cells       |           | missing/stale     | cached rows
                +---------------------+    +------v-----------+  +----v-------------+
                                           | FIRMS client     |  | cache.duckdb     |
                                           | retry, CSV parse |  | firms_rows       |
                                           +------+-----------+  | fetch_log        |
                                                  | HTTPS        +------------------+
                                           +------v-----------+
                                           | NASA FIRMS API   |
                                           +------------------+
"""), gap(8),
          table([["Part", "Choice", "Why"],
                 ["Web framework", "FastAPI + Uvicorn", "Python, so it reuses the pipeline's fire_date / pass rules; "
                  "automatic API docs at /docs"],
                 ["Historical store", "Parquet per year, queried with DuckDB", "Indexed by row-group statistics: a box "
                  "+ date query reads only the matching parts"],
                 ["FIRMS cache", "DuckDB file (cache.duckdb)", "One file, no server, same query engine"],
                 ["HTTP client", "httpx", "Timeouts, retries"],
                 ["Config", "pydantic-settings + .env", "The FIRMS map key stays on the server"]],
                [0.18, 0.32, 0.50])]

    # ------------------------------------------------------------ 3
    E += [PageBreak()] + section(3, "Data storage: CSV vs Parquet")
    E += [P("The unified dataset stays a <b>CSV</b>: it is the pipeline's output, the exchange format and the download "
            "format. The API must not query it directly."), gap(),
          table([["Option", "Box + date query", "Notes"],
                 ["One 3 GB CSV", "roughly 10–30 s, every query", "No index: every query reads all 30.9 M rows. A 26-year "
                  "calendar would rescan the whole file on each request."],
                 ["One CSV per year", "roughly 1–3 s per year requested", "Acceptable for a first prototype only."],
                 ["<b>Parquet per year + DuckDB</b>", "<b>under 1 s for a year</b>", "One-off conversion (about 5–10 min). "
                  "Several times smaller than the CSV, so only the Parquet files need to be deployed."]],
                [0.27, 0.25, 0.48]), gap(4),
          P("<font size=7.8 color='#4a4a4a'>Timings are estimates for a laptop; measure them in build step 1.</font>"), gap(),
          callout("<b>Rebuild first.</b> <i>unified_viirs_format_gapfilled.csv</i> is not currently on disk. Recreate it "
                  "with <font name='DVM'>python unify.py</font> then <font name='DVM'>python gapfill.py</font> in "
                  "<i>ml_pipeline</i> (about 20 min, about 3 GB). Use the <b>_gapfilled</b> file: in the plain "
                  "<i>unified_viirs_format.csv</i>, VIIRS outage days look like zero fires.", "amber")]

    E += section(4, "Local harmonized record (2000–2025)")
    E += [h("4.1 Conversion: scripts/build_parquet.py"),
          P("Streams the CSV in chunks (fits in memory) and writes Parquet:"),
          P("1. Read with string types for <i>acq_time</i>, <i>version</i>, <i>confidence</i>, <i>satellite</i>.<br/>"
            "2. Round <i>scan</i> and <i>track</i> to 2 decimals (model rows carry float noise such as 0.38999998).<br/>"
            "3. Add derived columns, using the same rules as <i>ml_pipeline/common.add_time_keys</i>:"), gap(3),
          table([["Column", "Rule"],
                 ["utc", "timestamp from acq_date + acq_time (UTC)"],
                 ["lst_hour", "local solar time = UTC hour + longitude / 15 (mod 24)"],
                 ["fire_date", "date of (local solar time − 6 h): the local fire-day used by every calendar view"],
                 ["pass", "D if 6 ≤ lst_hour &lt; 18, otherwise N"],
                 ["origin", "real (version 2) · ml (ML-XGB-1.0) · gapfill (ML-XGB-1.0-gapfill)"],
                 ["<b>weight</b>", "<b>1.0 for real rows; for ml and gapfill rows, the adjustment factor for "
                  "(month of fire_date, pass), from the table in 4.2</b>"]],
                [0.18, 0.82]), gap(),
          P("4. Write <font name='DVM'>data/parquet/year=YYYY/part-*.parquet</font>, sorted by acq_date then latitude "
            "(lets DuckDB skip row groups on box filters).<br/>"
            "5. Write <font name='DVM'>data/parquet/daily_cells.parquet</font>: per 0.1° cell × fire_date × pass × type × "
            "origin, the columns <i>n_rows</i>, <i>count</i> = SUM(weight) and <i>frp_sum</i> = SUM(frp × weight). Calendar "
            "and anomaly queries on large boxes read this table instead of the rows.")]

    E += [PageBreak(), h("4.2 Why counts are weighted")]
    E += [P("Real VIIRS rows include every detection. Model rows only cover VIIRS fires near a MODIS detection: about "
            "89% of all VIIRS detections in the training years. The pipeline corrects its daily totals for the missing "
            "share, but the rows themselves are not corrected. Comparing yearly row totals with the corrected daily "
            "totals:"), gap(4),
          table([["Year", "Model rows", "Corrected total", "Rows ÷ corrected"],
                 ["2001", "1,981,522", "2,237,853", "0.885"], ["2005", "818,584", "927,158", "0.883"],
                 ["2008", "783,581", "878,816", "0.892"], ["2011", "1,873,520", "2,085,867", "0.898"],
                 ["all 2000–2011", "", "", "0.88 – 0.92"]], [0.25, 0.25, 0.25, 0.25]), gap(),
          P("Unweighted, the calendar would show every 2000–2011 month about 8–12% lower than its true level relative "
            "to 2012–2025. The shortfall depends on season and pass (largest for winter night passes), so it would also "
            "distort the seasonal shape. <b>Weight</b> for model and gap-fill rows (from "
            "<i>output/models/training_report.json</i> → <i>unreachable_adjust</i>; month = month of fire_date):"), gap(4)]
    rows = [["Pass"] + MONTHS]
    for p, name in (("D", "Day"), ("N", "Night")):
        rows.append([name] + [f"{ADJ[f'{m}_{p}']:.3f}" for m in range(1, 13)])
    E += [table(rows, [0.088] + [0.076] * 12, zebra=False), gap(),
          code("""
-- every count in the API
SELECT fire_date, SUM(weight) AS count, SUM(frp * weight) AS frp_sum, COUNT(*) AS n_rows
FROM read_parquet('data/parquet/year=*/*.parquet', hive_partitioning = true)
WHERE acq_date BETWEEN ? AND ?
  AND latitude  BETWEEN ? AND ?
  AND longitude BETWEEN ? AND ?
  AND type IN (0)
GROUP BY fire_date
"""), gap(),
          P("The factors are Australia-wide averages, so for small boxes the weighted count is approximate. Row endpoints "
            "still return one row per detection; the <i>weight</i> column is included so clients can total correctly. "
            "FIRMS rows have weight 1.")]

    # ------------------------------------------------------------ 5
    E += [PageBreak()] + section(5, "FIRMS data (2026 → today)")
    E += [P("<b>Product.</b> VIIRS_SNPP_NRT for recent days, replaced by VIIRS_SNPP_SP once the standard archive covers "
            "the date. Use <b>S-NPP only</b>: it is the sensor the 2012–2025 record and the model's target come from. "
            "NOAA-20 and NOAA-21 pass about 50 minutes apart from it and would break the series' consistency."), gap(),
          P("<b>Always fetch the whole Australia box</b> (<font name='DVM'>112,-44,154,-9</font>), never the user's box. "
            "Each UTC day is fetched once and serves every user and every drawn box; the user's box is cut out locally."), gap(),
          P("<b>Cost.</b> 2026-01-01 → 2026-11-14 is about 318 days, about 64 calls of up to 5 days. After the first fill, "
            "staying current costs about one call per day. The documented limit is 5,000 transactions per 10 minutes."), gap(8)]
    E += [h("5.1 Cache tables (cache.duckdb)"),
          code("""
CREATE TABLE firms_rows (
    -- the 15 VIIRS columns, same names and types as the local store
    latitude DOUBLE, longitude DOUBLE, bright_ti4 DOUBLE, scan DOUBLE, track DOUBLE,
    acq_date DATE, acq_time VARCHAR, satellite VARCHAR, instrument VARCHAR,
    confidence VARCHAR, version VARCHAR, bright_ti5 DOUBLE, frp DOUBLE,
    daynight VARCHAR, type INTEGER,
    -- derived, same as the local store
    utc TIMESTAMP, lst_hour FLOAT, fire_date DATE, pass VARCHAR,
    origin VARCHAR,          -- 'firms_nrt' | 'firms_sp'
    weight DOUBLE,           -- always 1.0
    product VARCHAR, fetched_at TIMESTAMP
);

CREATE TABLE fetch_log (
    utc_date DATE, product VARCHAR, fetched_at TIMESTAMP,
    row_count INTEGER,       -- Australia-wide rows for this UTC day
    status VARCHAR,          -- 'ok' | 'low' | 'error'
    PRIMARY KEY (utc_date, product)
);
"""), gap(8), h("5.2 When is a cached day valid?"),
          table([["State of the UTC day", "Rule"],
                 ["Has an SP entry with status ok", "Final. Never refetched."],
                 ["NRT, ok, older than 3 days", "Final until the SP upgrade replaces it."],
                 ["NRT, ok, within the last 3 days", "Stale after 3 hours (detections are still being added)."],
                 ["Today", "Stale after 1 hour."],
                 ["<b>status = low</b> (fewer than MIN_DAY_ROWS rows)", "<b>Retried after 6 hours, up to 7 days after the "
                  "date; after that kept as low. Always reported as partial, never as a quiet day.</b>"],
                 ["status = error", "Retried on the next request."]],
                [0.42, 0.58]), gap(),
          callout("<b>Low-count days.</b> Across 2012–2025, an Australia-wide day has a median of 2,517 VIIRS detections "
                  "and 99% of days have more than 169. The three quietest days (12, 26 and 36 rows) are 2024-07-16, "
                  "2024-11-06 and 2024-01-31; the first two directly follow known outages. So a FIRMS day with fewer "
                  "than <b>MIN_DAY_ROWS = 100</b> rows is treated as an outage or partial delivery: status = low, "
                  "has_data = partial in responses.", "amber")]

    E += [PageBreak(), h("5.3 Filling missing days"),
          code("""
def ensure_days(days: list[date]) -> list[date]:
    \"\"\"Make sure every UTC day is cached; return the days still missing or low.\"\"\"
    todo = [d for d in days if not is_valid(d)]
    for run in group_consecutive(todo, max_len=5):          # <= 5 days per FIRMS call
        with day_lock(run):                                   # MVP: one in-process lock
            run = [d for d in run if not is_valid(d)]         # another request may have filled it
            if not run:
                continue
            try:
                rows = firms_client.fetch(PRODUCT, AUSTRALIA_BBOX, start=run[0], n_days=len(run))
                replace_days(run, rows)      # one transaction: delete old rows, insert new,
                                             # fetch_log status = 'low' if a day has < MIN_DAY_ROWS
            except FirmsError:
                log_error(run)
    return [d for d in days if status(d) != "ok"]
"""), gap(8), h("5.4 FIRMS client (firms_client.py)"),
          code("https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{PRODUCT}/{BBOX}/{DAY_RANGE}/{DATE}"), gap(),
          callout("<b>Verify before building the cache:</b> is DATE the start or the end of the range? Sources disagree. "
                  "Run one call with DAY_RANGE = 3 and check which acq_date values come back. fetch() must hide the "
                  "answer and take an explicit start and n_days.", "amber"), gap(),
          table([["Concern", "Implementation"],
                 ["Errors", "Retry 3× with exponential backoff on 5xx and timeouts (FIRMS sometimes returns 504); then FirmsError"],
                 ["Parsing", "String types for acq_time, version, confidence, satellite; zero-pad acq_time to 4; keep the 15 standard columns"],
                 ["Validation", "Header and no rows = 0 rows (then status low, see 5.2). HTML or error text = failure."],
                 ["Rate limit", "MVP: not needed (a few hundred calls in total). Full: token bucket, 4,000 per 10 min."],
                 ["Secret", "FIRMS_MAP_KEY from .env; never logged, never sent to the frontend"]],
                [0.18, 0.82]), gap(8), h("5.5 NRT → SP upgrade (after the hackathon)"),
          P("Weekly: ask <font name='DVM'>/api/data_availability/csv/{KEY}/VIIRS_SNPP_SP</font> for the last SP date, "
            "then refetch every cached NRT day up to it as SP. SP is the same processing as the real 2012–2025 rows.")]

    # ------------------------------------------------------------ 6
    E += [PageBreak()] + section(6, "Query service (service.py)")
    E += [code("""
def get_rows(bbox, start: date, end: date, types=(0,)) -> QueryResult:
    # 1. split the range by UTC acq_date at 2025-12-31 / 2026-01-01
    # 2. calendar views use local fire_date, which lags UTC by up to 6 h:
    #    read one extra UTC day at the end, then filter on fire_date
    # 3. local rows from Parquet (weight already stored)
    # 4. FIRMS rows: ensure_days(firms part) -> query cache.duckdb (weight = 1)
    # 5. union, filter to bbox and types
    # 6. quality: per fire_date -> has_data (true | partial | false), origin mix,
    #    uncertainty; box_warning if the box is small and covers model years
    # 7. return rows/aggregates + {"partial", "missing_dates", "warnings"}
"""), gap(),
          P("If FIRMS cannot be reached, the request still succeeds with what is available: <i>partial = true</i> and the "
            "missing dates listed, so the frontend shows those days as \"no data\", not \"no fires\"."), gap(8),
          h("6.1 Data quality returned with every daily value"),
          table([["Field", "Values and rule"],
                 ["has_data", "true · <b>partial</b> (FIRMS status low, or a day next to a known outage) · false (no source "
                  "has data: MODIS outages 2001-06-17 → 07-02 and 2002-03-21 → 03-27, or a FIRMS error)"],
                 ["origin_mix", "share of the weighted count from real, ml, gapfill, firms_nrt, firms_sp"],
                 ["uncertainty", "typical daily error for the origin and period, from the model tests: real / FIRMS 0; "
                  "ml Jul 2002 – 2011 ±11%; ml Nov 2000 – Jun 2002 (Terra-only) ±16%; gapfill 2012 ±11%; gapfill 2022–2025 ±13%"],
                 ["daynight_reliable", "false for gapfill days in 2022–2025 (MODIS orbit drift: day/night split off by up to "
                  "10–14%); true otherwise"],
                 ["box_warning", "set when the box is smaller than 0.5° × 0.5° and the range includes model rows: their "
                  "positions are statistical, so small boxes are reliable only as totals over many days"]],
                [0.2, 0.8])]

    # ------------------------------------------------------------ 7
    E += [PageBreak()] + section(7, "API endpoints")
    E += [P("Common parameters: <b>lat1, lon1, lat2, lon2</b> (two opposite corners, any order); <b>start, end</b> "
            "(YYYY-MM-DD, inclusive; a single date = start only); <b>types</b> (default 0: vegetation fires; 2 = static or "
            "industrial; 3 = offshore)."), gap(4),
          P("<b>Validation (400):</b> coordinates out of range; box outside 112, −44, 154, −9; start after end; start before "
            "2000-11-01; end after today; row endpoints over MAX_ROW_QUERY_CELLS (area × days), with a hint to use "
            "/api/daily or the CSV export instead."), gap(),
          table([["Method and path", "Returns"],
                 ["GET /api/hotspots", "JSON rows: 15 VIIRS columns + fire_date, pass, origin, weight. Paginated (limit, offset)."],
                 ["GET /api/hotspots.csv", "Streamed CSV: 15-column VIIRS schema + origin, weight."],
                 ["GET /api/daily", "Per fire_date: count (weighted), count_day, count_night, frp_sum, n_rows, origin_mix, "
                  "has_data, uncertainty, daynight_reliable."],
                 ["GET /api/calendar", "Year × month matrix of weighted counts + per-month median, p25, p75 across years, "
                  "with has_data per month."],
                 ["GET /api/anomaly?month=YYYY-MM", "That month vs the same calendar month in other years: z_score, "
                  "percent_of_median, rank. Months with partial or missing days are flagged."],
                 ["GET /api/meta", "Coverage, last cached FIRMS day, known gaps, weight table, accuracy notes, FIRMS key status."],
                 ["GET /api/health", "Liveness check."]],
                [0.3, 0.7]), gap(8),
          P("<b>Example:</b> <font name='DVM'>GET /api/daily?lat1=-30&amp;lon1=148&amp;lat2=-37&amp;lon2=153&amp;start=2005-11-01&amp;end=2005-11-01</font>"),
          code("""
{
  "bbox": {"min_lat": -37, "max_lat": -30, "min_lon": 148, "max_lon": 153},
  "start": "2005-11-01", "end": "2005-11-01",
  "partial": false, "missing_dates": [], "warnings": [],
  "days": [
    {"fire_date": "2005-11-01", "count": 612.4, "count_day": 401.8, "count_night": 210.6,
     "frp_sum": 7120.5, "n_rows": 571, "origin_mix": {"ml": 1.0},
     "has_data": "true", "uncertainty": 0.11, "daynight_reliable": true}
  ]
}
"""), gap(4),
          P("<font size=7.8 color='#4a4a4a'>Values are illustrative. count is a float because model days are weighted; "
            "round it for display.</font>")]

    # ------------------------------------------------------------ 8-9
    E += [PageBreak()] + section(8, "Folder structure and configuration")
    E += [code("""
backend/
|-- app/
|   |-- main.py            FastAPI app, CORS, startup (open DuckDB)
|   |-- settings.py        paths, Australia box, TTLs, thresholds, query limits
|   |-- api/routes.py      endpoints
|   |-- api/schemas.py     request/response models, box + date validation
|   |-- service.py         date split, merge, weights, quality flags
|   |-- local_store.py     DuckDB over Parquet (2000-2025)
|   |-- firms_cache.py     fetch_log, validity rules, ensure_days, replace_days
|   |-- firms_client.py    HTTP, retries, CSV parsing
|   |-- timekeys.py        utc / lst_hour / fire_date / pass (same rules as the pipeline)
|   `-- analytics.py       daily, calendar, median, anomaly
|-- scripts/
|   |-- build_parquet.py   CSV -> Parquet (+ weight) + daily_cells.parquet
|   |-- test_firms_date.py settles start/end meaning of DATE
|   |-- warm_cache.py      fill 2026 -> today before a demo
|   `-- upgrade_sp.py      NRT -> SP (after the hackathon)
|-- data/parquet/          year=2000 ... year=2025, daily_cells.parquet
|-- data/cache.duckdb
|-- tests/
|-- .env                   FIRMS_MAP_KEY=...  (git-ignored)
`-- requirements.txt       fastapi uvicorn duckdb pyarrow pandas httpx pydantic-settings
"""), gap(8),
          table([["Setting", "Default", "Meaning"],
                 ["LOCAL_END_DATE", "2025-12-31", "Last UTC date served from the local record"],
                 ["RECORD_START_DATE", "2000-11-01", "Earliest date served"],
                 ["AUSTRALIA_BBOX", "<b>112,-44,154,-9</b>", "FIRMS fetch area and request bounds"],
                 ["FIRMS_PRODUCT_NRT / _SP", "VIIRS_SNPP_NRT / VIIRS_SNPP_SP", "Products"],
                 ["FIRMS_MAX_DAYS_PER_CALL", "5", "Days per API call"],
                 ["<b>MIN_DAY_ROWS</b>", "<b>100</b>", "Below this, an Australia-wide day is low (partial), not quiet"],
                 ["<b>LOW_RETRY_H / LOW_RETRY_DAYS</b>", "<b>6 / 7</b>", "Retry interval and window for low days"],
                 ["NRT_RECENT_DAYS", "3", "Days treated as still changing"],
                 ["NRT_RECENT_TTL_H / TODAY_TTL_H", "3 / 1", "Staleness thresholds (hours)"],
                 ["<b>SMALL_BOX_DEG</b>", "<b>0.5</b>", "Box side below which model periods get a box_warning"],
                 ["MAX_ROW_QUERY_CELLS", "configurable", "Cap on area × days for row endpoints"],
                 ["DEFAULT_TYPES", "[0]", "Vegetation fires only"]],
                [0.33, 0.27, 0.40]), gap(4),
          P("<font size=7.8 color='#4a4a4a'>Bold = new or changed in version 2.</font>")]

    # ------------------------------------------------------------ 10
    E += [PageBreak()] + section(9, "Build order")
    E += [h("Hackathon MVP"),
          table([["Step", "Work", "Done when"],
                 ["0", "Rebuild <i>unified_viirs_format_gapfilled.csv</i> (unify.py, gapfill.py)", "File exists, about 30.9 M rows"],
                 ["1", "build_parquet.py (with weight), timekeys.py, local_store.py",
                  "A 2019 box + date query returns in under 1 s; COUNT(*) per year matches the CSV; SUM(weight) per "
                  "model year matches unified_daily_gapfilled.csv within 1%"],
                 ["2", "/api/daily, /api/calendar, /api/hotspots on local data", "Frontend builds against real responses"],
                 ["3", "test_firms_date.py, firms_client.py", "DATE meaning confirmed; one 2026 day parsed"],
                 ["4", "firms_cache.py (with low-day rule) + merge in service.py",
                  "A range across 2025-12-31 returns both parts; a repeat request makes no FIRMS call"],
                 ["5", "/api/anomaly, /api/hotspots.csv, /api/meta, quality fields", "Anomaly matches a hand check"],
                 ["6", "warm_cache.py", "All of 2026 cached; the demo runs without FIRMS"]],
                [0.07, 0.43, 0.5]), gap(8),
          h("After the hackathon"),
          P("Per-day file locks for several workers · token-bucket rate limiter · weekly NRT → SP upgrade · NOAA-20 "
            "fallback if S-NPP stops delivering (flagged origin = firms_noaa20).")]

    E += section(10, "Checks and tests")
    E += [table([["Check", "What it verifies"],
                 ["DATE meaning", "Start vs end of the FIRMS range (5.4)"],
                 ["S-NPP in 2026", "Recent days return rows for VIIRS_SNPP_NRT"],
                 ["Map key", "The key printed in the team's FIRMS guide PDF is not in a public repo; request a new one if it was shared"],
                 ["<b>Weights</b>", "<b>Per model year, SUM(weight) = unified_daily_gapfilled viirs_n within 1%; real rows all weight 1</b>"],
                 ["<b>Low day</b>", "<b>A mocked FIRMS day with 20 rows gets status low, has_data partial, and is retried after LOW_RETRY_H</b>"],
                 ["Corner order", "Any corner order gives the same box"],
                 ["Boundary split", "2025-12-29 → 2026-01-02 returns local rows to 12-31 and FIRMS rows from 01-01, no duplicates"],
                 ["Concurrency", "Two simultaneous requests for the same missing days make one FIRMS call (mocked)"],
                 ["FIRMS down", "Mocked 504: partial = true with the missing dates"],
                 ["Gaps", "2001-06-20 returns has_data false, not zero"],
                 ["fire_date", "A night row at 16:00 UTC lands on the next local fire-day"],
                 ["<b>Small box</b>", "<b>A 0.2° box over 2005 returns box_warning</b>"]],
                [0.22, 0.78])]

    doc = SimpleDocTemplate(str(OUT_PDF), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm, title="Backend Architecture v2")

    def footer(canvas, d):
        canvas.saveState(); canvas.setFont("DV", 7); canvas.setFillColor(INK2)
        canvas.drawCentredString(A4[0] / 2, 10 * mm, f"Harmonized Burning Activity Calendar · backend architecture v2 · page {d.page}")
        canvas.restoreState()

    doc.build(E, onFirstPage=footer, onLaterPages=footer)
    print("written", OUT_PDF)


if __name__ == "__main__":
    build()
