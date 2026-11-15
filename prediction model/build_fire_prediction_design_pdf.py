"""Builds Fire_Row_Prediction_Design.pdf: how to forecast fire rows in the VIIRS CSV format,
which models to use, and how the options compare.

    python build_fire_prediction_design_pdf.py

Numbers in section 3 were measured on backend/data/parquet/daily_cells.parquet
(origin = real, type = 0, fire_dates 2013-01-01 .. 2025-12-31) on 2026-11-15.
"""
from __future__ import annotations

import os
from pathlib import Path

import matplotlib
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

HERE = Path(__file__).resolve().parent
OUT = HERE / "Fire_Row_Prediction_Design.pdf"

# ---------------------------------------------------------------- fonts (DejaVu: full Unicode)
FD = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("DV", str(FD / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DVB", str(FD / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("DVI", str(FD / "DejaVuSans-Oblique.ttf")))
pdfmetrics.registerFont(TTFont("DVM", str(FD / "DejaVuSansMono.ttf")))
pdfmetrics.registerFontFamily("DV", normal="DV", bold="DVB", italic="DVI", boldItalic="DVB")

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#52606d")
ACCENT = colors.HexColor("#1d4e89")
RULE = colors.HexColor("#cbd2d9")
HEAD_BG = colors.HexColor("#e4ebf5")
REC_BG = colors.HexColor("#eef6ee")
REC_EDGE = colors.HexColor("#3f7d3f")
WARN_BG = colors.HexColor("#fff6e5")
WARN_EDGE = colors.HexColor("#b7791f")

BODY = ParagraphStyle("body", fontName="DV", fontSize=9.2, leading=13.2, textColor=INK, spaceAfter=5)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.0, leading=10.6, spaceAfter=0)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=7.9, leading=10.2, spaceAfter=0)
CELLB = ParagraphStyle("cellb", parent=CELL, fontName="DVB")
H1 = ParagraphStyle("h1", parent=BODY, fontName="DVB", fontSize=13.5, leading=17, textColor=ACCENT,
                    spaceBefore=10, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=BODY, fontName="DVB", fontSize=10.5, leading=14, textColor=INK,
                    spaceBefore=7, spaceAfter=3)
TITLE = ParagraphStyle("title", parent=BODY, fontName="DVB", fontSize=20, leading=25, textColor=ACCENT,
                       spaceAfter=4)
SUB = ParagraphStyle("sub", parent=BODY, fontSize=10.5, leading=14, textColor=MUTED, spaceAfter=2)
BUL = ParagraphStyle("bul", parent=BODY, leftIndent=11, bulletIndent=2, spaceAfter=2.5)
CODE = ParagraphStyle("code", parent=BODY, fontName="DVM", fontSize=7.4, leading=9.6, textColor=INK,
                      backColor=colors.HexColor("#f5f7fa"), borderPadding=(4, 5, 4, 5), spaceBefore=3, spaceAfter=8)

W = A4[0] - 36 * mm      # text width


def P(t, s=BODY):
    return Paragraph(t, s)


def bullets(items, style=BUL):
    return [Paragraph(t, style, bulletText="•") for t in items]


def table(rows, widths, header=True, zebra=True, bold_first_col=False, valign="TOP", font=None):
    data = []
    cell = CELL if font is None else ParagraphStyle("c%s" % font, parent=CELL, fontSize=font, leading=font * 1.3)
    cellb = CELLB if font is None else ParagraphStyle("cb%s" % font, parent=cell, fontName="DVB")
    for r, row in enumerate(rows):
        out = []
        for c, v in enumerate(row):
            st = cellb if (header and r == 0) or (bold_first_col and c == 0) else cell
            out.append(v if not isinstance(v, str) else Paragraph(v, st))
        data.append(out)
    t = Table(data, colWidths=[w * W for w in widths], repeatRows=1 if header else 0)
    cmds = [("VALIGN", (0, 0), (-1, -1), valign), ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), HEAD_BG), ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT)]
    if zebra:
        for r in range(1 if header else 0, len(rows)):
            if r % 2 == 0:
                cmds.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f8fafc")))
    t.setStyle(TableStyle(cmds))
    return t


