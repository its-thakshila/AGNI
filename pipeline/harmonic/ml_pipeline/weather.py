"""NASA POWER daily weather -> fire-weather features per 0.1 deg cell and fire-day (feature group "weather", V9).

Input: one or more files in data/weather/ (config.WEATHER_DIR), CSV or Parquet, in the wide schema of
nasa_power_australia_daily_dataset_instructions.md:

    date, latitude, longitude, T2M, T2M_MAX, T2M_MIN, PRECTOTCORR, RH2M, WS10M, PS

Column names are matched case-insensitively; `lat`/`lon` and YEAR+MO+DY or YEAR+DOY date columns are also
accepted, as are raw POWER CSVs with a "-BEGIN HEADER- ... -END HEADER-" block. NASA fill values (-999)
become missing. T2M itself is not used (T2M_MAX/T2M_MIN carry the same information).

The files are converted once into a cache (output/features/weather/: one float32 array per feature, shape
days x POWER-lat x POWER-lon, plus meta.json). The cache is rebuilt automatically when the input files
change. Features (all for the POWER grid point nearest to the cell centre, on the cell's fire_date):

    w_tmax, w_tmin      daily max / min 2 m air temperature (deg C)
    w_rh                2 m relative humidity (%)
    w_wind              10 m wind speed (m/s)
    w_prec              precipitation that day (mm)
    w_ps                surface pressure (unit as supplied; mostly an elevation proxy)
    w_vpd               vapour-pressure deficit at T2M_MAX using the day's mean RH (kPa, approximation)
    w_prec_7d/30d/90d   precipitation over the last 7 / 30 / 90 days including that day (mm)
    w_dry_days          consecutive days with < 1 mm rain up to that day (capped at 120)
    w_tmax_anom         T2M_MAX minus that grid point's normal for the calendar day (all years, +-15 days)

The POWER grid (about 0.5 x 0.625 deg) is coarser than the 0.1 deg fire cells, so 25-30 fire cells share
one weather point. No interpolation is done.

    python weather.py           build/refresh the cache, print coverage, check it covers the needed dates
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import config

log = logging.getLogger("weather")

CACHE_VERSION = 1
PARAMS = ["T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M", "WS10M", "PS"]     # T2M not needed
FEATURES = ["w_tmax", "w_tmin", "w_rh", "w_wind", "w_prec", "w_ps", "w_vpd",
            "w_prec_7d", "w_prec_30d", "w_prec_90d", "w_dry_days", "w_tmax_anom"]
FILL_BELOW = -990.0          # NASA POWER fill value is -999
RAIN_MM = 1.0                # a "rain day" for w_dry_days
DRY_CAP = 120


# ------------------------------------------------------------------ input files
def source_files() -> list[Path]:
    d = Path(config.WEATHER_DIR)
    files = sorted(list(d.glob("*.parquet")) + list(d.glob("*.csv"))) if d.exists() else []
    if any(f.suffix == ".parquet" for f in files):        # same data exported twice -> use the Parquet only
        files = [f for f in files if f.suffix == ".parquet"]
    return files


def _header_skip(path: Path) -> int:
    """Lines to skip before the table header (raw POWER CSVs start with a metadata block)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i > 200:
                break
            if line.strip().upper().startswith("-END HEADER-"):
                return i + 1
    return 0


def _relation(con, files: list[Path]) -> str:
    parts = []
    for f in files:
        p = str(f).replace("\\", "/").replace("'", "''")
        if f.suffix == ".parquet":
            parts.append(f"SELECT * FROM read_parquet('{p}')")
        else:
            parts.append(f"SELECT * FROM read_csv('{p}', header=true, delim=',', skip={_header_skip(f)}, "
                         f"sample_size=-1, nullstr=['', 'NA', 'NaN'])")
    return "(" + " UNION ALL BY NAME ".join(parts) + ")"


