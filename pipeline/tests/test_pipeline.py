import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from agni_pipeline import build_hist, consolidate, process_days
from agni_pipeline.firms import FakeFirms, Fetched
from agni_pipeline.storage import LocalStore

TODAY = date(2026, 11, 14)


class StubFirms:
    """Answers per product with a fixed number of detections; None = not available.
    viirs/modis are the near-real-time S-NPP / MODIS answers; other products (NASA's final
    versions, NOAA-20/21) are absent unless given in `extra`."""

    def __init__(self, viirs: int | None, modis: int | None, extra: dict | None = None, covered: set | None = None):
        self.counts = {"VIIRS_SNPP_NRT": viirs, "MODIS_NRT": modis, **(extra or {})}
        self.covered = covered or set()

    def covers(self, product, day):
        return product in self.covered or self.counts.get(product) is not None

    def first(self, products, day):
        product = products[0]
        n = self.counts.get(product)
        if n is None:
            return None
        return Fetched(product, [
            {"latitude": "-20.5", "longitude": "130.5", "acq_date": day.isoformat(), "acq_time": "0430",
             "frp": "5", "confidence": "n", "daynight": "D", "type": "0", "brightness": "330", "bright_t31": "295",
             "bright_ti4": "330", "bright_ti5": "295", "scan": "0.4", "track": "0.4"}] * n)


class SourceChoiceTest(unittest.TestCase):
    day = date(2026, 10, 1)

    def test_viirs_used_when_available(self):
        origin, rows, status = process_days.fetch_day(StubFirms(viirs=50, modis=10), self.day, TODAY)
        self.assertEqual((origin, len(rows), status["has_data"]), ("firms_nrt", 50, "full"))

    def test_harmonic_used_when_viirs_offline(self):
        origin, rows, status = process_days.fetch_day(StubFirms(viirs=None, modis=10), self.day, TODAY)
        self.assertEqual(origin, "gapfill")
        self.assertEqual(status["flag"], "viirs_offline")
        self.assertFalse(status["daynight_reliable"])
        self.assertGreater(len(rows), 10)  # VIIRS-equivalent: several per MODIS detection

    def test_too_few_viirs_detections_counts_as_offline(self):
        origin, _, _ = process_days.fetch_day(StubFirms(viirs=3, modis=10), self.day, TODAY)
        self.assertEqual(origin, "gapfill")

    def test_low_viirs_kept_as_partial_when_modis_missing(self):
        origin, rows, status = process_days.fetch_day(StubFirms(viirs=3, modis=None), self.day, TODAY)
        self.assertEqual((origin, len(rows), status["has_data"], status["flag"]), ("firms_nrt", 3, "partial", "firms_low"))

    def test_nothing_available_is_no_data(self):
        origin, rows, status = process_days.fetch_day(StubFirms(viirs=None, modis=None), self.day, TODAY)
        self.assertEqual((origin, rows, status["has_data"]), ("none", [], "none"))

    def test_noaa20_stands_in_while_snpp_is_down(self):
        origin, rows, status = process_days.fetch_day(
            StubFirms(viirs=None, modis=10, extra={"VIIRS_NOAA20_NRT": 40}), self.day, TODAY)
        self.assertEqual((origin, len(rows), status["source"], status["flag"]), ("firms_nrt", 40, "VIIRS_NOAA20_NRT", "snpp_offline"))

    def test_final_data_preferred_over_near_real_time(self):
        origin, _, status = process_days.fetch_day(
            StubFirms(viirs=50, modis=10, extra={"VIIRS_SNPP_SP": 45}), self.day, TODAY)
        self.assertEqual((origin, status["source"]), ("firms_sp", "VIIRS_SNPP_SP"))

    def test_too_few_snpp_falls_through_to_noaa20(self):
        _, _, status = process_days.fetch_day(
            StubFirms(viirs=3, modis=10, extra={"VIIRS_NOAA20_NRT": 30}), self.day, TODAY)
        self.assertEqual(status["source"], "VIIRS_NOAA20_NRT")

    def test_recent_days_are_provisional(self):
        _, _, status = process_days.fetch_day(StubFirms(viirs=50, modis=10), date(2026, 11, 13), TODAY)
        self.assertEqual(status["flag"], "provisional")


