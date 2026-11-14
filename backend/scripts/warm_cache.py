"""Fill the AGNI FIRMS cache without starting the API server."""

import argparse
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.contracts import COVERAGE, LOCAL_END
from app.firms import FirmsSource


def main():
    today = datetime.now(UTC).date()
    first_recent = LOCAL_END + timedelta(days=1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=first_recent)
    parser.add_argument("--end", type=date.fromisoformat, default=today)
    args = parser.parse_args()
    key = os.getenv("FIRMS_MAP_KEY")
    if not key:
        parser.error("Set FIRMS_MAP_KEY in the environment first.")
    if not first_recent <= args.start <= args.end <= today:
        parser.error("Choose a range from 2026-01-01 through today's UTC date.")

    default_cache = Path(__file__).resolve().parents[1] / "data" / "firms.duckdb"
    firms = FirmsSource(key, Path(os.getenv("AGNI_FIRMS_CACHE", default_cache)))
    incomplete = 0
    start = args.start
    while start <= args.end:
        end = min(start + timedelta(days=6), args.end)
        _, status = firms.query(COVERAGE, start, end, [0, 2, 3], "daily", today)
        incomplete += sum(state != "full" for state, _, _ in status.values())
        print(f"Checked {start} through {end}.", flush=True)
        start = end + timedelta(days=1)
    print(f"{incomplete} fire-days are partial or missing.")


if __name__ == "__main__":
    main()
