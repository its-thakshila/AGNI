"""Shared loading + time/space keys.

Overpass handling: every detection gets a *local solar time* (UTC + lon/15 h), a
`pass` (D = 06-18 LST, N otherwise) and a `fire_date` = date(LST - 6 h). This puts
Terra 09:07, Aqua 14:59 and S-NPP 13:40 on the same day pass, and Terra 21:27,
Aqua 02:36 (next calendar day) and S-NPP 01:13 (next calendar day) on the same
night pass, which a UTC calendar date splits apart.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config

MODIS_COLUMNS = ["latitude", "longitude", "brightness", "scan", "track", "acq_date", "acq_time",
                 "satellite", "instrument", "confidence", "version", "bright_t31", "frp",
                 "daynight", "type"]


def read_firms(path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"acq_time": str, "satellite": str, "confidence": str,
                                  "version": str, "daynight": str})
    df["acq_time"] = df["acq_time"].str.zfill(4)
    return df


def add_time_keys(df: pd.DataFrame) -> pd.DataFrame:
    utc = pd.to_datetime(df["acq_date"] + " " + df["acq_time"], format="%Y-%m-%d %H%M")
    lst = utc + pd.to_timedelta(df["longitude"] / 15.0, unit="h")
    df["utc"] = utc
    df["lst_hour"] = (lst.dt.hour + lst.dt.minute / 60.0).astype("float32")
    df["pass"] = np.where((df["lst_hour"] >= 6) & (df["lst_hour"] < 18), "D", "N")
    df["fire_date"] = (lst - pd.Timedelta(hours=config.DAY_START_HOUR)).dt.normalize()
    return df


def add_cell_keys(df: pd.DataFrame) -> pd.DataFrame:
    df["ci"] = np.floor((df["latitude"] - config.GRID_LAT0) / config.GRID_DEG).astype("int32")
    df["cj"] = np.floor((df["longitude"] - config.GRID_LON0) / config.GRID_DEG).astype("int32")
    return df


def cell_center(ci, cj):
    lat = config.GRID_LAT0 + (np.asarray(ci) + 0.5) * config.GRID_DEG
    lon = config.GRID_LON0 + (np.asarray(cj) + 0.5) * config.GRID_DEG
    return lat, lon


def load_modis(path) -> pd.DataFrame:
    df = read_firms(path)
    df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce").astype("float32")
    df["sensor"] = np.where(df["satellite"].str.upper().str.startswith("A"), "aqua", "terra")
    return add_cell_keys(add_time_keys(df))


def load_viirs(path) -> pd.DataFrame:
    df = read_firms(path)
    df["confidence"] = df["confidence"].str.strip().str.lower().str[0]   # l / n / h
    return add_cell_keys(add_time_keys(df))


KEYS = ["ci", "cj", "fire_date", "pass"]
