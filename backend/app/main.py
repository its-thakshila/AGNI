import os
import re
from calendar import monthrange
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import analytics
from .contracts import BBox, COVERAGE, GRID_MAX_DAYS, LOCAL_END, RECORD_START
from .firms import FirmsSource
from .local_store import LocalSource, dates_between


class ApiError(Exception):
    def __init__(self, code, message, field=None, hint=None, status=400):
        self.body = {"error": {"code": code, "message": message, "field": field, "hint": hint}}
        self.status = status


def required(value, field):
    if value is None:
        raise ApiError("MISSING_PARAMETER", f"{field} is required.", field)
    return value


def coordinate(value, field, limit, low, high):
    try:
        number = float(required(value, field))
    except ValueError:
        raise ApiError("INVALID_PARAMETER", f"{field} must be numeric.", field) from None
    if not -limit <= number <= limit:
        raise ApiError("COORDINATE_OUT_OF_RANGE", f"{field} is outside +/-{limit}.", field)
    if not low <= number <= high:
        raise ApiError("BOX_OUTSIDE_COVERAGE", "The selected area must be inside Australia.", field)
    return number


def fire_date(value, field):
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
    except ValueError:
        pass
    raise ApiError("INVALID_PARAMETER", f"{field} must be YYYY-MM-DD.", field)


FLAGS = {
    "outage": ("KNOWN_OUTAGE", "Satellite observations are missing during a known outage."),
    "firms_low": ("FIRMS_LOW_DELIVERY", "Some recent days have incomplete satellite delivery."),
    "firms_error": ("FIRMS_UNAVAILABLE", "Recent satellite observations are unavailable."),
    "provisional": ("RECENT_DATA_PROVISIONAL", "Recent counts may still change."),
}

LIMITS = {
    "hotspots_max_limit": 20_000,
    "small_box_deg": 0.5,
    "hotspots_max_deg2_days": 4000,
    "csv_max_deg2_days": 20_000,
    "grid_max_days": GRID_MAX_DAYS,
}


def page_integer(value, field, minimum, maximum):
    if not re.fullmatch(r"[0-9]{1,19}", value) or not minimum <= int(value) <= maximum:
        raise ApiError("INVALID_PARAMETER", f"{field} must be between {minimum} and {maximum}.", field)
    return int(value)


def query_year(value, field, default):
    if value is None:
        return default
    if not re.fullmatch(r"[0-9]{4}", value):
        raise ApiError("INVALID_PARAMETER", f"{field} must be a four-digit year.", field)
    return int(value)


