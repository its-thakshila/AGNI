"""Render the updated architecture PDF from the analysis + training results.

    python build_pdf.py   ->  ../MODIS_VIIRS_ML_Architecture_v2.pdf
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

import config

OUT_PDF = config.PDF_FILE

# ---------------------------------------------------------------- fonts / colours
FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("DV", str(FONT_DIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DVB", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("DVM", str(FONT_DIR / "DejaVuSansMono.ttf")))
pdfmetrics.registerFontFamily("DV", normal="DV", bold="DVB", italic="DV", boldItalic="DVB")

NAVY = colors.HexColor("#0d3c8f")
BLUE = colors.HexColor("#1f5fd6")
PALE = colors.HexColor("#eef3fd")
LINE = colors.HexColor("#c9d6f2")
CODE_BG = colors.HexColor("#1f2937")
INK = colors.HexColor("#1b1b1b")
INK2 = colors.HexColor("#4a4a4a")
GREEN_BG, GREEN_LN, GREEN_TX = colors.HexColor("#eafaf2"), colors.HexColor("#5fd3a0"), colors.HexColor("#0b6b45")
AMBER_BG, AMBER_LN, AMBER_TX = colors.HexColor("#fff8e6"), colors.HexColor("#f2c94c"), colors.HexColor("#8a5a00")
RED_HDR = colors.HexColor("#9b1c1c")
PURPLE = colors.HexColor("#6d28d9")

W = A4[0] - 36 * mm
S = {
    "body": ParagraphStyle("body", fontName="DV", fontSize=9, leading=13.2, textColor=INK, alignment=TA_JUSTIFY),
    "cell": ParagraphStyle("cell", fontName="DV", fontSize=8, leading=10.6, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="DVB", fontSize=8, leading=10.6, textColor=colors.white),
    "h2": ParagraphStyle("h2", fontName="DVB", fontSize=12.5, leading=16, textColor=BLUE, spaceBefore=8, spaceAfter=4),
    "sec": ParagraphStyle("sec", fontName="DVB", fontSize=11.5, leading=14, textColor=colors.white),
    "code": ParagraphStyle("code", fontName="DVM", fontSize=7.2, leading=9.4, textColor=colors.HexColor("#e5e7eb")),
    "cap": ParagraphStyle("cap", fontName="DV", fontSize=7.8, leading=10, textColor=INK2, alignment=TA_CENTER),
    "t1": ParagraphStyle("t1", fontName="DV", fontSize=13, leading=17, textColor=colors.HexColor("#c7d7ff"), alignment=TA_CENTER),
    "t2": ParagraphStyle("t2", fontName="DVB", fontSize=26, leading=32, textColor=colors.white, alignment=TA_CENTER),
    "t3": ParagraphStyle("t3", fontName="DV", fontSize=9.5, leading=13, textColor=colors.HexColor("#a9c1ff"), alignment=TA_CENTER),
    "flow": ParagraphStyle("flow", fontName="DVB", fontSize=7.4, leading=9, textColor=colors.white, alignment=TA_CENTER),
}


def P(t, st="body"):
    return Paragraph(t, S[st])


def section(num, title):
    t = Table([[P(f"{num}&nbsp;&nbsp;{title.upper()}", "sec")]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                           ("ROUNDEDCORNERS", [4, 4, 4, 4])]))
    return [t, Spacer(1, 8)]


def table(rows, widths, head=NAVY, zebra=True):
    data = [[P(c, "cellb") for c in rows[0]]] + [[P(str(c), "cell") for c in r] for r in rows[1:]]
    t = Table(data, colWidths=[w * W for w in widths], repeatRows=1)
    st = [("BACKGROUND", (0, 0), (-1, 0), head), ("GRID", (0, 0), (-1, -1), 0.5, LINE),
          ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 5)]
    if zebra:
        st += [("BACKGROUND", (0, i), (-1, i), PALE) for i in range(1, len(data), 2)]
    t.setStyle(TableStyle(st))
    return t


def code(txt):
    t = Table([[Preformatted(txt.strip("\n"), S["code"])]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CODE_BG), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                           ("ROUNDEDCORNERS", [4, 4, 4, 4])]))
    return t


def callout(html, kind="green"):
    bg, ln, tx = {"green": (GREEN_BG, GREEN_LN, GREEN_TX), "amber": (AMBER_BG, AMBER_LN, AMBER_TX)}[kind]
    st = ParagraphStyle("c", parent=S["body"], textColor=tx, fontSize=8.6, leading=12.4)
    t = Table([[Paragraph(html, st)]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.8, ln),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return t


def fig(path, caption, width=1.0):
    from reportlab.lib.utils import ImageReader
    iw, ih = ImageReader(str(path)).getSize()
    w = W * width
    return KeepTogether([Image(str(path), width=w, height=w * ih / iw), Spacer(1, 2), P(caption, "cap"), Spacer(1, 8)])


def flow(steps, palette):
    cells, widths = [], []
    for i, s in enumerate(steps):
        cells.append(P(s, "flow")); widths.append(1)
        if i < len(steps) - 1:
            cells.append(P("→", "cap")); widths.append(0.28)
    tot = sum(widths)
    t = Table([cells], colWidths=[W * w / tot for w in widths], rowHeights=[34])
    st = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]
    for i in range(len(steps)):
        st.append(("BACKGROUND", (2 * i, 0), (2 * i, 0), colors.HexColor(palette[i % len(palette)])))
        st.append(("ROUNDEDCORNERS", [4, 4, 4, 4]))
    t.setStyle(TableStyle(st))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("DV", 7)
    canvas.setFillColor(INK2)
    canvas.drawCentredString(A4[0] / 2, 10 * mm,
                             "NASA Space Apps Challenge 2026 · Harmonization of MODIS and VIIRS Hot Spots · "
                             f"Daily ML architecture v2 · page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------- content
def build():
    A = json.loads(config.ANALYSIS_JSON.read_text())
    R = json.loads(config.TRAIN_REPORT_FILE.read_text())
    schema = pd.read_csv(config.SCHEMA_CSV)
    pred_summary_p = config.pred_file("summary", config.REFERENCE_PAIR[0])
    PS = json.loads(pred_summary_p.read_text()) if pred_summary_p.exists() else {}
    cvraw, cvb, cvm = R["full"]["cv"]["hgb"], R["full"]["cv"]["baseline_fixed_ratio"], R["full"]["cv"]["baseline_monthly_ratio"]
    cv = R["full"]["cv"]["hgb_calibrated"]
    cvt = R["terra_only"]["cv"]["hgb_calibrated"]
    calib = R["full"]["cv"]["calibration_factors"]
    sec = R["full"]["cv"].get("secondary", {})
    lst = A["overpass_local_solar_time"]
    g10 = next(g for g in A["grid_matching_daily_by_pass"] if abs(g["grid_deg"] - 0.1) < 1e-9)

    # training run facts (older reports lack these keys -> they came from a 2025-only sklearn run)
    stale = "years" not in R["full"]
    model_name = R.get("model", "scikit-learn HistGradientBoosting (older run)" if stale else "XGBoost")
    years = R["full"].get("years") or [config.REFERENCE_YEAR]
    yrs = str(years[0]) if len(years) == 1 else f"{years[0]}–{years[-1]}"
    yrs_long = yrs if len(years) == 1 else f"{yrs} ({len(years)} years)"
    keep = R["full"].get("empty_keep", 1.0)
    rows_rep = R["full"].get("rows_represented", R["full"]["rows"])
    rows_rep_t = R["terra_only"].get("rows_represented", R["terra_only"]["rows"])
    xc, xr = config.XGB_PARAMS["count"], config.XGB_PARAMS["reg"]

    def hm(h):
        return f"{int(h):02d}:{int(round((h % 1) * 60)) % 60:02d}"

    story = []
    # ---------------- cover
    cover = Table([[P("MODIS → VIIRS", "t1")], [P("Daily ML Harmonization", "t2")],
                   [P("Architecture v2 · Dataset Analysis · Implementation Guide", "t1")],
                   [Spacer(1, 6)], [P(f"NASA Space Apps Challenge 2026 · Australia · XGBoost on GPU · trained {yrs}", "t3")]],
                  colWidths=[W])
    cover.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("TOPPADDING", (0, 0), (0, 0), 30),
                               ("BOTTOMPADDING", (0, -1), (0, -1), 30), ("ROUNDEDCORNERS", [8, 8, 8, 8])]))
    story += [Spacer(1, 20), cover, Spacer(1, 14)]
    story.append(callout(
        "<b>Core idea (v2):</b> learn, from days when MODIS and VIIRS S-NPP both flew, how a MODIS observation "
        "of one 0.1° cell on one day-pass or night-pass translates into what VIIRS records there — pixel count, FRP, "
        "brightness and confidence. Gradient-boosted trees (<b>XGBoost</b>, trained on the GPU) learn this from the "
        f"overlap years ({yrs_long}) and are applied to any MODIS-only year. The output is <b>day-by-day</b> and is written "
        "<b>in the exact column format of the FIRMS VIIRS S-NPP CSV</b>, so 2000–2011 MODIS years and real 2012+ "
        "VIIRS years can be concatenated into one record.", "green"))
    story.append(Spacer(1, 12))
    story.append(table([
        ["Training data", "Unit of analysis", "Model", "Output"],
        [f"MODIS + VIIRS S-NPP CSVs of the same year, Australia, {yrs_long}",
         "0.1° cell × local-solar fire-day × pass (day / night)",
         "5 × XGBoost (count, FRP, ti4, ti5, confidence) + Terra-only variant",
         "VIIRS-format rows + daily summary + daily cell table"],
    ], [.25, .25, .25, .25]))
    story.append(Spacer(1, 12))
    story.append(table([
        ["", "v1 (previous PDF)", "v2 (this document)"],
        ["Time resolution", "calendar month", "<b>day</b>, split into day pass and night pass"],
        ["Date definition", "UTC acq_date", "local solar time; fire-day = LST − 6 h (keeps a night pass on one date)"],
        ["Space", "0.25° cells", "0.1° cells + 3×3 / ±1-day neighbourhood"],
        ["Training years", "one year", f"{yrs_long}; days with no VIIRS data at all (instrument start, outages) are dropped"],
        ["Rows trained on", "MODIS cells only (left join)", "MODIS cells <b>and</b> empty neighbour cells → learns VIIRS-only small fires; "
         f"cells empty in both sensors sampled at {keep:g} and re-weighted (memory)"],
        ["Overpass", "ignored", "Terra / Aqua as separate feature groups, persistence features, S-NPP overpass-time model"],
        ["Confidence", "MODIS mean only", "MODIS 0–100 stats in, VIIRS l / n / h predicted out"],
        ["Model", "XGBoost (extra install)", "XGBoost on the GPU (CUDA, CPU fallback); Poisson loss, native NaN handling, row weights"],
        ["Output", "monthly count per cell", "pixel rows in VIIRS CSV schema + daily tables"],
        ["Validation", "random split", "week-blocked GroupKFold vs fixed / monthly ratio baselines"],
    ], [.2, .33, .47]))
    story.append(PageBreak())

    # ---------------- 01 dataset analysis
    story += section("01", "Dataset analysis — input & output formats")
    story.append(P(
        f"FIRMS archive CSVs for Australia: MODIS for every year from 2000, VIIRS S-NPP from 2012. This section analyses "
        f"the reference pair, {A['date_range'][0]} to {A['date_range'][1]}. "
        f"<b>{A['files']['modis']}</b> ({A['rows']['modis']:,} rows) defines the <b>input format</b> of every year; "
        f"<b>{A['files']['viirs']}</b> ({A['rows']['viirs']:,} rows) defines the <b>output format</b>. The column "
        "lists are identical in length and order; only the two brightness columns are renamed and the confidence "
        "field changes type."))
    story.append(Spacer(1, 6))
    rows = [["MODIS column (input)", "VIIRS column (output)", "MODIS values", "VIIRS values"]]
    for _, r in schema.iterrows():
        rows.append([r.modis_column, r.viirs_column, r.modis_values, r.viirs_values])
    story.append(table(rows, [.16, .16, .34, .34]))
    story.append(Spacer(1, 8))
    d = A["daily"]
    story.append(table([
        ["Measure", "Value", "Consequence for harmonization"],
        ["Total VIIRS / MODIS rows", f"{d['total_ratio']}×", "VIIRS has many more pixels for the same fires — the target must be a count, not a copy"],
        ["Daily VIIRS / MODIS ratio", f"median {d['ratio_median']}×, 10–90 % range {d['ratio_p10_p90'][0]}–{d['ratio_p10_p90'][1]}×",
         "a single multiplier (v1 Strategy B) is wrong on most days"],
        ["Correlation of daily counts", f"{d['corr_daily_counts']}", "MODIS carries strong signal for VIIRS — ML has something to learn"],
        ["Median FRP per pixel", f"MODIS {A['frp_quantiles_10_50_90_99']['modis'][1]} MW, VIIRS {A['frp_quantiles_10_50_90_99']['viirs'][1]} MW",
         "VIIRS splits a fire into smaller pixels and sees weaker fires"],
        ["Median pixel size (scan × track)", f"MODIS {A['pixel_size_km_median']['modis_scan_track'][0]}×{A['pixel_size_km_median']['modis_scan_track'][1]} km "
         f"(up to {A['pixel_size_km_median']['modis_scan_max']} km), VIIRS {A['pixel_size_km_median']['viirs_scan_track'][0]}×{A['pixel_size_km_median']['viirs_scan_track'][1]} km",
         "off-nadir MODIS pixels cover more VIIRS pixels → pixel area is a feature"],
        ["VIIRS confidence", ", ".join(f"{k}: {100*v:.1f}%" for k, v in A["confidence"]["viirs_class_share"].items()),
         "categorical l/n/h — the output needs a classifier"],
        ["MODIS confidence", f"0–100 integer, median {A['confidence']['modis_quantiles_10_25_50_75_90'][2]:.0f}", "numeric input feature"],
        ["Fire type (0 veg. fire / 2 static / 3 offshore)",
         "MODIS " + ", ".join(f"{k}: {int(v):,}" for k, v in A['type']['modis'].items())
         + "; VIIRS " + ", ".join(f"{k}: {int(v):,}" for k, v in A['type']['viirs'].items()),
         "static sources (type 2) carried to output type"],
    ], [.24, .3, .46]))
    story.append(fig(config.ANALYSIS_DIR / "fig_map.png", "Figure 1 — Spatial coverage of the two input files (log-density hexbins)."))
    story.append(fig(config.ANALYSIS_DIR / "fig_daily_counts.png", "Figure 2 — Daily detections by local-solar fire-day."))
    story.append(fig(config.ANALYSIS_DIR / "fig_daily_ratio.png", "Figure 3 — The daily VIIRS/MODIS ratio moves with fire regime and season."))
    story.append(PageBreak())

    story += section("02", "The four sensor differences — measured")
    story.append(P("<b>(1) Overpass time.</b> Local solar time (UTC + longitude / 15) of every detection shows three "
                   "distinct overpasses. Aqua's orbit has drifted: it is now about 1.3 h <i>after</i> S-NPP, while "
                   "Terra is about 4.5 h <i>before</i>. A fire seen by Terra in the morning may be out by 13:40, and "
                   "a fire that starts at noon is only seen by S-NPP and Aqua."))
    story.append(table([
        ["Platform", "Day pass median (5–95 %)", "Night pass median (5–95 %)", "Day / night detections"],
        *[[lab, f"{hm(lst[f'{k}_D']['median'])} ({hm(lst[f'{k}_D']['p05'])}–{hm(lst[f'{k}_D']['p95'])})",
           f"{hm(lst[f'{k}_N']['median'])} ({hm(lst[f'{k}_N']['p05'])}–{hm(lst[f'{k}_N']['p95'])})",
           f"{lst[f'{k}_D']['n']:,} / {lst[f'{k}_N']['n']:,}"]
          for k, lab in (("terra", "MODIS Terra"), ("aqua", "MODIS Aqua"), ("snpp", "VIIRS S-NPP"))],
    ], [.2, .28, .28, .24]))
    story.append(Spacer(1, 6))
    story.append(fig(config.ANALYSIS_DIR / "fig_overpass_lst.png", "Figure 4 — Detection local solar time per platform."))
    story.append(P("Night passes cross midnight (Terra 21:30 on day D, S-NPP 01:15 and Aqua 02:40 on D+1). Using the UTC "
                   "or local calendar date would split one night across two dates. v2 therefore defines "
                   "<b>fire_date = date(LST − 6 h)</b> and <b>pass = D if 06 ≤ LST &lt; 18 else N</b>; this agreed with "
                   f"the FIRMS daynight flag for {100*A['pass_vs_daynight_agreement']['modis']:.1f} % (MODIS) and "
                   f"{100*A['pass_vs_daynight_agreement']['viirs']:.1f} % (VIIRS) of rows."))
    story.append(Spacer(1, 6))
    story.append(P("<b>(2) Spatial resolution and (3) detection sensitivity</b> — matched at daily × pass resolution:"))
    gm = [["Grid", "MODIS cells with no VIIRS", "VIIRS px in MODIS cells", "VIIRS px in empty neighbour cells", "VIIRS px far from any MODIS", "VIIRS / MODIS per matched cell (median, IQR)"]]
    for g in A["grid_matching_daily_by_pass"]:
        gm.append([f"{g['grid_deg']}°", f"{g['modis_cells_without_viirs_pct']} %", f"{g['viirs_px_in_modis_cells_pct']} %",
                   f"{g['viirs_px_near_modis_pct']} %", f"{g['viirs_px_far_from_modis_pct']} %",
                   f"{g['ratio_median']} ({g['ratio_p25_p75'][0]}–{g['ratio_p25_p75'][1]})"])
    story.append(table(gm, [.09, .16, .16, .2, .17, .22]))
    story.append(Spacer(1, 6))
    story.append(callout(
        f"<b>Two findings overturn v1 assumptions.</b> (a) v1 expected &lt; 5 % of MODIS cells to have no VIIRS; at daily "
        f"resolution it is {g10['modis_cells_without_viirs_pct']} % at 0.1°, because of overpass timing, so zeros must be "
        f"modelled (Poisson loss). (b) A MODIS-only left join reaches only {g10['viirs_px_in_modis_cells_pct']} % of VIIRS "
        f"pixels; adding empty neighbour cells (3×3, ±1 day) reaches another {g10['viirs_px_near_modis_pct']} %. Only "
        f"{g10['viirs_px_far_from_modis_pct']} % lie far from any MODIS signal — this is the honest, unrecoverable part.", "amber"))
    story.append(fig(config.ANALYSIS_DIR / "fig_frp.png", "Figure 5 — Per-pixel FRP distributions."))
    story.append(P("<b>(4) Confidence.</b> MODIS reports an integer 0–100; VIIRS reports l / n / h. Rather than mapping "
                   "one onto the other with a fixed table, v2 uses MODIS confidence statistics as inputs and predicts the "
                   "VIIRS class mix with a classifier."))
    story.append(fig(config.ANALYSIS_DIR / "fig_confidence.png", "Figure 6 — Confidence encodings of the two sensors."))
    story.append(PageBreak())

    # ---------------- 03 architecture
    story += section("03", "System architecture v2")
    story.append(flow(["MODIS CSV<br/>(any year)", "Local solar<br/>time + pass", "0.1° cell<br/>candidates",
                       "Feature<br/>builder", "XGBoost<br/>models", "VIIRS-format<br/>rows", "Daily<br/>tables", "Unified<br/>record"],
                      ["#1d4ed8", "#2563eb", "#3b82f6", "#4f46e5", "#6d28d9", "#7c3aed", "#9333ea", "#a855f7"]))
    story.append(P("Offline training on overlap years → inference on any MODIS year → unified daily record", "cap"))
    story.append(Spacer(1, 8))
    story.append(table([
        ["Difference", "How v2 handles it", "Where in code"],
        ["Overpass time", "Local solar time; fire-day = LST − 6 h; day/night pass split; Terra and Aqua as separate feature "
         "groups; ±1-day and other-pass persistence features; S-NPP overpass-time distribution used to write acq_time",
         "common.add_time_keys, features.build, predict.synthesize_rows"],
        ["Spatial resolution", "Target = number of 375 m VIIRS pixels per cell-pass; MODIS pixel area (scan × track) as "
         "feature; output pixels placed inside the parent MODIS footprint", "features.build, predict.synthesize_rows"],
        ["Detection sensitivity", "FRP / brightness / confidence of MODIS pixels; empty neighbour cells as training rows "
         "(model learns small fires near MODIS activity); far-field remainder reported as a per-month/pass adjustment "
         "column (not invented as rows)", "features.build, train.synthesis_stats"],
        ["Confidence", "MODIS 0–100 mean/min as features; l/n/h class probabilities from a classifier; within a cell the "
         "highest-FRP pixels get the highest class", "train.fit_models, predict.synthesize_rows"],
        ["Terra-only years (2000 – Jul 2002)", "Second model set trained with Aqua removed from the training data; chosen "
         "automatically by date", "train.main (terra_only), predict.predict_cells"],
    ], [.18, .58, .24]))
    story.append(Spacer(1, 8))
    story.append(P("Step 1 — overpass-aware time keys", "h2"))
    story.append(code('''
utc = pd.to_datetime(df.acq_date + " " + df.acq_time, format="%Y-%m-%d %H%M")
lst = utc + pd.to_timedelta(df.longitude / 15.0, unit="h")        # local solar time
df["pass"] = np.where((lst.dt.hour >= 6) & (lst.dt.hour < 18), "D", "N")
df["fire_date"] = (lst - pd.Timedelta(hours=6)).dt.normalize()     # one night = one date
df["ci"] = np.floor((df.latitude  + 60) / 0.1).astype(int)          # 0.1 deg cell
df["cj"] = np.floor((df.longitude - 90) / 0.1).astype(int)
'''))
    story.append(P("Step 2 — candidate rows (fixes the left-join blind spot)", "h2"))
    story.append(code('''
# every cell with MODIS fire within 3x3 cells and +-1 day, same pass
cand = unique(encode(ci+a, cj+b, day+c, pass) for a,b in 3x3 for c in (-1,0,1))
X = features_for(cand)                     # MODIS = 0 rows are kept on purpose
y = viirs_pixels_per(cand).fillna(0)       # zeros are real (overpass / sensitivity)
'''))
    story.append(PageBreak())

    story += section("04", "Features")
    story.append(table([
        ["Group", "Features", "Captures"],
        ["Terra in cell (09:00 / 21:30)", "terra_n, frp_sum, frp_max, conf_mean, conf_min, bt_mean, bt31_mean, px_area, scan_mean, static_share",
         "morning observation; intensity, confidence, pixel size"],
        ["Aqua in cell (15:00 / 02:40)", "same 10 statistics for Aqua (absent in Terra-only model)", "afternoon observation, closest to S-NPP"],
        ["Neighbourhood", "nb_n, nb_frp, nb_active_cells (8 surrounding cells)", "fire fronts spilling into MODIS-empty cells"],
        ["Persistence", "own_prev_n, own_next_n, nb3_prev_n, nb3_next_n, own_7d_n, own_otherpass_n, own_otherpass_frp",
         "fires that burn through the S-NPP overpass but were missed at the MODIS time"],
        ["Totals", "modis_n, modis_frp, modis_empty", "combined MODIS signal"],
        ["Place / season", "lat, lon, doy_sin, doy_cos, is_night", "land cover / fire regime, season, day vs night pass"],
    ], [.22, .48, .30]))
    story.append(Spacer(1, 8))
    story += section("05", "Models — XGBoost on the GPU")
    story.append(P("XGBoost builds histogram-based gradient-boosted trees, grown leaf by leaf. Its standard install "
                   "includes NVIDIA CUDA, so training runs on the laptop GPU (falling back to the CPU automatically); "
                   "trained models predict on the CPU and run anywhere. It supports a <b>Poisson loss</b> (right for "
                   "counts with many zeros), <b>row weights</b> and <b>missing values natively</b> (e.g. a cell where "
                   "only Terra observed has no Aqua FRP). Training data is passed as a binned <i>QuantileDMatrix</i> "
                   "built from 32-bit floats, which keeps memory low."))
    story.append(Spacer(1, 4))
    story.append(table([
        ["Model", "Type / loss", "Trained on", "Predicts"],
        ["count", "Regression, Poisson", f"{R['full']['rows']:,} rows standing for {rows_rep:,} candidate cell-passes",
         "expected VIIRS pixels μ"],
        ["frp", "Regression, squared error on log FRP", f"{R['full']['rows_with_viirs']:,} cell-passes with VIIRS", "mean FRP per VIIRS pixel"],
        ["ti4 / ti5", "Regression, squared error", "cell-passes with VIIRS", "mean bright_ti4 / bright_ti5 (K)"],
        ["conf", "Multiclass (l / n / h), pixel-weighted", "cell-passes with VIIRS", "class probabilities"],
        ["*_terra_only", "same five, Aqua removed", f"{R['terra_only']['rows']:,} rows standing for {rows_rep_t:,}",
         "used before 2002-07-04"],
    ], [.16, .28, .3, .26]))
    story.append(Spacer(1, 6))
    story.append(P("<b>Training rows.</b> Built one year at a time. Fire-days on which the VIIRS file has no detection "
                   "anywhere (S-NPP not yet operating in January 2012, outages) are dropped, because MODIS fires there "
                   "would be labelled 'VIIRS saw 0' while VIIRS was not observing. About 86 % of candidate cell-passes "
                   f"have no fire in either sensor; a seeded {keep:g} share of those is kept with weight {1 / keep:g}, so "
                   "every sum, fit and score stays unbiased while the table shrinks about three-fold. All cells with "
                   "VIIRS fire are kept. Day indices count from one fixed date (config.DAY0), so cell-day keys are "
                   "unique across years."))
    story.append(Spacer(1, 6))
    story.append(code(f'''
import xgboost as xgb
params = dict(objective="{xc['objective']}", tree_method="hist", device="cuda",
              grow_policy="lossguide", max_leaves={xc['max_leaves']}, learning_rate={config.XGB_COMMON['learning_rate']},
              reg_lambda={config.XGB_COMMON['reg_lambda']}, min_child_weight={xc['min_child_weight']}, max_bin={config.XGB_COMMON['max_bin']})
dtrain = xgb.QuantileDMatrix(X_tr[FEATURES], X_tr["v_n"], weight=X_tr["w"])   # w = sampling weight
dval   = xgb.QuantileDMatrix(X_va[FEATURES], X_va["v_n"], weight=X_va["w"], ref=dtrain)
count_model = xgb.train(params, dtrain, num_boost_round={xc['num_boost_round']}, evals=[(dval, "val")],
                        early_stopping_rounds={xc['early_stopping_rounds']})
mu = count_model.inplace_predict(X_new[FEATURES])    # expected VIIRS pixels
'''))
    story.append(P(f"Regression models use {xr['max_leaves']} leaves and min_child_weight {xr['min_child_weight']:g} "
                   "(≈ rows, since their hessian is the row weight); the Poisson count model uses "
                   f"{xc['min_child_weight']:g}, because its hessian is the predicted count (≈ 0.02 in empty cells).", "cap"))
    story.append(PageBreak())

    # ---------------- 06 validation
    story += section("06", f"Validation — measured on {yrs}")
    if stale:
        story.append(callout("<b>Older training run.</b> The numbers in this section come from a training report "
                             "written before the move to XGBoost and multi-year training (scikit-learn, 2025 only). "
                             "Re-run train.py and build_pdf.py to refresh them.", "amber"))
        story.append(Spacer(1, 6))
    story.append(P(f"Model: {model_name}. Validation is <b>GroupKFold ({config.CV_FOLDS} folds) over ISO weeks</b>: "
                   "every week is predicted by a model that never saw it. Both baselines are refitted inside every fold. "
                   "Scores use the sampling weights, so they describe all candidate cell-passes, and refer to VIIRS "
                   "pixels inside the candidate set; the far-field remainder is reported separately."))
    story.append(Spacer(1, 6))
    cols = [cv, cvraw, cvt, cvm, cvb]
    story.append(table([
        ["Metric (out-of-fold)", "ML calibrated", "ML raw", "ML Terra-only (cal.)", "Monthly-ratio rule (v1)", "Fixed ratio"],
        ["Daily total R²", *[c["daily_r2"] for c in cols]],
        ["Daily total MAE (% of mean)", *[f"{c['daily_mae_pct']} %" for c in cols]],
        ["Monthly total R²", *[c["monthly_r2"] for c in cols]],
        ["Monthly total MAE (% of mean)", *[f"{c['monthly_mae_pct']} %" for c in cols]],
        ["Cell-pass R²", *[c["cell_r2"] for c in cols]],
        ["Cell-pass Poisson deviance (lower = better)", *[c["cell_poisson_dev"] for c in cols]],
        ["Total bias", *[f"{c['total_bias_pct'] + 0.0:+.2f} %".replace("-0.00", "0.00").replace("+0.00", "0.00") for c in cols]],
    ], [.25, .15, .13, .15, .17, .15], head=colors.HexColor("#065f46")))
    story.append(Spacer(1, 6))
    story.append(P(
        "<b>Reading the table.</b> The ML model wins where harmonization matters for a daily calendar: per cell (Poisson "
        f"deviance {cv['cell_poisson_dev']} vs {cvm['cell_poisson_dev']}) and per day (error {cv['daily_mae_pct']} % vs "
        f"{cvm['daily_mae_pct']} %). The monthly-ratio rule looks near-perfect on <i>monthly</i> totals only because its "
        "ratio is computed from the other weeks of the same month, which contain that month's answer; that advantage "
        "disappears for a MODIS-only year. Raw boosted-tree predictions under-shoot unseen weeks "
        f"(bias {cvraw['total_bias_pct']} %), mostly in MODIS-empty cells and night passes, so four out-of-fold "
        f"calibration factors are applied: MODIS-active day ×{calib['0_0']}, active night ×{calib['0_1']}, "
        f"MODIS-empty day ×{calib['1_0']}, empty night ×{calib['1_1']}. Because they are estimated on the same "
        "out-of-fold predictions, the calibrated total bias is 0 by construction; the daily and per-cell scores are "
        "the meaningful ones."))
    story.append(Spacer(1, 6))
    if sec:
        story.append(P(f"Secondary models (out-of-fold): log-FRP R² {sec['frp_r2_log']:.3f}; bright_ti4 MAE "
                       f"{sec['ti4_mae_k']:.2f} K; bright_ti5 MAE {sec['ti5_mae_k']:.2f} K; VIIRS confidence-class share "
                       f"error {sec['conf_share_mae_pct']:.2f} percentage points."))
    story.append(fig(config.MODEL_DIR / "fig_cv_daily.png", "Figure 7 — Held-out weeks: real VIIRS vs predictions from MODIS."))
    story.append(fig(config.MODEL_DIR / "fig_cv_scatter.png", "Figure 8 — Daily totals, predicted vs real (held-out weeks)."))
    story.append(fig(config.MODEL_DIR / "fig_feature_importance.png", "Figure 9 — Permutation importance of the count model.", 0.9))
    story.append(callout(f"<b>Reachability.</b> {100*R['reachable_share_total']:.1f} % of {yrs} VIIRS pixels fall in candidate "
                         "cells. The rest are small fires with no MODIS signal within 3×3 cells / ±1 day. Daily tables carry "
                         "both <i>viirs_equiv_n</i> (model only) and <i>viirs_equiv_n_sensitivity_adj</i> (scaled by the "
                         "month × pass reachability measured in training); pixel rows are generated only for the "
                         "model part, so no locations are invented without MODIS evidence.", "amber"))
    story.append(PageBreak())

    # ---------------- 07 outputs
    story += section("07", "Outputs — VIIRS format, day by day")
    story.append(P("predict.py turns the per-cell predictions into rows with <b>exactly the 15 columns of the FIRMS VIIRS "
                   "S-NPP CSV</b>, in the same order, so downstream code reads real and predicted years identically."))
    story.append(Spacer(1, 4))
    story.append(table([
        ["VIIRS column", "How the predicted value is produced"],
        ["latitude, longitude", "inside the footprint (scan × track km) of a MODIS pixel of the same cell / day / pass; "
         "uniform within the 0.1° cell for MODIS-empty neighbour cells"],
        ["acq_date, acq_time", "S-NPP local-solar overpass time sampled from the training distribution for that pass, "
         "converted to UTC with the longitude (overpass correction)"],
        ["frp", "cell mean from the frp model × Duan smearing factor (corrects the log-scale bias) × within-cell "
                "log-normal spread measured in training"],
        ["bright_ti4, bright_ti5", "cell means from the ti4 / ti5 models ± within-cell spread, rank-matched to FRP"],
        ["confidence", "l / n / h sampled from classifier probabilities; brightest pixels get the highest class"],
        ["scan, track", "sampled from the observed S-NPP pixel-size distribution"],
        ["satellite, instrument, daynight, type", "N, VIIRS, pass, 2 if the MODIS cell is mostly static sources else 0"],
        ["version", f"<b>{config.OUT_VERSION}</b> — marks model-generated rows (real FIRMS rows read 2)"],
    ], [.26, .74]))
    story.append(Spacer(1, 6))
    story.append(P("Pixel counts per cell are drawn by stochastic rounding of μ, so totals stay unbiased.", "body"))
    story.append(Spacer(1, 6))
    story.append(table([
        ["File", "Grain", "Key columns"],
        ["viirs_equivalent_&lt;file&gt;.csv", "one predicted VIIRS pixel", "the 15 VIIRS columns"],
        ["daily_summary_&lt;file&gt;.csv", "one fire-day", "modis_terra_n, modis_aqua_n, viirs_equiv_n (+ _day / _night / _sensitivity_adj), "
         "viirs_equiv_rows, viirs_equiv_frp_sum, viirs_actual_n when a real VIIRS file is supplied"],
        ["daily_cells_&lt;file&gt;.csv", "0.1° cell × fire-day × pass", "MODIS counts, viirs_equiv_n, FRP, ti4, p_conf_l/n/h, model"],
        ["unified/unified_daily.csv", "one fire-day, all years", "viirs_n, viirs_frp_sum, modis_n, source = real_viirs | ml_predicted"],
    ], [.3, .22, .48]))
    if PS:
        story.append(Spacer(1, 6))
        story.append(P(f"{config.REFERENCE_YEAR} run (in-sample demonstration): {PS['modis_rows']:,} MODIS rows → "
                       f"{PS['viirs_equiv_rows']:,} VIIRS-format rows (expected {PS['viirs_equiv_expected']:,.0f}); "
                       f"real VIIRS file has {PS.get('viirs_actual_rows', 0):,} rows."))
    story.append(Spacer(1, 6))
    story.append(P("Pipeline files (ml_pipeline/)", "h2"))
    story.append(table([
        ["Script", "Role"],
        ["config.py / common.py", "all paths and settings; CSV loading, local solar time, pass, fire-day, grid keys"],
        ["analyze_datasets.py", "format + sensor-difference analysis, figures 1–6"],
        ["features.py", "candidate cells, feature table, VIIRS targets"],
        ["gbm.py", "XGBoost training wrapper: GPU with CPU fallback, early stopping, scikit-learn style predict"],
        ["train.py", "per-year table building, week-blocked CV vs baselines, final models, synthesis statistics, figures 7–9"],
        ["predict.py", "any MODIS CSV → VIIRS-format rows + daily tables"],
        ["unify.py", "multi-year daily record: real VIIRS where it exists, predictions elsewhere"],
        ["build_pdf.py", "this document"],
    ], [.3, .7]))
    story.append(Spacer(1, 6))
    story.append(code('''
cd ml_pipeline
python run_all.py                  # analyse -> train -> predict reference year -> unify
python predict.py --year 2005      # any MODIS-only year (data/predict)
python predict.py --year 2025      # overlap year: also compared with the real VIIRS file
python unify.py                    # after adding CSVs to data/train/{MODIS,VIIRS} or data/predict
# all folders and file names are set in config.py
'''))
    story.append(PageBreak())

    # ---------------- 08 limitations + plan
    story += section("08", "Limitations & next steps")
    story.append(table([
        ["Limitation", "Effect", "Mitigation"],
        *([["One overlap year", f"model has seen one fire season ({yrs}); year-to-year drift untested",
            "set TRAIN_YEARS = 2012–2025 in config.py; switch CV to leave-one-year-out"]] if len(years) == 1 else
          [["Week-blocked CV", "held-out weeks still share their year with training weeks",
            "add leave-one-year-out CV: the honest test for predicting a MODIS-only year"]]),
        ["Sampled empty cells", f"{keep:g} of cells empty in both sensors are kept (weighted)",
         "unbiased on average; set EMPTY_KEEP = 1.0 when memory allows"],
        ["Trees capped", "count models often use all their boosting rounds", "try more rounds (num_boost_round in config.py)"],
        ["Aqua orbit drift", "Aqua's overpass time drifted later after 2022; earlier years had Aqua ≈ S-NPP",
         "train on several years and add a year / overpass-time feature"],
        ["Far-field small fires", f"{100 - 100*R['reachable_share_total']:.1f} % of VIIRS pixels have no MODIS evidence nearby",
         "reported as an aggregate adjustment column only — never as invented rows"],
        ["Synthetic pixel positions", "accurate to the MODIS footprint (~1–5 km), not to 375 m", "use daily cell table for analysis at 0.1°"],
        ["S-NPP only", "NOAA-20/21 differ in overpass by ~50 min", "train a sister model per VIIRS platform with the same code"],
    ], [.22, .4, .38]))
    story.append(Spacer(1, 10))
    story.append(table([
        ["Hours", "Task", "Who"],
        ["Pre-event", "Download modis_YYYY / viirs-snpp_YYYY CSVs, run run_all.py, check training_report.json", "Backend"],
        ["0 – 4", "FastAPI endpoints over unified_daily.csv and daily_cells (bbox + date filters)", "Backend"],
        ["0 – 4", "Leaflet map + draw; load VIIRS-format rows as hotspot layer", "Frontend"],
        ["4 – 12", "D3 daily calendar (24 years × 365 days) from unified_daily; colour real vs predicted", "Frontend"],
        ["12 – 20", "Anomaly engine on daily VIIRS-equivalent counts (z-score vs same day-of-year window)", "Backend"],
        ["20 – 32", "Integration, validation panel (figures 7–8) in the app", "Both"],
        ["32 – 48", "Demo regions (Black Summer 2019–20), rehearsal, write-up", "Team"],
    ], [.14, .7, .16]))
    story.append(Spacer(1, 10))
    story.append(callout(
        f"<b>Key number for judges:</b> out-of-fold daily R² = <b>{cv['daily_r2']}</b> for VIIRS-equivalent counts "
        f"predicted only from MODIS, against {cvm['daily_r2']} for the monthly-ratio rule (daily error "
        f"{cv['daily_mae_pct']} % vs {cvm['daily_mae_pct']} % of the mean). It is measured on weeks the model never saw "
        "and covers all four sensor differences, not only the pixel-count gap.", "green"))

    doc = SimpleDocTemplate(str(OUT_PDF), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title="MODIS to VIIRS Daily ML Harmonization — Architecture v2",
                            author="NASA Space Apps 2026 team")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print("written", OUT_PDF)


if __name__ == "__main__":
    build()