def _columns(con, rel: str) -> dict[str, str]:
    """lower-case name -> actual column name."""
    names = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
    return {n.lower(): n for n in names}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _date_expr(cols: dict[str, str]) -> str:
    if "date" in cols:
        c = f"CAST({_q(cols['date'])} AS VARCHAR)"
        return f"COALESCE(TRY_CAST({c} AS DATE), CAST(TRY_STRPTIME({c}, '%Y%m%d') AS DATE))"
    if {"year", "mo", "dy"} <= cols.keys():
        return f"make_date(CAST({_q(cols['year'])} AS INT), CAST({_q(cols['mo'])} AS INT), CAST({_q(cols['dy'])} AS INT))"
    if {"year", "doy"} <= cols.keys():
        return (f"CAST(make_date(CAST({_q(cols['year'])} AS INT), 1, 1) "
                f"+ (CAST({_q(cols['doy'])} AS INT) - 1) * INTERVAL 1 DAY AS DATE)")
    raise ValueError("weather data has no date column (expected 'date', or YEAR+MO+DY, or YEAR+DOY); "
                     f"columns are: {sorted(cols.values())}")


def _pick(cols: dict[str, str], *names: str) -> str:
    for n in names:
        if n in cols:
            return cols[n]
    raise ValueError(f"weather data has none of the columns {names}; columns are: {sorted(cols.values())}")


# ------------------------------------------------------------------ cache build
def _signature(files: list[Path]) -> list[list]:
    return [[f.name, f.stat().st_size, int(f.stat().st_mtime)] for f in files]


def cache_dir() -> Path:
    return Path(config.WEATHER_CACHE_DIR)


def _cache_ok(files: list[Path]) -> bool:
    meta = cache_dir() / "meta.json"
    if not meta.exists():
        return False
    m = json.loads(meta.read_text())
    return (m.get("version") == CACHE_VERSION and m.get("sources") == _signature(files)
            and all((cache_dir() / f"{f}.npy").exists() for f in FEATURES))


def _window_sum(a: np.ndarray, k: int) -> np.ndarray:
    """Sum over the last k days (axis 0, including today); missing if any of those days is missing."""
    ok = np.isfinite(a)
    c = np.cumsum(np.where(ok, a, 0.0), axis=0, dtype=np.float64)
    n = np.cumsum(ok, axis=0, dtype=np.int32)
    s, cnt = c.copy(), n.copy()
    s[k:] -= c[:-k]
    cnt[k:] -= n[:-k]
    out = s.astype(np.float32)
    out[cnt < k] = np.nan                     # also the first k-1 days of the record
    return out


def _dry_days(prec: np.ndarray) -> np.ndarray:
    out = np.empty_like(prec)
    run = np.zeros(prec.shape[1:], np.float32)
    for t in range(prec.shape[0]):
        p = prec[t]
        run = np.where(np.isfinite(p) & (p >= RAIN_MM), 0.0, np.minimum(run + 1, DRY_CAP)).astype(np.float32)
        out[t] = np.where(np.isfinite(p), run, np.nan)
    return out


def _doy_anomaly(a: np.ndarray, dates: pd.DatetimeIndex, half: int = 15) -> np.ndarray:
    """a minus the grid point's normal for that calendar day (mean of all years, smoothed +-half days)."""
    doy = np.minimum(dates.dayofyear.to_numpy(), 365) - 1          # 29 Feb joins 28 Feb's slot... close enough
    s = np.zeros((365,) + a.shape[1:], np.float64)
    n = np.zeros((365,) + a.shape[1:], np.float64)
    ok = np.isfinite(a)
    np.add.at(s, doy, np.where(ok, a, 0.0))
    np.add.at(n, doy, ok)
    pad_s = np.concatenate([s[-half:], s, s[:half]])                # circular smoothing over the year
    pad_n = np.concatenate([n[-half:], n, n[:half]])
    cs, cn = np.cumsum(pad_s, axis=0), np.cumsum(pad_n, axis=0)
    w = 2 * half + 1
    ss = cs[w - 1:] - np.concatenate([np.zeros((1,) + a.shape[1:]), cs[:-w]])
    nn = cn[w - 1:] - np.concatenate([np.zeros((1,) + a.shape[1:]), cn[:-w]])
    clim = np.where(nn > 0, ss / np.maximum(nn, 1), np.nan).astype(np.float32)
    return (a - clim[doy]).astype(np.float32)


