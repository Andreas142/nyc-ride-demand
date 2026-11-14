"""Pruning and the month-by-month backfill, on a few hand-written rows."""

import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pytest

from nycdemand.ingest.tlc import Manifest, bronze_path, ingest_tlc, prune_month, sample_path
from nycdemand.pipeline import backfill
from tests.conftest import REPO_ROOT, write_parquet

# A "today" for which March 2025 may still be unpublished.
SPRING_2025 = date(2025, 4, 20)


def _manifest(cfg) -> Manifest:
    return Manifest(cfg.bronze_dir / "_manifest.json")


def test_prune_refuses_months_that_are_not_built(cfg, yellow_file):
    ingest_tlc(cfg, services=["yellow"], months=["2024-12"], today=SPRING_2025)
    with pytest.raises(RuntimeError, match="not in the marts"):
        prune_month(cfg, _manifest(cfg), "yellow", "2024-12")
    assert bronze_path(cfg, "yellow", "2024-12").exists()


def test_pruned_month_keeps_a_sample_and_is_not_downloaded_again(cfg, yellow_file):
    ingest_tlc(cfg, services=["yellow"], months=["2024-12"], today=SPRING_2025)
    manifest = _manifest(cfg)
    manifest.update("yellow", "2024-12", built_at="2026-01-01T00:00:00+00:00")

    assert prune_month(cfg, manifest, "yellow", "2024-12", sample_percent=100)
    assert not bronze_path(cfg, "yellow", "2024-12").exists()
    sample = sample_path(cfg, "yellow", "2024-12")
    assert duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{sample}')").fetchone()[0] == 2
    assert _manifest(cfg).get("yellow", "2024-12")["sample_rows"] == 2

    # The source file is gone from "TLC" too: a second ingest must skip, not fail or re-download.
    yellow_file.unlink()
    again = ingest_tlc(cfg, services=["yellow"], months=["2024-12"], today=SPRING_2025)
    assert again[0].status == "skipped"
    assert prune_month(cfg, _manifest(cfg), "yellow", "2024-12") is False


def test_backfill_builds_in_order_prunes_older_months_and_resumes(cfg, tmp_path):
    for month in ("2024-12", "2025-01", "2025-02"):
        write_parquet(
            tmp_path / "source" / f"yellow_tripdata_{month}.parquet",
            f"""
            SELECT 1 AS VendorID, TIMESTAMP '{month}-03 08:00:00' AS tpep_pickup_datetime,
                   TIMESTAMP '{month}-03 08:10:00' AS tpep_dropoff_datetime,
                   161 AS PULocationID, 161 AS DOLocationID, 1.5 AS trip_distance,
                   10.0 AS fare_amount
            """,
        )
    built: list[str] = []
    months = ["2025-02", "2024-12", "2025-01", "2025-03"]  # out of order; March is unpublished

    first = backfill(cfg, months, services=["yellow"], build=built.append, today=SPRING_2025)
    assert built == ["2024-12", "2025-01", "2025-02"]
    assert [(s.month, s.status) for s in first] == [
        ("2024-12", "built"),
        ("2025-01", "built"),
        ("2025-02", "built"),
        ("2025-03", "unavailable"),
    ]
    # Only the newest built month keeps its trip-level file.
    assert not bronze_path(cfg, "yellow", "2024-12").exists()
    assert not bronze_path(cfg, "yellow", "2025-01").exists()
    assert bronze_path(cfg, "yellow", "2025-02").exists()
    assert sample_path(cfg, "yellow", "2025-01").exists()

    second = backfill(cfg, months, services=["yellow"], build=built.append, today=SPRING_2025)
    assert len(built) == 3  # nothing is rebuilt
    assert {s.status for s in second} == {"already-built", "unavailable"}


def test_backfill_stops_at_an_old_month_it_cannot_download(cfg, yellow_file):
    built: list[str] = []
    with pytest.raises(RuntimeError, match="rate-limiting"):
        backfill(
            cfg,
            ["2024-12", "2025-01", "2025-02"],
            services=["yellow"],
            build=built.append,
            today=date(2026, 11, 14),
            retry_waits=(),
        )
    assert built == ["2024-12"]  # December is safe; nothing after the gap was built


def test_failed_build_is_not_marked_built_or_pruned(cfg, yellow_file):
    def boom(month: str) -> None:
        raise RuntimeError("dbt failed")

    with pytest.raises(RuntimeError, match="dbt failed"):
        backfill(cfg, ["2024-12"], services=["yellow"], build=boom, today=SPRING_2025)
    assert not _manifest(cfg).is_built("yellow", "2024-12")
    assert bronze_path(cfg, "yellow", "2024-12").exists()