def create_app(data_dir=None, firms_source=None):
    app = FastAPI(title="AGNI API", version="1.2")
    default_dir = Path(__file__).resolve().parents[1] / "data"
    configured_dir = data_dir or os.getenv("AGNI_DATA_DIR")
    source = LocalSource(Path(configured_dir) if configured_dir else default_dir)
    if not configured_dir and not source.available:
        synthetic = LocalSource(default_dir / "synthetic")
        if synthetic.available:
            source = synthetic
    firms = firms_source or FirmsSource(
        os.getenv("FIRMS_MAP_KEY"),
        Path(os.getenv("AGNI_FIRMS_CACHE", default_dir / "firms.duckdb")),
    )
    origins = ["http://localhost:5173", "https://its-thakshila.github.io"]
    origins.extend(origin.strip() for origin in os.getenv("AGNI_CORS_ORIGINS", "").split(",") if origin.strip())
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"])

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, error: ApiError):
        return JSONResponse(error.body, status_code=error.status)

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, _error: Exception):
        error = ApiError("INTERNAL_ERROR", "An internal error occurred.", status=500)
        return JSONResponse(error.body, status_code=error.status)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/meta")
    def meta():
        recent, fetched = firms.metadata()
        return {
            "coverage": {"record_start": RECORD_START, "local_end": LOCAL_END,
                         "latest_fire_date": max(source.latest_fire_date(), recent), "bbox": asdict(COVERAGE)},
            "periods": [
                {"from": "2000-11-01", "to": "2002-07-03", "origin": "ml",
                 "label": "Modelled from MODIS Terra", "uncertainty": 0.16},
                {"from": "2002-07-04", "to": "2011-12-31", "origin": "ml",
                 "label": "Modelled from MODIS Terra + Aqua", "uncertainty": 0.11},
                {"from": "2012-01-01", "to": LOCAL_END, "origin": "real",
                 "label": "VIIRS S-NPP observations and modelled gap fills", "uncertainty": 0.0},
                {"from": "2026-01-01", "to": None, "origin": "firms_nrt",
                 "label": "VIIRS S-NPP via NASA FIRMS", "uncertainty": 0.0},
            ],
            "known_gaps": [
                {"from": "2001-06-17", "to": "2001-07-02", "reason": "MODIS outage"},
                {"from": "2002-03-21", "to": "2002-03-27", "reason": "MODIS outage"},
            ],
            "types": [{"value": value, "label": label} for value, label in
                      [(0, "Vegetation fire"), (2, "Static / industrial source"), (3, "Offshore")]],
            "limits": LIMITS,
            "firms": {"status": "ok" if fetched else "unavailable", "last_fetch": fetched},
            "contract_version": "1.2",
        }

    def report(kind, lat1, lon1, lat2, lon2, start, end, types, *, periods=None,
               query_fields=None, summarize=None, page=None):
        min_lat, max_lat = sorted([
            coordinate(lat1, "lat1", 90, COVERAGE.min_lat, COVERAGE.max_lat),
            coordinate(lat2, "lat2", 90, COVERAGE.min_lat, COVERAGE.max_lat),
        ])
        min_lon, max_lon = sorted([
            coordinate(lon1, "lon1", 180, COVERAGE.min_lon, COVERAGE.max_lon),
            coordinate(lon2, "lon2", 180, COVERAGE.min_lon, COVERAGE.max_lon),
        ])
        if min_lat == max_lat or min_lon == max_lon:
            raise ApiError("EMPTY_BOX", "Select an area with a nonzero width and height.")
        bbox = BBox(min_lat, max_lat, min_lon, max_lon)
        start_day = fire_date(required(start, "start"), "start")
        end_day = start_day if end is None else fire_date(end, "end")
        today = datetime.now(ZoneInfo("Australia/Sydney")).date()
        if not RECORD_START <= start_day <= end_day <= today:
            raise ApiError("INVALID_DATE_RANGE", "Choose a date range inside the record.",
                           "start" if start_day < RECORD_START else "end")
        if not re.fullmatch(r"[023](?:,[023])*", types):
            raise ApiError("INVALID_PARAMETER", "types must be a comma-separated list of 0, 2, or 3.", "types")
        fire_types = sorted({int(value) for value in types.split(",")})
        if kind == "grid" and (end_day - start_day).days >= GRID_MAX_DAYS:
            raise ApiError("QUERY_TOO_LARGE", f"Grid requests can cover up to {GRID_MAX_DAYS} days.",
                           "end", "Choose a shorter range or use /api/daily.")
        if kind in {"hotspots", "csv"}:
            area_days = (max_lat - min_lat) * (max_lon - min_lon) * ((end_day - start_day).days + 1)
            if area_days > LIMITS[f"{kind}_max_deg2_days"]:
                raise ApiError("QUERY_TOO_LARGE", "This request covers too much area or time.",
                               hint="Select a smaller area or date range, or use /api/daily.")
        row_request = kind in {"hotspots", "csv"}
        if row_request and start_day <= LOCAL_END and not source.row_available():
            raise ApiError("INTERNAL_ERROR", "Historical hotspot rows are unavailable.", status=500)
        recent_hotspots, row_status = [], {}
        if row_request and end_day > LOCAL_END:
            recent_hotspots, row_status = firms.query(
                bbox, max(start_day, LOCAL_END + timedelta(days=1)), end_day, fire_types, "rows", today,
            )
            if start_day > LOCAL_END and not any(state != "none" for state, _, _ in row_status.values()):
                raise ApiError("UPSTREAM_ERROR", "Recent satellite observations are unavailable.", status=502)
        if kind == "csv":
            filename = f"hotspots_{min_lat}_{min_lon}_{max_lat}_{max_lon}_{start_day}_{end_day}.csv"
            return StreamingResponse(
                source.hotspot_csv(bbox, start_day, end_day, fire_types, recent_hotspots),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        periods = periods or [(start_day, end_day)]
        rows, status, days, missing = [], {}, [], []
        for first, last in periods:
            group_by = "grid" if kind == "grid" else "daily"
            period_rows, period_status = source.summary(bbox, first, last, fire_types, group_by)
            if last > LOCAL_END and kind not in {"hotspots", "csv"}:
                recent_rows, recent_status = firms.query(
                    bbox, max(first, LOCAL_END + timedelta(days=1)), last, fire_types, group_by, today,
                )
                period_rows.extend(recent_rows)
                period_status.update(recent_status)
            if row_request:
                period_status.update(row_status)
            rows.extend(period_rows)
            status.update(period_status)
            if summarize:
                days.extend(analytics.daily(period_rows, period_status, first, last)["days"])
            missing.extend(day for day in dates_between(first, last)
                           if period_status.get(day, ("none", True, None))[0] != "full")
        if start_day > LOCAL_END and not any(state != "none" for state, _, _ in status.values()):
            raise ApiError("UPSTREAM_ERROR", "Recent satellite observations are unavailable.", status=502)
        if kind == "hotspots":
            data = source.hotspot_page(bbox, start_day, end_day, fire_types, *page, recent=recent_hotspots)
        elif summarize:
            data = summarize(days)
        elif kind == "daily":
            data = analytics.daily(rows, status, start_day, end_day)
        else:
            data = analytics.grid(rows)
        flags = {flag for _, _, flag in status.values()}
        warnings = [{"code": code, "message": message}
                    for flag, (code, message) in FLAGS.items() if flag in flags]
        origin_index = 2
        if any(row[origin_index] in {"ml", "gapfill"} for row in rows):
            warnings.append({"code": "MODEL_DATA_INCLUDED", "message": "Results include modelled hotspots."})
        small_box = LIMITS["small_box_deg"]
        if (max_lat - min_lat < small_box and max_lon - min_lon < small_box
                and (start_day.year <= 2011 or any(row[origin_index] == "gapfill" for row in rows))):
            warnings.append({"code": "SMALL_BOX_MODEL_PERIOD",
                             "message": "Modelled detections are approximate in small areas."})
        if any(not reliable for _, reliable, _ in status.values()):
            warnings.append({"code": "DAYNIGHT_UNRELIABLE", "message": "Some days have an unreliable day/night split."})
        if data.get("compared_days", {}).get("month_to_date"):
            warnings.append({"code": "MONTH_IN_PROGRESS", "message": "This comparison uses month-to-date counts."})
        return {
            "query": {"bbox": asdict(bbox), "start": start_day, "end": end_day,
                      "types": fire_types, **(query_fields or {})},
            "partial": bool(missing),
            "missing_dates": missing,
            "warnings": warnings,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": data,
        }

    @app.get("/api/daily")
    def daily(lat1: str | None = None, lon1: str | None = None,
              lat2: str | None = None, lon2: str | None = None,
              start: str | None = None, end: str | None = None, types: str = "0"):
        return report("daily", lat1, lon1, lat2, lon2, start, end, types)

    @app.get("/api/grid")
    def grid(lat1: str | None = None, lon1: str | None = None,
             lat2: str | None = None, lon2: str | None = None,
             start: str | None = None, end: str | None = None, types: str = "0"):
        return report("grid", lat1, lon1, lat2, lon2, start, end, types)

    @app.get("/api/hotspots")
    def hotspots(lat1: str | None = None, lon1: str | None = None,
                 lat2: str | None = None, lon2: str | None = None,
                 start: str | None = None, end: str | None = None, types: str = "0",
                 limit: str = "5000", offset: str = "0"):
        page = (page_integer(limit, "limit", 1, LIMITS["hotspots_max_limit"]),
                page_integer(offset, "offset", 0, 2**63 - 1))
        return report("hotspots", lat1, lon1, lat2, lon2, start, end, types,
                      page=page, query_fields={"limit": page[0], "offset": page[1]})

    @app.get("/api/hotspots.csv")
    def hotspots_csv(lat1: str | None = None, lon1: str | None = None,
                     lat2: str | None = None, lon2: str | None = None,
                     start: str | None = None, end: str | None = None, types: str = "0"):
        return report("csv", lat1, lon1, lat2, lon2, start, end, types)

    @app.get("/api/calendar")
    def calendar(lat1: str | None = None, lon1: str | None = None,
                 lat2: str | None = None, lon2: str | None = None,
                 start_year: str | None = None, end_year: str | None = None, types: str = "0"):
        today = datetime.now(ZoneInfo("Australia/Sydney")).date()
        first = query_year(start_year, "start_year", RECORD_START.year)
        last = query_year(end_year, "end_year", today.year)
        if not RECORD_START.year <= first <= last <= today.year:
            raise ApiError("INVALID_DATE_RANGE", "Choose calendar years inside the record.")
        start = max(RECORD_START, date(first, 1, 1))
        end = min(today, date(last, 12, 31))
        return report("daily", lat1, lon1, lat2, lon2, str(start), str(end), types,
                      query_fields={"start_year": first, "end_year": last},
                      summarize=lambda days: analytics.calendar(days, today))

    @app.get("/api/anomaly")
    def anomaly(lat1: str | None = None, lon1: str | None = None,
                lat2: str | None = None, lon2: str | None = None, month: str | None = None,
                baseline_start_year: str | None = None, baseline_end_year: str | None = None,
                types: str = "0"):
        today = datetime.now(ZoneInfo("Australia/Sydney")).date()
        assessed = fire_date(required(month, "month") + "-01", "month")
        if not RECORD_START <= assessed <= today:
            raise ApiError("INVALID_DATE_RANGE", "Choose a month inside the record.", "month")
        first = query_year(baseline_start_year, "baseline_start_year", 2001)
        last = query_year(baseline_end_year, "baseline_end_year", assessed.year - 1)
        empty_default = baseline_start_year is None and baseline_end_year is None and last < first
        # Contract v1.2: the baseline may include the assessed year (the frontend compares finished
        # months with the whole record). That year is then one of the baseline years, listed once.
        if not empty_default and not RECORD_START.year <= first <= last <= today.year:
            raise ApiError("INVALID_DATE_RANGE", "Choose baseline years inside the record.")
        periods = []
        month_to_date = assessed == today.replace(day=1)
        for year in sorted({*range(first, last + 1), assessed.year}):
            start = date(year, assessed.month, 1)
            if start > today:
                continue
            day_count = monthrange(year, assessed.month)[1]
            end = start.replace(day=min(day_count, today.day) if month_to_date else day_count)
            end = min(end, today)
            if end >= RECORD_START:
                periods.append((max(start, RECORD_START), end))
        return report("daily", lat1, lon1, lat2, lon2, str(periods[0][0]), str(periods[-1][1]), types,
                      periods=periods,
                      query_fields={"month": month, "baseline_start_year": first, "baseline_end_year": last},
                      summarize=lambda days: analytics.anomaly(days, assessed, first, last, today))

    return app


app = create_app()