class UpgradeTest(unittest.TestCase):
    """Which stored days are worth processing again."""

    def manifest(self, **days):
        old = "2026-11-01T00:00:00+00:00"
        return {"days": {d.replace("_", "-")[1:]: {"has_data": "full", "source": src, "processed_at": old}
                         for d, src in days.items()}}

    def test_final_data_and_snpp_return_trigger_a_revisit(self):
        m = self.manifest(d2026_08_10="VIIRS_SNPP_NRT", d2026_11_05="MODIS_NRT+ML-XGB-1.0-gapfill",
                          d2026_11_06="VIIRS_NOAA20_NRT", d2026_07_01="VIIRS_SNPP_SP", d2026_09_01="VIIRS_SNPP_NRT")
        firms = StubFirms(viirs=None, modis=None, covered={"VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"})
        firms.covers = lambda p, day: (p == "VIIRS_SNPP_SP" and day <= date(2026, 8, 31)) or (p == "VIIRS_SNPP_NRT" and day >= date(2026, 9, 1))
        got = process_days.upgrade_days(m, firms, TODAY, 10)
        # Aug 10: NASA's final S-NPP is out; Nov 5/6: S-NPP is back within 30 days.
        # Jul 1 is already final; Sep 1 is S-NPP near real time with no final version yet.
        self.assertEqual(got, [date(2026, 8, 10), date(2026, 11, 5), date(2026, 11, 6)])

    def test_harmonic_day_revisited_when_noaa20_has_it(self):
        m = self.manifest(d2026_11_05="MODIS_NRT+ML-XGB-1.0-gapfill", d2026_11_06="VIIRS_SNPP_NRT")
        firms = StubFirms(viirs=None, modis=None, covered={"VIIRS_NOAA20_NRT"})
        firms.covers = lambda p, day: p == "VIIRS_NOAA20_NRT"
        self.assertEqual(process_days.upgrade_days(m, firms, TODAY, 10), [date(2026, 11, 5)])

    def test_recently_processed_days_wait(self):
        m = self.manifest(d2026_11_05="VIIRS_NOAA20_NRT")
        m["days"]["2026-11-05"]["processed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        firms = StubFirms(viirs=None, modis=None, covered={"VIIRS_SNPP_NRT"})
        self.assertEqual(process_days.upgrade_days(m, firms, TODAY, 10), [])


class DatasetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LocalStore(Path(self.tmp.name))
        build_hist.build(self.store, None, None, "mock")

    def tearDown(self):
        self.tmp.cleanup()

    def count(self, key, where="true"):
        with duckdb.connect() as db:
            return db.execute(f"SELECT count(*) FROM read_parquet(?) WHERE {where}",
                              [str(Path(self.tmp.name) / key)]).fetchone()[0]

    def test_reprocessing_replaces_a_day(self):
        days = process_days.parse_days("2026-01-01:2026-01-03")
        process_days.run(self.store, FakeFirms(), days, TODAY, log=lambda *_: None)
        first = self.count("live/rows/2026-01.parquet")
        process_days.run(self.store, FakeFirms(), days[:1], TODAY, log=lambda *_: None)
        self.assertEqual(self.count("live/rows/2026-01.parquet"), first)

    def test_manifest_records_source_and_skips_csv_days(self):
        process_days.run(self.store, FakeFirms(), process_days.parse_days("2025-12-30:2026-01-10"), TODAY, log=lambda *_: None)
        manifest = json.loads((Path(self.tmp.name) / "manifest.json").read_text())
        self.assertNotIn("2025-12-30", manifest["days"])  # already in the CSV
        self.assertEqual(manifest["days"]["2026-01-09"]["has_data"], "full")
        self.assertIn("MODIS", manifest["days"]["2026-01-09"]["source"])  # VIIRS offline -> MODIS + Harmonic
        self.assertEqual(manifest["latest_fire_date"], "2026-01-10")
        self.assertEqual(manifest["live_months"], ["2026-01"])

    def test_requested_days_beat_the_backlog(self):
        wanted = process_days.parse_days("2026-11-01:2026-11-14")
        backlog = process_days.parse_days("2026-08-10:2026-12-31")
        days = process_days.choose_days(wanted, backlog, 20)
        self.assertEqual(len(days), 20)
        self.assertTrue(set(wanted) <= set(days))
        self.assertEqual(days[0], date(2026, 8, 10))  # the rest of the cap goes to the oldest gap

    def test_day_totals_match_cells(self):
        root = Path(self.tmp.name)
        process_days.run(self.store, FakeFirms(), process_days.parse_days("2026-01-01:2026-01-05"), TODAY, log=lambda *_: None)
        consolidate.consolidate(self.store)
        with duckdb.connect() as db:
            totals, cells = (db.execute(f"SELECT round(sum(count), 6), count(DISTINCT fire_date) FROM read_parquet(?){w}",
                                        [str(root / f)]).fetchone()
                             for f, w in (("hist/day_totals.parquet", ""),
                                          ("hist/cells.parquet", " WHERE cell_lat BETWEEN -44 AND -9 AND cell_lon BETWEEN 112 AND 154")))
        self.assertEqual(totals, cells)
        self.assertTrue(json.loads((root / "manifest.json").read_text())["totals"])

    def test_near_real_time_days_wait_for_final_data(self):
        root = Path(self.tmp.name)
        # Jan 1-3 on NASA's final data, Jan 4-5 still near real time
        process_days.run(self.store, StubFirms(viirs=None, modis=None, extra={"VIIRS_SNPP_SP": 30}),
                         process_days.parse_days("2026-01-01:2026-01-03"), TODAY, log=lambda *_: None)
        process_days.run(self.store, StubFirms(viirs=30, modis=None),
                         process_days.parse_days("2026-01-04:2026-01-05"), TODAY, log=lambda *_: None)
        manifest = consolidate.consolidate(self.store, final_wait_days=10_000)  # these days are old: don't give up on them
        self.assertEqual(manifest["local_end"], "2026-01-03")
        self.assertEqual(sorted(manifest["days"]), ["2026-01-04", "2026-01-05"])

    def test_consolidate_leaves_other_years_untouched(self):
        root = Path(self.tmp.name)
        before = (root / "hist/rows/2025.parquet").read_bytes()
        process_days.run(self.store, FakeFirms(), process_days.parse_days("2026-01-01:2026-01-05"), TODAY, log=lambda *_: None)
        consolidate.consolidate(self.store)
        self.assertEqual((root / "hist/rows/2025.parquet").read_bytes(), before)

    def test_consolidate_converts_the_old_single_rows_file(self):
        root = Path(self.tmp.name)
        with duckdb.connect() as db:
            db.execute("COPY (SELECT * FROM read_parquet(?)) TO (?) (FORMAT PARQUET)",
                       [str(root / "hist/rows/*.parquet"), str(root / "hist/rows.parquet")])
            n = db.execute("SELECT count(*) FROM read_parquet(?)", [str(root / "hist/rows.parquet")]).fetchone()[0]
        for f in (root / "hist/rows").glob("*.parquet"):
            f.unlink()
        manifest = json.loads((root / "manifest.json").read_text())
        del manifest["row_years"]
        (root / "manifest.json").write_text(json.dumps(manifest))
        process_days.run(self.store, FakeFirms(), process_days.parse_days("2026-01-01:2026-01-05"), TODAY, log=lambda *_: None)
        manifest = consolidate.consolidate(self.store)
        self.assertFalse((root / "hist/rows.parquet").exists())
        self.assertIn("2000", manifest["row_years"])
        self.assertIn("2026", manifest["row_years"])
        with duckdb.connect() as db:
            after = db.execute("SELECT count(*) FROM read_parquet(?) WHERE fire_date < DATE '2026-01-01'",
                               [str(root / "hist/rows/*.parquet")]).fetchone()[0]
        self.assertEqual(after, n)

    def test_missing_days_fill_from_csv_end(self):
        manifest = json.loads((Path(self.tmp.name) / "manifest.json").read_text())
        self.assertEqual(process_days.missing_days(manifest, date(2026, 1, 3), 10),
                         [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)])

    def test_consolidate_moves_days_without_double_counting(self):
        process_days.run(self.store, FakeFirms(), process_days.parse_days("2026-01-01:2026-02-05"), TODAY, log=lambda *_: None)
        with duckdb.connect() as db:
            before = db.execute("SELECT sum(count) FROM read_parquet(?)", [[
                str(Path(self.tmp.name) / k) for k in ("hist/cells.parquet", "live/cells/2026-01.parquet", "live/cells/2026-02.parquet")]]).fetchone()[0]
        consolidate.consolidate(self.store, export_csv="csv/export.csv")
        manifest = json.loads((Path(self.tmp.name) / "manifest.json").read_text())
        self.assertEqual(manifest["local_end"], "2026-02-05")
        self.assertEqual(manifest["days"], {})
        with duckdb.connect() as db:
            after = db.execute("SELECT sum(count) FROM read_parquet(?)", [str(Path(self.tmp.name) / "hist/cells.parquet")]).fetchone()[0]
        self.assertAlmostEqual(before, after, places=6)
        self.assertFalse((Path(self.tmp.name) / "live/cells/2026-01.parquet").exists())
        self.assertIn("2026", manifest["row_years"])
        with duckdb.connect() as db:
            self.assertEqual(db.execute("SELECT min(fire_date)::VARCHAR, max(fire_date)::VARCHAR FROM read_parquet(?)",
                                        [str(Path(self.tmp.name) / "hist/rows/2026.parquet")]).fetchone(), ("2026-01-01", "2026-02-05"))
        header = (Path(self.tmp.name) / "csv/export.csv").read_text().split("\n", 1)[0]
        self.assertEqual(header.split(","), list(process_days.VIIRS_COLUMNS))


