import csv
import io
from datetime import timedelta
from pathlib import Path

import duckdb

from .contracts import LOCAL_END

CSV_COLUMNS = (
    "latitude", "longitude", "bright_ti4", "scan", "track", "acq_date",
    "acq_time", "satellite", "instrument", "confidence", "version",
    "bright_ti5", "frp", "daynight", "type", "fire_date", "origin", "weight",
)
ROW_COLUMNS = (*CSV_COLUMNS, "pass")
ROW_FILTER = """
    FROM read_parquet(?)
    WHERE fire_date BETWEEN ? AND ? AND type IN (SELECT unnest(?))
      AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
      AND fire_date IN (
        SELECT fire_date FROM read_parquet(?) WHERE has_data IN ('full', 'partial')
      )
"""
ROW_ORDER = 'ORDER BY acq_date, acq_time, ' + ", ".join(
    f'"{column}"' for column in ROW_COLUMNS if column not in {"acq_date", "acq_time"}
)


class LocalSource:
    """Query precomputed cell aggregates and nationwide fire-day coverage."""

    def __init__(self, directory: Path):
        self.cells_path = directory / "cells.parquet"
        self.status_path = directory / "day_status.parquet"
        self.rows_path = directory / "rows.parquet"

    @property
    def available(self):
        return self.cells_path.is_file() and self.status_path.is_file()

    def row_available(self):
        return self.rows_path.is_file() and self.status_path.is_file()

    def latest_fire_date(self):
        if not self.available:
            return LOCAL_END
        with duckdb.connect() as db:
            return db.execute(
                "SELECT max(fire_date) FROM read_parquet(?) WHERE has_data IN ('full', 'partial') AND fire_date <= ?",
                [str(self.status_path), LOCAL_END],
            ).fetchone()[0] or LOCAL_END

    def row_parameters(self, bbox, start, end, types):
        return [str(self.rows_path), start, min(end, LOCAL_END), types,
                bbox.min_lat, bbox.max_lat, bbox.min_lon, bbox.max_lon, str(self.status_path)]

    def row_query(self, db, bbox, start, end, types, recent):
        columns = ", ".join(f'"{column}"' for column in ROW_COLUMNS)
        parameters = []
        historical = ""
        if start <= LOCAL_END:
            historical = f"UNION ALL SELECT {columns} {ROW_FILTER}"
            parameters = self.row_parameters(bbox, start, end, types)
        db.execute("""
            CREATE TEMP TABLE recent (
                latitude DOUBLE, longitude DOUBLE, bright_ti4 DOUBLE, scan DOUBLE, track DOUBLE,
                acq_date DATE, acq_time VARCHAR, satellite VARCHAR, instrument VARCHAR,
                confidence VARCHAR, version VARCHAR, bright_ti5 DOUBLE, frp DOUBLE,
                daynight VARCHAR, type INTEGER, fire_date DATE, origin VARCHAR, weight DOUBLE, "pass" VARCHAR
            )
        """)
        if recent:
            placeholders = ", ".join("?" for _ in ROW_COLUMNS)
            db.executemany(f"INSERT INTO recent VALUES ({placeholders})", recent)
        return f"FROM (SELECT {columns} FROM recent {historical})", parameters

    def hotspot_page(self, bbox, start, end, types, limit, offset, recent=()):
        with duckdb.connect() as db:
            query, parameters = self.row_query(db, bbox, start, end, types, recent)
            total = db.execute("SELECT count(*) " + query, parameters).fetchone()[0]
            rows = db.execute(
                f"SELECT * {query} {ROW_ORDER} LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        next_offset = offset + len(rows)
        return {"total": total, "limit": limit, "offset": offset,
                "next_offset": next_offset if next_offset < total else None,
                "rows": [dict(zip(ROW_COLUMNS, row)) for row in rows]}

    def hotspot_csv(self, bbox, start, end, types, recent=()):
        columns = ", ".join(f'"{column}"' for column in CSV_COLUMNS)
        with duckdb.connect() as db:
            query, parameters = self.row_query(db, bbox, start, end, types, recent)
            result = db.execute(f"SELECT {columns} {query} {ROW_ORDER}", parameters)
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer)
            writer.writerow(CSV_COLUMNS)
            yield buffer.getvalue()
            while batch := result.fetchmany(5000):
                buffer.seek(0)
                buffer.truncate()
                writer.writerows(batch)
                yield buffer.getvalue()

    def summary(self, bbox, start, end, types, group_by):
        if not self.available or start > LOCAL_END:
            return [], {}

        local_end = min(end, LOCAL_END)
        dimensions = {
            "daily": 'fire_date, "pass", origin',
            "grid": "ci, cj, origin",
        }[group_by]
        with duckdb.connect() as db:
            status_rows = db.execute(
                """
                SELECT fire_date, has_data, daynight_reliable, flag
                FROM read_parquet(?) WHERE fire_date BETWEEN ? AND ?
                """,
                [str(self.status_path), start, local_end],
            ).fetchall()
            rows = db.execute(
                f"""
                SELECT {dimensions}, SUM(count), SUM(frp_sum), SUM(n_rows)
                FROM read_parquet(?)
                WHERE fire_date BETWEEN ? AND ? AND type IN (SELECT unnest(?))
                  AND cell_lat BETWEEN ? AND ? AND cell_lon BETWEEN ? AND ?
                  AND fire_date IN (
                    SELECT fire_date FROM read_parquet(?) WHERE has_data IN ('full', 'partial')
                  )
                GROUP BY ALL
                ORDER BY {dimensions}
                """,
                [str(self.cells_path), start, local_end, types,
                 bbox.min_lat, bbox.max_lat, bbox.min_lon, bbox.max_lon,
                 str(self.status_path)],
            ).fetchall()
        return rows, {day: (coverage, reliable, flag) for day, coverage, reliable, flag in status_rows}


def dates_between(start, end):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)
