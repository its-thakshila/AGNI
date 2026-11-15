"""Run the whole forecast pipeline: attributes -> tables -> train -> evaluate -> forecast.

    python run_pipeline.py                     # everything, history-only model (phase 1 of the PDF)
    python run_pipeline.py --weather           # + NASA POWER weather of the issue day (phase 2; data/weather)
    python run_pipeline.py --weather target    # weather of the target day (perfect-forecast upper bound)
    python run_pipeline.py --from train        # skip the steps before 'train'
    python run_pipeline.py --name v1           # label stored with the models / archive folder

Earlier models are moved to models_archive/ by train_forecast.py, never overwritten.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

import fcst_config as fc

STEPS = ["attributes", "tables", "train", "evaluate", "forecast"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", choices=STEPS, default=STEPS[0])
    ap.add_argument("--to", dest="stop", choices=STEPS, default=STEPS[-1])
    ap.add_argument("--weather", nargs="?", const="issue", choices=["issue", "target"], default=fc.USE_WEATHER,
                    help="NASA POWER weather of the issue day (default, usable live) or the target day (upper bound)")
    ap.add_argument("--name", default="")
    ap.add_argument("--issue", help="forecast issue day (default: last day of the history)")
    a = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(errors="replace")
    fc.OUT.mkdir(parents=True, exist_ok=True)
    log_file = fc.OUT / f"pipeline_{time.strftime('%Y%m%d_%H%M')}.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")])
    log = logging.getLogger("pipeline")
    todo = STEPS[STEPS.index(a.start): STEPS.index(a.stop) + 1]
    t0 = time.perf_counter()
    for step in todo:
        log.info("==== %s", step)
        if step == "attributes":
            import attributes
            attributes.build()
        elif step == "tables":
            import build_dataset
            build_dataset.run(fc.LEADS, a.weather)
        elif step == "train":
            import train_forecast
            train_forecast.main(None, a.name)
        elif step == "evaluate":
            import evaluate
            evaluate.run()
        elif step == "forecast":
            import forecast
            forecast.run(a.issue)
    log.info("pipeline done in %.1f min (log: %s)", (time.perf_counter() - t0) / 60, log_file)


if __name__ == "__main__":
    main()