class HarmonicModelTest(unittest.TestCase):
    """The ML team's trained model, through the adapter."""

    def test_real_model_turns_modis_into_viirs_rows(self):
        from agni_pipeline import harmonic
        if harmonic.PLACEHOLDER:
            self.skipTest("model files not present")
        day = date(2026, 9, 15)
        rows = [{"latitude": "-14.5", "longitude": f"{132 + i * 0.01:.2f}", "brightness": "330", "scan": "1.1", "track": "1.0",
                 "acq_date": (day if i % 2 else date(2026, 9, 14)).isoformat(), "acq_time": "430", "satellite": "Aqua",
                 "instrument": "MODIS", "confidence": "80", "version": "6.1NRT", "bright_t31": "300", "frp": "20", "daynight": "D"}
                for i in range(30)]  # acq_time without its leading zero, on purpose
        out = harmonic.predict(rows, day)
        self.assertGreater(len(out), 30)  # VIIRS sees several pixels per MODIS pixel
        self.assertEqual(list(out[0]), list(process_days.VIIRS_COLUMNS))
        self.assertTrue(all(r["version"] == "ML-XGB-1.0-gapfill" and len(r["acq_time"]) == 4 for r in out))
        day_w, night_w = harmonic.gapfill_weights()
        self.assertEqual((day_w[10], night_w[10]), (1.0596, 1.111))  # same as the processed data

    def test_gapfill_day_uses_model_and_its_weights(self):
        from agni_pipeline import harmonic
        if harmonic.PLACEHOLDER:
            self.skipTest("model files not present")
        origin, rows, status = process_days.fetch_day(StubFirms(viirs=None, modis=25), date(2026, 10, 1), TODAY)
        self.assertEqual((origin, status["flag"]), ("gapfill", "viirs_offline"))
        self.assertIn("ML-XGB-1.0-gapfill", status["source"])
        self.assertGreater(len(rows), 0)


