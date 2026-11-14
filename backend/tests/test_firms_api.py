import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.firms import FirmsSource, FirmsUnavailable
from app.main import create_app


class FirmsApiTest(unittest.TestCase):
    def test_boundary_merge_cache_and_unavailable(self):
        historical = Path("data/synthetic")
        if not (historical / "cells.parquet").exists():
            self.skipTest("Generate the synthetic Parquet files first.")

        calls = []

        def fetch(_key, _product, day):
            calls.append(day)
            if day.day == 2:
                raise FirmsUnavailable
            return f"acq_date,acq_time,latitude,longitude,frp,type,bright_ti4,scan,track,bright_ti5\n{day},0401,-38.27,146.39,7.5,0,340,0.39,0.41,300\n"

        with tempfile.TemporaryDirectory() as root:
            source = FirmsSource("mock", Path(root) / "cache.duckdb", fetch)
            with TestClient(create_app(historical, source)) as client:
                params = dict(lat1=-44, lat2=-9, lon1=112, lon2=154,
                              start="2025-12-31", end="2026-01-01")
                daily = client.get("/api/daily", params=params)
                self.assertEqual(daily.status_code, 200, daily.text)
                data = daily.json()["data"]
                self.assertGreater(data["days"][0]["count"], 0)
                self.assertEqual(data["days"][1]["count"], 1)
                grid = client.get("/api/grid", params=params)
                self.assertEqual(grid.status_code, 200, grid.text)
                self.assertAlmostEqual(grid.json()["data"]["total"], data["totals"]["count"])
                recent = client.get("/api/hotspots", params=params)
                self.assertEqual(recent.status_code, 200, recent.text)
                self.assertEqual(recent.json()["data"]["rows"][-1]["origin"], "firms_sp")
                export = client.get("/api/hotspots.csv", params=params)
                self.assertEqual(export.status_code, 200, export.text)
                self.assertIn("2026-01-01,0401", export.text)
                self.assertEqual(client.get("/api/meta").json()["coverage"]["latest_fire_date"], "2026-01-01")
                self.assertIn("FIRMS_LOW_DELIVERY", {warning["code"] for warning in daily.json()["warnings"]})
                self.assertEqual(len(calls), 2)

                partial = client.get("/api/daily", params={**params, "end": "2026-01-02"})
                self.assertEqual(partial.status_code, 200, partial.text)
                self.assertIn("2026-01-02", partial.json()["missing_dates"])
                self.assertIn("FIRMS_UNAVAILABLE", {warning["code"] for warning in partial.json()["warnings"]})
