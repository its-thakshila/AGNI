"""Daily cell x pass feature table for MODIS -> VIIRS harmonization.

One row = one 0.1 deg cell, one fire_date, one pass (D/N). Candidate rows are every
cell with MODIS fire in its 3x3 neighbourhood within +-1 day in the same pass, so the
model also covers cells MODIS left empty but VIIRS (more sensitive) may detect.

How each sensor difference is represented:
  resolution   -> MODIS pixel size (scan*track) features; target = VIIRS 375 m pixel count
  sensitivity  -> FRP / brightness / confidence of MODIS pixels + neighbourhood rows (MODIS = 0)
  overpass     -> Terra (09:00/21:30) and Aqua (15:00/02:40) kept as separate feature groups,
                  pass flag, local-solar fire_date, +-1 day and other-pass persistence
  confidence   -> MODIS 0-100 stats as features; VIIRS l/n/h predicted by a classifier
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import common
import config

NI = NJ = 4000
STAT_COLS = ["n", "frp_sum", "frp_max", "conf_mean", "conf_min", "bt_mean", "bt31_mean",
             "px_area", "scan_mean", "static_share"]


def encode(ci, cj, day, p):
    return ((np.asarray(day, np.int64) * 2 + np.asarray(p, np.int64)) * NI
            + np.asarray(ci, np.int64)) * NJ + np.asarray(cj, np.int64)


DAY0 = pd.Timestamp(config.DAY0)   # shared by every year -> keys unique across years


def _day_index(dates: pd.Series, day0: pd.Timestamp = DAY0) -> np.ndarray:
    d = (dates - day0).dt.days.to_numpy(np.int64)
    if len(d) and d.min() < 2:   # candidate keys use day-1; keys must stay non-negative to decode
        raise ValueError(f"fire_date {dates.min().date()} is too close to config.DAY0 = {day0.date()}")
    return d


FIRE_HISTORY_COLS = ["own_15d_n", "own_31d_n", "nb3_7d_n", "days_since_last", "days_until_next", "burst_age"]
AQUA_GROWTH_COLS = ["aqua_terra_n_lr", "aqua_terra_frp_lr", "aqua_nb_n", "aqua_nb_frp"]
CLIMATOLOGY_COLS = ["clim_terra_n", "burned_3_12m_n"]
OVERPASS_COLS = ["terra_lst_mean", "aqua_lst_mean"]
WEATHER_COLS = ["w_tmax", "w_tmin", "w_rh", "w_wind", "w_prec", "w_ps", "w_vpd",     # NASA POWER (V9), weather.py
                "w_prec_7d", "w_prec_30d", "w_prec_90d", "w_dry_days", "w_tmax_anom"]


def wanted_groups(feats: list[str] | None = None) -> set[str]:
    """Optional feature groups to compute: from a saved model's feature list, else from the config switches."""
    if feats is not None:
        f = set(feats)
        return {g for g, cols in (("fire_history", FIRE_HISTORY_COLS), ("aqua_growth", AQUA_GROWTH_COLS),
                                  ("climatology", CLIMATOLOGY_COLS), ("overpass", OVERPASS_COLS),
                                  ("weather", WEATHER_COLS))
                if f & set(cols)}
    return {g for g, on in (("fire_history", config.FEAT_FIRE_HISTORY), ("aqua_growth", config.FEAT_AQUA_GROWTH),
                            ("climatology", config.FEAT_CLIMATOLOGY), ("overpass", config.FEAT_OVERPASS_TIME),
                            ("weather", config.FEAT_WEATHER)) if on}


def _sensor_stats(m: pd.DataFrame, overpass: bool = False) -> pd.DataFrame:
    g = m.assign(area=m["scan"] * m["track"], static=(m["type"] != 0).astype(float),
                 # hours after the fire-day start (6..30): night passes do not wrap around midnight
                 lst_rel=(m["lst_hour"] - config.DAY_START_HOUR) % 24 + config.DAY_START_HOUR) \
         .groupby("key")
    d = {
        "n": g.size(), "frp_sum": g["frp"].sum(), "frp_max": g["frp"].max(),
        "conf_mean": g["confidence"].mean(), "conf_min": g["confidence"].min(),
        "bt_mean": g["brightness"].mean(), "bt31_mean": g["bright_t31"].mean(),
        "px_area": g["area"].mean(), "scan_mean": g["scan"].mean(), "static_share": g["static"].mean(),
    }
    if overpass:
        d["lst_mean"] = g["lst_rel"].mean()
    return pd.DataFrame(d)


