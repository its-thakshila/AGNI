"""Score the forecast models on the test years (never used in training) against two baselines.

Every fcst_config.EVAL_EVERY-th issue day whose target day is in TEST_YEARS (complete real data only)
is predicted on the FULL land grid (every 0.1 deg land cell x pass), for each lead, with:
  model         the hurdle XGBoost bundle (models/lead_<L>.joblib)
  persistence   fire on the issue day in the same cell/pass -> calibrated rate (from the training rows)
  climatology   mean rows / share of fire days for the cell x calendar month x pass (CLIM_YEARS)

Metrics (section 7 of the design PDF):
  PR-AUC, Brier score, reliability      will there be fire?
  Poisson deviance, daily total error   how many rows?
  Fractions Skill Score 0.1/0.5/1.1 deg is the map right (near misses count)?
  top-k capture                         share of real rows inside the k highest-scored cell-passes
  log-FRP error                         row attributes on cells that had fire

Outputs in output/evaluation/: evaluation_report.json, evaluation_summary.md, daily_totals.csv,
daily_totals.png.

    python evaluate.py
    python evaluate.py --every 1 --models models_final      # every test day, other model folder
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

import attributes
import fcst_config as fc
import fmodels
from history import History, _box

log = logging.getLogger("evaluate")
METHODS = ("model", "persistence", "climatology")
EDGES = np.concatenate([[0.0], np.logspace(-7, 0, 3001)])
FSS_N = (1, 5, 11)                     # window in cells: 0.1, 0.5, 1.1 deg
REL_EDGES = np.array([0, 1e-4, 1e-3, 3e-3, 0.01, 0.03, 0.1, 0.2, 0.4, 0.7, 1.0001])


class Acc:
    def __init__(self):
        self.pos = np.zeros(len(EDGES)); self.neg = np.zeros(len(EDGES))
        self.brier = 0.0; self.dev = 0.0; self.n = 0
        self.fss_num = np.zeros(len(FSS_N)); self.fss_den = np.zeros(len(FSS_N))
        self.top_rows = 0.0; self.top_hits = 0; self.top_k = 0; self.rows = 0.0
        self.rel = np.zeros((len(REL_EDGES), 3))

    def add(self, r, p, e, y):
        """r = ranking score in [0, 1], p = probability, e = expected rows, y = real rows."""
        k = np.clip(np.searchsorted(EDGES, r, side="right") - 1, 0, len(EDGES) - 1)
        yb = y > 0
        self.pos += np.bincount(k[yb], minlength=len(EDGES))
        self.neg += np.bincount(k[~yb], minlength=len(EDGES))
        self.brier += float(((p - yb) ** 2).sum())
        mu = np.maximum(e, 1e-4)
        self.dev += float((2 * (np.where(y > 0, y * np.log(np.maximum(y, 1e-12) / mu), 0) - (y - mu))).sum())
        self.n += len(y)
        rk = np.clip(np.searchsorted(REL_EDGES, p, side="right") - 1, 0, len(REL_EDGES) - 1)
        np.add.at(self.rel, rk, np.c_[np.ones(len(p)), p, yb])

    def ap(self) -> float:
        tp = np.cumsum(self.pos[::-1]); fp = np.cumsum(self.neg[::-1])
        prec = tp / np.maximum(tp + fp, 1)
        rec = tp / max(tp[-1], 1)
        return float(np.sum(np.diff(np.concatenate([[0], rec])) * prec))


def fss_add(acc: Acc, H: History, F: np.ndarray, O: np.ndarray):
    """F, O: forecast probability / observed 0-1 maps (2, ni, nj); land cells only."""
    land = H.land.astype(np.float32)
    for k, n in enumerate(FSS_N):
        r = n // 2
        nl = np.maximum(_box(land, r), 1)
        for ps in range(2):
            ff = _box(F[ps] * land, r) / nl
            fo = _box(O[ps] * land, r) / nl
            m = H.land
            acc.fss_num[k] += float(((ff - fo)[m] ** 2).sum())
            acc.fss_den[k] += float((ff[m] ** 2).sum() + (fo[m] ** 2).sum())


def run(model_dir=fc.MODEL_DIR, every: int = fc.EVAL_EVERY, out_dir: Path | None = None):
    """model_dir: one model folder, or a list of folders = equal-weight ensemble. out_dir: default EVAL_DIR."""
    t0 = time.perf_counter()
    if out_dir is not None:
        fc.EVAL_DIR = Path(out_dir)
    bundles = fmodels.load_bundles(model_dir)
    model_dir = " + ".join(map(str, model_dir)) if isinstance(model_dir, (list, tuple)) else model_dir
    leads = sorted(bundles)
    use_weather = bundles[leads[0]].get("weather_day",
                                        "target" if any(c.startswith("w_") for c in bundles[leads[0]]["features"]) else False)
    H = History(clim_years=range(bundles[leads[0]]["clim_years"][0], bundles[leads[0]]["clim_years"][1] + 1))
    A = attributes.load()
    A["d"] = (A.fire_date - H.start).dt.days.astype(np.int32)
    A["night"] = (A["pass"] == "N").astype(np.int8)
    A = A.sort_values("d").reset_index(drop=True)
    Ab = np.searchsorted(A.d.to_numpy(), np.arange(H.n_days + 1))

    ty = list(fc.TEST_YEARS)
    first = H.day_index(f"{min(ty)}-01-01") - max(leads)
    last = min(H.day_index(f"{max(ty)}-12-31"), H.n_days - 1) - min(leads)
    li, lj = H.land_i, H.land_j
    nl = len(li)
    night = np.repeat(np.array([0, 1], np.int8), nl)
    ii, jj = np.tile(li, 2), np.tile(lj, 2)
    acc = {(L, m): Acc() for L in leads for m in METHODS}
    frp_err = {L: [] for L in leads}
    daily = []
    ndone = 0
    for day in H.iterate(first, last):
        if (day.d - first) % every:
            continue
        for L in leads:
            td = day.d + L
            if td >= H.n_days or not H.target_ok[td] or H.date(td).year not in ty:
                continue
            b = bundles[L]
            T = H.target(td)
            y = T[night, ii, jj]
            X = day.features(ii, jj, night, L, weather=use_weather)
            mo = H.date(td).month
            P = fmodels.predict(b, X, mo, attrs=False)
            row = {"target_date": H.date(td).date(), "lead": L, "actual_rows": float(y.sum())}
            for m in METHODS:
                if m == "model":
                    p, e = P.p_fire.to_numpy(), P.expected_rows.to_numpy()
                    r, score = p, e
                else:
                    B = fmodels.baseline(b, X, m)
                    p, e, score = B.p_fire.to_numpy(), B.expected_rows.to_numpy(), B.score.to_numpy()
                    r = score / (1 + score)
                a = acc[(L, m)]
                a.add(r, p, e, y)
                F = np.zeros((2,) + H.shape, np.float32); F[night, ii, jj] = p
                fss_add(a, H, F, (T > 0).astype(np.float32))
                top = np.argpartition(-score, fc.TOPK)[:fc.TOPK]
                a.top_rows += float(y[top].sum()); a.top_hits += int((y[top] > 0).sum())
                a.top_k += fc.TOPK; a.rows += float(y.sum())
                row[f"{m}_rows"] = float(e.sum())
            daily.append(row)
            # attributes on cells with real fire
            pos = np.flatnonzero(y > 0)
            if len(pos):
                Pa = fmodels.predict(b, X.iloc[pos], mo, attrs=True)
                s, e_ = Ab[td], Ab[td + 1]
                a_ = A.iloc[s:e_]
                key = pd.DataFrame({"ci": (ii[pos] + H.i0), "cj": (jj[pos] + H.j0), "night": night[pos],
                                    "pf": Pa.frp_mean.to_numpy(), "n": y[pos]})
                mrg = key.merge(a_[["ci", "cj", "night", "frp_mean"]], on=["ci", "cj", "night"], how="inner")
                mrg = mrg[mrg.frp_mean > 0]
                frp_err[L].append(np.c_[np.log(mrg.pf) - np.log(mrg.frp_mean), mrg.n])
        ndone += 1
        if ndone % 50 == 0:
            log.info("issue day %s: %d scored (%.0f s)", day.date.date(), ndone, time.perf_counter() - t0)

    D = pd.DataFrame(daily)
    fc.EVAL_DIR.mkdir(parents=True, exist_ok=True)
    D.to_csv(fc.EVAL_DIR / "daily_totals.csv", index=False)
    rep = {"evaluated": pd.Timestamp.now().isoformat(timespec="seconds"), "models": str(model_dir),
           "test_years": ty, "every_nth_issue_day": every, "topk": fc.TOPK, "fss_windows_deg":
           [round(n * fc.mlc.GRID_DEG, 2) for n in FSS_N], "leads": {}}
    for L in leads:
        dl = D[D.lead == L]
        out = {"days": int(len(dl)), "actual_rows": float(dl.actual_rows.sum())}
        for m in METHODS:
            a = acc[(L, m)]
            e = dl[f"{m}_rows"].to_numpy(); act = dl.actual_rows.to_numpy()
            out[m] = {
                "pr_auc": round(a.ap(), 4),
                "brier_x1e4": round(1e4 * a.brier / a.n, 4),
                "poisson_dev_per_cell": round(a.dev / a.n, 5),
                "fss": [round(1 - a.fss_num[k] / max(a.fss_den[k], 1e-12), 4) for k in range(len(FSS_N))],
                "topk_row_capture": round(a.top_rows / max(a.rows, 1), 4),
                "topk_hit_rate": round(a.top_hits / max(a.top_k, 1), 4),
                "daily_total_bias_pct": round(100 * (e.sum() - act.sum()) / max(act.sum(), 1), 2),
                "daily_total_mape_pct": round(100 * float(np.mean(np.abs(e - act) / np.maximum(act, 1))), 1),
                "daily_total_log_corr": round(float(np.corrcoef(np.log1p(e), np.log1p(act))[0, 1]), 4),
            }
            if m == "model":
                rel = a.rel[a.rel[:, 0] > 0]
                out[m]["reliability"] = [{"n": int(r[0]), "mean_p": round(r[1] / r[0], 5),
                                          "observed": round(r[2] / r[0], 5)} for r in rel]
                if frp_err[L]:
                    fe = np.concatenate(frp_err[L])
                    out[m]["log_frp_mae"] = round(float(np.average(np.abs(fe[:, 0]), weights=fe[:, 1])), 4)
                    out[m]["log_frp_bias"] = round(float(np.average(fe[:, 0], weights=fe[:, 1])), 4)
        rep["leads"][str(L)] = out
    rep["seconds"] = round(time.perf_counter() - t0)
    (fc.EVAL_DIR / "evaluation_report.json").write_text(json.dumps(rep, indent=1))
    _summary_md(rep)
    _plot(D)
    log.info("evaluation done in %.0f s -> %s", rep["seconds"], fc.EVAL_DIR)
    return rep


def _summary_md(rep):
    w = rep["fss_windows_deg"]
    lines = [f"# Forecast evaluation ({', '.join(map(str, rep['test_years']))})", "",
             f"Models: `{rep['models']}` · every {rep['every_nth_issue_day']} issue day(s) · full land grid · "
             f"evaluated {rep['evaluated']}", ""]
    hdr = ("| Lead | Method | PR-AUC | Brier ×1e4 | Poisson dev/cell | " + " | ".join(f"FSS {x}°" for x in w)
           + f" | Top-{rep['topk']} row capture | Daily total bias % | Daily total log corr |")
    lines += [hdr, "|" + "---|" * (9 + len(w) - 1)]
    for L, o in rep["leads"].items():
        for m in METHODS:
            s = o[m]
            name = f"**{m}**" if m == "model" else m
            lines.append(f"| {L} d | {name} | {s['pr_auc']:.3f} | {s['brier_x1e4']:.2f} | {s['poisson_dev_per_cell']:.4f} | "
                         + " | ".join(f"{v:.3f}" for v in s["fss"])
                         + f" | {s['topk_row_capture']:.3f} | {s['daily_total_bias_pct']:+.1f} | {s['daily_total_log_corr']:.3f} |")
    lines += ["", "Higher is better for PR-AUC, FSS, top-k capture and log correlation; lower for Brier and deviance.",
              "The model is only useful where it beats both baselines."]
    (fc.EVAL_DIR / "evaluation_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _plot(D: pd.DataFrame):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    leads = sorted(D.lead.unique())
    fig, axes = plt.subplots(len(leads), 1, figsize=(11, 2.6 * len(leads)), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, L in zip(axes, leads):
        d = D[D.lead == L].sort_values("target_date")
        x = pd.to_datetime(d.target_date)
        ax.plot(x, d.actual_rows, color="#222", lw=1.0, label="real VIIRS rows")
        ax.plot(x, d.model_rows, color="#1d4e89", lw=1.0, label="model (expected)")
        ax.plot(x, d.persistence_rows, color="#b7791f", lw=0.8, alpha=0.8, label="persistence")
        ax.set_yscale("symlog", linthresh=100)
        ax.set_ylabel(f"lead {L} d")
        ax.grid(alpha=0.25)
    axes[0].legend(loc="upper right", fontsize=8, ncol=3, frameon=False)
    axes[0].set_title("Daily Australia-wide VIIRS rows: real vs forecast (test years)", fontsize=10)
    fig.tight_layout()
    fig.savefig(fc.EVAL_DIR / "daily_totals.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", type=Path, nargs="+", default=[fc.MODEL_DIR],
                    help="model folder(s); several = equal-weight ensemble")
    ap.add_argument("--every", type=int, default=fc.EVAL_EVERY)
    ap.add_argument("--out", type=Path, help="output folder (default output/evaluation)")
    a = ap.parse_args()
    m = [x.resolve() for x in a.models]
    run(m[0] if len(m) == 1 else m, a.every, a.out)
