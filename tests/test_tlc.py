from datetime import date

import duckdb
import pytest
import requests

from nycdemand.ingest import tlc
from nycdemand.ingest.tlc import (
    OUTPUT_COLUMNS,
    SCHEMAS,
    NotAvailable,
    build_select_sql,
    connect,
    download,
    ingest_tlc,
    is_recent,
    month_url,
    write_month,
)

# A "today" for which 2025-01 is recent enough to be unpublished.
EARLY_2025 = date(2025, 2, 15)


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


def test_ingest_skips_done_months_and_flags_recent_missing_ones(cfg, yellow_file):
    first = ingest_tlc(cfg, services=["yellow"], months=["2024-12", "2025-01"], today=EARLY_2025)
    assert [(r.month, r.status) for r in first] == [("2024-12", "written"), ("2025-01", "unavailable")]
    assert first[0].method == "download"  # one request per file by default
    second = ingest_tlc(cfg, services=["yellow"], months=["2024-12"], today=EARLY_2025)
    assert second[0].status == "skipped"
    assert (cfg.bronze_dir / "_manifest.json").exists()


def test_missing_old_month_stops_the_run_instead_of_leaving_a_hole(cfg):
    with pytest.raises(RuntimeError, match="rate-limiting"):
        ingest_tlc(cfg, services=["yellow"], months=["2025-01"], today=date(2026, 11, 14))


def test_is_recent():
    today = date(2026, 11, 14)
    assert is_recent("2026-11", today) and is_recent("2026-08", today)
    assert not is_recent("2026-07", today)
    assert not is_recent("2025-01", today)


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b""):
        self.status_code = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size):
        yield self._body


def _fake_get(statuses, calls):
    def get(url, **kwargs):
        calls.append(kwargs.get("headers", {}).get("User-Agent"))
        status = statuses.pop(0)
        return _FakeResponse(status, b"parquet bytes" if status == 200 else b"")

    return get


def test_download_retries_refusals_then_succeeds(tmp_path, monkeypatch):
    calls, waits = [], []
    monkeypatch.setattr(tlc.requests, "get", _fake_get([403, 503, 200], calls))
    dest = download("https://x/f.parquet", tmp_path / "f.parquet", retry_waits=(1, 2), sleep=waits.append)
    assert dest.read_bytes() == b"parquet bytes"
    assert waits == [1, 2]
    assert len(calls) == 3 and all("nycdemand" in ua for ua in calls)


def test_download_gives_up_with_not_available(tmp_path, monkeypatch):
    calls, waits = [], []
    monkeypatch.setattr(tlc.requests, "get", _fake_get([403, 403], calls))
    with pytest.raises(NotAvailable):
        download("https://x/f.parquet", tmp_path / "f.parquet", retry_waits=(1,), sleep=waits.append)
    assert waits == [1]
    assert not (tmp_path / "f.parquet").exists()


def test_download_without_retries_fails_fast(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tlc.requests, "get", _fake_get([404], calls))
    with pytest.raises(NotAvailable):
        download("https://x/f.parquet", tmp_path / "f.parquet")
    assert len(calls) == 1
