"""Generate synthetic VIIRS input and build the historical API's Parquet files."""

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb


COLUMNS = (
    "latitude", "longitude", "bright_ti4", "scan", "track", "acq_date",
    "acq_time", "satellite", "instrument", "confidence", "version",
    "bright_ti5", "frp", "daynight", "type",
)
START = date(2000, 11, 1)
END = date(2025, 12, 31)
OUTAGES = (
    (date(2001, 6, 17), date(2001, 7, 2)),
    (date(2002, 3, 21), date(2002, 3, 27)),
)
GAPFILLS = (
    (date(2012, 1, 1), date(2012, 1, 19)),
    (date(2022, 7, 27), date(2022, 8, 10)),
    (date(2025, 11, 12), date(2025, 11, 12)),
)
LOCATIONS = ((-38.27, 146.39), (-33.71, 150.31), (-21.23, 123.68), (-16.41, 143.13))


def generate_mock(target):
    rng = random.Random(26)
    row_path = target / "synthetic_viirs.csv"
    status_path = target / "synthetic_day_status.csv"
    with row_path.open("w", newline="") as row_file, status_path.open("w", newline="") as status_file:
        rows = csv.writer(row_file)
        status = csv.writer(status_file)
        rows.writerow(COLUMNS)
        status.writerow(("fire_date", "has_data", "daynight_reliable", "flag"))

        day = START
        while day <= END:
            missing = any(first <= day <= last for first, last in OUTAGES)
            gapfill = any(first <= day <= last for first, last in GAPFILLS)
            partial = day == date(2019, 12, 15)
            coverage = "none" if missing else "partial" if partial else "full"
            status.writerow((day, coverage, not (gapfill and day.year >= 2022), "outage" if missing else ""))

            version = "ML-XGB-1.0" if day.year <= 2011 else "ML-XGB-1.0-gapfill" if gapfill else "2"
            if not missing and day.day != 8:
                seasonal = 10 if day.month in {11, 12, 1, 2} else 3
                count = 1 if partial else seasonal + rng.randrange(4)
                if day.year == 2019 and day.month == 12:
                    count *= 3
                for index in range(count):
                    lat, lon = LOCATIONS[index % len(LOCATIONS)]
                    night = index >= (count + 1) // 2
                    kind = 3 if index == 0 and day.day == 1 else 2 if index == 0 and day.day % 5 == 0 else 0
                    rows.writerow((
                        round(lat + rng.uniform(-0.035, 0.035), 5),
                        round(lon + rng.uniform(-0.035, 0.035), 5),
                        round(rng.uniform(305, 360), 2), 0.39, 0.41, day,
                        "1601" if night else "0401", "N", "VIIRS",
                        rng.choices(["l", "n", "h"], [9, 81, 10])[0], version,
                        round(rng.uniform(280, 315), 2), round(rng.uniform(0, 40), 2),
                        "N" if night else "D", kind,
                    ))
            day += timedelta(days=1)
    return row_path, status_path


DAY_WEIGHTS = [1.103, 1.161, 1.179, 1.114, 1.066, 1.059,
               1.056, 1.065, 1.051, 1.037, 1.060, 1.082]
NIGHT_WEIGHTS = [1.144, 1.244, 1.498, 1.518, 1.479, 1.621,
                 1.644, 1.368, 1.196, 1.101, 1.111, 1.118]


def build(row_path, status_path, output):
    with row_path.open(newline="", encoding="utf-8-sig") as source:
        if tuple(next(csv.reader(source), ())) != COLUMNS:
            raise ValueError("Input CSV must use the 15 VIIRS columns in the documented order.")

    with duckdb.connect() as db:
        db.execute("""
            CREATE TEMP TABLE detections AS
            WITH typed AS (
                SELECT latitude::DOUBLE AS latitude, longitude::DOUBLE AS longitude,
                       bright_ti4::DOUBLE AS bright_ti4, round(scan::DOUBLE, 2) AS scan, round(track::DOUBLE, 2) AS track,
                       acq_date::DATE AS acq_date, acq_time, satellite, instrument, confidence, version,
                       bright_ti5::DOUBLE AS bright_ti5, frp::DOUBLE AS frp, daynight, type::INT AS type
                FROM read_csv(?, header=true, all_varchar=true)
            ), solar AS (
                SELECT *, acq_date + strptime(acq_time, '%H%M')::TIME
                          + INTERVAL '1 second' * (longitude * 240) AS solar_time
                FROM typed
            ), keyed AS (
            SELECT *, (solar_time - INTERVAL '6 hours')::DATE AS fire_date,
                   CASE WHEN extract(hour FROM solar_time) BETWEEN 6 AND 17 THEN 'D' ELSE 'N' END AS "pass",
                   CASE version WHEN '2' THEN 'real' WHEN 'ML-XGB-1.0' THEN 'ml'
                                WHEN 'ML-XGB-1.0-gapfill' THEN 'gapfill' ELSE error('Unknown VIIRS version') END AS origin,
                   floor(longitude * 10)::INT AS ci, floor(latitude * 10)::INT AS cj
            FROM solar
            )
            SELECT *, CASE WHEN origin = 'real' THEN 1.0
                           WHEN "pass" = 'D' THEN list_extract(?, month(fire_date))
                           ELSE list_extract(?, month(fire_date)) END::DOUBLE AS weight
            FROM keyed
        """, [str(row_path), DAY_WEIGHTS, NIGHT_WEIGHTS])

        db.execute("""
            COPY (SELECT fire_date, "pass", type, origin, ci, cj,
                         (cj + 0.5) / 10 AS cell_lat, (ci + 0.5) / 10 AS cell_lon,
                         SUM(weight) AS count, SUM(frp * weight) AS frp_sum, COUNT(*) AS n_rows
                  FROM detections GROUP BY ALL ORDER BY fire_date) TO (?) (FORMAT PARQUET)
        """, [str(output / "cells.parquet")])
        db.execute("""
            COPY (SELECT * EXCLUDE (solar_time) FROM detections ORDER BY acq_date, acq_time)
            TO (?) (FORMAT PARQUET)
        """, [str(output / "rows.parquet")])
        db.execute("""
            COPY (SELECT fire_date::DATE AS fire_date, has_data::VARCHAR AS has_data,
                         daynight_reliable::BOOLEAN AS daynight_reliable, flag::VARCHAR AS flag
                  FROM read_csv(?, header=true)) TO (?) (FORMAT PARQUET)
        """, [str(status_path), str(output / "day_status.parquet")])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--status", type=Path)
    args = parser.parse_args()
    if bool(args.csv) != bool(args.status):
        parser.error("External input requires both --csv and --status.")
    args.output.mkdir(parents=True, exist_ok=True)
    rows, status = (args.csv, args.status) if args.csv else generate_mock(args.output)
    build(rows, status, args.output)
    print(f"Wrote historical Parquet files to {args.output.resolve()}")


if __name__ == "__main__":
    main()
