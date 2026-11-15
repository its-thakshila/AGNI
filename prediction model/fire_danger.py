"""McArthur Forest Fire Danger Index (FFDI, Mk 5) from the NASA POWER weather, per POWER grid point and day.

    KBDI   Keetch-Byram drought index (mm of soil-moisture deficit, 0-203.2): rises with heat, falls with rain
           (Keetch & Byram 1968, metric form; the first 5 mm of each run of rain days is intercepted).
    DF     drought factor 0-10 (Griffiths 1999): KBDI + days since the last rain event and its size (last 20 d)
    FFDI   2 exp(-0.450 + 0.987 ln DF - 0.0345 RH + 0.0338 T + 0.0234 V)
           with T = daily max temperature (C), RH = relative humidity (%), V = 10 m wind (km/h)

Inputs come from the ml_pipeline weather cache (daily T2M_MAX, RH2M, WS10M, PRECTOTCORR). The official
index uses mid-afternoon humidity and wind; POWER gives daily means, so these FFDI values run lower than
Bureau of Meteorology values. As a model feature only the ranking in space and time matters.

Features (prefix f_): f_kbdi, f_df, f_ffdi on the weather day, f_ffdi_max7 (highest FFDI of the last
7 days). The cache (output/cache/fire_danger/) is rebuilt when the weather cache changes.

    python fire_danger.py        build the cache and print a summary
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd

import fcst_config as fc
import weather as wx

log = logging.getLogger("fire_danger")
FEATURES = ["f_kbdi", "f_df", "f_ffdi", "f_ffdi_max7"]
CACHE = fc.CACHE / "fire_danger"
RAIN_EVENT_MM = 2.0          # Griffiths: rain days of an event have > 2 mm
EVENT_DAYS = 20              # events older than this no longer reduce the drought factor
INTERCEPT_MM = 5.0           # KBDI: first 5 mm of a run of rain days does not reach the soil


class State:
    """Day-by-day KBDI / rain-event state for every grid point (vectorized)."""

    def __init__(self, shape, annual_rain):
        self.q = np.zeros(shape, np.float64)              # KBDI
        self.kb_run = np.zeros(shape, np.float64)         # rain so far in the current run of rain days (KBDI)
        self.ev_p = np.zeros(shape, np.float64)           # size of the last rain event (mm)
        self.ev_max = np.zeros(shape, np.float64)         # largest daily rain in that event
        self.ev_n = np.full(shape, 99.0)                  # days since that event's largest rain day
        self.in_ev = np.zeros(shape, bool)
        self.den = 1 + 10.88 * np.exp(-0.001736 * annual_rain)

    def step(self, tmax, prec):
        """Advance one day. Missing inputs keep the state (and count the day as dry for the event age)."""
        ok_p = np.isfinite(prec)
        p = np.where(ok_p, prec, 0.0)
        # KBDI: net rain after interception, then evapotranspiration
        run = np.where(p > 0, self.kb_run + p, 0.0)
        net = np.maximum(run - INTERCEPT_MM, 0) - np.maximum(self.kb_run * (p > 0) - INTERCEPT_MM, 0)
        self.kb_run = run
        q = np.maximum(self.q - net, 0)
        t = np.where(np.isfinite(tmax), tmax, np.nan)
        et = (203.2 - q) * (0.968 * np.exp(0.0875 * t + 1.5552) - 8.30) / self.den * 1e-3
        et = np.where(np.isfinite(et), np.maximum(et, 0), 0)
        self.q = np.minimum(q + et, 203.2)
        # rain events (consecutive days > 2 mm)
        wet = p > RAIN_EVENT_MM
        start = wet & ~self.in_ev
        cont = wet & self.in_ev
        self.ev_p = np.where(start, p, np.where(cont, self.ev_p + p, self.ev_p))
        new_max = start | (cont & (p > self.ev_max))
        self.ev_max = np.where(new_max, p, self.ev_max)
        self.ev_n = np.where(new_max, 0, self.ev_n + 1)
        self.in_ev = wet
        return self.q.copy(), self.drought_factor()

    def drought_factor(self):
        i, n, pe = self.q, self.ev_n, self.ev_p
        x = np.where(n >= 1, n ** 1.3 / (n ** 1.3 + pe - 2), 0.8 ** 1.3 / (0.8 ** 1.3 + pe - 2))
        x = np.where((pe <= RAIN_EVENT_MM) | (n > EVENT_DAYS), 1.0, x)
        xlim = np.where(i < 20, 1 / (1 + 0.1135 * i), 75 / (270.525 - 1.267 * i))
        x = np.clip(np.minimum(x, xlim), 0, 1)
        df = 10.5 * (1 - np.exp(-(i + 30) / 40)) * (41 * x ** 2 + x) / (40 * x ** 2 + x + 1)
        return np.clip(df, 0, 10)


def ffdi(df, tmax, rh, wind_ms):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 2 * np.exp(-0.450 + 0.987 * np.log(np.maximum(df, 0.01)) - 0.0345 * rh + 0.0338 * tmax
                          + 0.0234 * wind_ms * 3.6)


def _load(name, sl=slice(None)):
    return np.asarray(np.load(wx.cache_dir() / f"{name}.npy", mmap_mode="r")[sl])


def annual_rain() -> np.ndarray:
    prec = np.load(wx.cache_dir() / "w_prec.npy", mmap_mode="r")
    s = np.zeros(prec.shape[1:]); n = np.zeros(prec.shape[1:])
    for a in range(0, prec.shape[0], 1000):
        b = np.asarray(prec[a:a + 1000]); ok = np.isfinite(b)
        s += np.where(ok, b, 0).sum(0); n += ok.sum(0)
    return s / np.maximum(n, 1) * 365.25


def run_state(t_end: int, spinup: int = 365) -> State:
    """KBDI / event state after observed weather day t_end (inclusive), from the start of the record."""
    tmax = np.load(wx.cache_dir() / "w_tmax.npy", mmap_mode="r")
    prec = np.load(wx.cache_dir() / "w_prec.npy", mmap_mode="r")
    st = State(tmax.shape[1:], annual_rain())
    for t in list(range(min(spinup, t_end + 1))) + list(range(t_end + 1)):   # first year twice = spin-up
        st.step(np.asarray(tmax[t], np.float64), np.asarray(prec[t], np.float64))
    return st


def build_cache(force: bool = False) -> dict:
    wmeta = wx.build_cache()
    meta_f = CACHE / "meta.json"
    if not force and meta_f.exists():
        m = json.loads(meta_f.read_text())
        if m.get("weather_built") == wmeta.get("built") and all((CACHE / f"{f}.npy").exists() for f in FEATURES):
            return m
    t0 = time.perf_counter()
    tmax = np.load(wx.cache_dir() / "w_tmax.npy", mmap_mode="r")
    prec = np.load(wx.cache_dir() / "w_prec.npy", mmap_mode="r")
    rh = np.load(wx.cache_dir() / "w_rh.npy", mmap_mode="r")
    wind = np.load(wx.cache_dir() / "w_wind.npy", mmap_mode="r")
    nd, shape = tmax.shape[0], tmax.shape[1:]
    st = State(shape, annual_rain())
    for t in range(min(365, nd)):                                   # spin-up on the first year
        st.step(np.asarray(tmax[t], np.float64), np.asarray(prec[t], np.float64))
    out = {f: np.lib.format.open_memmap(CACHE.mkdir(parents=True, exist_ok=True) or CACHE / f"{f}.tmp.npy",
                                        mode="w+", dtype=np.float32, shape=tmax.shape) for f in FEATURES}
    recent = []
    for t in range(nd):
        T = np.asarray(tmax[t], np.float64)
        q, df = st.step(T, np.asarray(prec[t], np.float64))
        fi = ffdi(df, T, np.asarray(rh[t], np.float64), np.asarray(wind[t], np.float64))
        recent = (recent + [fi])[-7:]
        out["f_kbdi"][t] = q; out["f_df"][t] = df; out["f_ffdi"][t] = fi
        with np.errstate(all="ignore"):
            out["f_ffdi_max7"][t] = np.nanmax(np.stack(recent), 0) if np.isfinite(np.stack(recent)).any() else np.nan
    for a in out.values():
        a.flush()
    del a
    out.clear()                                                     # close the memmaps (Windows locks open files)
    import gc
    gc.collect()
    for f in FEATURES:
        (CACHE / f"{f}.tmp.npy").replace(CACHE / f"{f}.npy")
    m = {"weather_built": wmeta.get("built"), "start": wmeta["start"], "days": nd,
         "built": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.perf_counter() - t0)}
    meta_f.write_text(json.dumps(m, indent=1))
    log.info("fire danger cache: %d days in %.0f s -> %s", nd, time.perf_counter() - t0, CACHE)
    return m


def add_features(lat, lon, dates) -> pd.DataFrame:
    """f_ features for cells (centre lat/lon) on weather days `dates` (scalar or array), nearest POWER point."""
    m = build_cache()
    wm = wx.load_meta()
    lats, lons = np.array(wm["lats"]), np.array(wm["lons"])
    i, ok_i = wx._nearest(lats, np.asarray(lat, float))
    j, ok_j = wx._nearest(lons, np.asarray(lon, float))
    t = np.broadcast_to(np.asarray((pd.to_datetime(dates) - pd.Timestamp(m["start"])).days if np.ndim(dates)
                                   else (pd.Timestamp(dates) - pd.Timestamp(m["start"])).days), len(i))
    ok = ok_i & ok_j & (t >= 0) & (t < m["days"])
    X = {}
    if ok.any():
        lo, hi = int(t[ok].min()), int(t[ok].max())
        for f in FEATURES:
            arr = np.asarray(np.load(CACHE / f"{f}.npy", mmap_mode="r")[lo:hi + 1])
            col = np.full(len(i), np.nan, np.float32)
            col[ok] = arr[t[ok] - lo, i[ok], j[ok]]
            X[f] = col
    else:
        X = {f: np.full(len(i), np.nan, np.float32) for f in FEATURES}
    return pd.DataFrame(X)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    build_cache(force=True)
    wm = wx.load_meta()
    for f in FEATURES:
        a = np.load(CACHE / f"{f}.npy", mmap_mode="r")
        s = np.asarray(a[-3650:])
        print(f"{f:12s} mean {np.nanmean(s):7.2f}  p50 {np.nanpercentile(s, 50):7.2f}  p99 {np.nanpercentile(s, 99):7.2f}"
              f"  missing {np.isnan(s).mean() * 100:.2f}%")
