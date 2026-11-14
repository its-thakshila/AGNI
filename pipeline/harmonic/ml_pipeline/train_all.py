"""One-command training run: checks -> 4-year memory test -> full training -> predictions
-> VIIRS gap fill -> PDF -> backend Parquet build (if backend/scripts exists).

Run it by double-clicking train_all.bat, or from PowerShell / Command Prompt in this folder:
    python train_all.py                 # everything
    python train_all.py --skip-test     # skip the 4-year memory test
    python train_all.py --daily-only    # combined record without pixel rows (faster, much smaller)
    python train_all.py --no-predict    # stop after training
    python train_all.py --no-backend    # skip step 7 (Parquet files for the backend)
    python train_all.py --with-drift-check   # + strict per-year accuracy and drift report (about 30 min extra)

Every step runs as its own Python process (memory is fully released between steps), its output
is shown on screen and saved to output/run_<date>_<time>.log. The laptop is kept awake while it runs.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
OUT.mkdir(exist_ok=True)
LOG = OUT / f"run_{dt.datetime.now():%Y%m%d_%H%M}.log"
BACKEND_SCRIPTS = HERE.parent / "backend" / "scripts"     # Parquet build for the API (step 7)
PY = sys.executable


def say(msg: str = "") -> None:
    print(msg, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def banner(n: int | str, title: str, eta: str) -> None:
    say("")
    say("=" * 78)
    say(f" STEP {n}: {title}   (about {eta})   started {dt.datetime.now():%H:%M}")
    say("=" * 78)


def run(args: list[str], step: str) -> list[str]:
    """Run `python <args>` here, echo + log every line, stop the whole run if it fails."""
    lines: list[str] = []
    t0 = time.time()
    p = subprocess.Popen([PY, "-u", *args], cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    for line in p.stdout:
        line = line.rstrip()
        lines.append(line)
        say("   " + line)
    p.wait()
    if p.returncode != 0:
        say("")
        say(f"!! {step} FAILED (exit code {p.returncode}) after {(time.time() - t0) / 60:.1f} min.")
        say(f"!! The full output is in {LOG}")
        say("!! See 'If something goes wrong' in the training guide, fix it, then run this script again.")
        sys.exit(1)
    say(f"   -> {step} done in {(time.time() - t0) / 60:.1f} min")
    return lines


def available_gb() -> float | None:
    try:
        import psutil
        return psutil.virtual_memory().available / 1e9
    except ImportError:
        return None


def keep_awake(on: bool) -> None:
    """Ask Windows not to sleep while the run is going (released automatically when it ends)."""
    if sys.platform == "win32":
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


def finished_line(lines: list[str]) -> tuple[float | None, float | None]:
    """(minutes, peak GB) from train.py's last line."""
    for line in reversed(lines):
        m = re.search(r"FINISHED: .* in ([\d.]+) min, peak memory ([\d.]+) GB", line)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None, None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-test", action="store_true", help="skip the 4-year memory/time test")
    ap.add_argument("--daily-only", action="store_true", help="combined record without pixel rows")
    ap.add_argument("--no-predict", action="store_true", help="stop after training")
    ap.add_argument("--no-backend", action="store_true", help="skip step 7 (backend Parquet build)")
    ap.add_argument("--with-drift-check", action="store_true",
                    help="after training, run drift_check.py + drift_report.py (about 30 min extra)")
    a = ap.parse_args()

    t_start = time.time()
    keep_awake(True)
    say(f"MODIS -> VIIRS training run, {dt.datetime.now():%Y-%m-%d %H:%M}")
    say(f"Python: {PY}")
    say(f"Log file: {LOG}")

    # ---------------------------------------------------------------- 1. checks
    banner(1, "Install / check packages", "1-2 min")
    run(["-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", "requirements.txt"], "package install")
    run(["-c", "import xgboost, config; config.check_train_files(); "
               "print('XGBoost', xgboost.__version__, '| GPU (CUDA) build:', xgboost.build_info()['USE_CUDA']); "
               "print('training years', list(config.TRAIN_YEARS)); print('all MODIS years', config.modis_years())"],
        "GPU and data check")
    if shutil.which("nvidia-smi"):
        run(["-c", "import subprocess; print(subprocess.run(['nvidia-smi', '--query-gpu=name,memory.used,memory.total',"
                   " '--format=csv,noheader'], capture_output=True, text=True).stdout.strip())"], "GPU status")
    free = available_gb()
    if free is not None:
        say(f"   Free memory now: {free:.1f} GB")
        if free < 4.5:
            say("   NOTE: less than 4.5 GB free. Close VS Code, browsers and other apps for a faster full run.")

    # ---------------------------------------------------------------- 2. backup
    models, backup = OUT / "models", OUT / "models_backup_before_full_run"
    if (models / "hgb_modis_to_viirs.joblib").exists() and not backup.exists():
        shutil.copytree(models, backup)
        say(f"\nBacked up the current model to {backup}")
    # keep every trained model: dated copy (named after its training time) before it can be overwritten
    report = models / "training_report.json"
    if (models / "hgb_modis_to_viirs.joblib").exists() and report.exists():
        stamp = dt.datetime.fromtimestamp(report.stat().st_mtime).strftime("%Y%m%d_%H%M")
        dated = OUT / "archive" / f"models_{stamp}"
        if not dated.exists():
            shutil.copytree(models, dated, ignore=shutil.ignore_patterns("*.pkl"))   # skip the large inspection table
            say(f"Kept a copy of the current model in {dated}")

    # ---------------------------------------------------------------- 3. 4-year test
    if not a.skip_test:
        banner(2, "4-year test (2012-2015) to measure memory and time", "15 min")
        lines = run(["train.py", "--years", "2012-2015", "--out", str(OUT / "test_4yr")], "4-year test")
        t4, p4 = finished_line(lines)
        if p4 is not None:
            est_peak, est_min = 1.5 + 3.1 * (p4 - 1.5), 3 * t4
            free = available_gb()
            say("")
            say(f"   4-year test: {t4:.1f} min, peak {p4:.2f} GB")
            say(f"   Estimate for all 14 years: peak about {est_peak:.1f} GB, about {est_min:.0f} min")
            if free is not None:
                say(f"   Free memory now: {free:.1f} GB")
                if est_peak > free - 0.5:
                    say("   WARNING: the full run may not fit in free memory. It will still run, but Windows")
                    say("   will page to disk and it can take much longer. To avoid that, close programs now")
                    say("   (the next step starts in 60 s), or stop with Ctrl+C and run:")
                    say("       python train_all.py --skip-test   after freeing memory, or")
                    say("       python train.py --empty-keep 0.1  to use less memory")
                    time.sleep(60)
                else:
                    say("   OK: it fits. Starting the full run.")

    # ---------------------------------------------------------------- 4. full training
    banner(3, "Train on all years (config.TRAIN_YEARS) -> output/models", "45 min")
    lines = run(["train.py"], "full training")
    run(["-c", "import json; r=json.load(open('output/models/training_report.json')); "
               "s=r['full']['cv']['hgb_calibrated']; t=r['terra_only']['cv']['hgb_calibrated']; "
               "print('model', r.get('model'), '| years', r['full']['years'][0], '-', r['full']['years'][-1]); "
               "print('daily R2', s['daily_r2'], '| monthly R2', s['monthly_r2'], '| cell R2', s['cell_r2'], "
               "'| bias %', s['total_bias_pct']); print('Terra-only daily R2', t['daily_r2'])"],
        "results summary")

    # ---------------------------------------------------------------- 3b. optional: strict per-year accuracy
    if a.with_drift_check:
        banner("3b", "Per-year accuracy + MODIS orbit-drift check (uncertainty values for the backend)", "30 min")
        run(["drift_check.py"], "drift check")
        run(["drift_report.py"], "drift report PDF")

    # ---------------------------------------------------------------- 5. predictions + PDF
    if not a.no_predict:
        banner(4, "Predict MODIS-only years + build the combined 2000-2025 record", "20 min")
        if a.daily_only:
            run(["-c", "import logging, sys, unify; logging.basicConfig(level=logging.INFO, stream=sys.stdout, "
                       "format='%(asctime)s %(name)s: %(message)s', datefmt='%H:%M:%S'); unify.main(write_rows=False)"],
                "combined record (daily)")
        else:
            run(["unify.py"], "combined record")
        banner(5, "Fill VIIRS date gaps (satellite outages) with model predictions", "4 min")
        run(["gapfill.py", "--daily-only"] if a.daily_only else ["gapfill.py"], "VIIRS gap fill")
        banner(6, "Refresh analysis charts and rebuild the project PDF", "2 min")
        run(["analyze_datasets.py"], "dataset analysis")
        run(["build_pdf.py"], "PDF")

    # ---------------------------------------------------------------- 7. backend data (Parquet)
    backend_ran = False
    if a.no_predict or a.no_backend:
        pass
    elif a.daily_only:
        say("\n(step 7 skipped: --daily-only does not write unified_viirs_format_gapfilled.csv)")
    elif not (BACKEND_SCRIPTS / "build_parquet.py").exists():
        say(f"\n(step 7 skipped: no backend build scripts in {BACKEND_SCRIPTS})")
    else:
        banner(7, "Split the combined CSV into Parquet files for the backend", "2-5 min")
        run([str(BACKEND_SCRIPTS / "build_parquet.py")], "backend Parquet rows")
        run([str(BACKEND_SCRIPTS / "build_aggregates.py")], "backend summary tables")
        run([str(BACKEND_SCRIPTS / "verify_build.py"), "--skip-csv-count"], "backend checks")
        backend_ran = True

    keep_awake(False)
    say("")
    say("=" * 78)
    say(f" ALL DONE in {(time.time() - t_start) / 60:.0f} min")
    say(f"   models:       {OUT / 'models'}")
    if not a.no_predict:
        say(f"   predictions:  {OUT / 'predictions'}")
        say(f"   combined:     {OUT / 'unified'}   (*_gapfilled.csv = VIIRS outage days filled)")
        say(f"   PDF:          {HERE.parent / 'MODIS_VIIRS_ML_Architecture_v2.pdf'}")
    if backend_ran:
        say(f"   backend data: {HERE.parent / 'backend' / 'data'}")
    if a.with_drift_check:
        say(f"   drift report: {HERE.parent / 'MODIS_Orbit_Drift_Report.pdf'}")
    say(f"   log:          {LOG}")
    say("=" * 78)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        keep_awake(False)
        say("\nStopped with Ctrl+C. If it stopped during the full training, output\\models may hold a mix of")
        say("old and new files: copy output\\models_backup_before_full_run back over it, or run this script again.")
        sys.exit(130)
