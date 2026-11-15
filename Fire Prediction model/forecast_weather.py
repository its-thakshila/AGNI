"""Weather features for a live forecast, from a weather-FORECAST file.

Models trained with `--weather target` use the 12 weather features of ml_pipeline/weather.py on the
TARGET day. In training and tests these come from observed NASA POWER weather. For a real forecast the
target day has not happened yet, so its weather must come from a weather forecast. This module builds
the same 12 features from:

  * observed POWER weather up to the issue day t (the cache of ml_pipeline/weather.py), for the rain
    sums over 7 / 30 / 90 days, the dry-day count and the temperature normals, and
  * the forecast file for the days t+1 .. target: these replace observed values for those days.

Forecast file: CSV or Parquet, one row per date x grid point, same columns as the POWER files:

    date, latitude, longitude, T2M_MAX, T2M_MIN, PRECTOTCORR, RH2M, WS10M [, PS]

Names are matched case-insensitively (lat / lon also accepted). PS is optional: it barely changes from
day to day (it mostly encodes elevation), so the last observed value is used when it is missing. The
forecast grid may differ from the POWER grid: every POWER point takes the nearest forecast point
(within MAX_DIST_DEG). Every day from t+1 to the target day must be in the file.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import fcst_config as fc  # noqa: F401  (puts ml_pipeline on sys.path)
import weather as wx
import fire_danger as fdx

REQUIRED = ["T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M", "WS10M"]
OPTIONAL = ["PS"]
MAX_DIST_DEG = 1.0          # a POWER point farther than this from every forecast point gets no forecast
HISTORY_DAYS = 90           # longest window of the features (w_prec_90d)


def read_forecast(path) -> pd.DataFrame:
    path = Path(path)
    df = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path, comment=None)
    cols = {c.lower(): c for c in df.columns}

    def pick(*names):
        for n in names:
            if n.lower() in cols:
                return cols[n.lower()]
        return None

    lat, lon = pick("latitude", "lat"), pick("longitude", "lon")
    if lat is None or lon is None:
        raise ValueError(f"{path.name}: needs latitude/longitude columns, has {list(df.columns)}")
    if pick("date"):
        date = pd.to_datetime(df[pick("date")].astype(str), errors="coerce")
    elif pick("year") and pick("mo") and pick("dy"):
        date = pd.to_datetime(dict(year=df[pick("year")], month=df[pick("mo")], day=df[pick("dy")]), errors="coerce")
    elif pick("year") and pick("doy"):
        date = pd.to_datetime(df[pick("year")].astype(int).astype(str), format="%Y") + \
            pd.to_timedelta(df[pick("doy")].astype(int) - 1, unit="D")
    else:
        raise ValueError(f"{path.name}: needs a date column (date, or YEAR+MO+DY, or YEAR+DOY)")
    missing = [p for p in REQUIRED if pick(p) is None]
    if missing:
        raise ValueError(f"{path.name}: missing {missing}; columns are {list(df.columns)}")
    out = pd.DataFrame({"date": date.dt.normalize(), "lat": df[lat].astype(float), "lon": df[lon].astype(float)})
    for p in REQUIRED + OPTIONAL:
        if pick(p):
            v = pd.to_numeric(df[pick(p)], errors="coerce").astype(float)
            out[p] = v.where(v > wx.FILL_BELOW)                  # -999 fill -> missing
    if out["date"].isna().any():
        raise ValueError(f"{path.name}: {int(out['date'].isna().sum())} rows with an unreadable date")
    return out.groupby(["date", "lat", "lon"], as_index=False).mean()


class ForecastWeather:
    """Weather features for target days after `issue`, observed history + forecast."""

    def __init__(self, path, issue):
        self.issue = pd.Timestamp(issue).normalize()
        self.meta = wx.build_cache()
        self.lats, self.lons = np.array(self.meta["lats"]), np.array(self.meta["lons"])
        start, end = pd.Timestamp(self.meta["start"]), pd.Timestamp(self.meta["end"])
        t_issue = (self.issue - start).days
        if self.issue > end:
            raise ValueError(f"observed weather ends {end.date()}, but the issue day is {self.issue.date()}: "
                             f"add the observed POWER days up to the issue day to {fc.mlc.WEATHER_DIR}")
        if t_issue < HISTORY_DAYS:
            raise ValueError("issue day too early for the 90-day weather history")
        self.t_issue, self.start = t_issue, start

        F = read_forecast(path)
        F = F[F["date"] > self.issue]
        if F.empty:
            raise ValueError(f"{Path(path).name} has no days after the issue day {self.issue.date()}")
        self.days = pd.date_range(self.issue + pd.Timedelta(days=1), F["date"].max(), freq="D")
        have = set(F["date"].unique())
        gaps = [d.date() for d in self.days if d not in have]
        if gaps:
            raise ValueError(f"forecast file is missing days {gaps[:5]}{' ...' if len(gaps) > 5 else ''}")

        # nearest forecast point for every POWER grid point
        pts = F[["lat", "lon"]].drop_duplicates().to_numpy()
        G = np.stack(np.meshgrid(self.lats, self.lons, indexing="ij"), -1).reshape(-1, 2)
        try:
            from scipy.spatial import cKDTree
            dist, k = cKDTree(pts).query(G)
        except ImportError:                                       # small grids: brute force
            d2 = ((G[:, None, :] - pts[None, :, :]) ** 2).sum(-1)
            k = d2.argmin(1); dist = np.sqrt(d2[np.arange(len(G)), k])
        use = dist <= MAX_DIST_DEG
        self.coverage = float(use.mean())
        key = {tuple(p): n for n, p in enumerate(pts)}
        F["pt"] = [key[(a, b)] for a, b in zip(F["lat"], F["lon"])]
        F["t"] = (F["date"] - self.days[0]).dt.days
        shape = (len(self.days), len(self.lats), len(self.lons))
        self.fc = {}
        for p in REQUIRED + OPTIONAL:
            if p not in F:
                continue
            A = np.full((len(self.days), len(pts)), np.nan, np.float32)
            A[F["t"].to_numpy(), F["pt"].to_numpy()] = F[p].to_numpy(np.float32)
            out = np.full((len(self.days), len(G)), np.nan, np.float32)
            out[:, use] = A[:, k[use]]
            self.fc[p] = out.reshape(shape)

        # observed history: the last HISTORY_DAYS days up to the issue day
        sl = slice(t_issue - HISTORY_DAYS + 1, t_issue + 1)
        load = lambda f: np.asarray(np.load(wx.cache_dir() / f"{f}.npy", mmap_mode="r")[sl])
        self.obs_prec = load("w_prec")
        self.obs_dry = load("w_dry_days")[-1]
        self.obs_ps = load("w_ps")[-1]
        self._tmax_all = np.load(wx.cache_dir() / "w_tmax.npy", mmap_mode="r")
        self._anom_all = np.load(wx.cache_dir() / "w_tmax_anom.npy", mmap_mode="r")
        self._grid_cache: dict = {}
        self._fd = None                                           # fire danger per forecast day (lazy)

    def _fire_danger(self) -> list[dict]:
        """KBDI / DF / FFDI for every forecast day: observed state at the issue day, then the forecast days."""
        if self._fd is None:
            fdx.build_cache()
            st = fdx.run_state(self.t_issue)
            obs = np.asarray(np.load(fdx.CACHE / "f_ffdi.npy", mmap_mode="r")[self.t_issue - 6:self.t_issue + 1])
            recent = list(obs)
            self._fd = []
            for n in range(len(self.days)):
                T = self.fc["T2M_MAX"][n].astype(np.float64)
                q, df = st.step(T, self.fc["PRECTOTCORR"][n].astype(np.float64))
                fi = fdx.ffdi(df, T, self.fc["RH2M"][n], self.fc["WS10M"][n])
                recent = (recent + [fi])[-7:]
                with np.errstate(all="ignore"):
                    mx = np.nanmax(np.stack(recent), 0)
                self._fd.append({"f_kbdi": q, "f_df": df, "f_ffdi": fi, "f_ffdi_max7": mx})
        return self._fd

    def _normal_tmax(self, target: pd.Timestamp) -> np.ndarray:
        """The grid's normal T2M_MAX for the target's calendar day (= tmax - anomaly on any past year)."""
        vals = []
        for y in range(1, 6):
            t = (target - pd.DateOffset(years=y) - self.start).days
            if 0 <= t < self.meta["days"]:
                vals.append(np.asarray(self._tmax_all[t]) - np.asarray(self._anom_all[t]))
        with np.errstate(all="ignore"):
            return np.nanmean(vals, axis=0) if vals else np.full(self.obs_ps.shape, np.nan, np.float32)

    def grid(self, target) -> dict[str, np.ndarray]:
        """The 12 features on the POWER grid for one target day."""
        target = pd.Timestamp(target).normalize()
        if target in self._grid_cache:
            return self._grid_cache[target]
        n = (target - self.issue).days
        if n < 1 or n > len(self.days):
            raise ValueError(f"target {target.date()} outside the forecast days "
                             f"{self.days[0].date()} .. {self.days[-1].date()}")
        f = {p: a[:n] for p, a in self.fc.items()}               # forecast days t+1 .. target
        tmax, rh = f["T2M_MAX"][-1], f["RH2M"][-1]
        es = 0.6108 * np.exp(17.27 * tmax / (tmax + 237.3))
        prec = np.concatenate([self.obs_prec, f["PRECTOTCORR"]])[-HISTORY_DAYS:]
        dry = self.obs_dry.copy()
        for p in f["PRECTOTCORR"]:
            dry = np.where(np.isfinite(p) & (p >= wx.RAIN_MM), 0.0, np.minimum(dry + 1, wx.DRY_CAP))
            dry = np.where(np.isfinite(p), dry, np.nan)
        ps = f["PS"][-1] if "PS" in f else self.obs_ps
        ps = np.where(np.isfinite(ps), ps, self.obs_ps)
        out = {
            "w_tmax": tmax, "w_tmin": f["T2M_MIN"][-1], "w_rh": rh, "w_wind": f["WS10M"][-1],
            "w_prec": f["PRECTOTCORR"][-1], "w_ps": ps,
            "w_vpd": es * (1 - np.clip(rh, 0, 100) / 100),
            "w_prec_7d": prec[-7:].sum(0), "w_prec_30d": prec[-30:].sum(0), "w_prec_90d": prec.sum(0),
            "w_dry_days": dry, "w_tmax_anom": tmax - self._normal_tmax(target),
        }
        out.update(self._fire_danger()[n - 1])
        out = {k: np.asarray(v, np.float32) for k, v in out.items()}
        self._grid_cache[target] = out
        return out

    def features(self, target, lat, lon) -> pd.DataFrame:
        """The 12 features for fire cells (cell-centre lat / lon), nearest POWER point, like weather.py."""
        g = self.grid(target)
        i, ok_i = wx._nearest(self.lats, np.asarray(lat, float))
        j, ok_j = wx._nearest(self.lons, np.asarray(lon, float))
        ok = ok_i & ok_j
        X = {}
        for c in wx.FEATURES + fdx.FEATURES:
            col = np.full(len(i), np.nan, np.float32)
            col[ok] = g[c][i[ok], j[ok]]
            X[c] = col
        return pd.DataFrame(X)


def observed_as_forecast(issue, days: int, path) -> Path:
    """Write the OBSERVED POWER weather of issue+1 .. issue+days in the forecast-file format
    (a test file: a 'perfect' forecast, and a template for the expected columns)."""
    import duckdb
    issue = pd.Timestamp(issue).normalize()
    files = wx.source_files()
    rel = " UNION ALL ".join(f"SELECT * FROM read_parquet('{str(f).replace(chr(92), '/')}')" for f in files)
    a, b = (issue + pd.Timedelta(days=1)).date(), (issue + pd.Timedelta(days=days)).date()
    df = duckdb.sql(f"SELECT date, latitude, longitude, {', '.join(REQUIRED + OPTIONAL)} FROM ({rel}) "
                    f"WHERE date BETWEEN DATE '{a}' AND DATE '{b}' ORDER BY date, latitude, longitude").df()
    path = Path(path)
    df.to_csv(path, index=False) if path.suffix.lower() == ".csv" else df.to_parquet(path, index=False)
    return path