def build_cache(force: bool = False) -> dict:
    """Read the weather files into per-feature arrays (skipped when the cache is up to date)."""
    files = source_files()
    if not files:
        raise FileNotFoundError(
            f"no weather data: put the NASA POWER daily CSV/Parquet in {config.WEATHER_DIR}\n"
            "  expected columns: date, latitude, longitude, T2M_MAX, T2M_MIN, PRECTOTCORR, RH2M, WS10M, PS\n"
            "  (see nasa_power_australia_daily_dataset_instructions.md), or set FEAT_WEATHER = False in config.py")
    if not force and _cache_ok(files):
        return load_meta()

    import duckdb
    t0 = time.perf_counter()
    log.info("reading weather from %s", ", ".join(f.name for f in files))
    con = duckdb.connect()
    rel = _relation(con, files)
    cols = _columns(con, rel)
    missing = [p for p in PARAMS if p.lower() not in cols]
    if missing:
        raise ValueError(f"weather data is missing {missing}; columns are: {sorted(cols.values())}")
    lat_c, lon_c = _pick(cols, "latitude", "lat"), _pick(cols, "longitude", "lon")
    vals = ", ".join(f"CASE WHEN CAST({_q(cols[p.lower()])} AS DOUBLE) <= {FILL_BELOW} THEN NULL "
                     f"ELSE CAST({_q(cols[p.lower()])} AS FLOAT) END AS {p}" for p in PARAMS)
    con.execute(f"CREATE TEMP VIEW w AS SELECT {_date_expr(cols)} AS d, "
                f"ROUND(CAST({_q(lat_c)} AS DOUBLE), 4) AS lat, ROUND(CAST({_q(lon_c)} AS DOUBLE), 4) AS lon, "
                f"{vals} FROM {rel}")
    bad = con.execute("SELECT COUNT(*) FROM w WHERE d IS NULL OR lat IS NULL OR lon IS NULL").fetchone()[0]
    if bad:
        raise ValueError(f"{bad} weather rows have an unreadable date or coordinate")
    lats = np.array([r[0] for r in con.execute("SELECT DISTINCT lat FROM w ORDER BY lat").fetchall()])
    lons = np.array([r[0] for r in con.execute("SELECT DISTINCT lon FROM w ORDER BY lon").fetchall()])
    d0, d1, nrows, nkeys = con.execute(
        "SELECT MIN(d), MAX(d), COUNT(*), COUNT(DISTINCT (d, lat, lon)) FROM w").fetchone()
    d0, d1 = pd.Timestamp(d0), pd.Timestamp(d1)
    ndays = (d1 - d0).days + 1
    shape = (ndays, len(lats), len(lons))
    log.info("%d rows, %d dates %s -> %s, grid %d lat x %d lon (spacing %s x %s deg), %d duplicate keys",
             nrows, ndays, d0.date(), d1.date(), len(lats), len(lons),
             np.round(np.median(np.diff(lats)), 4) if len(lats) > 1 else "-",
             np.round(np.median(np.diff(lons)), 4) if len(lons) > 1 else "-", nrows - nkeys)

    raw = {p: np.full(shape, np.nan, np.float32) for p in PARAMS}
    reader = con.execute(f"SELECT CAST(date_diff('day', DATE '{d0.date()}', d) AS INT) AS t, lat, lon, "
                         f"{', '.join(PARAMS)} FROM w").fetch_record_batch(4_000_000)
    for batch in reader:
        b = {n: batch.column(n).to_numpy(zero_copy_only=False) for n in batch.schema.names}   # nulls -> nan
        t = b["t"].astype(np.int64)
        i, j = np.searchsorted(lats, b["lat"]), np.searchsorted(lons, b["lon"])
        for p in PARAMS:
            raw[p][t, i, j] = np.asarray(b[p], np.float32)       # None -> nan
    con.close()

    # unit sanity (the spec says: check units, do not assume them)
    notes = []
    for p in ("T2M_MAX", "T2M_MIN"):
        if np.nanmedian(raw[p]) > 150:
            raw[p] -= 273.15
            notes.append(f"{p} looked like kelvin: converted to deg C")
    if np.nanmax(raw["RH2M"]) <= 1.5:
        raw["RH2M"] *= 100
        notes.append("RH2M looked like a 0-1 fraction: converted to %")
    for n in notes:
        log.warning(n)

    dates = pd.date_range(d0, d1, freq="D")
    tmax, rh, prec = raw["T2M_MAX"], raw["RH2M"], raw["PRECTOTCORR"]
    es = 0.6108 * np.exp(17.27 * tmax / (tmax + 237.3))                 # kPa (Tetens)
    out = {
        "w_tmax": tmax, "w_tmin": raw["T2M_MIN"], "w_rh": rh, "w_wind": raw["WS10M"], "w_prec": prec,
        "w_ps": raw["PS"], "w_vpd": (es * (1 - np.clip(rh, 0, 100) / 100)).astype(np.float32),
    }
    cache_dir().mkdir(parents=True, exist_ok=True)
    for f in [f for f in FEATURES if (cache_dir() / f"{f}.npy").exists()]:
        (cache_dir() / f"{f}.npy").unlink()
    (cache_dir() / "meta.json").unlink(missing_ok=True)                 # cache invalid until fully written

    def save(name, arr):
        np.save(cache_dir() / f"{name}.npy", arr.astype(np.float32, copy=False))
        miss[name] = round(float(np.isnan(arr).mean() * 100), 2)

    miss: dict[str, float] = {}
    for k, v in out.items():
        save(k, v)
    del out, es
    for k in (7, 30, 90):
        save(f"w_prec_{k}d", _window_sum(prec, k))
    save("w_dry_days", _dry_days(prec))
    save("w_tmax_anom", _doy_anomaly(tmax, dates))

    meta = {"version": CACHE_VERSION, "sources": _signature(files), "start": str(d0.date()), "end": str(d1.date()),
            "days": ndays, "lats": lats.tolist(), "lons": lons.tolist(), "rows": int(nrows),
            "duplicate_keys": int(nrows - nkeys), "missing_pct": miss, "unit_notes": notes,
            "built": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.perf_counter() - t0)}
    (cache_dir() / "meta.json").write_text(json.dumps(meta, indent=1))
    global _META
    _META = None
    log.info("weather cache written to %s in %.0f s (%.0f MB)", cache_dir(), time.perf_counter() - t0,
             sum((cache_dir() / f"{f}.npy").stat().st_size for f in FEATURES) / 1e6)
    return meta