def box(flowables, bg=REC_BG, edge=REC_EDGE):
    t = Table([[flowables]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 3, edge),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


# ---------------------------------------------------------------- architecture diagram
def arrow(d, x1, y1, x2, y2, col=MUTED):
    d.add(Line(x1, y1, x2, y2, strokeColor=col, strokeWidth=1.1))
    import math
    a = math.atan2(y2 - y1, x2 - x1)
    s = 5
    d.add(Polygon([x2, y2, x2 - s * math.cos(a - 0.4), y2 - s * math.sin(a - 0.4),
                   x2 - s * math.cos(a + 0.4), y2 - s * math.sin(a + 0.4)], fillColor=col, strokeColor=col))


def node(d, x, y, w, h, title, lines, fill, edge):
    d.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=fill, strokeColor=edge, strokeWidth=0.9))
    d.add(String(x + 6, y + h - 12, title, fontName="DVB", fontSize=7.8, fillColor=INK))
    for k, ln in enumerate(lines):
        d.add(String(x + 6, y + h - 23 - 9.2 * k, ln, fontName="DV", fontSize=6.6, fillColor=MUTED))


def diagram():
    Wd, Hd = W, 238
    d = Drawing(Wd, Hd)
    inp = colors.HexColor("#f1f5fb"); inpe = colors.HexColor("#8aa4c8")
    mdl = colors.HexColor("#eef6ee"); mdle = REC_EDGE
    outc = colors.HexColor("#fff6e5"); oute = WARN_EDGE
    # inputs column
    bw, bh, x0 = 128, 46, 0
    node(d, x0, 186, bw, bh, "Fire history (unified CSV)", ["rows -> 0.1° cell × fire_date × pass", "lags 1-3 d, 7/30 d sums, FRP,", "days since fire, burned 3-12 mo"], inp, inpe)
    node(d, x0, 132, bw, bh, "Weather", ["POWER (training) /", "forecast weather (operations)", "VPD, wind, rain sums, dry days"], inp, inpe)
    node(d, x0, 78, bw, bh, "Static + calendar", ["lat/lon, elevation, fuel type,", "fire climatology per cell-month,", "day of year, weekday, pass"], inp, inpe)
    node(d, x0, 24, bw, bh, "Latest fires (operations)", ["FIRMS VIIRS near-real-time", "for the issue day", "(history ends 2025-12-31)"], inp, inpe)
    # feature table
    fx = 150
    node(d, fx, 100, 92, 86, "Feature table", ["one row per", "cell × day × pass", "x lead time (1/3/7 d)", "only data known", "at issue time"], colors.white, ACCENT)
    for y in (209, 155, 101, 47):
        arrow(d, x0 + bw, y, fx, 143)
    # stages
    sx, sw, sh = 260, 108, 50
    node(d, sx, 176, sw, sh, "1  Occurrence", ["XGBoost classifier", "p = P(any VIIRS row)"], mdl, mdle)
    node(d, sx, 116, sw, sh, "2  Count | fire", ["XGBoost Poisson/Tweedie", "mu = rows if there is fire"], mdl, mdle)
    node(d, sx, 56, sw, sh, "3  Attributes", ["FRP mean, TI4, TI5,", "confidence l/n/h shares"], mdl, mdle)
    for y in (201, 141, 81):
        arrow(d, fx + 92, 143, sx, y)
    # outputs
    ox = 384
    ow = W - ox
    node(d, ox, 168, ow, 60, "Forecast maps", ["probability per cell", "expected rows = p × mu", "+ uncertainty band"], outc, oute)
    node(d, ox, 76, ow, 74, "Row synthesizer", ["existing, in predict.py", "rounds p × mu to rows,", "samples position, pass time,", "FRP, TI4, TI5, confidence"], outc, oute)
    node(d, ox, 6, ow, 54, "VIIRS-format CSV", ["same 15 columns", "version = FCST-XGB-1.0"], colors.white, ACCENT)
    arrow(d, sx + sw, 201, ox, 198)
    arrow(d, sx + sw, 141, ox, 120)
    arrow(d, sx + sw, 81, ox, 104)
    arrow(d, ox + ow / 2, 76, ox + ow / 2, 60)
    return d


