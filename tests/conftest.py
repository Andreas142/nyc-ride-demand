"""Shared fixtures. Tests use a handful of hand-written rows to exercise code paths only;
every analysis in this project runs on real TLC and NOAA data."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from nycdemand.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path):
    base = load_config(REPO_ROOT / "configs" / "project.yaml")
    return replace(
        base,
        tlc_base_url=str(tmp_path / "source"),
        bronze_dir=tmp_path / "bronze",
        raw_dir=tmp_path / "raw",
        warehouse=tmp_path / "warehouse.duckdb",
    )


def write_parquet(path: Path, select_sql: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"COPY ({select_sql}) TO '{path}' (FORMAT PARQUET)")
    return path


@pytest.fixture
def yellow_file(tmp_path):
    # 2024-style yellow file: capitalised Airport_fee, no cbd_congestion_fee column.
    return write_parquet(
        tmp_path / "source" / "yellow_tripdata_2024-12.parquet",
        """
        SELECT * FROM (VALUES
          (2, TIMESTAMP '2024-12-01 08:00:00', TIMESTAMP '2024-12-01 08:10:00', 161, 230,
           1.8, 12.1, 0.0, 3.0, 2.5, 0.0),
          (1, TIMESTAMP '2024-12-01 09:00:00', TIMESTAMP '2024-12-01 09:25:30', 132, 48,
           17.2, 70.0, 6.94, 15.0, 2.5, 1.75)
        ) t(VendorID, tpep_pickup_datetime, tpep_dropoff_datetime, PULocationID,
            DOLocationID, trip_distance, fare_amount, tolls_amount, tip_amount,
            congestion_surcharge, Airport_fee)
        """,
    )


@pytest.fixture
def fhvhv_file(tmp_path):
    return write_parquet(
        tmp_path / "source" / "fhvhv_tripdata_2025-01.parquet",
        """
        SELECT * FROM (VALUES
          ('HV0003', TIMESTAMP '2025-01-06 07:58:00', TIMESTAMP '2025-01-06 08:02:00',
           TIMESTAMP '2025-01-06 08:20:00', 79, 161, 3.1, 1080, 24.5, 0.0, 4.0, 2.75, 0.0,
           1.5, 19.0)
        ) t(hvfhs_license_num, request_datetime, pickup_datetime, dropoff_datetime,
            PULocationID, DOLocationID, trip_miles, trip_time, base_passenger_fare, tolls,
            tips, congestion_surcharge, airport_fee, cbd_congestion_fee, driver_pay)
        """,
    )
