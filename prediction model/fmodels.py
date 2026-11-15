"""XGBoost fitting with a fixed validation set, the saved model class, and bundle prediction.

A bundle (models/lead_<L>.joblib) holds the stage models of one lead:
  occ   stage 1  P(any real VIIRS row in the cell/pass on the target day)
  cnt   stage 2  rows if there is fire (zero-truncated mean, >= 1)
  frp / ti4 / ti5 / conf   stage 3  row attributes (log FRP mean, TI4 mean, TI5 mean, l/n/h shares)
  calib  month x pass factors that make expected rows match the validation totals
  iso    (v5+) monotone probability correction from out-of-fold predictions
An ensemble bundle ({"members": [bundle, ...]}, see load_bundles) averages its members' outputs.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import xgboost as xgb

import fcst_config as fc

log = logging.getLogger("fmodels")
_device = fc.XGB_DEVICE
CONF = np.array(["l", "n", "h"])


class FModel:
    def __init__(self, booster: xgb.Booster, n_iter: int):
        booster.set_param({"device": "cpu"})
        self.booster, self.n_iter = booster, int(n_iter)

    def predict(self, X) -> np.ndarray:
        X = np.ascontiguousarray(np.asarray(X, dtype=np.float32))
        return self.booster.inplace_predict(X, iteration_range=(0, self.n_iter))

    def importance(self, names: list[str], top: int = 15) -> list[list]:
        g = self.booster.get_score(importance_type="total_gain")
        tot = sum(g.values()) or 1.0
        items = sorted(((names[int(k[1:])] if k.startswith("f") and k[1:].isdigit() else k, v / tot)
                        for k, v in g.items()), key=lambda kv: -kv[1])
        return [[k, round(v, 4)] for k, v in items[:top]]


def fit(stage: str, Xtr, ytr, wtr=None, Xva=None, yva=None, wva=None, rounds: int | None = None,
        feature_names: list[str] | None = None) -> FModel:
    """Train one stage. With a validation set: early stopping on it. With `rounds`: fixed rounds, no validation."""
    global _device
    p = {**fc.XGB_COMMON, **fc.STAGES[stage]}
    max_rounds, stop = p.pop("rounds"), p.pop("stop")
    dtr = xgb.QuantileDMatrix(np.asarray(Xtr, np.float32), np.asarray(ytr, np.float32),
                              weight=None if wtr is None else np.asarray(wtr, np.float32), max_bin=p["max_bin"])
    evals, kw = [], {}
    if rounds is None:
        dva = xgb.QuantileDMatrix(np.asarray(Xva, np.float32), np.asarray(yva, np.float32),
                                  weight=None if wva is None else np.asarray(wva, np.float32), ref=dtr)
        evals, kw = [(dva, "val")], {"early_stopping_rounds": stop}

    def go(dev):
        return xgb.train({**p, "device": dev}, dtr, num_boost_round=rounds or max_rounds, evals=evals,
                         verbose_eval=False, **kw)
    try:
        b = go(_device)
    except xgb.core.XGBoostError as e:
        if _device == "cpu":
            raise
        log.warning("XGBoost on %s failed (%s) -> CPU", _device, str(e).splitlines()[0])
        _device = "cpu"
        b = go("cpu")
    n = rounds or (int(b.best_iteration) + 1 if getattr(b, "best_iteration", None) is not None
                   else b.num_boosted_rounds())
    log.info("  %s: %d rounds%s", stage, n, "" if rounds else f" (val {fc.STAGES[stage]['eval_metric']} "
             f"{b.best_score:.5f})")
    return FModel(b, n)


def device() -> str:
    return f"XGBoost {xgb.__version__} ({'GPU, CUDA' if _device == 'cuda' else 'CPU'})"


def load_bundles(model_dirs) -> dict:
    """{lead: bundle} from one model folder, or {lead: ensemble bundle} from several (leads they share)."""
    import joblib
    from pathlib import Path
    dirs = [Path(d) for d in (model_dirs if isinstance(model_dirs, (list, tuple)) else [model_dirs])]
    per = [{int(q.stem.split("_")[1]): joblib.load(q) for q in sorted(d.glob("lead_*.joblib"))} for d in dirs]
    if not per or not per[0]:
        raise FileNotFoundError(f"no lead_*.joblib in {dirs[0]}; run train_forecast.py first")
    if len(per) == 1:
        return per[0]
    out = {}
    for L in sorted(set.intersection(*(set(x) for x in per))):
        ms = [x[L] for x in per]
        days = {m.get("weather_day") for m in ms}
        if len(days) > 1:
            raise ValueError(f"ensemble members use different weather days: {days}")
        feats = list(dict.fromkeys(f for m in ms for f in m["features"]))
        out[L] = {**{k: ms[0][k] for k in ("clim_years", "weather_day", "baselines", "version", "history_start")
                     if k in ms[0]}, "lead": L, "features": feats, "members": ms,
                  "member_dirs": [str(d) for d in dirs]}
    return out


def predict(b: dict, X: pd.DataFrame, month, attrs: bool = True) -> pd.DataFrame:
    """Forecast columns for feature table X. `month` = target month (1-12), scalar or array."""
    if "members" in b:                       # ensemble: average of the members (equal weights)
        Ps = [predict(m, X, month, attrs) for m in b["members"]]
        out = sum(Ps) / len(Ps)
        return out
    F = X[b["features"]].to_numpy(np.float32)
    out = pd.DataFrame(index=X.index)
    p = np.clip(b["occ"].predict(F), 0, 1)
    if "iso" in b:                           # probability correction (tiny slope keeps the ranking)
        p = np.clip(np.interp(p, b["iso"][0], b["iso"][1]) + 1e-6 * p, 0, 1)
    mu = np.maximum(b["cnt"].predict(F), 1.0)
    month = np.broadcast_to(np.asarray(month), len(X))
    key = pd.Series(month.astype(int)).astype(str).to_numpy() + "_" + np.where(X["is_night"].to_numpy() == 1, "N", "D")
    f = pd.Series(key).map(b.get("calib", {})).fillna(1.0).to_numpy(np.float32)
    out["p_fire"] = p
    out["rows_if_fire"] = mu
    out["expected_rows"] = p * mu * f
    if attrs:
        out["frp_mean"] = np.exp(b["frp"].predict(F)) * b.get("frp_smear", 1.0)
        out["ti4"] = b["ti4"].predict(F)
        out["ti5"] = b["ti5"].predict(F)
        out[["p_conf_l", "p_conf_n", "p_conf_h"]] = b["conf"].predict(F).reshape(len(X), 3)
    return out


def baseline(b: dict, X: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Persistence or climatology forecast in the same columns (p_fire, expected_rows)."""
    if kind == "persistence":
        s = b["baselines"]["persistence"]
        own = X["own_same_t"].to_numpy()
        p = np.where(own > 0, s["p1"], s["p0"])
        e = np.where(own > 0, own * s["c1"], s["m0"])
        # tie-break the ranking with yesterday's 3x3 so top-k is not decided by chance
        score = own + 0.01 * X["nb3_t"].to_numpy()
    elif kind == "climatology":
        p = X["clim_p"].to_numpy()
        e = X["clim_mean"].to_numpy()
        score = e
    else:
        raise ValueError(kind)
    return pd.DataFrame({"p_fire": p.astype(np.float32), "expected_rows": e.astype(np.float32),
                         "score": score.astype(np.float32)}, index=X.index)
