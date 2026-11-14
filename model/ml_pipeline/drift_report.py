"""Render the orbit-drift check (drift_check.py results) as a PDF.

    python drift_check.py     # ~30 min, writes output/drift/drift_results.json
    python drift_report.py    # ->  ../MODIS_Orbit_Drift_Report.pdf
"""
from __future__ import annotations

import json

import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

import config
from build_pdf import INK2, NAVY, P, callout, fig, section, table
from vizstyle import AQUA, GRID, INK, INK_2, TERRA, VIIRS, plt, save

OUT_DIR = config.OUT / "drift"
OUT_PDF = config.ROOT / "MODIS_Orbit_Drift_Report.pdf"
R = json.loads((OUT_DIR / "drift_results.json").read_text())
STABLE, DRIFT_FROM = R["stable_years"], 2022
OV = {o["year"]: o for o in R["overpass"]}
YEARS = sorted(int(y) for y in R["full"]["loyo"])


def hm(h):
    return f"{int(h):d}:{int(round((h % 1) * 60)):02d}"


def loyo(mode, key, years):
    return [R[mode]["loyo"][str(y)][key] for y in years]


def avg(mode, key, years, absval=False):
    v = np.array(loyo(mode, key, years), float)
    return float(np.mean(np.abs(v) if absval else v))


def shade(ax):
    ax.axvspan(DRIFT_FROM - 0.5, YEARS[-1] + 0.5, color=GRID, alpha=0.55, lw=0, zorder=0)
    ax.text(DRIFT_FROM - 0.35, 0.97, "orbit drift", transform=ax.get_xaxis_transform(),
            fontsize=7.5, color=INK_2, va="top")


def endlabel(ax, x, y, text, color=INK_2, dy=0):
    ax.annotate(text, (x, y), xytext=(6, dy), textcoords="offset points", fontsize=8, color=color, va="center")