# ---------------------------------------------------------------- page furniture
def on_page(c, doc):
    c.saveState()
    c.setFont("DV", 7.2)
    c.setFillColor(MUTED)
    c.drawString(18 * mm, 10 * mm, "Forecasting VIIRS-format fire rows: model design and options")
    c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    c.setStrokeColor(RULE)
    c.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
    c.restoreState()


# ---------------------------------------------------------------- content
def story():
    s = []
    s += [P("Forecasting VIIRS-format fire rows", TITLE),
          P("Which models to use, how the options compare, and how to use the unified fire record and weather as inputs", SUB),
          P("MODIS → VIIRS project, Australia · design proposal · 15 November 2026 · nothing in this document has been trained yet", SMALL),
          Spacer(1, 8)]

    # ------------------------------------------------ summary
    s += [box([P("<b>Recommendation</b>", ParagraphStyle("rb", parent=BODY, textColor=REC_EDGE, fontSize=10)),
               *bullets([
                   "<b>Don't generate rows directly.</b> Forecast, for every 0.1° cell, fire-day and pass, "
                   "the <i>probability of fire</i>, the <i>expected number of VIIRS rows</i> and the <i>row attributes</i>. Then "
                   "turn those into rows with the synthesizer the pipeline already has. The output keeps the same 15 VIIRS columns.",
                   "<b>Model: a two-stage (hurdle) gradient-boosted tree model (XGBoost)</b>. Stage 1 is a classifier for "
                   "fire / no fire. Stage 2 is a Poisson/Tweedie regressor for how many rows when there is fire. Stage 3 adds small "
                   "models for FRP, brightness and confidence. Train one model per lead time (1, 3 and 7 days ahead).",
                   "<b>Your idea is right.</b> Past fires from the unified and gap-filled CSVs, plus weather, are the two most useful "
                   "inputs. In the real VIIRS record, <b>79% of rows</b> are in a cell whose 3×3 neighbourhood had fire the day before, and "
                   "<b>88%</b> had fire there within the last 7 days. Weather explains when fires grow, start or stop.",
                   "Use the <b>real</b> VIIRS rows (2012–2025) as training targets. Use the model-made rows from 2000–2011 "
                   "only as history inputs, never as targets.",
                   "Before anything else, build two baselines, <b>persistence</b> and <b>climatology</b>. A model is only useful if it beats both.",
                   "Revisit deep spatio-temporal models (ConvLSTM / U-Net) only if the tree model can't capture fire spread well enough.",
               ])])]

    # ------------------------------------------------ 1 task
    s += [P("1. What \"predicting fire rows\" means", H1),
          P("A VIIRS CSV row is one 375 m pixel flagged as burning during one satellite overpass. It is not a fire: a large fire "
            "makes hundreds of rows, and the same fire reappears on every pass. Forecasting rows means answering, for a future day: "
            "<i>where</i> will VIIRS detect burning pixels, <i>how many</i>, and with what <i>brightness, FRP and confidence</i>."),
          P("Rows are a variable-length set of points per day, from zero to more than 10,000 across Australia. Standard models "
            "need a fixed-size target, so the forecast is made on a fixed grid (0.1° cell × fire-day × pass, as in the "
            "existing pipeline). Rows are produced from it in a final step. The columns are produced like this:"),
          table([
              ["Column(s)", "How a forecast row gets it"],
              ["<b>latitude, longitude</b>", "Sampled inside the cell, weighted towards yesterday's detections in that cell (fires move slowly). The existing synthesizer places rows inside MODIS pixel footprints; for forecasts, use the latest VIIRS pixels instead."],
              ["<b>acq_date, acq_time</b>", "From the S-NPP overpass time for the pass (about 01:30 and 13:30 local solar time) converted to UTC, as the synthesizer already does. Future orbits are predictable, so exact pass times could be used."],
              ["<b>frp, bright_ti4, bright_ti5</b>", "Cell means from stage 3, plus within-cell spread from the training data (already stored in synthesis_stats.joblib)."],
              ["<b>confidence</b>", "Sampled from stage 3's l/n/h shares, with the brightest pixels given 'h'."],
              ["<b>scan, track</b>", "Sampled from the observed S-NPP pixel-size distribution (they depend on the orbit, not on the fire)."],
              ["<b>daynight</b>", "From the pass (D/N)."],
              ["<b>type</b>", "0 for vegetation. Known static sources (gas flares, industry, type 2) are best handled as a fixed list, not forecast."],
              ["<b>satellite, instrument, version</b>", "Constants: N, VIIRS, and a new version tag such as <font name='DVM'>FCST-XGB-1.0</font>, so forecast rows can never be mistaken for real (2) or harmonised (ML-XGB-1.0) rows."],
          ], [0.24, 0.76]),
          Spacer(1, 4),
          P("Rows produced this way are a <i>plausible sample</i>, not exact predicted pixels. The actual forecast is the probability "
            "and expected-count grid behind them. Publish that grid alongside the rows, and evaluate on it.", SMALL)]

    # ------------------------------------------------ 2 data
    s += [P("2. Data available as inputs", H1),
          table([
              ["Source", "Covers", "Use in a forecast model"],
              ["<b>unified_viirs_format_gapfilled.csv</b> (31.0 M rows, 15 VIIRS columns)", "2000-11-01 → 2025-12-31",
               "Main fire-history input. Aggregate rows to cell × fire-day × pass (count, FRP sum, max, confidence shares). "
               "This is already done in <font name='DVM'>backend/data/parquet/daily_cells.parquet</font>, with an <i>origin</i> column (real / ml / gapfill)."],
              ["    real rows (<font name='DVM'>version = 2</font>)", "2012-01-20 → 2025-12-31", "History inputs <b>and</b> training targets."],
              ["    harmonised rows (<font name='DVM'>ML-XGB-1.0</font>)", "2000-11-01 → 2011-12-31",
               "History inputs only (for example 'burned in the last 3–12 months', long climatology). Never targets: the model would learn to copy V8's output instead of real fires."],
              ["    gap-fill rows (<font name='DVM'>ML-XGB-1.0-gapfill</font>)", "60 outage days in 2012–2025",
               "History inputs, so outages don't look like 'no fire'. Exclude those days from the training targets."],
              ["<b>unified_daily_gapfilled.csv</b>", "per day, Australia-wide", "National totals for baselines and daily-total scoring."],
              ["<b>NASA POWER daily weather</b> (data/weather/, not yet downloaded)", "2000 → 2026",
               "Weather inputs for training and back-testing. POWER arrives with a few days' delay, so live forecasts need a weather forecast source (section 4)."],
              ["<b>MODIS CSVs</b>", "2000 → 2025", "Optional. Not needed for forecasting, since the unified record already contains their information."],
          ], [0.33, 0.19, 0.48])]

    # ------------------------------------------------ 3 what the data says
    s += [P("3. What the fire record says (measured)", H1),
          P("Measured on the real VIIRS vegetation-fire rows (type 0), fire-days 2013–2025, 0.1° cells with both passes combined:", BODY),
          table([
              ["Measure", "Value", "What it means for the model"],
              ["Cells that ever burned", "51,673 of about 70,000 land cells", "Fires can happen almost anywhere: score every land cell, not only known hot spots."],
              ["Active cells per day", "mean 375, median 360, max 1,345", "About 0.5% of cells per day: a very rare, imbalanced target. Use a hurdle model and weight or sample the empty cells."],
              ["Rows per active cell-day", "mean 8.4, median 3, 99th percentile 75", "Heavy-tailed counts: use Poisson/Tweedie, not squared error."],
              ["Active cell also active the day before", "44%", "Same-cell persistence alone is a strong baseline."],
              ["Active cell with fire in its 3×3 the day before", "65% of cell-days, <b>79% of rows</b>", "Spread to neighbours matters: use neighbourhood features (3×3, 5×5)."],
              ["Active cell with fire in its 3×3 within the last 7 days", "82% of cell-days, <b>88% of rows</b>", "About 12% of rows come from truly new fires (lightning, people): weather can raise their probability but can't place them exactly."],
              ["Day-to-day correlation of the national total (log)", "1 day 0.91 · 7 days 0.69 · 30 days 0.40", "Skill falls quickly with lead time. Expect useful cell-level skill for 1–7 days; beyond that, only seasonal or regional outlooks."],
          ], [0.30, 0.25, 0.45])]

    # ------------------------------------------------ 4 inputs
    s += [P("4. Input features", H1),
          P("Every feature must be computable <b>at the time the forecast is issued</b>. This is the most important rule. The current MODIS → VIIRS "
            "harmonisation model uses same-day and <i>next-day</i> MODIS (for example <font name='DVM'>own_next_n</font> and "
            "<font name='DVM'>days_until_next</font>). That's correct for filling in the past, but it's <b>leakage</b> for forecasting, so the feature table must be rebuilt."),
          table([
              ["Group", "Features (per cell, for issue day t, target day t + lead)", "Source"],
              ["Fire history", "Rows and FRP in the cell and its 3×3 / 5×5 on t, t−1, t−2; sums over 7 and 30 days; days since last fire; burst age; neighbour fire to the N/E/S/W (spread direction)", "unified rows (real + gapfill; ml rows before 2012)"],
              ["Fuel / burn history", "Rows in the cell 3–12 months earlier (burnt fuel can't reburn soon); years since the last large burn", "unified rows, all years"],
              ["Climatology", "Mean rows per cell × calendar month (2000–2025); share of years the cell burned in that month", "unified rows"],
              ["Weather", "Max temperature, humidity, wind, VPD, rain on the target day; 7/30/90-day rain; dry-day run; temperature anomaly. These are the same 12 features as V9 (weather.py), which can be reused.", "POWER for training; forecast weather for live use"],
              ["Static", "lat, lon, elevation (or POWER surface pressure); land cover / fuel type, an optional external layer that is often a strong predictor", "fixed per cell"],
              ["Calendar", "Day of year (sin/cos), <b>weekday</b> (planned burns and land clearing follow the working week), pass (D/N), lead time", "date"],
          ], [0.15, 0.62, 0.23]),
          Spacer(1, 4),
          box([P("<b>Weather at forecast time.</b> For a 3-day forecast, only <i>forecast</i> weather is known, not the observed weather. "
                 "Training and testing with observed POWER weather (a 'perfect forecast') overstates skill. To handle this: (a) report "
                 "results with and without weather, (b) add realistic noise to the weather inputs in testing, or (c) train on archived "
                 "weather forecasts (for example ECMWF or BoM ACCESS) if they're available. For live use, yesterday's fires come from "
                 "FIRMS near-real-time VIIRS, since the unified CSV is history only and ends 2025-12-31.", SMALL)],
              bg=WARN_BG, edge=WARN_EDGE)]

    # ------------------------------------------------ 5 options
    s += [P("5. Model options compared", H1),
          P("All options predict on the cell × day × pass grid and use the same row synthesizer, except H, which generates rows directly. "
            "The ratings are for this project: about 14 years of real targets, one GPU, an existing XGBoost pipeline, and a need for explainable results."),
          table([
              ["Option", "Rare events, heavy-tail counts", "Fire spread, neighbours", "Data and compute", "Easy to explain", "Fit with current pipeline", "Verdict"],
              ["<b>A. Persistence / climatology</b>", "n/a", "persistence only", "none", "fully", "trivial", "<b>Must build</b> as the baseline to beat"],
              ["<b>B. GLM</b> (logistic + Poisson)", "fair", "only via hand-made features", "very low", "high", "easy", "Good second baseline"],
              ["<b>C. Hurdle gradient-boosted trees</b> (XGBoost / LightGBM)", "good (classifier + count model)", "good, via 3×3 / 5×5 / direction features", "low; minutes per model on GPU", "good (SHAP)", "<b>excellent</b>: same library, features, synthesizer and backend", "<b>Recommended</b>"],
              ["<b>D. Random forest</b>", "fair (squared-error leaves)", "via features", "medium (memory)", "good", "easy", "Use C instead"],
              ["<b>E. ConvLSTM / U-Net / 3-D CNN</b> on daily 0.1° rasters (~340 × 410)", "fair: needs focal / weighted loss", "<b>excellent</b>: learns spread patterns itself", "high: GPU hours, careful tuning, about 5,000 training days", "low", "new code; output feeds the same synthesizer", "Phase 2 challenger"],
              ["<b>F. Sequence models</b> (LSTM / Temporal Fusion Transformer) per cell", "fair", "weak unless neighbours are added", "high", "medium", "new code", "Not worth it here"],
              ["<b>G. Spatio-temporal point process</b> (self-exciting Hawkes, log-Gaussian Cox)", "natural for point events", "good: 'fire begets nearby fire'", "high at 1.7 M cell-days; hard to scale", "high", "new code", "Research option; too slow at national scale"],
              ["<b>H. Direct row generator</b> (GAN / diffusion over point sets)", "poor", "can look realistic", "very high", "very low", "none", "<b>Avoid</b>: looks realistic, can't be verified"],
          ], [0.19, 0.13, 0.14, 0.14, 0.11, 0.14, 0.15], font=7.3),
          Spacer(1, 6),
          P("Why C wins", H2),
          *bullets([
              "<b>The target is rare and heavy-tailed.</b> A hurdle model separates 'will there be fire?' (about 0.5% of cells) from 'how much?'. "
              "Tree models handle the mixed, partly missing inputs (weather, history, static layers) without scaling or imputation.",
              "<b>The hard part is mostly in the features, not the model.</b> Section 3 shows that fire next door yesterday and weather drive most "
              "rows. Trees exploit hand-made neighbourhood features very well. A CNN learns them itself, but needs far more data and tuning to do so.",
              "<b>The pipeline already supports it.</b> XGBoost on the GPU, the 0.1° cell keys, the weather features (V9), "
              "calibration, the row synthesizer and the backend Parquet build all exist.",
              "<b>Results can be explained</b> with feature importance and SHAP values, for example 'high because of the 30-day dry spell plus fire next door'. "
              "That matters for a fire product.",
          ]),
          P("When to try E (ConvLSTM / U-Net)", H2),
          P("Try it if C's errors cluster along fire fronts (spread speed and direction poorly captured), or if leads beyond 3 days need large-scale "
            "patterns. Train it on the same splits, and compare on the same metrics. Combining the two (averaging their probabilities) often beats both."),
          ]

    # ------------------------------------------------ 6 design
    s += [P("6. Recommended design", H1), diagram(), Spacer(1, 6),
          table([
              ["Decision", "Choice", "Why"],
              ["Unit of prediction", "0.1° cell × fire-day × pass (D/N)", "The same keys as the existing pipeline and backend, and the passes match VIIRS's two daily looks"],
              ["Lead times", "Separate models for 1, 3 and 7 days ('direct' strategy)", "Feeding a model its own forecasts (recursive) compounds errors and hides weather-forecast effects"],
              ["Training rows", "Every cell with fire in its 5×5 within the last 30 days, plus a random 5–10% of quiet land cells, weighted by 1 / sampling rate", "Keeps the table to tens of millions of rows; the weights keep totals unbiased (the same trick as EMPTY_KEEP in the current pipeline)"],
              ["Stage 1", "XGBoost binary:logistic, target = any real VIIRS row", "A calibrated probability map is the main product"],
              ["Stage 2", "XGBoost count:poisson or reg:tweedie on cells with fire, target = row count", "Heavy-tailed counts (median 3, 99th percentile 75)"],
              ["Stage 3", "Small regressors for FRP mean, TI4, TI5; a classifier for l/n/h shares", "Fills the radiometric columns; reuses synthesis_stats for within-cell spread"],
              ["Expected rows", "E[n] = p × mu, then calibrated by month and pass on validation data", "Keeps national totals unbiased"],
              ["Rows", "Stochastic rounding plus the synthesizer; optionally 20–50 random realisations for uncertainty", "Same 15-column output, which the backend and API can read unchanged"],
          ], [0.17, 0.42, 0.41])]

    # ------------------------------------------------ 7 validation
    s += [P("7. Validation", H1),
          P("<b>Split by time, never randomly.</b> Neighbouring cells and consecutive days are almost copies of each other, so random splits leak and "
            "overstate skill badly. Suggested: train 2012–2021, tune on 2022, and test once on 2023–2025, which includes the large 2023 season. For "
            "robustness, also run a rolling-origin back-test (train up to year Y, test Y + 1, for Y = 2018–2024)."),
          table([
              ["What", "Metric", "Compared against"],
              ["Will there be fire? (stage 1)", "PR-AUC (better than ROC-AUC for rare events), Brier score, reliability curve", "Persistence; climatology"],
              ["How many rows? (stage 2 + 1)", "Poisson deviance per cell; daily national total error %; monthly total error %", "Persistence of yesterday's count; climatology"],
              ["Is the map right?", "Fractions Skill Score at 0.1°, 0.5° and 1° (rewards near misses); hit rate of top-k cells", "Same"],
              ["Do the rows look like VIIRS?", "Distributions of FRP, TI4, TI5, confidence shares, day/night ratio (quantiles, KS distance)", "Real rows on the same days"],
              ["By lead and season", "All of the above for leads 1, 3 and 7 days, by month and region", "Skill should fall with lead; check where it reaches baseline"],
          ], [0.22, 0.50, 0.28])]

    # ------------------------------------------------ 8 risks
    s += [P("8. Limits and risks", H1),
          *bullets([
              "<b>New ignitions are largely unpredictable in place.</b> About 12% of rows have no fire nearby in the previous week (section 3). Weather and "
              "calendar raise or lower their probability, but the forecast will spread them as low probabilities over many cells.",
              "<b>Synthetic history.</b> The 2000–2011 rows are V8 model output. Use them for long-memory inputs only, and check that adding them actually "
              "improves results on the 2023–2025 test.",
              "<b>Cloud and smoke.</b> No row doesn't mean no fire. Heavy rain days remove detections, not only fires. The model learns 'detectable fire', "
              "which is the right target for VIIRS-format output, but it should be described that way.",
              "<b>Static sources.</b> Exclude type 2 (gas flares, industry) from training and add it back as a fixed layer. Otherwise it inflates scores with "
              "trivially predictable pixels.",
              "<b>Satellite continuity.</b> S-NPP is ageing. Live inputs may need NOAA-20/21 VIIRS (same 375 m product). Check that they're consistent before mixing them.",
              "<b>Weather forecast quality</b> limits leads of 3 days or more. Report how much skill comes from weather versus fire history.",
          ])]

    # ------------------------------------------------ 9 roadmap
    s += [P("9. Roadmap", H1),
          table([
              ["Phase", "Work", "Outcome"],
              ["0. Baselines", "Persistence and climatology forecasts for leads 1/3/7 days on the 2023–2025 test; the metric code from section 7", "The numbers to beat"],
              ["1. History-only model", "Leak-free feature table from daily_cells.parquet; hurdle XGBoost for lead 1", "Skill from fire history alone"],
              ["2. Add weather", "Download POWER (V9 data); add the 12 weather features; leads 3 and 7", "How much weather adds, per lead"],
              ["3. Rows", "Stage 3 attributes; synthesizer with version FCST-XGB-1.0; row-distribution checks", "Forecast CSVs in the VIIRS format"],
              ["4. Challenger (optional)", "ConvLSTM / U-Net on the same splits; ensemble with phase 2", "Keep it only if it clearly wins"],
              ["5. Live use", "FIRMS NRT VIIRS + forecast weather feeds; daily run; backend layer for the forecast maps", "Operational daily forecast"],
          ], [0.18, 0.55, 0.27]),
          Spacer(1, 8),
          P("Source of the measured numbers: <font name='DVM'>backend/data/parquet/daily_cells.parquet</font>, origin = real, type = 0, "
            "fire-days 2013-01-01 to 2025-12-31, both passes combined, measured 15 November 2026. 'About 70,000 land cells' is an estimate "
            "(Australia's land area ÷ the area of a 0.1° cell).", SMALL)]
    return s


def main():
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="Forecasting VIIRS-format fire rows",
                            author="MODIS-VIIRS project", subject="Fire row forecasting: model design and options")
    doc.build(story(), onFirstPage=on_page, onLaterPages=on_page)
    print(f"written {OUT} ({OUT.stat().st_size / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()