# ------------------------------------------------------------------ climatology (feature group 5+7)
_CLIM = None


def load_climatology():
    """(clim, monthly, first_ym): Terra detections per cell x calendar month (mean per year with data),
    Terra detections per cell x year-month, and the first year-month with Terra data. Built once from
    every MODIS file and cached in config.CLIMATOLOGY_FILE. Terra only, so the meaning does not change
    when Aqua starts in 2002."""
    global _CLIM
    if _CLIM is not None:
        return _CLIM
    path = config.CLIMATOLOGY_FILE
    if path.exists():
        _CLIM = pd.read_pickle(path)
        return _CLIM
    parts = []
    for y in config.modis_years():
        m = common.load_modis(config.modis_path(y))
        m = m[m["sensor"] == "terra"]
        ym = (m["fire_date"].dt.year * 12 + m["fire_date"].dt.month - 1).to_numpy(np.int64)
        ck = m["ci"].to_numpy(np.int64) * NJ + m["cj"].to_numpy(np.int64)
        parts.append(pd.Series(ck * 100_000 + ym).value_counts())
    monthly = pd.concat(parts).groupby(level=0).sum().astype(np.float32)
    ck, ym = monthly.index.to_numpy() // 100_000, monthly.index.to_numpy() % 100_000
    months_seen = pd.Series(np.unique(ym)) % 12                    # calendar months that have Terra data
    n_years = months_seen.value_counts().reindex(range(12), fill_value=1)
    clim = pd.Series(monthly.to_numpy(), index=ck * 12 + ym % 12).groupby(level=0).sum()
    clim = (clim / n_years.reindex(clim.index % 12).to_numpy()).astype(np.float32)
    _CLIM = (clim, monthly, int(ym.min()))
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(_CLIM, path)
    return _CLIM


def _series_lookup(s: pd.Series, keys: np.ndarray) -> np.ndarray:
    pos = s.index.get_indexer(keys)
    return np.where(pos >= 0, s.to_numpy()[np.clip(pos, 0, None)], 0.0)


