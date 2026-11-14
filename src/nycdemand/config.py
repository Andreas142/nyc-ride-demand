"""Load project configuration and expand month ranges."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path("configs/project.yaml")


def parse_month(value: str) -> date:
    """Parse ``YYYY-MM`` into the first day of that month."""
    try:
        year, month = str(value).split("-")
        return date(int(year), int(month), 1)
    except ValueError as exc:
        raise ValueError(f"Month must look like YYYY-MM, got {value!r}") from exc


def month_range(start: str, end: str) -> list[str]:
    """All months from ``start`` to ``end`` inclusive, as ``YYYY-MM`` strings."""
    first, last = parse_month(start), parse_month(end)
    if first > last:
        raise ValueError(f"start month {start} is after end month {end}")
    months: list[str] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


@dataclass(frozen=True)
class Config:
    tlc_base_url: str
    zone_lookup_url: str
    services: tuple[str, ...]
    start_month: str
    end_month: str
    ghcnh_station: str
    isd_station: str
    weather_start_year: int
    weather_end_year: int
    bronze_dir: Path
    raw_dir: Path
    warehouse: Path
    duckdb_memory_limit: str
    duckdb_threads: int

    @property
    def months(self) -> list[str]:
        return month_range(self.start_month, self.end_month)


def load_config(path: Path | str = DEFAULT_CONFIG) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    tlc, weather, paths, db = raw["tlc"], raw["weather"], raw["paths"], raw["duckdb"]
    return Config(
        tlc_base_url=tlc["base_url"],
        zone_lookup_url=tlc["zone_lookup_url"],
        services=tuple(tlc["services"]),
        start_month=str(tlc["start_month"]),
        end_month=str(tlc["end_month"]),
        ghcnh_station=str(weather["ghcnh_station"]),
        isd_station=str(weather["isd_station"]),
        weather_start_year=int(weather["start_year"]),
        weather_end_year=int(weather["end_year"]),
        bronze_dir=Path(paths["bronze"]),
        raw_dir=Path(paths["raw"]),
        warehouse=Path(paths["warehouse"]),
        duckdb_memory_limit=str(db["memory_limit"]),
        duckdb_threads=int(db["threads"]),
    )
