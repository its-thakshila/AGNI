"""Add fire-days after the processed CSV to the dataset.

For each fire-day:
  1. VIIRS from FIRMS: S-NPP (the satellite of the processed data), or when S-NPP is down the same
     instrument on NOAA-20 / NOAA-21; NASA's final version before near real time -> firms_sp / firms_nrt
  2. otherwise (no VIIRS delivered): MODIS from FIRMS, converted to VIIRS-equivalent
     detections by Harmonic                                                        -> gapfill
  3. if neither is available: recorded as "no data" and retried on the next run

Later runs revisit stored days (--upgrade): recent days that had to use NOAA-20/21 or Harmonic are
tried again for S-NPP for RECHECK_DAYS, and any day still on near-real-time data is reprocessed
once NASA publishes its final version (about 2-3 months later, with NASA's static-source "type"),
so every day ends up on the same footing as the processed history.

Results are merged into live/rows/YYYY-MM.parquet and live/cells/YYYY-MM.parquet, and every
decision is written to manifest.json, so the website and later runs know what is stored.

Usage:
  python -m agni_pipeline.process_days --store r2:mock --recent 3 --fill-missing
  python -m agni_pipeline.process_days --store local:../frontend/public/data/mock --days 2026-11-01:2026-11-13 --fake
"""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from . import harmonic
from .common import BBOX, CELLS_SELECT, ROW_COLUMNS, ROWS_TABLE_SQL, VIIRS_COLUMNS, load_backend_builder, month_key
from .firms import FINAL_PRODUCTS, MODIS_PRODUCTS, VIIRS_PRODUCTS, FakeFirms, Firms, FirmsError, is_final, is_snpp, utc_days
from .storage import open_store, read_json, write_json

MIN_VIIRS_DETECTIONS = int(os.getenv("AGNI_MIN_VIIRS", "20"))  # fewer nationwide = satellite effectively offline
PROVISIONAL_DAYS = 3  # near-real-time data can still change for about 3 days
RETRY_NONE_AFTER_HOURS = 6
RECHECK_DAYS = 30           # how long a day on NOAA-20/21 or Harmonic is retried for S-NPP
REVISIT_AFTER_HOURS = 12    # a stored day is looked at again at most this often


def _first(firms, products, day: date, minimum: int):
    """Try products in order; the first answer with at least `minimum` rows wins.
    Also returns the largest answer seen (for when none reaches the minimum)."""
    best = None
    for product in products:
        got = firms.first((product,), day)
        if got is None:
            continue
        if len(got.rows) >= minimum:
            return got, got
        if best is None or len(got.rows) > len(best.rows):
            best = got
    return None, best


def fetch_day(firms, day: date, today: date) -> tuple[str, list[dict], dict]:
    """Decide the source for one fire-day. Returns (origin, raw 15-column rows, status)."""
    provisional = day >= today - timedelta(days=PROVISIONAL_DAYS - 1)
    in_progress = day >= today
    status = {"daynight_reliable": True, "flag": "provisional" if provisional else None}

    viirs, viirs_best = _first(firms, VIIRS_PRODUCTS, day, MIN_VIIRS_DETECTIONS)
    if viirs:
        origin = "firms_nrt" if viirs.product.endswith("NRT") else "firms_sp"
        rows = [_viirs_row(r) for r in viirs.rows]
        if not is_snpp(viirs.product) and not provisional:
            status["flag"] = "snpp_offline"  # observed by VIIRS, on NOAA-20/21 instead of S-NPP
        return origin, rows, {**status, "has_data": "partial" if in_progress else "full", "source": viirs.product}

    modis, _ = _first(firms, MODIS_PRODUCTS, day, 1)
    if modis is not None:
        # Harmonic's features use MODIS from CONTEXT_BEFORE days before to CONTEXT_AFTER days after
        # (as in training), so fetch that window; days after today don't exist yet and are skipped.
        context = list(modis.rows)
        for offset in range(-harmonic.CONTEXT_BEFORE, harmonic.CONTEXT_AFTER + 1):
            other = day + timedelta(days=offset)
            if offset == 0 or other > today:
                continue
            extra, _ = _first(firms, MODIS_PRODUCTS, other, 1)
            if extra is not None:
                context += extra.rows
        rows = harmonic.predict(context, day)
        return "gapfill", rows, {
            "has_data": "partial" if in_progress else "full", "source": f"{modis.product}+{harmonic.model_version()}",
            "daynight_reliable": False, "flag": "viirs_offline",
        }

    if viirs_best is not None:  # VIIRS answered with too few detections and MODIS is unavailable too
        origin = "firms_nrt" if viirs_best.product.endswith("NRT") else "firms_sp"
        return origin, [_viirs_row(r) for r in viirs_best.rows], {**status, "has_data": "partial", "source": viirs_best.product, "flag": "firms_low"}

    return "none", [], {"has_data": "none", "source": None, "daynight_reliable": True, "flag": "firms_error"}


