import tempfile
import unittest
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from app.main import create_app


class HistoricalApiTest(unittest.TestCase):
    def test_weighted_grid_matches_daily_and_distinguishes_missing_from_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with duckdb.connect() as db:
                db.execute("""
                    CREATE TABLE cells AS SELECT DATE '2000-11-01' AS fire_date,
                        'D' AS "pass", 'ml' AS origin, 0 AS type, 1480 AS ci, -350 AS cj,
                        -34.95 AS cell_lat, 148.05 AS cell_lon, 1.5::DOUBLE AS count,
                        6.0::DOUBLE AS frp_sum, 1 AS n_rows
                """)
                db.execute("""
                    CREATE TABLE status AS SELECT * FROM (VALUES
                        (DATE '2000-11-01', 'full', true, NULL),
                        (DATE '2000-11-02', 'none', true, 'outage'),
                        (DATE '2000-11-03', 'full', true, NULL)
                    ) AS t(fire_date, has_data, daynight_reliable, flag)
                """)
                db.execute("COPY cells TO (?) (FORMAT PARQUET)", [str(root / "cells.parquet")])
                db.execute("COPY status TO (?) (FORMAT PARQUET)", [str(root / "day_status.parquet")])

            with TestClient(create_app(root)) as client:
                params = dict(lat1=-34, lat2=-36, lon1=149, lon2=147,
                              start="2000-11-01", end="2000-11-03")
                daily = client.get("/api/daily", params=params)
                grid = client.get("/api/grid", params=params)
                self.assertEqual(daily.status_code, 200, daily.text)
                self.assertEqual(grid.status_code, 200, grid.text)
                self.assertEqual(daily.json()["missing_dates"], ["2000-11-02"])
                self.assertEqual([day["count"] for day in daily.json()["data"]["days"]], [1.5, None, 0.0])
                self.assertEqual(grid.json()["data"]["total"], daily.json()["data"]["totals"]["count"])
                self.assertEqual(grid.json()["data"]["cells"][0]["lon"], 148.0)
                self.assertEqual(grid.json()["data"]["cells"][0]["lat"], -35.0)

                box = {name: params[name] for name in ("lat1", "lat2", "lon1", "lon2")}
                calendar = client.get("/api/calendar", params={**box, "end_year": 2000})
                self.assertEqual(calendar.status_code, 200, calendar.text)
                calendar_data = calendar.json()["data"]
                self.assertEqual([cell["month"] for cell in calendar_data["cells"]], [11, 12])
                self.assertEqual(calendar_data["cells"][0]["count"], 1.5)
                self.assertEqual(calendar_data["climatology"][10]["years_used"], 0)

                anomaly = client.get("/api/anomaly", params={**box, "month": "2000-11"})
                self.assertEqual(anomaly.status_code, 200, anomaly.text)
                self.assertEqual(anomaly.json()["data"]["value"], 1.5)
                self.assertEqual(anomaly.json()["data"]["baseline"]["n_years"], 0)

    def test_health_and_required_parameter_error(self):
        with TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
            response = client.get("/api/daily")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["field"], "lat1")