# ---------------------------------------------------------------- figures
def figures():
    yrs = sorted(OV)
    # 1. overpass time
    f, ax = plt.subplots(figsize=(7.2, 3.1))
    for key, name, col in (("aqua_lst", "Aqua", AQUA), ("viirs_lst", "VIIRS (S-NPP)", VIIRS), ("terra_lst", "Terra", TERRA)):
        xs = [y for y in yrs if key in OV[y]]
        ys = [OV[y][key] for y in xs]
        ax.plot(xs, ys, "-o", color=col, ms=4, label=name)
        endlabel(ax, xs[-1], ys[-1], f"{name} {hm(ys[-1])}", INK, dy=-6 if key == "viirs_lst" else 0)
    shade(ax)
    ax.set_yticks(range(9, 16)); ax.set_yticklabels([f"{h}:00" for h in range(9, 16)])
    ax.set_ylabel("median daytime overpass (local solar time)")
    ax.set_xlim(yrs[0] - 0.5, yrs[-1] + 3.2); ax.set_xticks(range(2000, 2026, 5))
    ax.set_title("Daytime overpass time over Australia, by year")
    ax.legend(loc="center left", ncol=3, bbox_to_anchor=(0.0, 0.62))
    save(f, OUT_DIR / "fig_overpass.png")

    # 2. leave-one-year-out daily error
    f, ax = plt.subplots(figsize=(7.2, 3.0))
    for mode, name, col in (("terra_only", "Terra-only model", TERRA), ("full", "Full model (Terra+Aqua)", VIIRS)):
        ys = loyo(mode, "daily_mae_pct", YEARS)
        ax.plot(YEARS, ys, "-o", color=col, ms=4, label=name)
        endlabel(ax, YEARS[-1], ys[-1], name.split(" (")[0], INK)
    shade(ax)
    ax.set_ylim(0, None); ax.set_ylabel("daily count error (% of mean day)")
    ax.set_xlim(YEARS[0] - 0.5, YEARS[-1] + 2.6); ax.set_xticks(YEARS[::2])
    ax.set_title("Out-of-sample daily error, each year predicted by a model that never saw it")
    ax.legend(loc="lower left")
    save(f, OUT_DIR / "fig_loyo_error.png")

    # 3. day / night bias, small multiples
    f, axs = plt.subplots(1, 2, figsize=(7.2, 3.0), sharey=True)
    for ax, mode, title in zip(axs, ("full", "terra_only"), ("Full model", "Terra-only model")):
        ax.axhline(0, color=INK_2, lw=0.8)
        d, n = loyo(mode, "bias_day_pct", YEARS), loyo(mode, "bias_night_pct", YEARS)
        ax.plot(YEARS, d, "-o", color=VIIRS, ms=4, label="day passes")
        ax.plot(YEARS, n, "--s", color=INK_2, ms=4, label="night passes")
        shade(ax)
        ax.set_title(title); ax.set_xlim(YEARS[0] - 0.5, YEARS[-1] + 0.5)
        ax.set_xticks(YEARS[::3])
    axs[0].set_ylabel("predicted vs real (%)")
    axs[0].legend(loc="lower left")
    f.suptitle("Bias of day and night counts by year (+ = over-predicted)", fontsize=11, fontweight="bold", color=INK)
    save(f, OUT_DIR / "fig_daynight_bias.png")

    # 4. raw VIIRS / MODIS daytime ratio
    f, ax = plt.subplots(figsize=(7.2, 2.8))
    xs = [y for y in yrs if "ratio_viirs_terra" in OV[y]]
    for key, name, col in (("ratio_viirs_terra", "VIIRS per Terra detection", TERRA),
                           ("ratio_viirs_aqua", "VIIRS per Aqua detection", AQUA)):
        ys = [OV[y][key] for y in xs]
        ax.plot(xs, ys, "-o", color=col, ms=4, label=name)
        endlabel(ax, xs[-1], ys[-1], name.split(" per ")[1].replace(" detection", ""), INK)
    shade(ax)
    ax.set_ylim(0, None); ax.set_ylabel("VIIRS rows per MODIS row")
    ax.set_xlim(xs[0] - 0.5, xs[-1] + 1.6); ax.set_xticks(xs[::2])
    ax.set_title("Raw data: daytime VIIRS detections per MODIS detection")
    ax.legend(loc="lower left")
    save(f, OUT_DIR / "fig_raw_ratio.png")


# ---------------------------------------------------------------- content
def f1(x):
    return f"{x:.1f}"


def sgn(x):
    return f"{x:+.1f}"


