"""Contract v1.2: /api/anomaly with the assessed year inside the requested baseline."""

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

HISTORICAL = Path("data/synthetic")
BOX = dict(lat1=-44, lat2=-9, lon1=112, lon2=154)


class AnomalyBaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (HISTORICAL / "cells.parquet").exists():
            raise unittest.SkipTest("Generate the synthetic Parquet files first.")
        cls.client = TestClient(create_app(HISTORICAL))

    def get(self, **params):
        response = self.client.get("/api/anomaly", params={**BOX, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_whole_record_baseline_lists_each_year_once(self):
        data = self.get(month="2018-12", baseline_start_year=2001, baseline_end_year=2025)
        years = [entry["year"] for entry in data["history"]]
        self.assertEqual(years, list(range(2001, 2026)))
        self.assertEqual(data["baseline"]["years"], [2001, 2025])
        # the assessed year is one of the baseline years, so it is not counted twice
        self.assertEqual(data["rank_of"], data["baseline"]["n_years"])
        self.assertIsNotNone(data["category"])

    def test_partial_assessed_month_stays_out_of_its_own_baseline(self):
        # the synthetic data marks 2019-12-15 as partial, so Dec 2019 is not a baseline year
        data = self.get(month="2019-12", baseline_start_year=2001, baseline_end_year=2025)
        self.assertEqual(len(data["history"]), 25)
        self.assertEqual(data["rank_of"], data["baseline"]["n_years"] + 1)

    def test_assessed_year_before_baseline_is_added_once(self):
        data = self.get(month="2000-11", baseline_start_year=2001, baseline_end_year=2025)
        years = [entry["year"] for entry in data["history"]]
        self.assertEqual(years, list(range(2000, 2026)))
        self.assertEqual(data["rank_of"], data["baseline"]["n_years"] + 1)

    def test_default_baseline_is_unchanged(self):
        data = self.get(month="2019-12")
        self.assertEqual(data["baseline"]["years"], [2001, 2018])
        self.assertEqual([entry["year"] for entry in data["history"]][-1], 2019)
        self.assertEqual(data["rank_of"], data["baseline"]["n_years"] + 1)

    def test_meta_reports_contract_1_2(self):
        self.assertEqual(self.client.get("/api/meta").json()["contract_version"], "1.2")


if __name__ == "__main__":
    unittest.main()
