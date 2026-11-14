"""Fetch and cache nationwide VIIRS S-NPP detections by UTC acquisition day."""

import csv
import io
import json
import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

import duckdb

from .contracts import LOCAL_END
from .local_store import ROW_COLUMNS, dates_between


class FirmsUnavailable(Exception):
    pass


def download(key, source, day):
    url = (
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"{quote(key, safe='')}/{source}/112,-44,154,-9/1/{day}"
    )
    for attempt in range(3):
        try:
            with urlopen(url, timeout=8) as response:
                return response.read().decode("utf-8-sig")
        except HTTPError as error:
            if not 500 <= error.code < 600:
                raise FirmsUnavailable from None
        except (URLError, TimeoutError):
            pass
        except UnicodeError:
            raise FirmsUnavailable from None
        if attempt < 2:
            sleep(2 ** attempt)
    raise FirmsUnavailable


def parse_csv(text, day):
    reader = csv.DictReader(io.StringIO(text))
    required = {"acq_date", "acq_time", "latitude", "longitude", "frp"}
    if not required <= set(reader.fieldnames or []):
        raise FirmsUnavailable

    records = set()
    for row in reader:
        try:
            if date.fromisoformat(row["acq_date"]) != day:
                continue
            utc = datetime.strptime(row["acq_date"] + row["acq_time"].zfill(4), "%Y-%m-%d%H%M")
            lat, lon, frp = (float(row[key]) for key in ("latitude", "longitude", "frp"))
            kind = int(row.get("type") or 0)
            if not (-44 <= lat <= -9 and 112 <= lon <= 154 and math.isfinite(frp)):
                raise ValueError
        except (ValueError, TypeError):
            raise FirmsUnavailable from None
        solar = utc + timedelta(hours=lon / 15)
        fire_day = (solar - timedelta(hours=6)).date()
        pass_name = "D" if 6 <= solar.hour < 18 else "N"
        try:
            measurements = [float(row[name]) for name in ("bright_ti4", "scan", "track", "bright_ti5")]
            if not all(map(math.isfinite, measurements)):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise FirmsUnavailable from None
        records.add((lat, lon, *measurements[:3], day.isoformat(), utc.strftime("%H%M"),
                     "N", "VIIRS", row.get("confidence") or "n", row.get("version") or "2",
                     measurements[3], frp, row.get("daynight") or pass_name,
                     kind, fire_day.isoformat(), 1.0, pass_name))
    return sorted(records)


class FirmsSource:
    def __init__(self, key, path: Path, fetch=download):
        self.key = key
        self.path = path
        self.fetch = fetch
        self.lock = Lock()

    def query(self, bbox, start, end, types, group_by, today):
        if not self.key and not self.path.is_file():
            return [], {day: ("none", True, "firms_error") for day in dates_between(start, end)}

        utc_today = datetime.now(UTC).date()
        results = {}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with duckdb.connect(str(self.path)) as db:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS firms_days_v2 (
                        utc_day DATE PRIMARY KEY, origin VARCHAR, records VARCHAR, fetched TIMESTAMPTZ
                    )
                """)
                for day in dates_between(start - timedelta(days=1), min(end, utc_today)):
                    results[day] = self.load_day(db, day, utc_today)

        selected = []
        totals = defaultdict(lambda: [0.0, 0.0, 0])
        status = {}
        for day in dates_between(start, end):
            previous, current = results.get(day - timedelta(days=1)), results.get(day)
            partitions = [entry for entry in (previous, current) if entry is not None]
            provisional = day >= today - timedelta(days=2)
            flag = "provisional" if provisional else None
            if len(partitions) < 2:
                flag = "firms_error"
            low = any(len(detections) < 100 for _, detections in partitions)
            if low and len(partitions) == 2:
                flag = "firms_low"
            coverage = "full" if len(partitions) == 2 and not low and day < today else "partial"
            if not partitions:
                coverage = "none"
            status[day] = (coverage, True, flag)
            for origin, detections in partitions:
                for record in detections:
                    row = dict(zip((name for name in ROW_COLUMNS if name != "origin"), record))
                    if row["fire_date"] != day.isoformat() or row["type"] not in types:
                        continue
                    lat, lon = row["latitude"], row["longitude"]
                    if group_by == "rows":
                        if bbox.min_lat <= lat <= bbox.max_lat and bbox.min_lon <= lon <= bbox.max_lon:
                            selected.append(tuple(origin if name == "origin" else row[name] for name in ROW_COLUMNS))
                        continue
                    frp, pass_name = row["frp"], row["pass"]
                    ci, cj = math.floor(lon * 10), math.floor(lat * 10)
                    if not (bbox.min_lat <= (cj + 0.5) / 10 <= bbox.max_lat
                            and bbox.min_lon <= (ci + 0.5) / 10 <= bbox.max_lon):
                        continue
                    dimensions = (day, pass_name, origin) if group_by == "daily" else (ci, cj, origin)
                    value = totals[dimensions]
                    value[0] += 1
                    value[1] += frp
                    value[2] += 1
        if group_by == "rows":
            return selected, status
        return [(*key, *value) for key, value in totals.items()], status

    def metadata(self):
        if not self.path.is_file():
            return LOCAL_END, None
        with self.lock, duckdb.connect(str(self.path)) as db:
            exists = db.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = 'firms_days_v2'"
            ).fetchone()[0]
            if not exists:
                return LOCAL_END, None
            day, fetched = db.execute(
                "SELECT max(utc_day), epoch(max(fetched)) FROM firms_days_v2"
            ).fetchone()
        return max(LOCAL_END, day or LOCAL_END), datetime.fromtimestamp(fetched, UTC) if fetched is not None else None

    def load_day(self, db, day, today):
        cached = db.execute(
            "SELECT origin, records, epoch(fetched) FROM firms_days_v2 WHERE utc_day = ?", [day]
        ).fetchone()
        now = datetime.now(UTC)
        if cached:
            origin, payload, fetched = cached
            rows = json.loads(payload)
            age = now.timestamp() - fetched
            low = len(rows) < 100
            ttl = 3600 if day == today else 10800
            if low:
                valid = day < today - timedelta(days=7) or age < 21600
            else:
                valid = origin == "firms_sp" or day < today - timedelta(days=2) or age < ttl
            if valid:
                return origin, rows

        if self.key:
            sources = [("VIIRS_SNPP_NRT", "firms_nrt"), ("VIIRS_SNPP_SP", "firms_sp")]
            if day < today - timedelta(days=90):
                sources.reverse()
            for product, origin in sources:
                try:
                    rows = parse_csv(self.fetch(self.key, product, day), day)
                except FirmsUnavailable:
                    continue
                db.execute("INSERT OR REPLACE INTO firms_days_v2 VALUES (?, ?, ?, ?)",
                           [day, origin, json.dumps(rows), now])
                return origin, rows
        return (cached[0], json.loads(cached[1])) if cached else None