def build():
    figures()
    st, dr = [y for y in YEARS if y < DRIFT_FROM], [y for y in YEARS if y >= DRIFT_FROM]
    s = {m: {"st_d": avg(m, "daily_mae_pct", st), "dr_d": avg(m, "daily_mae_pct", dr),
             "st_m": avg(m, "monthly_mae_pct", st), "dr_m": avg(m, "monthly_mae_pct", dr),
             "st_b": avg(m, "total_bias_pct", st, True), "dr_b": avg(m, "total_bias_pct", dr, True),
             "all_d": avg(m, "daily_mae_pct", YEARS), "all_m": avg(m, "monthly_mae_pct", YEARS)}
         for m in ("full", "terra_only")}
    bc = {m: R[m]["backcast"] for m in ("full", "terra_only")}
    fw = {m: R[m]["forward"] for m in ("full", "terra_only")}
    early = [OV[y] for y in range(2003, 2022)]
    t_nom, a_nom = np.mean([o["terra_lst"] for o in early]), np.mean([o["aqua_lst"] for o in early])
    t25, a25 = OV[2025]["terra_lst"], OV[2025]["aqua_lst"]
    nb24 = R["full"]["loyo"]["2024"]["bias_night_pct"]

    E = []
    E += [Paragraph("<b>MODIS orbit drift: does it affect the MODIS→VIIRS model?</b>", ParagraphStyle("title", fontName="DVB", fontSize=19, leading=24, textColor=NAVY)),
          Spacer(1, 6),
          P(f"<font color='#4a4a4a'>Check of the trained models against 14 years of real VIIRS data (2012–2025). "
            f"Model: {R['model']}. Results from <i>drift_check.py</i>.</font>"), Spacer(1, 10)]

    E += section(1, "Summary")
    E += [callout(
        f"<b>The 2000–2011 predictions are not affected.</b> Training with or without the drift years gives the same "
        f"accuracy on the earliest test years (daily error {f1(bc['full']['stable_2014_2021']['daily_mae_pct'])}% vs "
        f"{f1(bc['full']['all_2014_2025']['daily_mae_pct'])}%), and in 2000–2021 MODIS passed at its normal times, "
        f"which the model learned from 2012–2021.", "green"), Spacer(1, 6),
          callout(
        f"<b>Predictions made from 2022–2025 MODIS are less accurate.</b> Terra now passes about "
        f"{f1((t_nom - t25) * 60 / 60)} h earlier and Aqua about {f1((a25 - a_nom))} h later than before. "
        f"The model has no time-of-day input, so it cannot correct for this: daily error rises from "
        f"{f1(s['full']['st_d'])}% to {f1(s['full']['dr_d'])}% and monthly error from {f1(s['full']['st_m'])}% to "
        f"{f1(s['full']['dr_m'])}%. The day/night split is hit hardest (night counts {sgn(nb24)}% in 2024). "
        f"In the record this only concerns the 39 gap-filled VIIRS outage days in 2022–2025.", "amber"),
          Spacer(1, 8)]
    E += [table([["", "2012–2021 (normal overpass)", "2022–2025 (drifting)"],
                 ["Full model: daily error", f"±{f1(s['full']['st_d'])}%", f"±{f1(s['full']['dr_d'])}%"],
                 ["Full model: monthly error", f"±{f1(s['full']['st_m'])}%", f"±{f1(s['full']['dr_m'])}%"],
                 ["Full model: year total off by (mean)", f"{f1(s['full']['st_b'])}%", f"{f1(s['full']['dr_b'])}%"],
                 ["Terra-only model: daily error", f"±{f1(s['terra_only']['st_d'])}%", f"±{f1(s['terra_only']['dr_d'])}%"],
                 ["Terra-only model: monthly error", f"±{f1(s['terra_only']['st_m'])}%", f"±{f1(s['terra_only']['dr_m'])}%"]],
                [0.46, 0.27, 0.27]), Spacer(1, 4),
          P("<font size=7.8 color='#4a4a4a'>Daily error = mean |predicted − real| of the Australia-wide daily VIIRS "
            "detection count, as % of the mean day. Out-of-sample: each year is predicted by a model trained on the "
            "other 13 years.</font>")]

    E += [PageBreak()] + section(2, "What drifted")
    E += [P(f"Terra and Aqua ran out of fuel for orbit corrections, so their equator-crossing times have been drifting. "
            f"The median daytime overpass over Australia, measured directly from the detection times in the raw "
            f"CSVs, was stable at about <b>{hm(t_nom)}</b> (Terra) and <b>{hm(a_nom)}</b> (Aqua) until 2021. "
            f"By 2025 it was <b>{hm(t25)}</b> and <b>{hm(a25)}</b>. VIIRS (Suomi-NPP), the target the model "
            f"predicts, stayed at about 1:40 pm throughout."), Spacer(1, 6),
          fig(OUT_DIR / "fig_overpass.png", "Figure 1. Median local solar time of daytime detections per year. "
              "Shaded: years with drift."),
          P("Fires are strongly tied to time of day: activity in Australia peaks in the early afternoon. "
            "A Terra pass at 9 am sees fewer and smaller fires than one at 10:20 am, and Aqua at 3 pm sees the "
            "fire after its peak. So the same MODIS count means a different number of VIIRS fires once the "
            "orbits drift."), Spacer(1, 6),
          fig(OUT_DIR / "fig_raw_ratio.png", "Figure 2. Raw data, no model: VIIRS daytime detections per MODIS "
              "daytime detection. The ratio also moves with each year's fire regime, so on its own it is only a "
              "rough indicator; the model tests below control for that.", 0.95)]

    E += [PageBreak()] + section(3, "How the model was tested")
    E += [table([["Test", "Trained on", "Tested on", "Question it answers"],
                 ["Leave-one-year-out", "the other 13 years", "each year 2012–2025",
                  "How accurate is the model on a year it has never seen?"],
                 ["Backcast", "2014–2021 (stable) vs 2014–2025 (with drift)", "2012–2013",
                  "Does training on drift years hurt predictions for the past (2000–2011)?"],
                 ["Forward", "2012–2021 (stable)", "2023–2025",
                  "How much did the MODIS→VIIRS relation change with the drift?"]],
                [0.17, 0.27, 0.17, 0.39]), Spacer(1, 6),
          P("Every test was run for both the full model (Terra+Aqua, used from July 2002) and the Terra-only model "
            "(used from November 2000 to July 2002). The scores use the model's raw output (the production model "
            "adds calibration factors of 0.98–1.02, which do not change any conclusion).")]

    E += section(4, "Accuracy by year")
    E += [fig(OUT_DIR / "fig_loyo_error.png", "Figure 3. Leave-one-year-out daily error. Shaded: years with drift.")]
    rows = [["Year", "Full: daily R²", "Full: daily err.", "Full: monthly err.", "Full: year total",
             "Terra-only: daily err.", "Terra-only: year total"]]
    for y in YEARS:
        a, b = R["full"]["loyo"][str(y)], R["terra_only"]["loyo"][str(y)]
        rows.append([f"<b>{y}</b>" if y >= DRIFT_FROM else str(y), f"{a['daily_r2']:.3f}", f"±{f1(a['daily_mae_pct'])}%",
                     f"±{f1(a['monthly_mae_pct'])}%", f"{sgn(a['total_bias_pct'])}%",
                     f"±{f1(b['daily_mae_pct'])}%", f"{sgn(b['total_bias_pct'])}%"])
    rows.append(["<b>Mean</b>", "", f"<b>±{f1(s['full']['all_d'])}%</b>", f"<b>±{f1(s['full']['all_m'])}%</b>", "",
                 f"<b>±{f1(s['terra_only']['all_d'])}%</b>", ""])
    E += [table(rows, [0.09, 0.13, 0.14, 0.15, 0.14, 0.18, 0.17]), Spacer(1, 6),
          P("2022 is the weakest year although Terra had drifted only ~10 minutes, so part of its error has other "
            "causes (a low-fire year and 19 days without VIIRS data). 2020 is also weaker with no drift at all. "
            "The clearest drift signal is the day/night split in 2024–2025, below.")]

    E += [PageBreak()] + section(5, "Day versus night")
    E += [fig(OUT_DIR / "fig_daynight_bias.png", "Figure 4. Bias of predicted day-pass and night-pass counts. "
              "Shaded: years with drift."),
          P(f"Before the drift, day and night biases stay mostly within ±6% (Terra-only: ±11%) and partly cancel. "
            f"In 2024–2025 the night passes are over-predicted by {f1(R['full']['loyo']['2024']['bias_night_pct'])}% "
            f"and {f1(R['full']['loyo']['2025']['bias_night_pct'])}% (Terra-only up to "
            f"{f1(max(loyo('terra_only', 'bias_night_pct', dr)))}%), and in 2025 the day passes are under-predicted "
            f"by {f1(-R['full']['loyo']['2025']['bias_day_pct'])}%. The model is moving fires between day and night. "
            f"Daily totals hide most of this because the two errors have opposite signs.")]

    E += section(6, "Does training on drift years hurt 2000–2011?")
    rows = [["Model", "Trained on", "Daily R²", "Daily err.", "Monthly err.", "Total bias", "Day / night bias"]]
    for m, mn in (("full", "Full"), ("terra_only", "Terra-only")):
        for k, kn in (("stable_2014_2021", "2014–2021 (stable)"), ("all_2014_2025", "2014–2025 (with drift)")):
            b = bc[m][k]
            rows.append([mn, kn, f"{b['daily_r2']:.3f}", f"±{f1(b['daily_mae_pct'])}%", f"±{f1(b['monthly_mae_pct'])}%",
                         f"{sgn(b['total_bias_pct'])}%", f"{sgn(b['bias_day_pct'])}% / {sgn(b['bias_night_pct'])}%"])
    E += [table(rows, [0.12, 0.22, 0.1, 0.12, 0.13, 0.12, 0.19]), Spacer(1, 6),
          callout("<b>No.</b> Tested on 2012–2013, the years closest to the historical period, the two training sets "
                  "give the same accuracy; with the drift years included the total bias is even slightly smaller. "
                  "The current model is fine for 2000–2011 and does not need retraining for that purpose.", "green")]

    E += [PageBreak()] + section(7, "Can the model predict the drift years?")
    rows = [["Year", "Model", "Forward: trained 2012–2021", "Leave-one-year-out: trained on all other years"]]
    for y in (2023, 2024, 2025):
        for m, mn in (("full", "Full"), ("terra_only", "Terra-only")):
            a, b = fw[m][str(y)], R[m]["loyo"][str(y)]
            rows.append([str(y), mn, f"±{f1(a['daily_mae_pct'])}%  (day {sgn(a['bias_day_pct'])}%, night {sgn(a['bias_night_pct'])}%)",
                         f"±{f1(b['daily_mae_pct'])}%  (day {sgn(b['bias_day_pct'])}%, night {sgn(b['bias_night_pct'])}%)"])
    E += [table(rows, [0.08, 0.14, 0.36, 0.42]), Spacer(1, 6),
          P("Daily error, with day and night bias in brackets. The two columns are almost identical: seeing other "
            "drift years in training does <b>not</b> help the model predict a drift year. This is expected because "
            "the model has no input that tells it the overpass time changed, so it cannot learn the new relation. "
            "Having the drift years in training is still harmless for the historical years (section 6).")]

    E += section(8, "What this means for the output files")
    E += [table([["Part of the record", "Affected?", "Notes"],
                 ["2000 – Jun 2002 (Terra-only predictions)", "No", f"Normal Terra overpass. Expect ±{f1(s['terra_only']['st_d'])}% daily error."],
                 ["Jul 2002 – 2011 (full-model predictions)", "No", f"Normal overpass. Expect ±{f1(s['full']['st_d'])}% daily error."],
                 ["2012 – 2025 real VIIRS days", "No", "Real satellite data, no model involved."],
                 ["21 gap-filled VIIRS outage days in 2012", "No", "2012 overpass is normal."],
                 ["Gap-filled days in 2022–2025 (39 days)", "Yes",
                  f"About ±{f1(s['full']['dr_d'])}% daily error. Use the daily total; do not rely on the day/night split. "
                  "Only 39 of ~1,460 days, so annual totals change very little."]],
                [0.33, 0.12, 0.55]), Spacer(1, 8)]
    E += [P("<b>If the 2022–2025 gap-fill days need to be more accurate:</b> add each satellite's overpass time as a "
            "model input. The orbits are tracked, so the time is known for every day and cell, including cells "
            "where MODIS saw nothing. Tree models only fail outside the range they were trained on, and 2000–2011 "
            "values fall inside the well-covered 2012–2021 range, so this input would not harm the historical "
            "predictions."), Spacer(1, 6),
          P("<font size=7.8 color='#4a4a4a'>Reproduce: <i>python drift_check.py</i> (about 30 min with a GPU) then "
            "<i>python drift_report.py</i>. Neither changes any model or output file.</font>")]

    doc = SimpleDocTemplate(str(OUT_PDF), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm, title="MODIS orbit drift check")

    def footer(canvas, d):
        canvas.saveState(); canvas.setFont("DV", 7); canvas.setFillColor(INK2)
        canvas.drawCentredString(A4[0] / 2, 10 * mm, f"MODIS → VIIRS harmonization · orbit drift check · page {d.page}")
        canvas.restoreState()

    doc.build(E, onFirstPage=footer, onLaterPages=footer)
    print("written", OUT_PDF)


if __name__ == "__main__":
    build()
