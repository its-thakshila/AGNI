"""Experimental fire forecast: 1, 3 and 7 days ahead, for all of Australia, once a day.

Runs the ML team's forecast model (pipeline/forecast/, v5: XGBoost per lead, fire history + weather)
on the latest data and writes it to the store for the website:

  forecast/latest.json         issue day, target days, national totals with a P5-P95 range, accuracy,
                               weather sources, generation time
  forecast/lead_<L>.parquet    per 0.1 deg cell (website grid: ci = floor(lon*10), cj = floor(lat*10)):
                               p = probability of at least one detection (day or night pass),
                               expected = expected detections

Inputs, all rebuilt every run except the observed-weather file, which the run keeps up to date:
  fire history      hist/cells.parquet + live/cells/*.parquet (all origins, vegetation fires: type 0),
                    the same record the model was trained on. The land grid and the per-cell climatology
                    come from it exactly as history.py builds them.
  observed weather  forecast/weather_observed.parquet: NASA POWER daily, the last 731 days (90-day rain
                    sums, drought index spin-up). New days are added from the POWER API on every run.
  normals           pipeline/forecast/models/tmax_normals.parquet (normal max temperature per POWER
                    point and calendar day, built by the ML team like weather.py does in training)
  weather forecast  Open-Meteo (days after the issue day, and the 1-3 days NASA POWER hasn't published
                    yet), mapped to the POWER grid like forecast_weather.py: nearest point within 1 deg.

The weather features follow forecast_weather.py step by step (rain sums, dry days, VPD, temperature
anomaly, then KBDI / drought factor / FFDI advanced through the forecast days).

    python -m agni_pipeline.forecast --store r2:real
    python -m agni_pipeline.forecast --store local:../data/real --issue 2026-11-14
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import tempfile
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import duckdb
import numpy as np
import pandas as pd

from .storage import open_store, read_json, write_json

log = logging.getLogger("forecast")

FORECAST_DIR = Path(__file__).resolve().parents[1] / "forecast"
NORMALS_FILE = FORECAST_DIR / "models" / "tmax_normals.parquet"
EVAL_FILE = FORECAST_DIR / "models" / "evaluation_report.json"
OBS_KEY = "forecast/weather_observed.parquet"
OBS_DAYS = 731                 # observed weather kept: 90-day sums + a year of drought-index spin-up, twice
WARM_DAYS = 366 + 35           # history.py: fire-days before the issue day the rolling features need
PARAMS = ["T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M", "WS10M", "PS"]
KEEP_P, KEEP_E = 0.01, 0.02    # cells written for the website: probability or expected detections at least this

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/regional"
POWER_REFETCH = 10             # days before the last observed day fetched again (POWER revises recent days)
OM_URL = "https://api.open-meteo.com/v1/forecast"
OM_VARS = {"T2M_MAX": "temperature_2m_max", "T2M_MIN": "temperature_2m_min", "PRECTOTCORR": "precipitation_sum",
           "RH2M": "relative_humidity_2m_mean", "WS10M": "wind_speed_10m_mean", "PS": "surface_pressure_mean"}
OM_STRIDE = 2                  # every 2nd POWER point in each direction (about 1 x 1.25 deg): ~700 points a day
OM_BATCH = 100                 # locations per Open-Meteo request
OM_PER_MINUTE = 400            # stay under Open-Meteo's free limit of 600 location-calls a minute
UA = {"User-Agent": "AGNI-forecast/1.0 (NASA Space Apps 2026)"}


# ------------------------------------------------------------------ the ML team's modules
@lru_cache(maxsize=1)
def _ml():
    """Import the forecast model's modules (and Harmonic's ml_pipeline, which they build on)."""
    if str(FORECAST_DIR) not in sys.path:
        sys.path.insert(0, str(FORECAST_DIR))     # fcst_config adds pipeline/harmonic/ml_pipeline itself
    import fcst_config as fc
    import fire_danger as fdx
    import fmodels
    import history
    import weather as wx
    return fc, fdx, fmodels, history, wx


# ------------------------------------------------------------------ fire history
class FireHistory:
    """The fire record on the model's 0.1 deg grid: land mask and climatology from the whole history
    (history.py), and the daily maps of the last WARM_DAYS fire-days before the issue day."""

    def __init__(self, db, hist_cells: str, live_cells: list[str], issue: date, clim_years: tuple[int, int]):
        fc, _, _, history, _ = _ml()
        self.fc, self.history = fc, history
        g = fc.mlc.GRID_DEG
        # model grid from the website's cell centres: ci from latitude, cj from longitude
        src = ("(SELECT fire_date, \"pass\", type, cell_lat, cell_lon, count, frp_sum FROM read_parquet(?)"
               + "".join(" UNION ALL SELECT fire_date, \"pass\", type, cell_lat, cell_lon, count, frp_sum FROM read_parquet(?)"
                         for _ in live_cells) + ")")
        args = [hist_cells, *live_cells]
        db.execute(f"""CREATE OR REPLACE TEMP TABLE fire AS
            SELECT fire_date, "pass" = 'N' AS night,
                   CAST(round((cell_lat - {fc.mlc.GRID_LAT0}) / {g} - 0.5) AS INT) AS ci,
                   CAST(round((cell_lon - {fc.mlc.GRID_LON0}) / {g} - 0.5) AS INT) AS cj,
                   SUM(count)::FLOAT AS n, SUM(frp_sum)::FLOAT AS frp
            FROM {src} WHERE type IN ({",".join(map(str, fc.FIRE_TYPES))}) AND fire_date <= DATE '{issue}'
            GROUP BY ALL""", args)
        (h0, h1, ci0, ci1, cj0, cj1) = db.execute(
            "SELECT min(fire_date), max(fire_date), min(ci), max(ci), min(cj), max(cj) FROM fire").fetchone()
        pad = history.PAD
        self.i0, self.j0 = int(ci0) - pad, int(cj0) - pad
        self.shape = (int(ci1) - self.i0 + pad + 1, int(cj1) - self.j0 + pad + 1)
        land = db.execute("SELECT DISTINCT ci, cj FROM fire").fetchnumpy()
        self.land = np.zeros(self.shape, bool)
        self.land[land["ci"] - self.i0, land["cj"] - self.j0] = True

        # climatology (history.py _climatology): per pass x month x cell, mean daily count and share of
        # days with fire over the clim years; the forecast days are never inside them, so no leave-one-out
        y0, y1 = clim_years
        days = pd.date_range(f"{y0}-01-01", f"{y1}-12-31")
        days = days[(days >= pd.Timestamp(h0)) & (days <= pd.Timestamp(h1))]
        ndays = np.bincount(days.month - 1, minlength=12).astype(np.float32)
        c = db.execute(f"""SELECT night::INT AS nt, month(fire_date) - 1 AS mo, ci, cj, SUM(n) AS n, COUNT(*) AS k
                           FROM fire WHERE year(fire_date) BETWEEN {y0} AND {y1} GROUP BY ALL""").fetchnumpy()
        cn = np.zeros((2, 12) + self.shape, np.float32)
        ck = np.zeros((2, 12) + self.shape, np.float32)
        cn[c["nt"], c["mo"], c["ci"] - self.i0, c["cj"] - self.j0] = c["n"]
        ck[c["nt"], c["mo"], c["ci"] - self.i0, c["cj"] - self.j0] = c["k"]
        self.clim_mean = cn / ndays[None, :, None, None]
        self.clim_p = np.minimum(ck / ndays[None, :, None, None], 1.0)

        start = issue - timedelta(days=WARM_DAYS)
        self.recent = db.execute(f"""SELECT fire_date, night, ci, cj, n, frp FROM fire
                                     WHERE fire_date >= DATE '{start}' ORDER BY fire_date""").df()
        self.history_end = pd.Timestamp(h1)
        log.info("fire history %s .. %s, grid %s, %d land cells, %d recent cell-days",
                 h0, h1, self.shape, int(self.land.sum()), len(self.recent))

    def live_history(self, issue: date):
        """A history.History holding the recent days, so its own iterate() / Day.features() build the
        fire features exactly as in training."""
        H = self.history.History.__new__(self.history.History)
        H.start = pd.Timestamp(issue) - pd.Timedelta(days=WARM_DAYS)
        H.end = pd.Timestamp(issue)
        H.n_days = (H.end - H.start).days + 1
        H.i0, H.j0, H.shape, H.land = self.i0, self.j0, self.shape, self.land
        H.land_i, H.land_j = np.nonzero(self.land)
        r = self.recent
        d = (pd.to_datetime(r.fire_date) - H.start).dt.days.to_numpy(np.int32)
        ii = (r.ci.to_numpy() - self.i0).astype(np.int32)
        jj = (r.cj.to_numpy() - self.j0).astype(np.int32)
        H._h = dict(d=d, i=ii, j=jj, night=r.night.to_numpy(bool), n=r.n.to_numpy(np.float32),
                    frp=np.nan_to_num(r.frp.to_numpy(np.float32)))
        H._hb = np.searchsorted(d, np.arange(H.n_days + 1))
        H.clim_mean, H.clim_p = self.clim_mean, self.clim_p
        H._clim_year_days, H._clim_cache = {}, {}
        return H


# ------------------------------------------------------------------ observed weather (NASA POWER)
def _get(url: str, timeout: int = 180, tries: int = 4) -> bytes:
    for attempt in range(tries):
        try:
            with urlopen(Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(20 * (attempt + 1))
                continue
            raise
        except (URLError, TimeoutError):
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"no answer from {url.split('?')[0]}")


def _power_boxes(lats: np.ndarray, lons: np.ndarray) -> list[tuple[float, float, float, float]]:
    """10 x 10 degree boxes (POWER's limit) covering the grid, edges on grid points."""
    def spans(v: np.ndarray, width: float):
        out, k = [], 0
        while k < len(v):
            end = k
            while end + 1 < len(v) and v[end + 1] - v[k] <= width + 1e-9:
                end += 1
            out.append((v[k], v[end]))
            k = end + 1
        return out
    return [(a, b, c, d) for a, b in spans(lats, 10) for c, d in spans(lons, 10)]


def fetch_power(lats: np.ndarray, lons: np.ndarray, start: date, end: date) -> pd.DataFrame:
    """POWER daily values for the grid, start..end (one parameter per request, 10 x 10 deg boxes)."""
    jobs = [(box, p) for box in _power_boxes(lats, lons) for p in PARAMS]

    def one(job):
        (la0, la1, lo0, lo1), p = job
        q = urlencode({"latitude-min": la0, "latitude-max": la1, "longitude-min": lo0, "longitude-max": lo1,
                       "parameters": p, "community": "AG", "start": f"{start:%Y%m%d}", "end": f"{end:%Y%m%d}",
                       "format": "CSV"})
        text = _get(f"{POWER_URL}?{q}").decode("utf-8", "replace")
        k = text.find("LAT,LON,")
        if k < 0:
            raise RuntimeError(f"NASA POWER answered without data for {p}: {text[:200]}")
        df = pd.read_csv(io.StringIO(text[k:]))
        df["date"] = pd.to_datetime(df["YEAR"].astype(str), format="%Y") + pd.to_timedelta(df["DOY"] - 1, unit="D")
        return df.rename(columns={"LAT": "latitude", "LON": "longitude"})[["date", "latitude", "longitude", p]]

    with ThreadPoolExecutor(max_workers=4) as ex:
        parts = list(ex.map(one, jobs))
    by = {}
    for (_, p), df in zip(jobs, parts):
        by.setdefault(p, []).append(df)
    out = None
    for p, dfs in by.items():
        d = pd.concat(dfs).drop_duplicates(["date", "latitude", "longitude"])
        out = d if out is None else out.merge(d, on=["date", "latitude", "longitude"], how="outer")
    for p in PARAMS:
        out[p] = out[p].where(out[p] > -990)
    return out


def update_observed(store, db, today: date) -> pd.DataFrame:
    """The stored observed weather, with the newest NASA POWER days added (and the last few refreshed)."""
    raw = store.read(OBS_KEY)
    if raw is None:
        raise SystemExit(f"{OBS_KEY} is missing in {store}: upload the ML team's "
                         "australia_daily_weather_recent.parquet there once (see pipeline/forecast/README.md).")
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        f.write(raw)
    obs = db.execute("SELECT date, latitude, longitude, " + ", ".join(PARAMS) + " FROM read_parquet(?)",
                     [f.name]).df()
    Path(f.name).unlink(missing_ok=True)
    obs["date"] = pd.to_datetime(obs["date"])
    last_ok = obs.loc[obs["T2M_MAX"].notna() & (obs["T2M_MAX"] > -990), "date"].max()
    start = (last_ok - pd.Timedelta(days=POWER_REFETCH)).date()
    if start < today:
        lats, lons = np.sort(obs.latitude.unique()), np.sort(obs.longitude.unique())
        new = fetch_power(lats, lons, start, today)
        new = new[new["date"] >= pd.Timestamp(start)]
        obs = pd.concat([obs[obs["date"] < pd.Timestamp(start)], new], ignore_index=True)
    obs = obs[obs["date"] > obs["date"].max() - pd.Timedelta(days=OBS_DAYS)]
    obs = obs.sort_values(["date", "latitude", "longitude"]).reset_index(drop=True)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "obs.parquet"
        db.register("obs_df", obs)
        db.execute("COPY obs_df TO (?) (FORMAT PARQUET, COMPRESSION ZSTD)", [str(path)])
        db.unregister("obs_df")
        store.write(OBS_KEY, path.read_bytes())
    valid = obs.loc[obs["T2M_MAX"].notna(), "date"]
    log.info("observed weather %s .. %s (NASA POWER up to %s)", obs["date"].min().date(), obs["date"].max().date(),
             valid.max().date())
    return obs


# ------------------------------------------------------------------ weather forecast (Open-Meteo)
def fetch_open_meteo(points: np.ndarray, past_days: int, forecast_days: int) -> pd.DataFrame:
    """Daily values at (lat, lon) points: the last `past_days` days and the next `forecast_days` days."""
    rows, sent, t0 = [], 0, time.monotonic()
    for k in range(0, len(points), OM_BATCH):
        batch = points[k:k + OM_BATCH]
        if sent + len(batch) > OM_PER_MINUTE:          # pace: Open-Meteo counts every location as a call
            time.sleep(max(0.0, 61 - (time.monotonic() - t0)))
            sent, t0 = 0, time.monotonic()
        q = urlencode({"latitude": ",".join(f"{a:g}" for a in batch[:, 0]),
                       "longitude": ",".join(f"{b:g}" for b in batch[:, 1]),
                       "daily": ",".join(OM_VARS.values()), "wind_speed_unit": "ms", "timezone": "auto",
                       "past_days": past_days, "forecast_days": forecast_days})
        data = json.loads(_get(f"{OM_URL}?{q}", timeout=120))
        data = data if isinstance(data, list) else [data]
        sent += len(batch)
        for (lat, lon), loc in zip(batch, data):
            d = loc["daily"]
            frame = pd.DataFrame({"date": pd.to_datetime(d["time"]), "latitude": lat, "longitude": lon})
            for p, v in OM_VARS.items():
                frame[p] = pd.to_numeric(pd.Series(d.get(v)), errors="coerce").to_numpy(np.float64)
            rows.append(frame)
    out = pd.concat(rows, ignore_index=True)
    out["PS"] = out["PS"] / 10.0                      # hPa -> kPa, as NASA POWER
    return out


# ------------------------------------------------------------------ weather features
class Weather:
    """The model's 16 weather features on the POWER grid for each target day (forecast_weather.py)."""

    def __init__(self, obs: pd.DataFrame, fire_lat: np.ndarray, fire_lon: np.ndarray, issue: date, last_target: date):
        _, fdx, _, _, wx = _ml()
        self.fdx, self.wx = fdx, wx
        self.lats = np.sort(obs.latitude.unique())
        self.lons = np.sort(obs.longitude.unique())
        self.issue = pd.Timestamp(issue)
        nl, nm = len(self.lats), len(self.lons)

        # POWER points that fire cells use (nearest, like weather.py), and the Open-Meteo points behind them
        i, ok_i = wx._nearest(self.lats, fire_lat)
        j, ok_j = wx._nearest(self.lons, fire_lon)
        used = np.zeros((nl, nm), bool)
        used[i[ok_i & ok_j], j[ok_i & ok_j]] = True
        ui, uj = np.nonzero(used)
        om = np.zeros((nl, nm), bool)
        om[(ui // OM_STRIDE) * OM_STRIDE, (uj // OM_STRIDE) * OM_STRIDE] = True
        oi, oj = np.nonzero(om)
        pts = np.stack([self.lats[oi], self.lons[oj]], 1)

        # observed days up to the issue day: NASA POWER where published, Open-Meteo for the newest days
        last_power = obs.loc[obs["T2M_MAX"].notna(), "date"].max()
        days_obs = pd.date_range(obs["date"].min(), self.issue)
        gap = max(0, (self.issue - last_power).days)
        today = pd.Timestamp(datetime.now(UTC).date())
        past = max(gap, (today - self.issue).days) + 1
        fdays = max(1, (pd.Timestamp(last_target) - today).days + 1)
        log.info("Open-Meteo: %d points, %d past day(s) (NASA POWER ends %s), %d forecast day(s)",
                 len(pts), past, last_power.date(), fdays)
        F = fetch_open_meteo(pts, min(past, 92), min(fdays, 16))
        self.om_points = len(pts)
        self.power_end = last_power

        # dense arrays: observed (POWER) for days_obs; Open-Meteo for every day it covers
        cube = lambda days: {p: np.full((len(days), nl, nm), np.nan, np.float64) for p in PARAMS}
        Obs = cube(days_obs)
        t = (obs["date"] - days_obs[0]).dt.days.to_numpy()
        a = np.searchsorted(self.lats, obs["latitude"].to_numpy())
        b = np.searchsorted(self.lons, obs["longitude"].to_numpy())
        inside = (t >= 0) & (t < len(days_obs))
        for p in PARAMS:
            Obs[p][t[inside], a[inside], b[inside]] = obs[p].to_numpy(np.float64)[inside]

        # Open-Meteo onto the POWER grid: every used point takes the nearest Open-Meteo point (<= 1 deg)
        pi, pj = np.nonzero(used)
        d2 = (self.lats[pi][:, None] - pts[None, :, 0]) ** 2 + (self.lons[pj][:, None] - pts[None, :, 1]) ** 2
        near = d2.argmin(1)
        close = np.sqrt(d2[np.arange(len(pi)), near]) <= 1.0
        pi, pj, near = pi[close], pj[close], near[close]
        key = {(round(x, 4), round(y, 4)): n for n, (x, y) in enumerate(pts)}
        F["pt"] = [key[(round(x, 4), round(y, 4))] for x, y in zip(F["latitude"], F["longitude"])]
        om_days = pd.date_range(F["date"].min(), F["date"].max())
        OM = cube(om_days)
        ft = (F["date"] - om_days[0]).dt.days.to_numpy()
        for p in PARAMS:
            A = np.full((len(om_days), len(pts)), np.nan)
            A[ft, F["pt"].to_numpy()] = F[p].to_numpy(np.float64)
            OM[p][:, pi, pj] = A[:, near]

        # fill the observed days POWER hasn't published yet
        for n, day in enumerate(days_obs):
            if day > last_power and day in om_days:
                k = om_days.get_loc(day)
                for p in PARAMS:
                    Obs[p][n] = np.where(np.isfinite(Obs[p][n]), Obs[p][n], OM[p][k])
        self.filled_days = int(((days_obs > last_power)).sum())

        # forecast days issue+1 .. last target, from Open-Meteo
        self.days = pd.date_range(self.issue + pd.Timedelta(days=1), pd.Timestamp(last_target))
        missing = [d.date() for d in self.days if d not in om_days]
        if missing:
            raise RuntimeError(f"Open-Meteo has no forecast for {missing}")
        sel = [om_days.get_loc(d) for d in self.days]
        self.fc = {p: OM[p][sel] for p in PARAMS}

        # history up to the issue day (forecast_weather.py: last 90 days of rain, dry days, pressure)
        prec = Obs["PRECTOTCORR"]
        self.obs_prec = prec[-90:]
        dry = np.zeros((nl, nm))
        for p in prec:
            dry = np.where(np.isfinite(p) & (p >= wx.RAIN_MM), 0.0, np.minimum(dry + 1, wx.DRY_CAP))
        self.obs_dry = np.where(np.isfinite(prec[-1]), dry, np.nan)
        ps = np.full((nl, nm), np.nan)
        for p in Obs["PS"]:
            ps = np.where(np.isfinite(p), p, ps)
        self.obs_ps = ps

        # drought state (fire_danger.run_state): first year twice as spin-up, then every observed day
        with np.errstate(all="ignore"):
            annual = np.nanmean(prec, 0) * 365.25
        st = fdx.State((nl, nm), np.nan_to_num(annual))
        tmax, rh, wind = Obs["T2M_MAX"], Obs["RH2M"], Obs["WS10M"]
        n = len(days_obs)
        recent = []
        for k in list(range(min(365, n))) + list(range(n)):
            st.step(tmax[k], prec[k])
        # last 7 observed FFDI values (for f_ffdi_max7): replay the last week from a copy of the state
        st7 = fdx.State((nl, nm), np.nan_to_num(annual))
        for k in list(range(min(365, n - 7))) + list(range(n - 7)):
            st7.step(tmax[k], prec[k])
        for k in range(n - 7, n):
            _, df = st7.step(tmax[k], prec[k])
            recent.append(fdx.ffdi(df, tmax[k], rh[k], wind[k]))
        self.state, self.recent_ffdi = st, recent

        # normals: normal max temperature per point and calendar day
        nrm = duckdb.sql(f"SELECT * FROM read_parquet('{NORMALS_FILE.as_posix()}')").df()
        self.normal = np.full((365, nl, nm), np.nan, np.float32)
        a = np.searchsorted(self.lats, nrm["latitude"].to_numpy())
        b = np.searchsorted(self.lons, nrm["longitude"].to_numpy())
        okn = (a < nl) & (b < nm)
        okn[okn] &= np.isclose(self.lats[a[okn]], nrm["latitude"].to_numpy()[okn]) & \
            np.isclose(self.lons[b[okn]], nrm["longitude"].to_numpy()[okn])
        self.normal[nrm["doy"].to_numpy()[okn] - 1, a[okn], b[okn]] = nrm["T2M_MAX_normal"].to_numpy()[okn]
        self._fd = None

    def _fire_danger(self) -> list[dict]:
        if self._fd is None:
            fdx, st, recent = self.fdx, self.state, list(self.recent_ffdi)
            self._fd = []
            for n in range(len(self.days)):
                T = self.fc["T2M_MAX"][n]
                q, df = st.step(T, self.fc["PRECTOTCORR"][n])
                fi = fdx.ffdi(df, T, self.fc["RH2M"][n], self.fc["WS10M"][n])
                recent = (recent + [fi])[-7:]
                with np.errstate(all="ignore"), warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)   # sea points: all NaN
                    mx = np.nanmax(np.stack(recent), 0)
                self._fd.append({"f_kbdi": q, "f_df": df, "f_ffdi": fi, "f_ffdi_max7": mx})
        return self._fd

    def grid(self, target: pd.Timestamp) -> dict[str, np.ndarray]:
        wx = self.wx
        n = (target - self.issue).days
        f = {p: a[:n] for p, a in self.fc.items()}
        tmax, rh = f["T2M_MAX"][-1], f["RH2M"][-1]
        es = 0.6108 * np.exp(17.27 * tmax / (tmax + 237.3))
        prec = np.concatenate([self.obs_prec, f["PRECTOTCORR"]])[-90:]
        dry = self.obs_dry.copy()
        for p in f["PRECTOTCORR"]:
            dry = np.where(np.isfinite(p) & (p >= wx.RAIN_MM), 0.0, np.minimum(dry + 1, wx.DRY_CAP))
            dry = np.where(np.isfinite(p), dry, np.nan)
        ps = np.where(np.isfinite(f["PS"][-1]), f["PS"][-1], self.obs_ps)
        slot = min(target.day_of_year, 365) - 1
        out = {
            "w_tmax": tmax, "w_tmin": f["T2M_MIN"][-1], "w_rh": rh, "w_wind": f["WS10M"][-1],
            "w_prec": f["PRECTOTCORR"][-1], "w_ps": ps, "w_vpd": es * (1 - np.clip(rh, 0, 100) / 100),
            "w_prec_7d": prec[-7:].sum(0), "w_prec_30d": prec[-30:].sum(0), "w_prec_90d": prec.sum(0),
            "w_dry_days": dry, "w_tmax_anom": tmax - self.normal[slot],
        }
        out.update(self._fire_danger()[n - 1])
        return {k: np.asarray(v, np.float32) for k, v in out.items()}

    def features(self, target: pd.Timestamp, lat: np.ndarray, lon: np.ndarray) -> pd.DataFrame:
        g = self.grid(target)
        i, ok_i = self.wx._nearest(self.lats, lat)
        j, ok_j = self.wx._nearest(self.lons, lon)
        ok = ok_i & ok_j
        X = {}
        for k, a in g.items():
            col = np.full(len(lat), np.nan, np.float32)
            col[ok] = a[i[ok], j[ok]]
            X[k] = col
        return pd.DataFrame(X)


# ------------------------------------------------------------------ run
def spread(p: np.ndarray, mu: np.ndarray, f: np.ndarray, rng, sims: int = 200) -> list[float]:
    """P5 / P50 / P95 of the national total, cells independent (forecast.py; understates the real spread)."""
    k = p > 1e-4
    p, lam = p[k], np.maximum(mu[k] * f[k] - 1, 0)
    tot = np.empty(sims)
    for s in range(sims):
        fire = rng.random(len(p)) < p
        tot[s] = (fire * (1 + rng.poisson(lam))).sum()
    return [float(x) for x in np.percentile(tot, [5, 50, 95])]


def choose_issue(manifest: dict, today: date) -> date:
    """The newest finished fire-day with data (today's fire-day is still in progress)."""
    latest = date.fromisoformat(manifest["latest_fire_date"])
    return min(latest, today - timedelta(days=1))


def run(store, issue: date | None = None) -> dict:
    fc, _, fmodels, history, _ = _ml()
    t0 = time.perf_counter()
    bundles = fmodels.load_bundles(fc.MODEL_DIR)
    leads = sorted(bundles)
    b0 = bundles[leads[0]]
    manifest = read_json(store, "manifest.json", None)
    if manifest is None:
        raise SystemExit("manifest.json not found.")
    today = datetime.now(UTC).date()
    issue = issue or choose_issue(manifest, today)
    targets = {L: issue + timedelta(days=L) for L in leads}

    with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as db:
        tmp = Path(tmp)
        # fire record: history file + the live months up to the issue day
        hist = tmp / "cells.parquet"
        hist.write_bytes(store.read("hist/cells.parquet") or b"")
        live = []
        for month in manifest.get("live_months", []):
            if month <= f"{issue:%Y-%m}" and (data := store.read(f"live/cells/{month}.parquet")):
                (tmp / f"live_{month}.parquet").write_bytes(data)
                live.append(str(tmp / f"live_{month}.parquet"))
        fire = FireHistory(db, str(hist), live, issue, tuple(b0["clim_years"]))
        H = fire.live_history(issue)
        day = next(H.iterate(H.n_days - 1, H.n_days - 1))

        obs = update_observed(store, db, today)
        li, lj = H.land_i, H.land_j
        lat, lon = H.cell_latlon(li, lj)
        W = Weather(obs, lat, lon, issue, max(targets.values()))

        nl = len(li)
        night = np.repeat(np.array([0, 1], np.int8), nl)
        ii, jj = np.tile(li, 2), np.tile(lj, 2)
        rng = np.random.default_rng(fc.SEED)
        out_leads = {}
        for L in leads:
            target = pd.Timestamp(targets[L])
            X = day.features(ii, jj, night, L, weather=False)
            Wf = W.features(target, X["lat"].to_numpy(np.float64), X["lon"].to_numpy(np.float64))
            for c in Wf.columns:
                X[c] = Wf[c].to_numpy()
            P = fmodels.predict(bundles[L], X, target.month, attrs=False)
            p = P["p_fire"].to_numpy().reshape(2, nl)
            e = P["expected_rows"].to_numpy().reshape(2, nl)
            # one value per cell: probability of a detection in either pass, expected detections of both
            p_any = 1 - (1 - p[0]) * (1 - p[1])
            e_all = e[0] + e[1]
            keep = (p_any >= KEEP_P) | (e_all >= KEEP_E)
            clat, clon = H.cell_latlon(li[keep], lj[keep])
            cells = pd.DataFrame({"ci": np.floor(clon * 10 + 1e-6).astype(np.int32),
                                  "cj": np.floor(clat * 10 + 1e-6).astype(np.int32),
                                  "p": p_any[keep].round(4).astype(np.float32),
                                  "expected": e_all[keep].round(3).astype(np.float32)})
            path = tmp / f"lead_{L}.parquet"
            db.register("cells_df", cells)
            db.execute("COPY (SELECT * FROM cells_df ORDER BY cj, ci) TO (?) (FORMAT PARQUET, PARQUET_VERSION V1)",
                       [str(path)])
            db.unregister("cells_df")
            store.write(f"forecast/lead_{L}.parquet", path.read_bytes())
            f = (P["expected_rows"] / np.maximum(P["p_fire"] * P["rows_if_fire"], 1e-12)).to_numpy()
            p5, p50, p95 = spread(P["p_fire"].to_numpy(), P["rows_if_fire"].to_numpy(), f, rng)
            out_leads[str(L)] = {"target_date": str(targets[L]), "expected": round(float(e_all.sum()), 1),
                                 "range": [round(p5), round(p95)], "cells": int(keep.sum())}
            log.info("lead %d -> %s: %.0f expected detections (P5-P95 %d-%d), %d cells",
                     L, targets[L], e_all.sum(), p5, p95, keep.sum())

    ev = json.loads(EVAL_FILE.read_text())
    accuracy = {L: {"daily_total_error_pct": v["model"]["daily_total_mape_pct"],
                    "persistence_error_pct": v["persistence"]["daily_total_mape_pct"],
                    "top1000_capture_pct": round(v["model"]["topk_row_capture"] * 100, 1)}
                for L, v in ev["leads"].items()}
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    summary = {
        "experimental": True, "issue_date": str(issue), "generated_at": generated, "version": generated,
        "model": b0.get("version", fc.OUT_VERSION), "test_years": ev.get("test_years"),
        "leads": out_leads, "accuracy": accuracy,
        "weather": {"observed": "NASA POWER", "observed_until": str(W.power_end.date()),
                    "filled_days": W.filled_days, "forecast": "Open-Meteo", "points": W.om_points},
    }
    write_json(store, "forecast/latest.json", summary)
    log.info("forecast for %s written in %.0f s", issue, time.perf_counter() - t0)
    return summary


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", required=True, help="local:<folder> or r2:<prefix>")
    parser.add_argument("--issue", help="issue fire-day YYYY-MM-DD (default: the newest finished day)")
    args = parser.parse_args()
    run(open_store(args.store), date.fromisoformat(args.issue) if args.issue else None)


if __name__ == "__main__":
    main()