def _viirs_row(row: dict) -> dict:
    out = {name: row.get(name, "") for name in VIIRS_COLUMNS}
    out["acq_time"] = str(out["acq_time"]).zfill(4)
    out["satellite"] = out["satellite"] or "N"
    out["instrument"] = out["instrument"] or "VIIRS"
    out["type"] = out["type"] or "0"
    return out


def _derive_rows(db, raw: list[dict], origin: str, day: date, weights) -> None:
    """raw 15-column rows -> table `new_rows` with fire_date, pass, origin, cell and weight."""
    db.execute("CREATE OR REPLACE TEMP TABLE raw (" + ", ".join(f'"{c}" VARCHAR' for c in VIIRS_COLUMNS) + ")")
    if raw:
        db.executemany(f"INSERT INTO raw VALUES ({', '.join('?' for _ in VIIRS_COLUMNS)})",
                       [[str(r.get(c, "")) for c in VIIRS_COLUMNS] for r in raw])
    day_weights, night_weights = weights
    db.execute(f"""
        CREATE OR REPLACE TEMP TABLE new_rows AS
        WITH typed AS (
            SELECT latitude::DOUBLE AS latitude, longitude::DOUBLE AS longitude,
                   TRY_CAST(bright_ti4 AS DOUBLE) AS bright_ti4, round(TRY_CAST(scan AS DOUBLE), 2) AS scan,
                   round(TRY_CAST(track AS DOUBLE), 2) AS track, acq_date::DATE AS acq_date, lpad(acq_time, 4, '0') AS acq_time,
                   satellite, instrument, confidence, version, TRY_CAST(bright_ti5 AS DOUBLE) AS bright_ti5,
                   COALESCE(TRY_CAST(frp AS DOUBLE), 0) AS frp, daynight, COALESCE(TRY_CAST(type AS INT), 0) AS type
            FROM raw
        ), solar AS (
            SELECT *, acq_date + strptime(acq_time, '%H%M')::TIME + INTERVAL '1 second' * (longitude * 240) AS solar_time
            FROM typed
            WHERE latitude BETWEEN {BBOX[1]} AND {BBOX[3]} AND longitude BETWEEN {BBOX[0]} AND {BBOX[2]}
        ), keyed AS (
            SELECT *, (solar_time - INTERVAL '6 hours')::DATE AS fire_date,
                   CASE WHEN extract(hour FROM solar_time) BETWEEN 6 AND 17 THEN 'D' ELSE 'N' END AS "pass",
                   ? AS origin, floor(longitude * 10)::INT AS ci, floor(latitude * 10)::INT AS cj
            FROM solar
        )
        SELECT {', '.join(f'"{c}"' for c in ROW_COLUMNS if c != 'weight')},
               CASE WHEN origin <> 'gapfill' THEN 1.0
                    WHEN "pass" = 'D' THEN list_extract(?, month(fire_date))
                    ELSE list_extract(?, month(fire_date)) END::DOUBLE AS weight
        FROM keyed WHERE fire_date = ?
    """, [origin, day_weights, night_weights, day])


def _write_month(store, db, month: str, days: list[date]) -> int:
    """Replace `days` inside live/rows/<month>.parquet with new_rows, then rebuild live/cells/<month>."""
    with tempfile.TemporaryDirectory() as tmp:
        rows_path, cells_path = Path(tmp) / "rows.parquet", Path(tmp) / "cells.parquet"
        db.execute(ROWS_TABLE_SQL.format(name="month_rows").replace("CREATE TABLE", "CREATE OR REPLACE TEMP TABLE"))
        existing = store.read(f"live/rows/{month}.parquet")
        if existing:
            old = Path(tmp) / "old.parquet"
            old.write_bytes(existing)
            db.execute(f"INSERT INTO month_rows SELECT {', '.join(f'{chr(34)}{c}{chr(34)}' for c in ROW_COLUMNS)} "
                       "FROM read_parquet(?) WHERE fire_date NOT IN (SELECT unnest(?::DATE[]))", [str(old), days])
        db.execute("INSERT INTO month_rows SELECT * FROM month_rows_new")
        total = db.execute("SELECT count(*) FROM month_rows").fetchone()[0]
        db.execute("COPY (SELECT * FROM month_rows ORDER BY acq_date, acq_time) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)", [str(rows_path)])
        db.execute(f"COPY ({CELLS_SELECT.format(source='month_rows')} ORDER BY fire_date, cj, ci) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)", [str(cells_path)])
        store.write(f"live/rows/{month}.parquet", rows_path.read_bytes())
        store.write(f"live/cells/{month}.parquet", cells_path.read_bytes())
        return total


