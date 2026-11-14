from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class BBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float


COVERAGE = BBox(-44, -9, 112, 154)
RECORD_START = date(2000, 11, 1)
LOCAL_END = date(2025, 12, 31)
GRID_MAX_DAYS = 366