def build(modis: pd.DataFrame, use_aqua: bool = True, day0: pd.Timestamp | None = None,
          groups: set[str] | None = None) -> pd.DataFrame:
    """Return the candidate feature table (index-free, with ci/cj/day/pass/fire_date).
    groups = optional feature groups (see wanted_groups); None = from the config switches."""
    groups = wanted_groups() if groups is None else groups
    m = modis if use_aqua else modis[modis["sensor"] == "terra"]
    m = m.copy()
    day0 = DAY0 if day0 is None else day0
    m["day"] = _day_index(m["fire_date"], day0)
    m["p"] = (m["pass"] == "N").astype(np.int64)
    m["key"] = encode(m["ci"], m["cj"], m["day"], m["p"])

    # --- candidate keys: 3x3 cells x +-1 day around any MODIS detection, same pass
    base = m[["ci", "cj", "day", "p"]].drop_duplicates()
    R, D = config.NEIGHBOUR_CELLS, config.NEIGHBOUR_DAYS
    cand = np.unique(np.concatenate([
        encode(base.ci + a, base.cj + b, base.day + c, base.p)
        for a in range(-R, R + 1) for b in range(-R, R + 1) for c in range(-D, D + 1)]))
    cj = cand % NJ
    ci = (cand // NJ) % NI
    p = (cand // (NI * NJ)) % 2
    day = cand // (NI * NJ * 2)
    X = pd.DataFrame({"key": cand, "ci": ci.astype(np.int32), "cj": cj.astype(np.int32),
                      "day": day, "p": p.astype(np.int8)})

    # --- own-cell stats per sensor (overpass-specific)
    sensors = ["terra", "aqua"] if use_aqua else ["terra"]
    for s in sensors:
        st = _sensor_stats(m[m["sensor"] == s], overpass="overpass" in groups)
        st.columns = [f"{s}_{c}" for c in st.columns]
        X = X.join(st, on="key")
        X[f"{s}_n"] = X[f"{s}_n"].fillna(0)
        X[f"{s}_frp_sum"] = X[f"{s}_frp_sum"].fillna(0)

    # --- context lookups on combined MODIS (counts + FRP)
    def make_look(det: pd.DataFrame):
        tot = det.groupby("key").agg(n=("frp", "size"), frp=("frp", "sum"))
        idx = pd.Index(tot.index)
        n_arr, f_arr = tot["n"].to_numpy(float), tot["frp"].to_numpy(float)

        def look(di=0, dj=0, dd=0, flip_pass=False):
            k = encode(X.ci + di, X.cj + dj, X.day + dd, (1 - X.p) if flip_pass else X.p)
            pos = idx.get_indexer(k)
            ok = pos >= 0
            if not len(n_arr):   # no detections from this sensor in the window (e.g. a Terra or Aqua outage)
                return np.zeros(len(X)), np.zeros(len(X))
            n = np.where(ok, n_arr[np.clip(pos, 0, None)], 0.0)
            f = np.where(ok, f_arr[np.clip(pos, 0, None)], 0.0)
            return n, f
        return look

    look = make_look(m)

    own_n, own_f = look()
    X["modis_n"], X["modis_frp"] = own_n, own_f
    nb_n = np.zeros(len(X)); nb_f = np.zeros(len(X)); nb_cells = np.zeros(len(X))
    for a in (-1, 0, 1):
        for b in (-1, 0, 1):
            if a == 0 and b == 0:
                continue
            n, f = look(a, b)
            nb_n += n; nb_f += f; nb_cells += n > 0
    X["nb_n"], X["nb_frp"], X["nb_active_cells"] = nb_n, nb_f, nb_cells

    for dd, lab in ((-1, "prev"), (1, "next")):
        X[f"own_{lab}_n"], _ = look(dd=dd)
        s = np.zeros(len(X))
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                s += look(a, b, dd)[0]
        X[f"nb3_{lab}_n"] = s
    X["own_otherpass_n"], X["own_otherpass_frp"] = look(flip_pass=True)
    week = np.zeros(len(X))
    for dd in range(-3, 4):
        week += look(dd=dd)[0]
    X["own_7d_n"] = week

    # --- optional group 1+2: fire history (computed on the fly; no per-day arrays are kept)
    if "fire_history" in groups:
        nrow = len(X)
        own15, own31 = np.zeros(nrow), np.zeros(nrow)
        since, until = np.full(nrow, 31.0), np.full(nrow, 16.0)
        age, gap, alive = np.zeros(nrow), np.zeros(nrow), np.ones(nrow, bool)
        for dd in [0] + list(range(-1, -31, -1)) + list(range(1, 16)):    # today, back 30 days, ahead 15
            same = look(dd=dd)[0]
            active = (same + look(dd=dd, flip_pass=True)[0]) > 0          # either pass that fire-day
            if abs(dd) <= 15:
                own31 += same
            if abs(dd) <= 7:
                own15 += same
            if dd < 0:
                since = np.where((since == 31) & active, -dd, since)
                age = np.where(alive & active, -dd, age)                  # start of the current burst
                gap = np.where(active, 0, gap + 1)
                alive &= gap <= 2                                          # a burst ends after 3 quiet days
            elif dd > 0:
                until = np.where((until == 16) & active, dd, until)
        nb7 = np.zeros(nrow)
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                for dd in range(-3, 4):
                    nb7 += look(a, b, dd)[0]
        X["own_15d_n"], X["own_31d_n"], X["nb3_7d_n"] = own15, own31, nb7
        X["days_since_last"], X["days_until_next"], X["burst_age"] = since, until, age

    # --- optional group 3+4: Aqua (VIIRS-like overpass time) vs Terra
    if "aqua_growth" in groups and use_aqua:
        X["aqua_terra_n_lr"] = np.log1p(X["aqua_n"]) - np.log1p(X["terra_n"])
        X["aqua_terra_frp_lr"] = np.log1p(X["aqua_frp_sum"]) - np.log1p(X["terra_frp_sum"])
        look_aqua = make_look(m[m["sensor"] == "aqua"])
        an, af = np.zeros(len(X)), np.zeros(len(X))
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                if a == 0 and b == 0:
                    continue
                n, f = look_aqua(a, b)
                an += n; af += f
        X["aqua_nb_n"], X["aqua_nb_frp"] = an, af

    # --- place / season
    lat, lon = common.cell_center(X.ci, X.cj)
    X["lat"], X["lon"] = lat, lon
    X["fire_date"] = day0 + pd.to_timedelta(X["day"], unit="D")
    doy = X["fire_date"].dt.dayofyear.to_numpy()
    X["doy_sin"], X["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    X["month"] = X["fire_date"].dt.month.astype(np.int8)
    X["is_night"] = X["p"].astype(np.int8)
    X["modis_empty"] = (X["modis_n"] == 0).astype(np.int8)

    # --- optional group 5+7: fire climatology and recent burning (Terra, all years)
    if "climatology" in groups:
        clim, monthly, first_ym = load_climatology()
        mon0 = X["fire_date"].dt.month.to_numpy(np.int64) - 1
        ym = X["fire_date"].dt.year.to_numpy(np.int64) * 12 + mon0
        ck = X["ci"].to_numpy(np.int64) * NJ + X["cj"].to_numpy(np.int64)
        X["clim_terra_n"] = _series_lookup(clim, ck * 12 + mon0)
        burned = np.zeros(len(X))
        for k in range(3, 13):
            t = ym - k
            actual = _series_lookup(monthly, ck * 100_000 + t)
            typical = _series_lookup(clim, ck * 12 + t % 12)          # before Terra data: use the climatology
            burned += np.where(t >= first_ym, actual, typical)
        X["burned_3_12m_n"] = burned

    # --- optional group "weather" (V9): NASA POWER daily weather at the nearest POWER grid point
    if "weather" in groups:
        import weather
        weather.add_features(X)

    X.attrs["day0"] = day0
    return X


def feature_names(use_aqua: bool = True, groups: set[str] | None = None) -> list[str]:
    groups = wanted_groups() if groups is None else groups
    sensors = ["terra", "aqua"] if use_aqua else ["terra"]
    own = [f"{s}_{c}" for s in sensors for c in STAT_COLS]
    if "overpass" in groups:
        own += [f"{s}_lst_mean" for s in sensors]
    ctx = ["modis_n", "modis_frp", "nb_n", "nb_frp", "nb_active_cells", "own_prev_n", "own_next_n",
           "nb3_prev_n", "nb3_next_n", "own_otherpass_n", "own_otherpass_frp", "own_7d_n", "modis_empty"]
    if "fire_history" in groups:
        ctx += FIRE_HISTORY_COLS
    if "aqua_growth" in groups and use_aqua:
        ctx += AQUA_GROWTH_COLS
    if "climatology" in groups:
        ctx += CLIMATOLOGY_COLS
    place = ["lat", "lon", "doy_sin", "doy_cos", "is_night"]
    if "weather" in groups:
        place += WEATHER_COLS
    return own + ctx + place


def viirs_targets(viirs: pd.DataFrame, day0: pd.Timestamp) -> pd.DataFrame:
    """Per-key VIIRS targets: pixel count, FRP, brightness, confidence class counts."""
    v = viirs.copy()
    v["day"] = _day_index(v["fire_date"], day0)
    v["key"] = encode(v["ci"], v["cj"], v["day"], (v["pass"] == "N").astype(np.int64))
    g = v.groupby("key")
    t = pd.DataFrame({
        "v_n": g.size(), "v_frp_sum": g["frp"].sum(), "v_frp_mean": g["frp"].mean(),
        "v_ti4": g["bright_ti4"].mean(), "v_ti5": g["bright_ti5"].mean(),
    })
    conf = pd.crosstab(v["key"], v["confidence"]).reindex(columns=["l", "n", "h"], fill_value=0)
    conf.columns = ["v_l", "v_nom", "v_h"]
    return t.join(conf), v