def _trips_sql(service: str, month: str) -> str:
    """Six trips an hour apart on the 6th: two inside zone 161, two leaving it, two elsewhere."""
    pickup = f"TIMESTAMP '{month}-06 08:00:00' + to_hours(i)"
    if service == "yellow":
        return f"""
            SELECT 1 AS VendorID, {pickup} AS tpep_pickup_datetime,
                   {pickup} + INTERVAL 10 MINUTE AS tpep_dropoff_datetime,
                   CASE WHEN i < 4 THEN 161 ELSE 7 END AS PULocationID,
                   CASE WHEN i < 2 THEN 161 ELSE 7 END AS DOLocationID,
                   2.0 AS trip_distance, 12.0 AS fare_amount,
                   CASE WHEN i < 4 AND '{month}' >= '2025-01' THEN 0.75 ELSE 0.0 END AS cbd_congestion_fee
            FROM range(6) r(i)
        """
    return f"""
        SELECT 'HV0003' AS hvfhs_license_num, {pickup} - INTERVAL 4 MINUTE AS request_datetime,
               {pickup} AS pickup_datetime, {pickup} + INTERVAL 10 MINUTE AS dropoff_datetime,
               CASE WHEN i < 4 THEN 161 ELSE 7 END AS PULocationID,
               CASE WHEN i < 2 THEN 161 ELSE 7 END AS DOLocationID,
               -- the last trip has an impossible distance and must be dropped and audited
               CASE WHEN i = 5 THEN 500.0 ELSE 2.0 END AS trip_miles, 600 AS trip_time,
               15.0 AS base_passenger_fare, 11.0 AS driver_pay,
               CASE WHEN i < 4 AND '{month}' >= '2025-01' THEN 1.5 ELSE 0.0 END AS cbd_congestion_fee
        FROM range(6) r(i)
    """


def test_backfill_end_to_end_with_dbt(cfg, tmp_path, monkeypatch):
    """The real dbt project on hand-written rows: two months, a rebuild, and the marts' contents."""
    work = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "dbt", work / "dbt", ignore=shutil.ignore_patterns("target", "logs"))
    monkeypatch.chdir(work)  # dbt paths (data/bronze, data/warehouse.duckdb) are relative
    cfg = replace(cfg, bronze_dir=Path("data/bronze"), raw_dir=Path("data/raw"))

    zones = Path("data/bronze/zones/taxi_zone_lookup.csv")
    zones.parent.mkdir(parents=True)
    zones.write_text(
        '"LocationID","Borough","Zone","service_zone"\n'
        '7,"Queens","Astoria","Boro Zone"\n'
        '161,"Manhattan","Midtown Center","Yellow Zone"\n'
        '264,"Unknown","N/A","N/A"\n'
    )
    write_parquet(
        Path("data/bronze/weather/weather_2025.parquet"),
        "SELECT TIMESTAMP '2025-01-06 08:00:00' AS hour_local, 1.0 AS temp_c, 3.0 AS wind_ms, "
        "0.0 AS precip_mm, 'ghcnh' AS source",
    )
    for service in cfg.services:
        for month in ("2024-12", "2025-01"):
            write_parquet(
                tmp_path / "source" / f"{service}_tripdata_{month}.parquet", _trips_sql(service, month)
            )

    statuses = backfill(cfg, ["2024-12", "2025-01"], today=SPRING_2025)
    assert [s.status for s in statuses] == ["built", "built"]
    assert not Path("data/bronze/fhvhv/fhvhv_2024-12.parquet").exists()  # pruned to a sample

    def query(sql: str) -> list[tuple]:
        con = duckdb.connect("data/warehouse.duckdb", read_only=True)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    # Complete grid: 2 mappable zones x 2 services x every hour of both months.
    assert query("SELECT count(*), sum(trips) FROM fct_zone_hour_demand") == [(2 * 2 * (744 + 744), 22)]
    # Filter audit: one Uber/Lyft trip a month fails the distance rule, and the columns add up.
    assert query(
        "SELECT rows_in, lost_distance, rows_kept FROM mart_filter_audit "
        "WHERE service = 'fhvhv' ORDER BY source_month"
    ) == [(6, 1, 5), (6, 1, 5)]
    # Request-to-pickup wait: 4 minutes on every kept Uber/Lyft trip.
    assert query(
        "SELECT sum(sum_wait_seconds) / sum(trips_with_wait) FROM fct_zone_hour_demand "
        "WHERE service = 'fhvhv'"
    ) == [(240.0,)]
    # Zone 161 is flagged only by its post-launch trips; no zone is flagged from 2024 data.
    assert query("SELECT zone_id FROM dim_zones WHERE in_congestion_zone") == []
    assert query(
        "SELECT sum(trips), sum(trips_with_cbd_fee) FROM fct_od_day "
        "WHERE pu_zone_id = 161 AND do_zone_id = 161 AND day >= DATE '2025-01-05'"
    ) == [(4, 4)]

    # Rebuilding a month replaces it instead of adding to it.
    from nycdemand.pipeline import dbt_build_month

    dbt_build_month("2025-01")
    assert query("SELECT sum(trips) FROM fct_zone_hour_demand") == [(22,)]
