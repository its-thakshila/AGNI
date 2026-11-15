"""Forecast fire rows in the VIIRS CSV format for the days after an issue day.

For issue day t (default: the last fire-day in the history, 2025-12-31) and every lead in models/:
  forecast_cells_<t>_lead<L>.csv   the forecast itself: per 0.1 deg cell x pass, P(fire), rows if fire,
                                   expected rows, FRP / TI4 / TI5 means, confidence shares
  forecast_rows_<t>_lead<L>.csv    one plausible set of VIIRS pixel rows (same 15 columns as the FIRMS
                                   VIIRS CSV, version = FCST-XGB-1.0) synthesized from that grid
  forecast_summary_<t>.json        totals per lead, a simple spread (P5-P95), and, when the target day
                                   is in the record, the real rows for comparison (back-test)

Rows: expected rows are rounded stochastically (totals kept); each row is placed on a recent VIIRS pixel
of the same cell (fire-days t-6..t) with 375 m jitter, else anywhere in the cell; overpass time from the
S-NPP local-time distribution of its pass; FRP / TI4 / TI5 / confidence from the stage-3 means plus the
within-cell spread in ml_pipeline synthesis_stats. Static sources (type 2/3: gas flares, industry) are
not forecast: their mean daily rate over the last 365 days is added as a fixed layer, rows resampled
from their past pixels.

    python forecast.py                       # issue day = last day of the history
    python forecast.py --issue 2024-10-15    # back-test: also compares with the real rows
    python forecast.py --leads 1 --no-rows
    python forecast.py --issue 2025-12-31 --weather-forecast wx_forecast.csv            # weather-forecast input
    python forecast.py --issue 2025-12-31 --target 2026-01-07 --weather-forecast wx.csv  # one target date
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import fcst_config as fc
import fmodels
from history import History

log = logging.getLogger("forecast")
CONF = np.array(["l", "n", "h"])


# ------------------------------------------------------------ recent rows (positions, static layer)
def recent_rows(issue: pd.Timestamp, days_fire: int = 7, days_static: int = fc.STATIC_DAYS) -> pd.DataFrame:
    """VIIRS-format rows around the issue day: type 0 for fire-days t-6..t, type 2/3 for the last year."""
    import duckdb
    import common
    lo_s = (issue - pd.Timedelta(days=days_static + 1)).date()
    lo_f = (issue - pd.Timedelta(days=days_fire)).date()
    hi = (issue + pd.Timedelta(days=1)).date()
    years = range(lo_s.year, hi.year + 1)
    files = [fc.mlc.viirs_path(y) for y in years]
    if all(files):
        src = "[" + ", ".join(f"'{f.as_posix()}'" for f in files) + "]"
    elif fc.UNIFIED_ROWS_CSV.exists():
        src = f"'{fc.UNIFIED_ROWS_CSV.as_posix()}'"
    else:
        log.warning("no VIIRS rows for %s .. %s: rows are placed uniformly in their cells", lo_s, hi)
        return pd.DataFrame(columns=fc.VIIRS_COLUMNS)
    q = f"""SELECT * FROM read_csv({src}, header = true, union_by_name = true,
                 types = {{'acq_time': 'VARCHAR', 'confidence': 'VARCHAR', 'version': 'VARCHAR',
                          'satellite': 'VARCHAR', 'daynight': 'VARCHAR', 'acq_date': 'VARCHAR'}})
            WHERE acq_date <= '{hi}' AND ((type IN (2, 3) AND acq_date >= '{lo_s}')
                                          OR (type = 0 AND acq_date >= '{lo_f}'))"""
    df = duckdb.connect().execute(q).df()
    if df.empty:
        return df
    df["acq_time"] = df["acq_time"].astype(str).str.zfill(4)
    df = common.add_cell_keys(common.add_time_keys(df))
    df = df[df.fire_date <= issue]
    log.info("recent rows: %d type-0 (t-6..t), %d static (type 2/3, last %d days)",
             int((df.type == 0).sum()), int((df.type != 0).sum()), days_static)
    return df


def _rank_align(group, driver, values):
    out = np.empty_like(values)
    out[np.lexsort((driver, group))] = values[np.lexsort((values, group))]
    return out


def _utc(fire_date: np.ndarray, lst: np.ndarray, lon: np.ndarray):
    hours_after = fc.mlc.DAY_START_HOUR + np.mod(lst - fc.mlc.DAY_START_HOUR, 24)
    return (pd.to_datetime(fire_date) + pd.to_timedelta(hours_after - lon / 15.0, unit="h")).floor("min")


def synthesize(C: pd.DataFrame, target: pd.Timestamp, recent: pd.DataFrame, stats: dict, rng) -> pd.DataFrame:
    """C: forecast cells (ci, cj, night, expected_rows, frp_mean, ti4, ti5, p_conf_*) -> VIIRS rows."""
    e = C["expected_rows"].to_numpy()
    n = np.floor(e).astype(int)
    n += rng.random(len(C)) < (e - n)
    C = C[n > 0].reset_index(drop=True); n = n[n > 0]
    cell = np.repeat(np.arange(len(C)), n)
    N = len(cell)
    if N == 0:
        return pd.DataFrame(columns=fc.VIIRS_COLUMNS)
    g = fc.mlc.GRID_DEG
    ci, cj = C.ci.to_numpy()[cell], C.cj.to_numpy()[cell]
    # ---- position: a recent pixel of the same cell (fires move slowly), else uniform in the cell
    r0 = recent[recent.type == 0] if len(recent) else recent
    has = np.zeros(N, bool); lat = np.empty(N); lon = np.empty(N)
    if len(r0):
        key_r = r0.ci.to_numpy().astype(np.int64) * 10000 + r0.cj.to_numpy()
        o = np.argsort(key_r, kind="stable"); key_r = key_r[o]
        rl, rn = r0.latitude.to_numpy()[o], r0.longitude.to_numpy()[o]
        rs, rt = r0.scan.to_numpy()[o], r0.track.to_numpy()[o]
        uk, st, ct = np.unique(key_r, return_index=True, return_counts=True)
        kc = ci.astype(np.int64) * 10000 + cj
        pos = np.clip(np.searchsorted(uk, kc), 0, len(uk) - 1)
        has = uk[pos] == kc
        pick = st[pos] + np.floor(rng.random(N) * ct[pos]).astype(int)
        lat = rl[pick] + (rng.random(N) - .5) * rt[pick] / 111.32
        lon = rn[pick] + (rng.random(N) - .5) * rs[pick] / (111.32 * np.cos(np.radians(rl[pick])))
    clat = fc.mlc.GRID_LAT0 + (ci + rng.random(N)) * g
    clon = fc.mlc.GRID_LON0 + (cj + rng.random(N)) * g
    lat = np.where(has, lat, clat); lon = np.where(has, lon, clon)
    # ---- overpass time
    is_n = C.night.to_numpy()[cell].astype(bool)
    qs = np.linspace(0, 1, 201); u = rng.random(N)
    # the stored quantiles are raw local hours (night: 0-2 h plus a few at 23.7 h); interpolating those
    # across midnight invents daytime "night" overpasses -> unwrap to hours after the fire-day start first
    qd, qn = (np.sort(fc.mlc.DAY_START_HOUR + np.mod(np.asarray(stats["lst_quantiles"][k]) - fc.mlc.DAY_START_HOUR, 24))
              for k in ("D", "N"))
    lst = np.where(is_n, np.interp(u, qs, qn), np.interp(u, qs, qd))
    utc = _utc(np.full(N, np.datetime64(target.date())), lst, lon)
    # ---- radiometry and confidence
    w = np.exp(rng.normal(0, stats["sd_logfrp"], N))
    w = w / pd.Series(w).groupby(cell).transform("mean").to_numpy()
    frp = np.clip(C.frp_mean.to_numpy()[cell] * w, 0.1, None)
    lo4, hi4 = stats["ti4_range"]; lo5, hi5 = stats["ti5_range"]
    ti4 = np.clip(C.ti4.to_numpy()[cell] + _rank_align(cell, frp, rng.normal(0, stats["sd_ti4"], N)), lo4, hi4)
    ti5 = np.clip(C.ti5.to_numpy()[cell] + _rank_align(cell, frp, rng.normal(0, stats["sd_ti5"], N)), lo5, hi5)
    P = C[["p_conf_l", "p_conf_n", "p_conf_h"]].to_numpy()[cell]
    code = (rng.random(N)[:, None] > np.cumsum(P, axis=1)[:, :2]).sum(1)
    code = _rank_align(cell, frp, code)
    stt = np.asarray(stats["scan_track"])[rng.integers(0, len(stats["scan_track"]), N)]
    return pd.DataFrame({
        "latitude": lat.round(5), "longitude": lon.round(5), "bright_ti4": ti4.round(2),
        "scan": stt[:, 0], "track": stt[:, 1], "acq_date": utc.strftime("%Y-%m-%d"), "acq_time": utc.strftime("%H%M"),
        "satellite": fc.mlc.OUT_SATELLITE, "instrument": "VIIRS", "confidence": CONF[code],
        "version": fc.OUT_VERSION, "bright_ti5": ti5.round(2), "frp": frp.round(2),
        "daynight": np.where(is_n, "N", "D"), "type": 0})[fc.VIIRS_COLUMNS]


def static_layer(recent: pd.DataFrame, issue: pd.Timestamp, target: pd.Timestamp, rng) -> pd.DataFrame:
    """Type 2/3 sources: stochastic rounding of the mean daily rate per cell x pass,
    rows resampled from that cell/pass's past pixels, moved to the target day (same local time)."""
    s = recent[(recent.type != 0) & (recent.fire_date > issue - pd.Timedelta(days=fc.STATIC_DAYS))] \
        if len(recent) else recent
    if not len(s):
        return pd.DataFrame(columns=fc.VIIRS_COLUMNS), 0.0
    g = s.groupby(["ci", "cj", "pass"]).indices
    out = []
    expected = 0.0
    for (ci, cj, ps), idx in g.items():
        rate = len(idx) / fc.STATIC_DAYS
        expected += rate
        k = int(np.floor(rate) + (rng.random() < rate - np.floor(rate)))
        if k:
            out.append(rng.choice(idx, k, replace=True))
    if not out:
        return pd.DataFrame(columns=fc.VIIRS_COLUMNS), expected
    R = s.iloc[np.concatenate(out)].copy()
    utc = _utc(np.full(len(R), np.datetime64(target.date())), R.lst_hour.to_numpy(), R.longitude.to_numpy())
    R["acq_date"], R["acq_time"] = utc.strftime("%Y-%m-%d"), utc.strftime("%H%M")
    R["version"] = fc.OUT_VERSION
    R["satellite"] = fc.mlc.OUT_SATELLITE
    return R[fc.VIIRS_COLUMNS], expected


