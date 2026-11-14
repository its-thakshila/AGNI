"""NASA FIRMS client: which sensors have data for a day, and the detections for Australia.

Only the pipeline (GitHub Actions) talks to FIRMS. The MAP key comes from FIRMS_MAP_KEY and
never reaches the browser.
"""

from __future__ import annotations

import csv
import io
import random
import time
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

from .common import BBOX

API = "https://firms.modaps.eosdis.nasa.gov/api"
AREA = ",".join(str(v) for v in BBOX)

VIIRS_PRODUCTS = ("VIIRS_SNPP_NRT", "VIIRS_SNPP_SP")  # the CSV and Harmonic are both S-NPP based
MODIS_PRODUCTS = ("MODIS_NRT", "MODIS_SP")


class FirmsError(Exception):
    """FIRMS could not be reached or returned something unusable."""


@dataclass
class Fetched:
    product: str
    rows: list[dict]


class Firms:
    def __init__(self, key: str, retries: int = 3, timeout: int = 30):
        if not key:
            raise FirmsError("FIRMS_MAP_KEY is not set.")
        self.key = key
        self.retries = retries
        self.timeout = timeout
        self._availability: dict[str, tuple[date, date]] | None = None

    def _get(self, url: str) -> str:
        for attempt in range(self.retries):
            try:
                with urlopen(url, timeout=self.timeout) as response:
                    return response.read().decode("utf-8-sig")
            except HTTPError as error:
                if not 500 <= error.code < 600:
                    raise FirmsError(f"FIRMS HTTP {error.code}") from None
            except (URLError, TimeoutError):
                pass
            time.sleep(2 ** attempt)
        raise FirmsError("FIRMS did not answer.")

    def availability(self) -> dict[str, tuple[date, date]]:
        """{product: (first day, last day)} from FIRMS's own data-availability list."""
        if self._availability is None:
            text = self._get(f"{API}/data_availability/csv/{quote(self.key, safe='')}/ALL")
            table = {}
            for row in csv.DictReader(io.StringIO(text)):
                try:
                    table[row["data_id"]] = (date.fromisoformat(row["min_date"]), date.fromisoformat(row["max_date"]))
                except (KeyError, ValueError):
                    continue
            self._availability = table
        return self._availability

    def covers(self, product: str, day: date) -> bool | None:
        """True/False from the availability list; None when the list itself is unavailable."""
        try:
            span = self.availability().get(product)
        except FirmsError:
            return None
        return bool(span) and span[0] <= day <= span[1]

    def detections(self, product: str, day: date) -> list[dict]:
        text = self._get(f"{API}/area/csv/{quote(self.key, safe='')}/{product}/{AREA}/1/{day.isoformat()}")
        rows = list(csv.DictReader(io.StringIO(text)))
        if text.strip() and not rows and "latitude" not in text.split("\n", 1)[0]:
            raise FirmsError(f"Unexpected FIRMS answer for {product} {day}: {text[:80]!r}")
        return rows

    def first(self, products, day: date) -> Fetched | None:
        """The first product that covers the day and answers. None if none do."""
        for product in products:
            if self.covers(product, day) is False:
                continue
            try:
                return Fetched(product, self.detections(product, day))
            except FirmsError:
                continue
        return None


class FakeFirms:
    """Deterministic stand-in for tests and the mock dataset. Never calls the network.

    VIIRS is "offline" on every day listed in viirs_offline (and on the 9th and 23rd of
    each month by default), so the MODIS -> Harmonic path gets exercised too.
    """

    def __init__(self, viirs_offline: set[date] | None = None):
        self.viirs_offline = viirs_offline

    def _offline(self, day: date) -> bool:
        return day in self.viirs_offline if self.viirs_offline is not None else day.day in (9, 23)

    def covers(self, product: str, day: date) -> bool:
        return not (product.startswith("VIIRS") and self._offline(day))

    def first(self, products, day: date) -> Fetched | None:
        product = products[0]
        if not self.covers(product, day):
            return None
        rng = random.Random(f"{product}-{day}")
        viirs = product.startswith("VIIRS")
        # same scale and season as the backend's synthetic history (about 4-13 detections a day)
        base = 11 if day.month in (11, 12, 1, 2) else 4
        n = max(1, round(base * (0.7 + 0.6 * rng.random()) / (1 if viirs else 4.5)))
        sites = [(-12.9, 134.2), (-16.4, 143.1), (-21.2, 123.7), (-33.7, 150.3), (-38.3, 146.4)]
        rows = []
        for i in range(n):
            lat, lon = sites[i % len(sites)]
            night = rng.random() < 0.45
            row = {
                "latitude": f"{lat + rng.uniform(-0.6, 0.6):.5f}", "longitude": f"{lon + rng.uniform(-0.6, 0.6):.5f}",
                "acq_date": day.isoformat(), "acq_time": "1600" if night else "0430",
                "satellite": "N" if viirs else "Aqua", "confidence": rng.choice(["l", "n", "n", "n", "h"]) if viirs else str(rng.randint(30, 99)),
                "version": "2.0NRT" if viirs else "6.1NRT", "frp": f"{rng.uniform(0.5, 40):.2f}",
                "daynight": "N" if night else "D", "type": "0",
            }
            if viirs:
                row.update(bright_ti4=f"{rng.uniform(300, 360):.2f}", scan="0.39", track="0.41", bright_ti5=f"{rng.uniform(280, 310):.2f}")
            else:
                row.update(brightness=f"{rng.uniform(305, 360):.2f}", scan="1.0", track="1.0", bright_t31=f"{rng.uniform(285, 310):.2f}")
            rows.append(row)
        return Fetched(product, rows)


def utc_days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)
