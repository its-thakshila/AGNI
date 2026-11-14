"""Shared constants and the detections -> cells conversion used by every pipeline step."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BBOX = (112.0, -44.0, 154.0, -9.0)  # min_lon, min_lat, max_lon, max_lat (Australia)
RECORD_START = date(2000, 11, 1)

# The 15 FIRMS VIIRS columns, in the documented order (same as the processed CSV).
VIIRS_COLUMNS = (
    "latitude", "longitude", "bright_ti4", "scan", "track", "acq_date", "acq_time",
    "satellite", "instrument", "confidence", "version", "bright_ti5", "frp", "daynight", "type",
)
# Columns of rows.parquet: the 15 above plus what the builder derives.
ROW_COLUMNS = (*VIIRS_COLUMNS, "fire_date", "pass", "origin", "ci", "cj", "weight")

ROWS_TABLE_SQL = """
    CREATE TABLE {name} (
        latitude DOUBLE, longitude DOUBLE, bright_ti4 DOUBLE, scan DOUBLE, track DOUBLE,
        acq_date DATE, acq_time VARCHAR, satellite VARCHAR, instrument VARCHAR, confidence VARCHAR,
        version VARCHAR, bright_ti5 DOUBLE, frp DOUBLE, daynight VARCHAR, type INTEGER,
        fire_date DATE, "pass" VARCHAR, origin VARCHAR, ci INTEGER, cj INTEGER, weight DOUBLE
    )
"""

# cells.parquet is always derived from rows the same way (matches backend/scripts/build_viirs.py).
CELLS_SELECT = """
    SELECT fire_date, "pass", type, origin, ci, cj,
           (cj + 0.5) / 10 AS cell_lat, (ci + 0.5) / 10 AS cell_lon,
           SUM(weight) AS count, SUM(frp * weight) AS frp_sum, COUNT(*) AS n_rows
    FROM {source} GROUP BY ALL
"""

CELLS_MONTH_SELECT = """
    SELECT year(fire_date) AS year, month(fire_date) AS month, type, origin, ci, cj, cell_lat, cell_lon,
           SUM(count) AS count, SUM(frp_sum) AS frp_sum, SUM(n_rows) AS n_rows
    FROM {source} GROUP BY ALL
"""


def load_backend_builder():
    """The backend's CSV -> Parquet builder, reused so there is one source of truth."""
    path = REPO / "backend" / "scripts" / "build_viirs.py"
    spec = importlib.util.spec_from_file_location("agni_build_viirs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_from_parquet(db, files: list[str], status_path: str | None, output: Path, weights) -> None:
    """Per-year Parquet files (the 15 CSV columns) -> cells / rows / day_status in `output`.

    Same logic as the backend's CSV builder, but read straight from Parquet and tolerant of
    writer differences (acq_time stored as a number, version as 2 / "2" / "2.0", text dates).
    Without a status file, days with detections count as "full" and days without as "none"
    (fine for Australia, where some detection happens every day the satellite delivers).
    """
    day_weights, night_weights = weights
    # The team's processed files may already carry weight (more precise than the rounded
    # monthly table) and their own ci/cj. Use their weight; always recompute ci/cj here,
    # because their grid numbering differs from the one the website uses (lon*10, lat*10).
    columns = {name for name, *_ in db.execute("DESCRIBE SELECT * FROM read_parquet(?, union_by_name = true)", [files]).fetchall()}
    given_weight = "TRY_CAST(src_weight AS DOUBLE)" if "weight" in columns else "NULL"
    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE detections AS
        WITH typed AS (
            SELECT latitude::DOUBLE AS latitude, longitude::DOUBLE AS longitude,
                   TRY_CAST(bright_ti4 AS DOUBLE) AS bright_ti4, round(TRY_CAST(scan AS DOUBLE), 2) AS scan,
                   round(TRY_CAST(track AS DOUBLE), 2) AS track, CAST(acq_date AS DATE) AS acq_date,
                   lpad(regexp_replace(CAST(acq_time AS VARCHAR), '\\.0+$', ''), 4, '0') AS acq_time,
                   CAST(satellite AS VARCHAR) AS satellite, CAST(instrument AS VARCHAR) AS instrument,
                   CAST(confidence AS VARCHAR) AS confidence,
                   regexp_replace(CAST(version AS VARCHAR), '^2\\.0$', '2') AS version,
                   TRY_CAST(bright_ti5 AS DOUBLE) AS bright_ti5, COALESCE(TRY_CAST(frp AS DOUBLE), 0) AS frp,
                   CAST(daynight AS VARCHAR) AS daynight, COALESCE(TRY_CAST(type AS INT), 0) AS type,
                   {given_weight} AS given_weight
            FROM (SELECT *{', weight AS src_weight' if 'weight' in columns else ''} FROM read_parquet(?, union_by_name = true))
        ), solar AS (
            SELECT *, acq_date + strptime(acq_time, '%H%M')::TIME + INTERVAL '1 second' * (longitude * 240) AS solar_time
            FROM typed
        ), keyed AS (
            SELECT *, (solar_time - INTERVAL '6 hours')::DATE AS fire_date,
                   CASE WHEN extract(hour FROM solar_time) BETWEEN 6 AND 17 THEN 'D' ELSE 'N' END AS "pass",
                   CASE WHEN version = '2' THEN 'real'
                        WHEN version LIKE 'ML-%gapfill' THEN 'gapfill'
                        WHEN version LIKE 'ML-%' THEN 'ml'
                        ELSE error('Unknown version ' || version) END AS origin,
                   floor(longitude * 10)::INT AS ci, floor(latitude * 10)::INT AS cj
            FROM solar
        )
        SELECT * EXCLUDE (solar_time, given_weight), COALESCE(given_weight, CASE WHEN origin = 'real' THEN 1.0
                       WHEN "pass" = 'D' THEN list_extract(?, month(fire_date))
                       ELSE list_extract(?, month(fire_date)) END)::DOUBLE AS weight
        FROM keyed
    """, [files, day_weights, night_weights])
    db.execute(f"COPY ({CELLS_SELECT.format(source='detections')}) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)",
               [str(output / "cells.parquet")])
    db.execute("COPY (SELECT * FROM detections ORDER BY acq_date, acq_time) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)",
               [str(output / "rows.parquet")])
    if status_path:
        reader = "read_parquet(?)" if status_path.endswith(".parquet") else "read_csv(?, header=true)"
        db.execute(f"""COPY (SELECT fire_date::DATE AS fire_date, has_data::VARCHAR AS has_data,
                              daynight_reliable::BOOLEAN AS daynight_reliable, flag::VARCHAR AS flag FROM {reader})
                       TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)""", [status_path, str(output / "day_status.parquet")])
    else:
        db.execute("""COPY (
            WITH span AS (SELECT min(fire_date) AS a, max(fire_date) AS b FROM detections),
                 days AS (SELECT unnest(generate_series(a, b, INTERVAL 1 DAY))::DATE AS fire_date FROM span),
                 seen AS (SELECT DISTINCT fire_date FROM detections)
            SELECT d.fire_date, CASE WHEN s.fire_date IS NULL THEN 'none' ELSE 'full' END AS has_data,
                   true AS daynight_reliable, CASE WHEN s.fire_date IS NULL THEN 'outage' END AS flag
            FROM days d LEFT JOIN seen s USING (fire_date) ORDER BY 1
        ) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)""", [str(output / "day_status.parquet")])


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"