class ParquetInputTest(unittest.TestCase):
    """Per-year Parquet input must give exactly the same dataset as the CSV."""

    def test_per_year_parquet_matches_csv_build(self):
        from agni_pipeline.common import load_backend_builder

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path, status_path = load_backend_builder().generate_mock(root)
            years = root / "years"
            years.mkdir()
            with duckdb.connect() as db:
                db.execute("CREATE TABLE src AS SELECT * FROM read_csv(?, header=true, all_varchar=true)", [str(csv_path)])
                for (year,) in db.execute("SELECT DISTINCT substr(acq_date, 1, 4) FROM src").fetchall():
                    # acq_time as a number (the leading-zero trap) and dates as real DATEs
                    db.execute("""COPY (SELECT * REPLACE (acq_time::INT AS acq_time, acq_date::DATE AS acq_date)
                                        FROM src WHERE substr(acq_date, 1, 4) = ?) TO (?) (FORMAT PARQUET)""",
                               [year, str(years / f"viirs_{year}.parquet")])

            from_csv, from_parquet = LocalStore(root / "a"), LocalStore(root / "b")
            build_hist.build(from_csv, csv_path, status_path, "real")
            build_hist.build(from_parquet, None, status_path, "real", parquet_files=sorted(years.glob("*.parquet")))
            with duckdb.connect() as db:
                for name, glob in (("cells", "hist/cells.parquet"), ("rows", "hist/rows/*.parquet")):
                    a, b = (db.execute("SELECT count(*), round(sum(count), 6) FROM read_parquet(?)" if name == "cells"
                                       else "SELECT count(*), count(DISTINCT acq_time) FROM read_parquet(?)",
                                       [str(root / s / glob)]).fetchone() for s in ("a", "b"))
                    self.assertEqual(a, b, name)
                times = db.execute("SELECT DISTINCT acq_time FROM read_parquet(?)", [str(root / "b/hist/rows/*.parquet")]).fetchall()
                self.assertTrue(all(len(t) == 4 for (t,) in times))  # "0401" kept, not "401"

            # without a status file, coverage is derived from the detections
            derived = LocalStore(root / "c")
            manifest = build_hist.build(derived, None, None, "real", parquet_files=sorted(years.glob("*.parquet")))
            self.assertEqual(manifest["record_start"], "2000-11-01")
            self.assertTrue(any(g["from"] == "2001-06-17" for g in manifest["known_gaps"]))
            self.assertEqual(sorted(manifest["row_years"]), [str(y) for y in range(2000, 2026)])
            self.assertFalse((root / "c/hist/rows.parquet").exists())


