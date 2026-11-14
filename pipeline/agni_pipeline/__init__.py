"""AGNI data pipeline: builds the browser data files and keeps them up to date.

Every dataset lives under one storage root (a local folder or an R2 prefix):

    manifest.json              what exists: record dates, live days and their sources
    hist/cells.parquet         fire-day x 0.1 deg cell aggregates from the processed data
    hist/cells_month.parquet   the same, summed per month (fast calendar and trends)
    hist/day_status.parquet    per fire-day coverage: full / partial / none
    hist/rows/YYYY.parquet     individual detections, one file per year (CSV downloads);
                               manifest.row_years lists them with their versions
    live/cells/YYYY-MM.parquet days after the CSV, added by process_days
    live/rows/YYYY-MM.parquet
    pending/YYYY-MM-DD         markers written by the Worker while a day is being processed
"""