def run(store, firms, days: list[date], today: date, log=print) -> dict:
    manifest = read_json(store, "manifest.json", None)
    if manifest is None:
        raise SystemExit("manifest.json not found: build the historical files first (build_hist).")
    builder = load_backend_builder()
    # exact per-month day/night weights from the trained model when it is present
    weights = harmonic.gapfill_weights() or (builder.DAY_WEIGHTS, builder.NIGHT_WEIGHTS)
    local_end = date.fromisoformat(manifest["local_end"])
    days = sorted({d for d in days if local_end < d <= today})
    if not days:
        log("Nothing to process.")
        return manifest

    by_month: dict[str, list[date]] = {}
    with duckdb.connect() as db:
        db.execute(ROWS_TABLE_SQL.format(name="all_new").replace("CREATE TABLE", "CREATE TEMP TABLE"))
        for day in days:
            try:
                origin, raw, status = fetch_day(firms, day, today)
            except FirmsError as error:
                origin, raw, status = "none", [], {"has_data": "none", "source": None, "daynight_reliable": True, "flag": "firms_error"}
                log(f"{day}: FIRMS error ({error})")
            _derive_rows(db, raw, origin, day, weights)
            db.execute("INSERT INTO all_new SELECT * FROM new_rows")
            n = db.execute("SELECT count(*) FROM new_rows").fetchone()[0]
            manifest["days"][day.isoformat()] = {**status, "detections": n, "processed_at": datetime.now(UTC).isoformat(timespec="seconds")}
            by_month.setdefault(month_key(day), []).append(day)
            log(f"{day}: {status['has_data']:7} {origin:9} {n:6} detections  ({status['source']})")

        for month, month_days in sorted(by_month.items()):
            db.execute("CREATE OR REPLACE TEMP TABLE month_rows_new AS SELECT * FROM all_new "
                       "WHERE strftime(fire_date, '%Y-%m') = ?", [month])
            _write_month(store, db, month, month_days)

    months = set(manifest.get("live_months", [])) | set(by_month)
    manifest["live_months"] = sorted(months)
    with_data = [d for d, s in manifest["days"].items() if s["has_data"] != "none"]
    manifest["latest_fire_date"] = max([manifest["local_end"], *with_data])
    manifest["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    write_json(store, "manifest.json", manifest)
    for day in days:
        store.delete(f"pending/{day.isoformat()}")
    return manifest


def missing_days(manifest: dict, today: date, limit: int) -> list[date]:
    """Days after the CSV that have no entry yet, or whose 'no data' entry is old enough to retry."""
    out, day = [], date.fromisoformat(manifest["local_end"]) + timedelta(days=1)
    now = datetime.now(UTC)
    while day <= today and len(out) < limit:
        entry = manifest["days"].get(day.isoformat())
        stale_none = entry and entry["has_data"] == "none" and (
            now - datetime.fromisoformat(entry["processed_at"])).total_seconds() > RETRY_NONE_AFTER_HOURS * 3600
        if entry is None or stale_none:
            out.append(day)
        day += timedelta(days=1)
    return out


def choose_days(wanted: list[date], backlog: list[date], limit: int) -> list[date]:
    """Requested/recent days first, then the oldest missing days, up to `limit`; returned in date order."""
    picked = list(dict.fromkeys(sorted(wanted)))[:limit]
    picked += [d for d in backlog if d not in picked][: limit - len(picked)]
    return sorted(picked)


def upgrade_days(manifest: dict, firms, today: date, limit: int) -> list[date]:
    """Stored days worth processing again, oldest first:
      - NASA's final data now exists for a day still on near-real-time data (or on Harmonic from
        near-real-time MODIS): the final version brings NASA's types, positions and confidence;
      - a recent day that had to use NOAA-20/21 or Harmonic, when FIRMS now has S-NPP for it;
      - a recent Harmonic day, when FIRMS has any VIIRS (e.g. NOAA-20) for it;
      - a Harmonic day estimated before the MODIS days after it existed (its features look
        harmonic.CONTEXT_AFTER days ahead): estimated once more when they do.
    Only FIRMS's availability list is consulted here (one request); a day is looked at again at
    most every REVISIT_AFTER_HOURS."""
    out, now = [], datetime.now(UTC)
    for key, entry in sorted(manifest["days"].items()):
        day, source = date.fromisoformat(key), entry.get("source")
        if entry["has_data"] == "none" or is_final(source):
            continue  # "no data" days are retried by missing_days; final days are done
        processed = entry.get("processed_at")
        if processed and (now - datetime.fromisoformat(processed)).total_seconds() < REVISIT_AFTER_HOURS * 3600:
            continue
        final_ready = any(firms.covers(p, day) is True for p in FINAL_PRODUCTS)
        recent = day >= today - timedelta(days=RECHECK_DAYS)
        snpp_back = recent and not is_snpp(source) and firms.covers("VIIRS_SNPP_NRT", day) is True
        # a Harmonic estimate is also replaced by real VIIRS from NOAA-20/21 when that exists
        viirs_now = (recent and (source or "").startswith("MODIS")
                     and any(firms.covers(p, day) is True for p in VIIRS_PRODUCTS))
        # estimated too early for its look-ahead window: predict again now that the window exists
        context_ready = ((source or "").startswith("MODIS") and processed is not None
                         and today > day + timedelta(days=harmonic.CONTEXT_AFTER)
                         and datetime.fromisoformat(processed).date() <= day + timedelta(days=harmonic.CONTEXT_AFTER))
        if final_ready or snpp_back or viirs_now or context_ready:
            out.append(day)
        if len(out) >= limit:
            break
    return out


def parse_days(text: str) -> list[date]:
    days = []
    for part in filter(None, (p.strip() for p in (text or "").split(","))):
        first, _, last = part.partition(":")
        days += list(utc_days(date.fromisoformat(first), date.fromisoformat(last or first)))
    return days


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True, help="local:<folder> or r2:<prefix>")
    parser.add_argument("--days", default="", help="e.g. 2026-11-01:2026-11-13,2026-10-05")
    parser.add_argument("--recent", type=int, default=0, help="also reprocess the last N days (provisional data)")
    parser.add_argument("--fill-missing", action="store_true", help="also process days after the CSV that are missing")
    parser.add_argument("--upgrade", action="store_true",
                        help="also reprocess stored days when S-NPP returns or NASA's final data is published")
    parser.add_argument("--max-days", type=int, default=62, help="safety cap per run")
    parser.add_argument("--fake", action="store_true", help="use deterministic fake FIRMS data (no network, no key)")
    parser.add_argument("--today", type=date.fromisoformat, help="override today's UTC date (testing)")
    args = parser.parse_args()

    store = open_store(args.store)
    today = args.today or datetime.now(UTC).date()
    firms = FakeFirms() if args.fake else Firms(os.getenv("FIRMS_MAP_KEY", ""))
    global MIN_VIIRS_DETECTIONS
    if args.fake and "AGNI_MIN_VIIRS" not in os.environ:
        MIN_VIIRS_DETECTIONS = 1  # the fake data is deliberately sparse
    manifest = read_json(store, "manifest.json", None)
    if manifest is None:
        raise SystemExit(f"{store}: manifest.json not found. Run build_hist first.")

    # Days someone asked for (the Worker passes the days a visitor is looking at) and the last few
    # days come first; the backlog only fills what is left of the per-run cap. Before, the backlog
    # was sorted in front and pushed the requested days past the cap, so they stayed "pending".
    wanted = parse_days(args.days)
    if args.recent:
        wanted += [today - timedelta(days=i) for i in range(args.recent)]
    wanted = sorted({d for d in wanted if d <= today})
    backlog = missing_days(manifest, today, args.max_days) if args.fill_missing else []
    if args.upgrade:
        backlog += [d for d in upgrade_days(manifest, firms, today, args.max_days) if d not in backlog]
    days = choose_days(wanted, backlog, args.max_days)
    print(f"{store}: processing {len(days)} day(s)" + (" with FAKE FIRMS data" if args.fake else ""))
    if harmonic.PLACEHOLDER:
        # never let made-up estimates reach real data: the stand-in is for fake/test runs only
        if not args.fake:
            raise SystemExit("Harmonic model files are missing (pipeline/harmonic/ml_pipeline/output/models/); "
                             "refusing to process real days with the placeholder.")
        print("note: Harmonic model files not found, using the test placeholder")
    run(store, firms, days, today)


if __name__ == "__main__":
    main()
