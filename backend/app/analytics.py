from collections import defaultdict
from datetime import date

from .local_store import dates_between


def uncertainty(origin, day):
    if origin == "ml":
        return 0.16 if day < date(2002, 7, 4) else 0.11
    if origin == "gapfill":
        return 0.13 if day.year >= 2022 else 0.11
    return 0.0


def daily(rows, status, start, end):
    grouped = defaultdict(list)
    for day, *values in rows:
        grouped[day].append(values)

    days = []
    for day in dates_between(start, end):
        coverage, reliable, _ = status.get(day, ("none", True, None))
        values = grouped[day]
        weights = defaultdict(float)
        for _, origin, count, _, _ in values:
            weights[origin] += count
        count = sum(weights.values(), 0.0)
        available = coverage != "none"
        days.append({
            "fire_date": day,
            "count": count if available else None,
            "count_day": sum((x[2] for x in values if x[0] == "D"), 0.0) if available else None,
            "count_night": sum((x[2] for x in values if x[0] == "N"), 0.0) if available else None,
            "frp_sum": sum((x[3] or 0 for x in values), 0.0) if available else None,
            "n_rows": sum(x[4] for x in values),
            "origin_mix": {key: weight / count for key, weight in weights.items()} if count else {},
            "has_data": coverage,
            "uncertainty": (
                sum(uncertainty(origin, day) * weight for origin, weight in weights.items()) / count
                if count else 0.0
            ) if available else None,
            "daynight_reliable": reliable,
        })
    return {"days": days, "totals": {
        "count": sum((day["count"] or 0 for day in days), 0.0),
        "frp_sum": sum((day["frp_sum"] or 0 for day in days), 0.0),
        "days_with_data": sum(day["count"] is not None for day in days),
        "days_total": len(days),
    }}


def grid(rows):
    grouped = defaultdict(lambda: defaultdict(float))
    radiative_power = defaultdict(float)
    for ci, cj, origin, count, frp, _ in rows:
        grouped[ci, cj][origin] += count
        radiative_power[ci, cj] += frp or 0.0

    cells = []
    for (ci, cj), weights in grouped.items():
        count = sum(weights.values())
        if count <= 0:
            continue
        cells.append({
            "lat": cj / 10,
            "lon": ci / 10,
            "count": count,
            "frp_sum": radiative_power[ci, cj],
            "main_origin": max(weights, key=weights.get),
        })
    return {
        "res": 0.1,
        "total": sum((cell["count"] for cell in cells), 0.0),
        "max_count": max((cell["count"] for cell in cells), default=0.0),
        "cells": cells,
    }

def percentile(values, proportion):
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def period_summary(days, expected_days):
    observed = [day for day in days if day["count"] is not None]
    if not observed:
        return {"count": None, "has_data": "none", "days_with_data": 0,
                "main_origin": None, "uncertainty": None}

    weights = defaultdict(float)
    for day in observed:
        for origin, share in day["origin_mix"].items():
            weights[origin] += share * day["count"]
    count = sum(day["count"] for day in observed)
    full = len(observed) == expected_days and all(day["has_data"] == "full" for day in observed)
    return {
        "count": count,
        "has_data": "full" if full else "partial",
        "days_with_data": len(observed),
        "main_origin": max(weights, key=weights.get) if weights else None,
        "uncertainty": sum(day["uncertainty"] * day["count"] for day in observed) / count if count else 0.0,
    }


def calendar(days, today):
    from calendar import monthrange

    months = defaultdict(list)
    for day in days:
        fire_day = day["fire_date"]
        months[fire_day.year, fire_day.month].append(day)

    cells = []
    seasonal = defaultdict(list)
    for (year, month), values in months.items():
        days_total = monthrange(year, month)[1]
        summary = period_summary(values, days_total)
        complete = (year, month) < (today.year, today.month)
        cells.append({"year": year, "month": month, "days_total": days_total,
                      "complete": complete, **summary})
        if complete and summary["has_data"] == "full":
            seasonal[month].append(summary["count"])

    climatology = []
    for month in range(1, 13):
        values = seasonal[month]
        climatology.append({
            "month": month, "median": percentile(values, 0.5),
            "p10": percentile(values, 0.1), "p25": percentile(values, 0.25),
            "p75": percentile(values, 0.75), "p90": percentile(values, 0.9),
            "min": min(values, default=0.0), "max": max(values, default=0.0),
            "years_used": len(values),
        })
    counts = [cell["count"] for cell in cells if cell["count"] is not None]
    return {"cells": cells, "climatology": climatology,
            "scale": {"min": 0.0, "max": max(counts, default=0.0), "p95": percentile(counts, 0.95)}}


def anomaly(days, assessed, baseline_start, baseline_end, today):
    from calendar import monthrange
    from statistics import mean, pstdev

    month_to_date = (assessed.year, assessed.month) == (today.year, today.month)
    last_day = today.day if month_to_date else monthrange(assessed.year, assessed.month)[1]
    by_year = defaultdict(list)
    for day in days:
        by_year[day["fire_date"].year].append(day)

    history = []
    for year in [*range(baseline_start, baseline_end + 1), assessed.year]:
        expected = monthrange(year, assessed.month)[1]
        if month_to_date:
            expected = min(expected, last_day)
        summary = period_summary(by_year[year], expected)
        history.append({"year": year, "value": summary["count"],
                        "has_data": summary["has_data"], "main_origin": summary["main_origin"]})

    current = history[-1]
    value = current["value"]
    values = [entry["value"] for entry in history[:-1] if entry["has_data"] == "full"]
    average = mean(values) if values else 0.0
    std = pstdev(values) if values else 0.0
    p10, p25, median, p75, p90 = (percentile(values, p) for p in (0.1, 0.25, 0.5, 0.75, 0.9))
    category = None
    if value is not None and values:
        category = "normal"
        if value < p25:
            category = "well_below" if value < p10 else "below"
        if value > p75:
            category = "well_above" if value > p90 else "above"
    return {
        "month": assessed.strftime("%Y-%m"),
        "compared_days": {"from_day": 1, "to_day": last_day, "month_to_date": month_to_date},
        "value": value,
        "baseline": {"years": [baseline_start, baseline_end], "n_years": len(values),
                     "median": median, "mean": average, "std": std, "p10": p10, "p25": p25,
                     "p75": p75, "p90": p90},
        "percent_of_median": 100 * value / median if value is not None and median else None,
        "z_score": (value - average) / std if value is not None and std else None,
        "rank": 1 + sum(other > value for other in values) if value is not None else None,
        "rank_of": len(values) + int(value is not None),
        "category": category,
        "has_data": current["has_data"],
        "history": history,
    }
