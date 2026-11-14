"""Analyse the MODIS (input format) and VIIRS S-NPP (output format) CSVs.

Writes output/analysis/: dataset_analysis.json, schema.csv and PNG figures used
in the architecture PDF. Run: python analyze_datasets.py
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

import common
import config
from vizstyle import AQUA, INK_2, TERRA, VIIRS, plt, save

log = logging.getLogger("analyze")


def schema_table(m: pd.DataFrame, v: pd.DataFrame) -> pd.DataFrame:
    rows = []
    vmap = {"brightness": "bright_ti4", "bright_t31": "bright_ti5"}
    for mc in common.MODIS_COLUMNS:
        vc = vmap.get(mc, mc)

        def desc(df, c):
            s = df[c]
            if pd.api.types.is_numeric_dtype(s):
                return f"{s.min():g} .. {s.max():g} (median {s.median():g})"
            vals = s.value_counts()
            if len(vals) <= 6:
                return ", ".join(f"{k} ({100 * n / len(s):.1f}%)" for k, n in vals.items())
            return f"{s.min()} .. {s.max()}"
        rows.append({"modis_column": mc, "viirs_column": vc,
                     "modis_values": desc(m, mc), "viirs_values": desc(v, vc)})
    return pd.DataFrame(rows)


def grid_matching(m: pd.DataFrame, v: pd.DataFrame, grids=(0.05, 0.1, 0.25)) -> list[dict]:
    """How VIIRS pixels relate to MODIS cells at daily x pass resolution."""
    out = []
    day0 = pd.Timestamp(config.DAY0)
    for g in grids:
        def key(df, di=0, dj=0, dd=0):
            ci = np.floor((df["latitude"] - config.GRID_LAT0) / g).astype(np.int64) + di
            cj = np.floor((df["longitude"] - config.GRID_LON0) / g).astype(np.int64) + dj
            day = (df["fire_date"] - day0).dt.days.astype(np.int64) + dd
            p = (df["pass"] == "N").astype(np.int64)
            return ((day * 2 + p) * 4000 + ci) * 4000 + cj
        mk, vk = key(m).to_numpy(), key(v).to_numpy()
        mu, mc = np.unique(mk, return_counts=True)
        vu, vc = np.unique(vk, return_counts=True)
        in_cell = np.isin(vk, mu)
        nb = np.unique(np.concatenate([key(m, a, b, c).to_numpy()
                                       for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)]))
        near = np.isin(vk, nb) & ~in_cell
        ms, vs = pd.Series(mc, index=mu), pd.Series(vc, index=vu)
        both = ms.index.intersection(vs.index)
        ratio = vs[both] / ms[both]
        out.append({
            "grid_deg": g, "modis_cells": int(len(mu)), "viirs_cells": int(len(vu)),
            "modis_cells_without_viirs_pct": round(100 * (1 - np.isin(mu, vu).mean()), 1),
            "viirs_px_in_modis_cells_pct": round(100 * in_cell.mean(), 1),
            "viirs_px_near_modis_pct": round(100 * near.mean(), 1),
            "viirs_px_far_from_modis_pct": round(100 * (1 - in_cell.mean() - near.mean()), 1),
            "ratio_median": round(float(ratio.median()), 2),
            "ratio_p25_p75": [round(float(ratio.quantile(.25)), 2), round(float(ratio.quantile(.75)), 2)],
        })
        log.info("grid %.2f: %s", g, out[-1])
    return out


def figures(m: pd.DataFrame, v: pd.DataFrame, d: dict) -> None:
    A = config.ANALYSIS_DIR
    # 1. overpass local solar time
    fig, ax = plt.subplots(figsize=(8, 3.2))
    bins = np.arange(0, 24.25, 0.25)
    for lab, s, c in [("Terra (MODIS)", m[m.sensor == "terra"].lst_hour, TERRA),
                      ("Aqua (MODIS)", m[m.sensor == "aqua"].lst_hour, AQUA),
                      ("S-NPP (VIIRS)", v.lst_hour, VIIRS)]:
        w = np.full(len(s), 100 / len(s))
        ax.hist(s, bins=bins, weights=w, histtype="step", linewidth=1.8, color=c, label=lab)
    ax.set_xlabel("local solar time of detection (h)")
    ax.set_ylabel("% of sensor's detections")
    ax.set_xticks(range(0, 25, 3))
    ax.set_title("Overpass times differ: Terra ~09:00/21:30, S-NPP ~13:40/01:15, Aqua ~15:00/02:40")
    ax.legend(loc="upper center", ncol=3)
    save(fig, A / "fig_overpass_lst.png")

    # 2. daily counts
    dm = m.groupby("fire_date").size()
    dv = v.groupby("fire_date").size()
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(dv.index, dv.values, color=VIIRS, label="VIIRS S-NPP detections")
    ax.plot(dm.index, dm.values, color=TERRA, label="MODIS (Terra+Aqua) detections")
    ax.set_ylabel("detections per fire-day")
    ax.set_title("Daily detections, Australia 2025")
    ax.legend(loc="upper left")
    save(fig, A / "fig_daily_counts.png")

    # 3. daily ratio
    r = (dv / dm).dropna()
    fig, ax = plt.subplots(figsize=(8, 2.8))
    ax.plot(r.index, r.values, color=VIIRS, linewidth=1.2)
    ax.axhline(r.median(), color=INK_2, linestyle="--", linewidth=1)
    ax.text(r.index[5], r.median() * 1.05, f"median {r.median():.1f}x", color=INK_2, fontsize=8)
    ax.set_ylabel("VIIRS / MODIS")
    ax.set_title("Daily VIIRS-to-MODIS ratio is not constant - a fixed multiplier is wrong")
    save(fig, A / "fig_daily_ratio.png")

    # 4. FRP distributions
    fig, ax = plt.subplots(figsize=(8, 3))
    bins = np.logspace(-1, 4, 60)
    for lab, s, c in [("MODIS (1 km pixel)", m.frp, TERRA), ("VIIRS (375 m pixel)", v.frp, VIIRS)]:
        ax.hist(s.clip(lower=0.1), bins=bins, weights=np.full(len(s), 100 / len(s)),
                histtype="step", linewidth=1.8, color=c, label=lab)
    ax.set_xscale("log")
    ax.set_xlabel("FRP per pixel (MW, log scale)")
    ax.set_ylabel("% of detections")
    ax.set_title("Per-pixel FRP: VIIRS sees smaller fires (median %.1f vs %.1f MW)"
                 % (v.frp.median(), m.frp.median()))
    ax.legend()
    save(fig, A / "fig_frp.png")

    # 5. confidence
    fig, axes = plt.subplots(1, 2, figsize=(8, 2.8), gridspec_kw={"width_ratios": [2.2, 1]})
    axes[0].hist(m.confidence, bins=np.arange(0, 102, 2), color=TERRA, edgecolor="#fcfcfb", linewidth=0.5)
    axes[0].set_title("MODIS confidence: integer 0-100")
    axes[0].set_xlabel("confidence (%)")
    axes[0].set_ylabel("detections")
    shares = v.confidence.value_counts(normalize=True).reindex(["l", "n", "h"]) * 100
    axes[1].bar(["low", "nominal", "high"], shares.values, color=VIIRS, width=0.6)
    for i, s in enumerate(shares.values):
        axes[1].text(i, s + 1, f"{s:.0f}%", ha="center", fontsize=8, color=INK_2)
    axes[1].set_title("VIIRS confidence: l / n / h")
    axes[1].set_ylabel("% of detections")
    save(fig, A / "fig_confidence.png")

    # 6. map
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.4), sharex=True, sharey=True)
    for ax, df, lab, c in [(axes[0], m, "MODIS detections", "Oranges"), (axes[1], v, "VIIRS detections", "Blues")]:
        ax.hexbin(df.longitude, df.latitude, gridsize=70, bins="log", cmap=c, mincnt=1, linewidths=0)
        ax.set_title(f"{lab} ({len(df):,})")
        ax.set_aspect("equal")
        ax.grid(False)
    save(fig, A / "fig_map.png")


def main():
    config.ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    mp, vp = config.REFERENCE_PAIR
    m, v = common.load_modis(mp), common.load_viirs(vp)

    sch = schema_table(m, v)
    sch.to_csv(config.SCHEMA_CSV, index=False)

    lst = {}
    for lab, df in [("terra", m[m.sensor == "terra"]), ("aqua", m[m.sensor == "aqua"]), ("snpp", v)]:
        for p in ("D", "N"):
            s = df[df["pass"] == p].lst_hour
            lst[f"{lab}_{p}"] = {"median": round(float(s.median()), 2),
                                 "p05": round(float(s.quantile(.05)), 2), "p95": round(float(s.quantile(.95)), 2),
                                 "n": int(len(s))}
    dm, dv = m.groupby("fire_date").size(), v.groupby("fire_date").size()
    daily = pd.concat([dm.rename("m"), dv.rename("v")], axis=1).fillna(0)
    r = (daily.v / daily.m.replace(0, np.nan)).dropna()
    d = {
        "files": {"modis": mp.name, "viirs": vp.name},
        "rows": {"modis": len(m), "viirs": len(v)},
        "date_range": [str(m.acq_date.min()), str(m.acq_date.max())],
        "modis_satellites": m.satellite.value_counts().to_dict(),
        "daynight": {"modis": m.daynight.value_counts().to_dict(), "viirs": v.daynight.value_counts().to_dict()},
        "type": {"modis": m["type"].value_counts().to_dict(), "viirs": v["type"].value_counts().to_dict()},
        "confidence": {"modis_quantiles_10_25_50_75_90": m.confidence.quantile([.1, .25, .5, .75, .9]).tolist(),
                       "viirs_class_share": v.confidence.value_counts(normalize=True).round(4).to_dict()},
        "frp_quantiles_10_50_90_99": {"modis": m.frp.quantile([.1, .5, .9, .99]).round(2).tolist(),
                                      "viirs": v.frp.quantile([.1, .5, .9, .99]).round(2).tolist()},
        "pixel_size_km_median": {"modis_scan_track": [float(m.scan.median()), float(m.track.median())],
                                 "viirs_scan_track": [float(v.scan.median()), float(v.track.median())],
                                 "modis_scan_max": float(m.scan.max())},
        "overpass_local_solar_time": lst,
        "pass_vs_daynight_agreement": {"modis": float((m["pass"] == m.daynight).mean()),
                                       "viirs": float((v["pass"] == v.daynight).mean())},
        "daily": {"days": int(len(daily)), "corr_daily_counts": round(float(daily.m.corr(daily.v)), 3),
                  "ratio_median": round(float(r.median()), 2),
                  "ratio_p10_p90": [round(float(r.quantile(.1)), 2), round(float(r.quantile(.9)), 2)],
                  "total_ratio": round(len(v) / len(m), 2)},
        "grid_matching_daily_by_pass": grid_matching(m, v),
    }
    config.ANALYSIS_JSON.write_text(json.dumps(d, indent=1, default=str))
    figures(m, v, d)
    log.info("analysis written to %s", config.ANALYSIS_DIR)
    return d


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    main()
