"""Fire history on the 0.1 deg grid and the leak-free forecast features.

`History` loads backend/data/parquet/daily_cells.parquet (unified + gap-filled record, one row per
cell x fire-day x pass x type x origin) into sparse per-day arrays.

`History.iterate(first, last)` walks fire-days in order and keeps rolling state (ring buffer of the
last 366 daily maps, running 7/30-day and 3-12-month sums, days since fire, burst age). For each
issue day t it yields a `Day` whose `features(ci, cj, night, lead)` returns the feature table for any
set of cells. Everything is computed from fire-days <= t only, so nothing leaks from the future.

History inputs use every origin (real, gapfill, and the V8 model rows of 2000-2011).
Targets (`History.target`) use real VIIRS rows only, on days with complete real data.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import fcst_config as fc

log = logging.getLogger("history")
logging.getLogger("weather").setLevel(logging.WARNING)   # one "rows without weather" line per day otherwise

PAD = 5                     # grid margin, enough for the 9x9 window
RING = 366
NONE_DAYS = 400.0           # days_since value for "no fire in the last 365 days"

HIST_COLS = ["own_same_t", "own_other_t", "own_t1", "own_t2", "frp_t", "frp_7d",
             "nb3_t", "nb5_t", "nb9_t", "nb3_t1", "own_7d", "own_30d", "nb3_7d", "nb5_30d", "nb9_30d",
             "days_since", "burst_age", "burned_3_12m", "dir_n", "dir_s", "dir_e", "dir_w",
             "nat_t", "nat_7d"]
CLIM_COLS = ["clim_mean", "clim_p"]
CAL_COLS = ["is_night", "doy_sin", "doy_cos", "weekday", "lat", "lon"]
BASE_FEATURES = HIST_COLS + CLIM_COLS + CAL_COLS


def weather_cols() -> list[str]:
    import weather
    import fire_danger
    return list(weather.FEATURES) + list(fire_danger.FEATURES)


def _integral(a: np.ndarray) -> np.ndarray:
    """I[x, y] = sum of a[:x, :y] (zero first row/column)."""
    out = np.zeros((a.shape[0] + 1, a.shape[1] + 1), np.float64)
    np.cumsum(np.cumsum(a, 0, dtype=np.float64), 1, out=out[1:, 1:])
    return out


def _rect(I: np.ndarray, shape, di0, di1, dj0, dj1) -> np.ndarray:
    """Sum of a[i+di0 .. i+di1, j+dj0 .. j+dj1] for every (i, j); outside the grid counts as 0.
    The grid has a PAD margin of empty cells, so offsets up to PAD stay inside."""
    ni, nj = shape
    r0, r1 = max(di0, -ni), di1 + 1
    c0, c1 = max(dj0, -nj), dj1 + 1
    rows0 = np.clip(np.arange(ni) + r0, 0, ni); rows1 = np.clip(np.arange(ni) + r1, 0, ni)
    cols0 = np.clip(np.arange(nj) + c0, 0, nj); cols1 = np.clip(np.arange(nj) + c1, 0, nj)
    return (I[np.ix_(rows1, cols1)] - I[np.ix_(rows0, cols1)] - I[np.ix_(rows1, cols0)]
            + I[np.ix_(rows0, cols0)]).astype(np.float32)


def _box(a: np.ndarray, r: int) -> np.ndarray:
    return _rect(_integral(a), a.shape, -r, r, -r, r)


class History:
    def __init__(self, cells_parquet=fc.CELLS_PARQUET, status_parquet=fc.DAY_STATUS_PARQUET,
                 clim_years=fc.CLIM_YEARS):
        import duckdb
        con = duckdb.connect()
        types = ",".join(str(t) for t in fc.FIRE_TYPES)
        df = con.execute(f"""
            SELECT fire_date, pass = 'N' AS night, ci, cj, origin, CAST(count AS FLOAT) AS n,
                   CAST(n_rows AS FLOAT) AS nr, CAST(frp_sum AS FLOAT) AS frp
            FROM read_parquet('{cells_parquet.as_posix()}') WHERE type IN ({types})""").df()
        st = con.execute(f"SELECT * FROM read_parquet('{status_parquet.as_posix()}')").df()

        self.start = pd.Timestamp(df.fire_date.min())
        self.end = pd.Timestamp(df.fire_date.max())
        self.n_days = (self.end - self.start).days + 1
        self.i0 = int(df.ci.min()) - PAD
        self.j0 = int(df.cj.min()) - PAD
        self.shape = (int(df.ci.max()) - self.i0 + PAD + 1, int(df.cj.max()) - self.j0 + PAD + 1)

        d = (pd.to_datetime(df.fire_date) - self.start).dt.days.to_numpy(np.int32)
        ii = (df.ci.to_numpy() - self.i0).astype(np.int32)
        jj = (df.cj.to_numpy() - self.j0).astype(np.int32)
        nt = df.night.to_numpy(bool)
        n = df.n.to_numpy(np.float32)
        frp = np.nan_to_num(df.frp.to_numpy(np.float32))
        real = (df.origin == "real").to_numpy()
        unified = fc.TARGET_SOURCE == "unified"
        nr = df.nr.to_numpy(np.float32)            # rows written in the unified CSV (= count for real rows)

        # history: every origin, summed over origins
        o = np.argsort(d, kind="stable")
        self._h = dict(d=d[o], i=ii[o], j=jj[o], night=nt[o], n=n[o], frp=frp[o])
        self._hb = np.searchsorted(self._h["d"], np.arange(self.n_days + 1))
        # targets: real rows only, or (TARGET_SOURCE = "unified") every row of the unified gap-filled CSV
        r = o if unified else o[real[o]]
        self._t = dict(d=d[r], i=ii[r], j=jj[r], night=nt[r], n=(nr if unified else n)[r])
        self._tb = np.searchsorted(self._t["d"], np.arange(self.n_days + 1))

        self.land = np.zeros(self.shape, bool)
        self.land[ii, jj] = True
        self.land_i, self.land_j = np.nonzero(self.land)

        # days whose real data can be used as a target: complete, both passes reliable, no gap-fill
        st["fire_date"] = pd.to_datetime(st.fire_date)
        if unified:      # every complete day; gap-filled days count (their rows are in the unified CSV)
            ok = (st.has_data == "full") & (st.fire_date >= pd.Timestamp(fc.TARGET_START))
        else:
            ok = ((st.has_data == "full") & st.daynight_reliable & (st.count_gapfill == 0)
                  & (st.fire_date >= pd.Timestamp(fc.REAL_START)))
        self.target_ok = np.zeros(self.n_days, bool)
        k = (st.fire_date[ok] - self.start).dt.days.to_numpy()
        k = k[(k >= 0) & (k < self.n_days)]
        self.target_ok[k] = True

        self._climatology(df.assign(d=d, i=ii, j=jj, night=nt), list(clim_years))
        log.info("history %s .. %s (%d days), %d land cells, grid %s, %d target days",
                 self.start.date(), self.end.date(), self.n_days, len(self.land_i), self.shape,
                 int(self.target_ok.sum()))

    # ------------------------------------------------------------ helpers
    def day_index(self, date) -> int:
        return (pd.Timestamp(date) - self.start).days

    def date(self, d: int) -> pd.Timestamp:
        return self.start + pd.Timedelta(days=int(d))

    def cell_latlon(self, i, j):
        g = fc.mlc.GRID_DEG
        return (fc.mlc.GRID_LAT0 + (np.asarray(i) + self.i0 + 0.5) * g,
                fc.mlc.GRID_LON0 + (np.asarray(j) + self.j0 + 0.5) * g)

    def _maps(self, d: int):
        """Dense day/night count maps and an FRP map for history day d."""
        s, e = self._hb[d], self._hb[d + 1]
        h = self._h
        D = np.zeros(self.shape, np.float32); N = np.zeros(self.shape, np.float32)
        F = np.zeros(self.shape, np.float32)
        nt = h["night"][s:e]
        np.add.at(D, (h["i"][s:e][~nt], h["j"][s:e][~nt]), h["n"][s:e][~nt])
        np.add.at(N, (h["i"][s:e][nt], h["j"][s:e][nt]), h["n"][s:e][nt])
        np.add.at(F, (h["i"][s:e], h["j"][s:e]), h["frp"][s:e])
        return D, N, F

    def target(self, d: int) -> np.ndarray:
        """Real row counts on day d, shape (2, ni, nj) = (day pass, night pass)."""
        T = np.zeros((2,) + self.shape, np.float32)
        s, e = self._tb[d], self._tb[d + 1]
        t = self._t
        np.add.at(T, (t["night"][s:e].astype(int), t["i"][s:e], t["j"][s:e]), t["n"][s:e])
        return T

    def _climatology(self, df: pd.DataFrame, years: list[int]):
        """Per pass x calendar month x cell: mean daily count and share of days with fire."""
        fd = pd.to_datetime(df.fire_date)
        sel = fd.dt.year.isin(years).to_numpy()
        days = pd.date_range(f"{min(years)}-01-01", f"{max(years)}-12-31")
        days = days[(days >= self.start) & (days <= self.end)]
        ndays = np.bincount(days.month - 1, minlength=12).astype(np.float32)       # days per month
        # one value per cell x day x pass first (a day can hold real + gapfill rows)
        cd = df[sel].groupby(["d", "night", "i", "j"], sort=False)["n"].sum().reset_index()
        dt = self.start + pd.to_timedelta(cd["d"], unit="D")
        cd["mo"] = dt.dt.month - 1
        cd["yr"] = dt.dt.year
        g = cd.groupby(["night", "mo", "i", "j"]).agg(n=("n", "sum"), k=("n", "size")).reset_index()
        self._clim_n = np.zeros((2, 12) + self.shape, np.float32)      # sums over CLIM_YEARS
        self._clim_k = np.zeros((2, 12) + self.shape, np.float32)
        nt, mo = g["night"].to_numpy().astype(int), g["mo"].to_numpy().astype(int)
        i, j = g["i"].to_numpy(), g["j"].to_numpy()
        self._clim_n[nt, mo, i, j] = g["n"].to_numpy()
        self._clim_k[nt, mo, i, j] = g["k"].to_numpy()
        self._clim_days = ndays
        self.clim_mean = self._clim_n / ndays[None, :, None, None]
        self.clim_p = np.minimum(self._clim_k / ndays[None, :, None, None], 1.0)
        # per-year sums, so a target day inside CLIM_YEARS gets a leave-one-year-out climatology
        # (otherwise its own fire count is part of its feature: target leakage)
        gy = cd.groupby(["yr", "mo", "night", "i", "j"]).agg(n=("n", "sum"), k=("n", "size")).reset_index()
        self._clim_year = {key: grp for key, grp in gy.groupby(["yr", "mo"])}
        self._clim_year_days = {(int(y), int(m)): int(c) for (y, m), c in
                                pd.Series(1, index=[days.year, days.month - 1]).groupby(level=[0, 1]).sum().items()}
        self._clim_cache = {}
        self.clim_years = (min(years), max(years))

    def clim(self, night: np.ndarray, mo: int, i: np.ndarray, j: np.ndarray, year: int):
        """clim_mean, clim_p for target month `mo` (0-11) of `year`; leave-one-year-out inside CLIM_YEARS."""
        nt = night.astype(int)
        key = (int(year), int(mo))
        if key not in self._clim_year_days:
            return self.clim_mean[nt, mo, i, j], self.clim_p[nt, mo, i, j]
        if key not in self._clim_cache:
            if len(self._clim_cache) > 4:
                self._clim_cache.clear()
            n = self._clim_n[:, mo].copy(); k = self._clim_k[:, mo].copy()
            grp = self._clim_year.get(key)
            if grp is not None:
                gn, gi, gj = grp["night"].to_numpy().astype(int), grp["i"].to_numpy(), grp["j"].to_numpy()
                np.subtract.at(n, (gn, gi, gj), grp["n"].to_numpy(np.float32))
                np.subtract.at(k, (gn, gi, gj), grp["k"].to_numpy(np.float32))
            nd = max(self._clim_days[mo] - self._clim_year_days[key], 1)
            self._clim_cache[key] = (np.maximum(n, 0) / nd, np.clip(k / nd, 0, 1))
        m, p = self._clim_cache[key]
        return m[nt, i, j], p[nt, i, j]

    # ------------------------------------------------------------ rolling state
    def iterate(self, first: int, last: int, warm: int = RING + 35):
        """Yield Day objects for issue days first..last (day indexes), after `warm` warm-up days."""
        last = min(last, self.n_days - 1)
        d0 = max(0, first - warm)
        sh = self.shape
        ring = np.zeros((RING,) + sh, np.float32)     # daily total counts
        ringf = np.zeros((RING,) + sh, np.float32)    # daily FRP
        s7 = np.zeros(sh, np.float32); s30 = np.zeros(sh, np.float32); burn = np.zeros(sh, np.float32)
        f7 = np.zeros(sh, np.float32)
        dsl = np.full(sh, NONE_DAYS, np.float32); burst = np.zeros(sh, np.float32)
        nat = np.zeros(RING, np.float64)
        for d in range(d0, last + 1):
            D, N, F = self._maps(d)
            tot = D + N
            k = d % RING
            # drop what leaves each window, then add today
            if d - 7 >= d0:
                s7 -= ring[(d - 7) % RING]; f7 -= ringf[(d - 7) % RING]
            if d - 30 >= d0:
                s30 -= ring[(d - 30) % RING]
            if d - 90 >= d0:
                burn += ring[(d - 90) % RING]           # enters the 90..365-day window
            if d - 366 >= d0:
                burn -= ring[(d - 366) % RING]
            ring[k] = tot; ringf[k] = F
            s7 += tot; s30 += tot; f7 += F
            np.maximum(s7, 0, out=s7); np.maximum(s30, 0, out=s30); np.maximum(burn, 0, out=burn)
            dsl = np.where(tot > 0, 0.0, np.minimum(dsl + 1, NONE_DAYS)).astype(np.float32)
            nb3 = _box(tot, 1)
            burst = np.where(nb3 > 0, burst + 1, 0).astype(np.float32)
            nat[k] = tot.sum()
            if d < first:
                continue
            yield Day(self, d, D, N, F, tot, ring[(d - 1) % RING], ring[(d - 2) % RING], s7, s30, f7,
                      burn, dsl, burst, nb3, float(nat[k]), float(nat[[(d - x) % RING for x in range(7)]].sum()))


class Day:
    """Feature maps for one issue day; `features()` gathers them for any cells/passes/lead."""

    def __init__(self, H: History, d, D, N, F, tot, t1, t2, s7, s30, f7, burn, dsl, burst, nb3, nat_t, nat_7d):
        self.H, self.d = H, d
        sh = H.shape
        It = _integral(tot)
        I30 = _integral(s30)
        self.maps = {
            "D": D, "N": N, "own_t1": t1.copy(), "own_t2": t2.copy(), "frp_t": F, "frp_7d": f7.copy(),
            "nb3_t": nb3, "nb5_t": _rect(It, sh, -2, 2, -2, 2), "nb9_t": _rect(It, sh, -4, 4, -4, 4),
            "nb3_t1": _box(t1, 1), "own_7d": s7.copy(), "own_30d": s30.copy(), "nb3_7d": _box(s7, 1),
            "nb5_30d": _rect(I30, sh, -2, 2, -2, 2), "nb9_30d": _rect(I30, sh, -4, 4, -4, 4),
            "days_since": dsl.copy(), "burst_age": burst.copy(), "burned_3_12m": burn.copy(),
            # spread direction: fire in the half of the 5x5 to the north / south / east / west (ci grows north)
            "dir_n": _rect(It, sh, 1, 2, -2, 2), "dir_s": _rect(It, sh, -2, -1, -2, 2),
            "dir_e": _rect(It, sh, -2, 2, 1, 2), "dir_w": _rect(It, sh, -2, 2, -2, -1),
        }
        self.nat_t, self.nat_7d = nat_t, nat_7d

    @property
    def date(self) -> pd.Timestamp:
        return self.H.date(self.d)

    def active(self) -> np.ndarray:
        """Cells with fire in their 5x5 within the last 30 days."""
        return self.maps["nb5_30d"] > 0

    def features(self, i, j, night, lead: int, weather=False) -> pd.DataFrame:
        """weather: False | "issue" (POWER weather of issue day t: known when the forecast is made) |
        "target" (weather of the target day: observed in tests = a perfect weather forecast; True = "target")."""
        i = np.asarray(i); j = np.asarray(j); night = np.asarray(night).astype(bool)
        m = self.maps
        X = {}
        same = np.where(night, m["N"][i, j], m["D"][i, j])
        other = np.where(night, m["D"][i, j], m["N"][i, j])
        X["own_same_t"], X["own_other_t"] = same, other
        for c in HIST_COLS[2:-2]:
            X[c] = m[c][i, j]
        X["nat_t"] = np.full(len(i), self.nat_t, np.float32)
        X["nat_7d"] = np.full(len(i), self.nat_7d, np.float32)
        tgt = self.H.date(self.d + lead)
        mo = tgt.month - 1
        X["clim_mean"], X["clim_p"] = self.H.clim(night, mo, i, j, tgt.year)
        doy = 2 * np.pi * (tgt.dayofyear - 1) / 365.25
        X["is_night"] = night.astype(np.float32)
        X["doy_sin"] = np.full(len(i), np.sin(doy), np.float32)
        X["doy_cos"] = np.full(len(i), np.cos(doy), np.float32)
        X["weekday"] = np.full(len(i), tgt.dayofweek, np.float32)
        lat, lon = self.H.cell_latlon(i, j)
        X["lat"], X["lon"] = lat.astype(np.float32), lon.astype(np.float32)
        X = pd.DataFrame({k: np.asarray(v, np.float32) for k, v in X.items()})
        if weather:
            import weather as wx
            W = pd.DataFrame({"lat": X["lat"].astype(float), "lon": X["lon"].astype(float),
                              "fire_date": pd.Series(self.H.date(self.d) if weather == "issue" else tgt,
                                                     index=X.index)})
            wx.add_features(W)
            for c in wx.FEATURES:
                X[c] = W[c].to_numpy(np.float32)
            import fire_danger
            Fd = fire_danger.add_features(W["lat"].to_numpy(), W["lon"].to_numpy(), W["fire_date"].iloc[0])
            for c in fire_danger.FEATURES:
                X[c] = Fd[c].to_numpy(np.float32)
        return X