def spread(p: np.ndarray, mu: np.ndarray, f: np.ndarray, rng, sims: int = 200) -> list[float]:
    """P5 / P50 / P95 of the national row total, cells independent (understates the real spread)."""
    k = p > 1e-4
    p, lam = p[k], np.maximum(mu[k] * f[k] - 1, 0)
    tot = np.empty(sims)
    for s in range(sims):
        fire = rng.random(len(p)) < p
        tot[s] = (fire * (1 + rng.poisson(lam))).sum()
    return [float(x) for x in np.percentile(tot, [5, 50, 95])]


def run(issue=None, leads=None, model_dir: Path = fc.MODEL_DIR, rows: bool = True, out_dir: Path = fc.FORECAST_DIR,
        weather_forecast=None, target_date=None):
    """weather_forecast: weather-forecast file for the days after the issue day (forecast_weather.py); needed
    by target-day weather models when the target day is past the observed weather. target_date: a target
    date instead of leads (target_date - issue must be one of the trained leads)."""
    t0 = time.perf_counter()
    bundles = fmodels.load_bundles(model_dir)              # one folder, or a list = ensemble
    model_dir = " + ".join(map(str, model_dir)) if isinstance(model_dir, (list, tuple)) else model_dir
    leads = sorted(leads or bundles)
    b0 = bundles[leads[0]]
    use_weather = b0.get("weather_day", "target" if any(c.startswith("w_") for c in b0["features"]) else False)
    H = History(clim_years=range(b0["clim_years"][0], b0["clim_years"][1] + 1))
    issue = pd.Timestamp(issue) if issue else H.end
    if issue > H.end or issue < H.start + pd.Timedelta(days=400):
        raise ValueError(f"issue day must be within {H.start.date() + pd.Timedelta(days=400)} .. {H.end.date()} "
                         "(the fire history in daily_cells.parquet)")
    if target_date is not None:
        L = (pd.Timestamp(target_date) - issue).days
        if L not in bundles:
            raise ValueError(f"target {pd.Timestamp(target_date).date()} is {L} days after the issue day; "
                             f"trained leads are {sorted(bundles)}")
        leads = [L]
    FW = None
    if weather_forecast:
        if use_weather != "target":
            raise ValueError(f"these models use weather = {use_weather!r}; a weather forecast is only used by "
                             "models trained with --weather target")
        from forecast_weather import ForecastWeather
        FW = ForecastWeather(weather_forecast, issue)
        log.info("weather forecast %s: %s .. %s, covers %.0f%% of the POWER grid", Path(weather_forecast).name,
                 FW.days[0].date(), FW.days[-1].date(), FW.coverage * 100)
    d = H.day_index(issue)
    day = next(H.iterate(d, d))
    stats = joblib.load(fc.SYNTH_STATS_FILE)
    recent = recent_rows(issue) if rows else pd.DataFrame()
    rng = np.random.default_rng(fc.SEED)
    out_dir.mkdir(parents=True, exist_ok=True)
    li, lj = H.land_i, H.land_j
    nl = len(li)
    night = np.repeat(np.array([0, 1], np.int8), nl)
    ii, jj = np.tile(li, 2), np.tile(lj, 2)
    tag = issue.strftime("%Y%m%d")
    summary = {"issue_date": str(issue.date()), "models": str(model_dir), "version": fc.OUT_VERSION,
               "weather": use_weather, "weather_source": (str(weather_forecast) if FW else
                                                          ("observed POWER" if use_weather else None)), "leads": {}}
    for L in leads:
        b = bundles[L]
        target = issue + pd.Timedelta(days=L)
        if FW is not None:
            X = day.features(ii, jj, night, L, weather=False)
            W = FW.features(target, X["lat"].to_numpy(), X["lon"].to_numpy())
            for c in W.columns:
                X[c] = W[c].to_numpy()
        else:
            X = day.features(ii, jj, night, L, weather=use_weather)
        P = fmodels.predict(b, X, target.month, attrs=True)
        lat, lon = H.cell_latlon(ii, jj)
        C = pd.DataFrame({"target_date": str(target.date()), "pass": np.where(night == 1, "N", "D"),
                          "cell_lat": lat.round(3), "cell_lon": lon.round(3),
                          "ci": ii + H.i0, "cj": jj + H.j0, "night": night}).join(P)
        keep = (C.p_fire >= 0.005) | (C.expected_rows >= 0.01)
        cols = ["target_date", "pass", "cell_lat", "cell_lon", "p_fire", "rows_if_fire", "expected_rows",
                "frp_mean", "ti4", "ti5", "p_conf_l", "p_conf_n", "p_conf_h"]
        Cout = C.loc[keep, cols].round({"p_fire": 4, "rows_if_fire": 2, "expected_rows": 3, "frp_mean": 2,
                                        "ti4": 2, "ti5": 2, "p_conf_l": 3, "p_conf_n": 3, "p_conf_h": 3})
        Cout.sort_values("expected_rows", ascending=False).to_csv(
            out_dir / f"forecast_cells_{tag}_lead{L}.csv", index=False)
        f = (C.expected_rows / np.maximum(C.p_fire * C.rows_if_fire, 1e-12)).to_numpy()
        s = {"target_date": str(target.date()), "expected_rows_fire": round(float(C.expected_rows.sum()), 1),
             "expected_rows_fire_P5_P50_P95": [round(x) for x in spread(C.p_fire.to_numpy(), C.rows_if_fire.to_numpy(), f, rng)],
             "cells_p_over_0.5": int((C.p_fire > 0.5).sum()), "cells_p_over_0.1": int((C.p_fire > 0.1).sum()),
             "cells_written": int(keep.sum())}
        if rows:
            R = synthesize(C[C.expected_rows > 0], target, recent, stats, rng)
            S, s_exp = static_layer(recent, issue, target, rng)
            R = pd.concat([R, S], ignore_index=True).sort_values(["acq_date", "acq_time"], kind="mergesort")
            R.to_csv(out_dir / f"forecast_rows_{tag}_lead{L}.csv", index=False)
            s.update({"rows_written": int(len(R)), "rows_fire": int((R.type == 0).sum()),
                      "rows_static": int((R.type != 0).sum()), "expected_rows_static": round(s_exp, 1)})
        td = H.day_index(target)
        if td < H.n_days:                                    # back-test: the real answer is known
            T = H.target(td)
            y = T[night, ii, jj]
            top = np.argpartition(-C.expected_rows.to_numpy(), fc.TOPK)[:fc.TOPK]
            s["actual"] = {"real_rows_fire": float(y.sum()), "complete_real_data": bool(H.target_ok[td]),
                           "active_cell_passes": int((y > 0).sum()),
                           f"share_of_real_rows_in_top{fc.TOPK}": round(float(y[top].sum() / max(y.sum(), 1)), 3),
                           "mean_p_where_fire": round(float(C.p_fire.to_numpy()[y > 0].mean()), 3) if (y > 0).any() else None}
        summary["leads"][str(L)] = s
        log.info("lead %d -> %s: %.0f expected fire rows%s", L, target.date(), s["expected_rows_fire"],
                 f", {s['actual']['real_rows_fire']:.0f} real" if "actual" in s else "")
    (out_dir / f"forecast_summary_{tag}.json").write_text(json.dumps(summary, indent=1))
    log.info("forecast written to %s (%.0f s)", out_dir, time.perf_counter() - t0)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--issue", help="issue fire-day YYYY-MM-DD (default: last day of the history)")
    ap.add_argument("--leads", type=int, nargs="+")
    ap.add_argument("--models", type=Path, nargs="+", default=[fc.MODEL_DIR],
                    help="model folder(s); several = equal-weight ensemble")
    ap.add_argument("--out", type=Path, default=fc.FORECAST_DIR)
    ap.add_argument("--no-rows", action="store_true", help="write only the forecast grid, no VIIRS-format rows")
    ap.add_argument("--weather-forecast", type=Path,
                    help="weather-forecast CSV/Parquet for the days after the issue day (forecast_weather.py)")
    ap.add_argument("--target", help="target date YYYY-MM-DD instead of --leads (must be issue + a trained lead)")
    a = ap.parse_args()
    m = [x.resolve() for x in a.models]
    run(a.issue, a.leads, m[0] if len(m) == 1 else m, not a.no_rows, a.out, a.weather_forecast, a.target)
