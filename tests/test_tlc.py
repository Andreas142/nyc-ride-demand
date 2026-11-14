import duckdb
import pytest

from nycdemand.ingest.tlc import (
    OUTPUT_COLUMNS,
    SCHEMAS,
    build_select_sql,
    connect,
    ingest_tlc,
    month_url,
    write_month,
)


def test_every_service_shares_one_schema():
    keys = {tuple(schema) for schema in SCHEMAS.values()}
    assert len(keys) == 1


def test_month_url():
    assert month_url("https://x/trip-data/", "fhvhv", "2025-01") == (
        "https://x/trip-data/fhvhv_tripdata_2025-01.parquet"
    )


def test_select_matches_case_insensitively_and_null_fills():
    sql = build_select_sql("yellow", ["tpep_pickup_datetime", "Airport_fee"], "src", "2024-12")
    assert 'TRY_CAST("Airport_fee" AS DOUBLE) AS airport_fee' in sql
    assert "CAST(NULL AS DOUBLE) AS cbd_congestion_fee" in sql
    assert "'2024-12' AS source_month" in sql


def test_unknown_service_rejected():
    with pytest.raises(ValueError):
        build_select_sql("limo", [], "src", "2025-01")


def test_write_month_yellow(tmp_path, yellow_file):
    out = tmp_path / "bronze" / "yellow" / "yellow_2024-12.parquet"
    rows = write_month(connect(), "yellow", "2024-12", yellow_file, out)
    assert rows == 2
    con = duckdb.connect()
    result = con.execute(f"SELECT * FROM read_parquet('{out}') ORDER BY pickup_at").fetchdf()
    assert tuple(result.columns) == OUTPUT_COLUMNS
    assert result["trip_seconds"].tolist() == [600.0, 1530.0]  # derived from timestamps
    assert result["airport_fee"].tolist() == [0.0, 1.75]
    assert result["cbd_congestion_fee"].isna().all()  # column did not exist in 2024


def test_write_month_fhvhv_keeps_cbd_fee(tmp_path, fhvhv_file):
    out = tmp_path / "bronze" / "fhvhv" / "fhvhv_2025-01.parquet"
    write_month(connect(), "fhvhv", "2025-01", fhvhv_file, out)
    row = duckdb.connect().execute(f"SELECT * FROM read_parquet('{out}')").fetchdf().iloc[0]
    assert row["cbd_congestion_fee"] == 1.5
    assert row["trip_seconds"] == 1080
    assert row["provider"] == "HV0003"


def test_ingest_skips_done_months_and_flags_missing(cfg, yellow_file):
    first = ingest_tlc(cfg, services=["yellow"], months=["2024-12", "2025-01"], remote=True)
    assert [(r.month, r.status) for r in first] == [("2024-12", "written"), ("2025-01", "unavailable")]
    second = ingest_tlc(cfg, services=["yellow"], months=["2024-12"])
    assert second[0].status == "skipped"
    assert (cfg.bronze_dir / "_manifest.json").exists()