_META = None


def load_meta() -> dict:
    global _META
    if _META is None:
        _META = json.loads((cache_dir() / "meta.json").read_text())
    return _META


# ------------------------------------------------------------------ lookup
def _nearest(grid: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Index of the nearest grid value, and whether x is within half a grid step of it."""
    k = np.clip(np.searchsorted(grid, x), 1, len(grid) - 1) if len(grid) > 1 else np.zeros(len(x), np.int64)
    if len(grid) > 1:
        k = np.where(np.abs(x - grid[k - 1]) <= np.abs(x - grid[k]), k - 1, k)
        step = np.median(np.diff(grid))
    else:
        step = 1.0
    return k, np.abs(x - grid[k]) <= step * 0.5 + 1e-6


def add_features(X: pd.DataFrame) -> None:
    """Add the FEATURES columns to the feature table X in place (needs lat, lon, fire_date)."""
    meta = build_cache()
    lats, lons = np.array(meta["lats"]), np.array(meta["lons"])
    i, ok_i = _nearest(lats, X["lat"].to_numpy())
    j, ok_j = _nearest(lons, X["lon"].to_numpy())
    t = (X["fire_date"] - pd.Timestamp(meta["start"])).dt.days.to_numpy(np.int64)
    in_time = (t >= 0) & (t < meta["days"])
    ok = in_time & ok_i & ok_j
    if len(X) and (~in_time).mean() > config.WEATHER_MAX_MISSING_SHARE:
        lo, hi = X["fire_date"].min().date(), X["fire_date"].max().date()
        raise ValueError(f"weather data covers {meta['start']} -> {meta['end']}, but these MODIS rows need "
                         f"{lo} -> {hi} ({(~in_time).mean():.1%} of rows have no weather day). "
                         f"Add the missing dates to {config.WEATHER_DIR}.")
    if (~ok).any():
        log.info("weather: %d of %d rows (%.2f%%) without a weather value (outside the dates or the POWER grid)",
                 int((~ok).sum()), len(X), (~ok).mean() * 100)
    if not ok.any():
        for f in FEATURES:
            X[f] = np.float32(np.nan)
        return
    tlo, thi = int(t[ok].min()), int(t[ok].max())
    tt, ii, jj = t[ok] - tlo, i[ok], j[ok]
    for f in FEATURES:
        arr = np.asarray(np.load(cache_dir() / f"{f}.npy", mmap_mode="r")[tlo:thi + 1])   # only these days
        col = np.full(len(X), np.nan, np.float32)
        col[ok] = arr[tt, ii, jj]
        X[f] = col


# ------------------------------------------------------------------ check (train_all step 1)
def check() -> int:
    meta = build_cache()
    print(f"weather: {meta['start']} -> {meta['end']} ({meta['days']} days), "
          f"{len(meta['lats'])} x {len(meta['lons'])} grid points, {meta['rows']:,} rows")
    print("missing %: " + ", ".join(f"{k} {v}" for k, v in meta["missing_pct"].items()))
    for n in meta.get("unit_notes", []):
        print("NOTE:", n)
    if meta.get("duplicate_keys"):
        print(f"NOTE: {meta['duplicate_keys']} duplicate (date, latitude, longitude) rows: the last one was used")
    start, end = pd.Timestamp(meta["start"]), pd.Timestamp(meta["end"])
    # fire_date runs from about 31 Oct of a year's file start to 1 Jan after it (+-1 day candidate rows)
    need_train = (pd.Timestamp(f"{min(config.TRAIN_YEARS)}-01-01") - pd.Timedelta(days=2),
                  pd.Timestamp(f"{max(config.TRAIN_YEARS)}-12-31") + pd.Timedelta(days=1))
    other = [y for y in config.modis_years() if y not in config.TRAIN_YEARS]
    rc = 0
    if start > need_train[0] or end < need_train[1]:
        print(f"ERROR: training needs weather {need_train[0].date()} -> {need_train[1].date()}")
        rc = 1
    else:
        print(f"OK: covers the training years {min(config.TRAIN_YEARS)}-{max(config.TRAIN_YEARS)}")
    if other:
        need = (pd.Timestamp(f"{min(other)}-10-01"), pd.Timestamp(f"{max(other)}-12-31") + pd.Timedelta(days=1))
        if start > need[0] or end < need[1]:
            print(f"WARNING: predicting the MODIS-only years {min(other)}-{max(other)} (step 4) needs weather "
                  f"{need[0].date()} -> {need[1].date()}; step 4 will stop if those dates are missing")
        else:
            print(f"OK: covers the prediction years {min(other)}-{max(other)}")
    if start + pd.Timedelta(days=89) > need_train[0]:
        print("NOTE: less than 90 days of weather before the first training day: w_prec_90d is missing there")
    return rc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    force = "--rebuild" in sys.argv
    if force:
        build_cache(force=True)
    sys.exit(check())