class DerivedStatusTest(unittest.TestCase):
    """Day coverage worked out from the detections when no status file is uploaded."""

    def test_full_partial_none_and_daynight(self):
        from agni_pipeline.common import derive_day_status
        with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as db:
            # per day: number of rows and origin
            plan = {"2021-12-30": (150, "real"), "2021-12-31": (150, "real"), "2022-01-01": (0, None),
                    "2022-01-02": (150, "real"), "2022-01-03": (150, "real"), "2022-01-04": (40, "real"),
                    "2022-01-05": (150, "gapfill"), "2022-01-06": (150, "real")}
            db.execute("CREATE TABLE r (fire_date DATE, origin VARCHAR)")
            for d, (n, origin) in plan.items():
                if n:
                    db.execute(f"INSERT INTO r SELECT DATE '{d}', '{origin}' FROM range({n})")
            out = Path(tmp) / "status.parquet"
            derive_day_status(db, "r", out)
            got = {str(d): (h, r, f) for d, h, r, f in db.execute(
                "SELECT fire_date, has_data, daynight_reliable, flag FROM read_parquet(?)", [str(out)]).fetchall()}
        self.assertEqual(got["2021-12-30"], ("full", True, None))
        self.assertEqual(got["2021-12-31"][0], "partial")        # next to an outage day
        self.assertEqual(got["2022-01-01"], ("none", True, "outage"))
        self.assertEqual(got["2022-01-02"][0], "partial")
        self.assertEqual(got["2022-01-04"], ("full", True, "low_coverage"))  # a quiet day is fully observed
        self.assertEqual(got["2022-01-05"], ("full", False, None))  # gap-filled from 2022: day/night unreliable


if __name__ == "__main__":
    unittest.main()
