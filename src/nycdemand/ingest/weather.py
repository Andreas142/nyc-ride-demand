"""Hourly weather for NYC (Central Park) from NOAA, converted to local time.

Primary source is GHCN-hourly (GHCNh), which NOAA describes as the successor of the
Integrated Surface Database (ISD); ISD global-hourly is the fallback. Both report in UTC,
while TLC timestamps are New York local time, so observations are converted to
America/New_York and averaged per local hour (the repeated hour when clocks go back is
averaged too).

Precipitation caveat: stations report precipitation over varying periods. GHCNh values are
taken as reported and the hourly maximum is kept; ISD uses only 1-hour (AA1 period = 01)
amounts. Check the coverage before relying on it as a feature.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from nycdemand.config import Config
from nycdemand.ingest.tlc import download

log = logging.getLogger(__name__)

GHCNH_BASE = "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/access"
# NOAA has served the by-year files both with and without a "psv" folder; try both.
GHCNH_URLS = (
    GHCNH_BASE + "/by-year/{year}/psv/GHCNh_{station}_{year}.psv",
    GHCNH_BASE + "/by-year/{year}/GHCNh_{station}_{year}.psv",
)
ISD_URL = "https://www.ncei.noaa.gov/data/global-hourly/access/{year}/{station}.csv"
LOCAL_TZ = "America/New_York"
OUTPUT_COLUMNS = ["hour_local", "temp_c", "wind_ms", "precip_mm", "source"]


def isd_value(field: object, index: int, scale: float = 10.0) -> float:
    """Decode one ISD comma-separated field, e.g. TMP '+0228,1' -> 22.8; all-9s -> NaN."""
    if not isinstance(field, str) or not field:
        return math.nan
    parts = field.split(",")
    if index >= len(parts):
        return math.nan
    token = parts[index].strip()
    digits = token.lstrip("+-")
    if not digits or set(digits) == {"9"}:
        return math.nan
    try:
        return int(token) / scale
    except ValueError:
        return math.nan


def _isd_precip_1h(field: object) -> float:
    """AA1 is 'period_hours,depth_tenths_mm,condition,quality'; keep 1-hour amounts only."""
    if not isinstance(field, str) or not field.startswith("01,"):
        return math.nan
    return isd_value(field, 1)


def _to_local_hourly(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    local = frame["time_utc"].dt.tz_convert(LOCAL_TZ).dt.tz_localize(None).dt.floor("h")
    hourly = (
        frame.assign(hour_local=local)
        .groupby("hour_local", as_index=False)
        .agg(temp_c=("temp_c", "mean"), wind_ms=("wind_ms", "mean"), precip_mm=("precip_mm", "max"))
    )
    hourly["source"] = source
    return hourly[OUTPUT_COLUMNS]


def isd_to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    nan = pd.Series(math.nan, index=df.index)
    frame = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(df["DATE"], utc=True),
            "temp_c": df["TMP"].map(lambda v: isd_value(v, 0)),
            "wind_ms": df["WND"].map(lambda v: isd_value(v, 3)) if "WND" in df else nan,
            "precip_mm": df["AA1"].map(_isd_precip_1h) if "AA1" in df else nan,
        }
    )
    return _to_local_hourly(frame, "isd")


def ghcnh_to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """GHCNh rows to local hours. Accepts both layouts NOAA has published: separate
    Year/Month/Day/Hour/Minute columns, or a single DATE column (UTC in both)."""
    cols = {c.lower(): c for c in df.columns}
    has_parts = all(c in cols for c in ("year", "month", "day", "hour"))
    if "temperature" not in cols or not (has_parts or "date" in cols):
        raise ValueError(
            "GHCNh file lacks temperature or its time columns "
            f"(DATE, or Year/Month/Day/Hour); first columns: {list(df.columns)[:15]}"
        )

    def num(name: str) -> pd.Series:
        if name not in cols:
            return pd.Series(math.nan, index=df.index)
        return pd.to_numeric(df[cols[name]], errors="coerce")

    if has_parts:
        parts = {k: num(k) for k in ("year", "month", "day", "hour")}
        parts["minute"] = num("minute").fillna(0)
        time_utc = pd.to_datetime(pd.DataFrame(parts), utc=True, errors="coerce")
    else:
        time_utc = pd.to_datetime(df[cols["date"]], utc=True, errors="coerce")
    frame = pd.DataFrame(
        {
            "time_utc": time_utc,
            "temp_c": num("temperature"),
            "wind_ms": num("wind_speed"),
            "precip_mm": num("precipitation"),
        }
    ).dropna(subset=["time_utc"])
    return _to_local_hourly(frame, "ghcnh")


def _fetch_year(cfg: Config, year: int) -> pd.DataFrame:
    raw = cfg.raw_dir / "weather"
    for url in GHCNH_URLS:
        try:
            path = download(
                url.format(year=year, station=cfg.ghcnh_station),
                raw / f"GHCNh_{cfg.ghcnh_station}_{year}.psv",
            )
            return ghcnh_to_hourly(pd.read_csv(path, sep="|", low_memory=False))
        except Exception as exc:  # noqa: BLE001 - any GHCNh failure moves on to the next source
            log.warning("GHCNh %s failed (%s)", year, exc)
    log.warning("GHCNh %s unavailable; falling back to ISD", year)
    path = download(
        ISD_URL.format(year=year, station=cfg.isd_station),
        raw / f"ISD_{cfg.isd_station}_{year}.csv",
    )
    return isd_to_hourly(pd.read_csv(path, low_memory=False))


def ingest_weather(
    cfg: Config, start_year: int | None = None, end_year: int | None = None, force: bool = False
) -> list[Path]:
    """Write one Parquet file per year. Past years are skipped once written; the current
    year is always refreshed because NOAA keeps adding to it. Future years are skipped."""
    written: list[Path] = []
    this_year = datetime.now(UTC).year
    last_year = min(end_year or cfg.weather_end_year, this_year)
    for year in range(start_year or cfg.weather_start_year, last_year + 1):
        out = cfg.bronze_dir / "weather" / f"weather_{year}.parquet"
        if out.exists() and not force and year < this_year:
            log.info("skip weather %s (already in bronze)", year)
            continue
        hourly = _fetch_year(cfg, year)
        out.parent.mkdir(parents=True, exist_ok=True)
        hourly.to_parquet(out, index=False)
        log.info("wrote weather %s: %s hours from %s", year, len(hourly), hourly["source"].iat[0])
        written.append(out)
    return written
