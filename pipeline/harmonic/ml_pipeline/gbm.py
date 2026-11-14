"""Gradient-boosted tree models on XGBoost, trained on the GPU (CUDA) when available.

`fit(kind, X, y, w)` trains one model with the settings in config.XGB_PARAMS[kind] and returns a
`Model` with the scikit-learn style calls the rest of the pipeline uses:
`.predict(X)`, `.predict_proba(X)`, `.classes_`, `.n_iter_`.

Early stopping holds out config.VAL_FRACTION of the training rows (random, seeded), like
scikit-learn's HistGradientBoosting `validation_fraction` did.
Trained models predict on the CPU, so a saved model also works on a machine without a GPU.
"""
from __future__ import annotations

import logging

import numpy as np
import xgboost as xgb

import config

log = logging.getLogger("gbm")
_device = config.XGB_DEVICE          # switched to "cpu" for the session if CUDA fails once


class Model:
    def __init__(self, booster: xgb.Booster, classes: np.ndarray | None = None):
        booster.set_param({"device": "cpu"})                      # predict on CPU, no device warnings
        self.booster = booster
        self.classes_ = classes
        best = getattr(booster, "best_iteration", None)
        self.n_iter_ = int(best) + 1 if best is not None else booster.num_boosted_rounds()

    def _raw(self, X) -> np.ndarray:
        X = np.ascontiguousarray(np.asarray(X, dtype=np.float32))
        return self.booster.inplace_predict(X, iteration_range=(0, self.n_iter_))

    def predict(self, X) -> np.ndarray:
        p = self._raw(X)
        return p if self.classes_ is None else self.classes_[p.argmax(1)]

    def predict_proba(self, X) -> np.ndarray:
        """Class probabilities, columns in the order of `classes_`."""
        return self._raw(X)


class SplitModel:
    """Separate day-pass and night-pass count models (config.COUNT_SPLIT_DAYNIGHT).
    Routes each row by its `is_night` feature; same .predict / .n_iter_ calls as Model."""

    def __init__(self, day: Model, night: Model, night_col: int):
        self.day, self.night, self.night_col = day, night, night_col
        self.classes_ = None
        self.n_iter_ = max(day.n_iter_, night.n_iter_)

    def predict(self, X) -> np.ndarray:
        X = np.asarray(X, dtype=np.float32)
        nt = X[:, self.night_col] == 1
        out = np.empty(len(X), dtype=np.float32)
        if (~nt).any():
            out[~nt] = self.day.predict(X[~nt])
        if nt.any():
            out[nt] = self.night.predict(X[nt])
        return out


def fit_count(X, y, w, feats: list[str], split: bool | None = None):
    """The VIIRS-count model: one model, or a day/night pair when config.COUNT_SPLIT_DAYNIGHT."""
    split = config.COUNT_SPLIT_DAYNIGHT if split is None else split
    if not split:
        return fit("count", X, y, w)
    X = np.asarray(X, dtype=np.float32)
    y, w = np.asarray(y), np.asarray(w)
    col = list(feats).index("is_night")
    nt = X[:, col] == 1
    return SplitModel(fit("count", X[~nt], y[~nt], w[~nt]), fit("count", X[nt], y[nt], w[nt]), col)


def describe() -> str:
    """Library + device actually used in this session (for the training report)."""
    return f"XGBoost {xgb.__version__} ({'GPU, CUDA' if _device == 'cuda' else 'CPU'})"


def _train(params, rounds, stop, dtr, dva):
    return xgb.train(params, dtr, num_boost_round=rounds, evals=[(dva, "val")],
                     early_stopping_rounds=stop, verbose_eval=False)


def fit(kind: str, X, y, w=None, classes=None, seed: int = config.RANDOM_SEED) -> Model:
    """kind = "count" | "reg" | "conf" (see config.XGB_PARAMS). For "conf", y holds class labels
    and `classes` their order; labels are encoded as 0..k-1 in that order."""
    global _device
    p = {**config.XGB_COMMON, **config.XGB_PARAMS[kind]}
    rounds, stop = p.pop("num_boost_round"), p.pop("early_stopping_rounds")
    X = np.asarray(X, dtype=np.float32)
    y = np.asarray(y)
    if classes is not None:
        classes = np.asarray(classes)
        order = np.argsort(classes)                       # label -> its index in `classes`
        pos = np.searchsorted(classes, y, sorter=order)
        if not np.array_equal(classes[order[np.clip(pos, 0, len(classes) - 1)]], y):
            raise ValueError(f"labels outside {classes.tolist()}")
        y = order[pos]
        p["num_class"] = len(classes)
    y = y.astype(np.float32)
    w = None if w is None else np.asarray(w, dtype=np.float32)

    perm = np.random.default_rng(seed).permutation(len(y))
    nv = max(1, int(round(config.VAL_FRACTION * len(y))))
    va, tr = np.sort(perm[:nv]), np.sort(perm[nv:])
    dtr = xgb.QuantileDMatrix(X[tr], y[tr], weight=None if w is None else w[tr], max_bin=p["max_bin"])
    dva = xgb.QuantileDMatrix(X[va], y[va], weight=None if w is None else w[va], ref=dtr)
    try:
        booster = _train({**p, "device": _device}, rounds, stop, dtr, dva)
    except xgb.core.XGBoostError as e:
        if _device == "cpu":
            raise
        log.warning("XGBoost on %s failed (%s) -> using CPU for this run", _device, str(e).splitlines()[0])
        _device = "cpu"
        booster = _train({**p, "device": "cpu"}, rounds, stop, dtr, dva)
    return Model(booster, classes)
