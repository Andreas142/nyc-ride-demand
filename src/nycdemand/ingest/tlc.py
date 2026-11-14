"""Ingest NYC TLC trip records into a slim, standardised bronze layer.

Each monthly file is downloaded once with a single request (retried with back-off when the
server refuses it), reduced to the columns this project uses, renamed to one schema shared
by every service and year, and written to ``data/bronze/<service>/<service>_<YYYY-MM>.parquet``.
The download is deleted afterwards.

TLC's file server answers 403 both for files that are not published yet and when it is
refusing a client that sent too many requests. A month older than PUBLICATION_LAG_MONTHS
cannot be unpublished, so a 403 for it is retried and then raised as an error, never
skipped: a silent hole in the history would be worse than a stopped run.

A manifest (``data/bronze/_manifest.json``) records row counts and sources, so re-runs skip
months that are already done and every bronze file can be traced back to its source URL.

Trip-level files are large, and every later step reads monthly aggregates. Once a month has
been built into the marts it can be pruned: a 2% random sample of its trips is kept in
``data/bronze/<service>_sample/`` and the full file is deleted (ADR-007).
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import requests

from nycdemand.config import Config

log = logging.getLogger(__name__)

SUPPORTED_SERVICES = ("yellow", "green", "fhvhv")

# TLC publishes with a lag of 1-3 months; a month older than this must exist.
PUBLICATION_LAG_MONTHS = 4
# Waits before each retry of a refused or failed download, in seconds.
RETRY_WAITS = (30, 90, 270)
USER_AGENT = "nycdemand/0.1 (portfolio project; +https://github.com/Andreas142/nyc-ride-demand)"
_RETRY_STATUSES = {403, 429, 500, 502, 503, 504}


class NotAvailable(FileNotFoundError):
    """The server says the file is not there (403/404), and retrying did not change that."""


def is_recent(month: str, today: date | None = None, lag_months: int = PUBLICATION_LAG_MONTHS) -> bool:
    """True when TLC may simply not have published ``month`` yet."""
    today = today or date.today()
    year, mon = (int(part) for part in month.split("-"))
    age = (today.year - year) * 12 + (today.month - mon)
    return age < lag_months


# Bronze schema: output column -> (source column candidates, SQL type).
# Matching is case-insensitive because TLC has changed capitalisation over the years
# (airport_fee vs Airport_fee). A source column that does not exist becomes NULL, which is
# how cbd_congestion_fee behaves for months before January 2025.
SCHEMAS: dict[str, dict[str, tuple[tuple[str, ...], str]]] = {
    "fhvhv": {
        "provider": (("hvfhs_license_num",), "VARCHAR"),
        "request_at": (("request_datetime",), "TIMESTAMP"),
        "pickup_at": (("pickup_datetime",), "TIMESTAMP"),
        "dropoff_at": (("dropoff_datetime",), "TIMESTAMP"),
        "pu_zone_id": (("PULocationID",), "INTEGER"),
        "do_zone_id": (("DOLocationID",), "INTEGER"),
        "trip_miles": (("trip_miles",), "DOUBLE"),
        "trip_seconds": (("trip_time",), "DOUBLE"),
        "fare": (("base_passenger_fare",), "DOUBLE"),
        "tolls": (("tolls",), "DOUBLE"),
        "tips": (("tips",), "DOUBLE"),
        "congestion_surcharge": (("congestion_surcharge",), "DOUBLE"),
        "airport_fee": (("airport_fee",), "DOUBLE"),
        "cbd_congestion_fee": (("cbd_congestion_fee",), "DOUBLE"),
        "driver_pay": (("driver_pay",), "DOUBLE"),
    },
    "yellow": {
        "provider": (("VendorID",), "VARCHAR"),
        "request_at": ((), "TIMESTAMP"),
        "pickup_at": (("tpep_pickup_datetime",), "TIMESTAMP"),
        "dropoff_at": (("tpep_dropoff_datetime",), "TIMESTAMP"),
        "pu_zone_id": (("PULocationID",), "INTEGER"),
        "do_zone_id": (("DOLocationID",), "INTEGER"),
        "trip_miles": (("trip_distance",), "DOUBLE"),
        "trip_seconds": ((), "DOUBLE"),
        "fare": (("fare_amount",), "DOUBLE"),
        "tolls": (("tolls_amount",), "DOUBLE"),
        "tips": (("tip_amount",), "DOUBLE"),
        "congestion_surcharge": (("congestion_surcharge",), "DOUBLE"),
        "airport_fee": (("airport_fee",), "DOUBLE"),
        "cbd_congestion_fee": (("cbd_congestion_fee",), "DOUBLE"),
        "driver_pay": ((), "DOUBLE"),
    },
    "green": {
        "provider": (("VendorID",), "VARCHAR"),
        "request_at": ((), "TIMESTAMP"),
        "pickup_at": (("lpep_pickup_datetime",), "TIMESTAMP"),
        "dropoff_at": (("lpep_dropoff_datetime",), "TIMESTAMP"),
        "pu_zone_id": (("PULocationID",), "INTEGER"),
        "do_zone_id": (("DOLocationID",), "INTEGER"),
        "trip_miles": (("trip_distance",), "DOUBLE"),
        "trip_seconds": ((), "DOUBLE"),
        "fare": (("fare_amount",), "DOUBLE"),
        "tolls": (("tolls_amount",), "DOUBLE"),
        "tips": (("tip_amount",), "DOUBLE"),
        "congestion_surcharge": (("congestion_surcharge",), "DOUBLE"),
        "airport_fee": (("airport_fee",), "DOUBLE"),
        "cbd_congestion_fee": (("cbd_congestion_fee",), "DOUBLE"),
        "driver_pay": ((), "DOUBLE"),
    },
}
OUTPUT_COLUMNS: tuple[str, ...] = (*SCHEMAS["fhvhv"], "source_month")

# Taxi files have no duration column, so it is derived from the two timestamps.
_DERIVED_DURATION = {
    "yellow": ("tpep_pickup_datetime", "tpep_dropoff_datetime"),
    "green": ("lpep_pickup_datetime", "lpep_dropoff_datetime"),
}


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def month_url(base_url: str, service: str, month: str) -> str:
    return f"{base_url.rstrip('/')}/{service}_tripdata_{month}.parquet"


def build_select_sql(service: str, available_columns: Iterable[str], source_sql: str, month: str) -> str:
    """SELECT that maps one raw TLC file onto the bronze schema."""
    if service not in SCHEMAS:
        raise ValueError(f"Unsupported service {service!r}; choose from {SUPPORTED_SERVICES}")
    lookup = {col.lower(): col for col in available_columns}
    parts: list[str] = []
    for out_col, (candidates, sql_type) in SCHEMAS[service].items():
        derived = _DERIVED_DURATION.get(service) if out_col == "trip_seconds" else None
        if derived and all(col.lower() in lookup for col in derived):
            start, end = (_ident(lookup[col.lower()]) for col in derived)
            expr = (
                f"CAST(date_diff('second', TRY_CAST({start} AS TIMESTAMP), "
                f"TRY_CAST({end} AS TIMESTAMP)) AS {sql_type})"
            )
        else:
            match = next((lookup[c.lower()] for c in candidates if c.lower() in lookup), None)
            expr = f"TRY_CAST({_ident(match)} AS {sql_type})" if match else f"CAST(NULL AS {sql_type})"
        parts.append(f"{expr} AS {out_col}")
    parts.append(f"{sql_literal(month)} AS source_month")
    return "SELECT " + ", ".join(parts) + f" FROM {source_sql}"


def connect(memory_limit: str = "4GB", threads: int = 2) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET memory_limit = {sql_literal(memory_limit)}")
    con.execute(f"SET threads = {int(threads)}")
    return con


def write_month(
    con: duckdb.DuckDBPyConnection, service: str, month: str, source: str | Path, out_path: Path
) -> int:
    """Read one raw file (URL or local path), write the slim bronze file, return its row count."""
    source_sql = f"read_parquet({sql_literal(str(source))})"
    columns = [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {source_sql}").fetchall()]
    query = build_select_sql(service, columns, source_sql, month)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    con.execute(f"COPY ({query}) TO {sql_literal(str(tmp_path))} (FORMAT PARQUET, COMPRESSION ZSTD)")
    rows = con.execute(f"SELECT count(*) FROM read_parquet({sql_literal(str(tmp_path))})").fetchone()[0]
    tmp_path.replace(out_path)
    return int(rows)


def download(
    url: str,
    dest: Path,
    timeout: int = 120,
    retry_waits: tuple[float, ...] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Stream a file to disk with one request; retry after each wait in ``retry_waits``.

    Raises NotAvailable when the server keeps answering 403/404. A plain filesystem path is
    copied instead, which allows an offline mirror of the data (and the tests).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not url.startswith(("http://", "https://")):
        source = Path(url)
        if not source.exists():
            raise NotAvailable(f"Not available: {url}")
        shutil.copyfile(source, dest)
        return dest

    attempts = len(retry_waits) + 1
    for attempt in range(1, attempts + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": USER_AGENT}) as resp:
                if resp.status_code in _RETRY_STATUSES and attempt < attempts:
                    wait = retry_waits[attempt - 1]
                    log.warning("HTTP %s for %s; retrying in %ss", resp.status_code, url, wait)
                    sleep(wait)
                    continue
                if resp.status_code in (403, 404):
                    raise NotAvailable(f"Not available ({resp.status_code}): {url}")
                resp.raise_for_status()
                part = dest.with_name(dest.name + ".part")
                with part.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
                part.replace(dest)
                return dest
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == attempts:
                raise
            wait = retry_waits[attempt - 1]
            log.warning("%s for %s; retrying in %ss", type(exc).__name__, url, wait)
            sleep(wait)
    raise AssertionError("unreachable")


class Manifest:
    """Row counts and sources for every bronze file, keyed by ``service/month``."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}

    def get(self, service: str, month: str) -> dict:
        return self.entries.get(f"{service}/{month}", {})

    def is_done(self, service: str, month: str, out_path: Path) -> bool:
        """Ingested already: the bronze file exists, or it was pruned after being built."""
        entry = self.get(service, month)
        return bool(entry) and (out_path.exists() or self.is_pruned(service, month))

    def is_built(self, service: str, month: str) -> bool:
        return "built_at" in self.get(service, month)

    def is_pruned(self, service: str, month: str) -> bool:
        return "pruned_at" in self.get(service, month)

    def record(self, service: str, month: str, **info) -> None:
        """Start a fresh entry for a newly written bronze file."""
        self.entries[f"{service}/{month}"] = info
        self._save()

    def update(self, service: str, month: str, **info) -> None:
        """Add fields to an existing entry (built_at, pruned_at, ...)."""
        self.entries[f"{service}/{month}"] = {**self.get(service, month), **info}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.entries, indent=2, sort_keys=True))
        tmp.replace(self.path)


@dataclass
class MonthResult:
    service: str
    month: str
    status: str  # written | skipped | unavailable
    rows: int = 0
    method: str = ""


def ingest_tlc(
    cfg: Config,
    services: Iterable[str] | None = None,
    months: Iterable[str] | None = None,
    force: bool = False,
    keep_raw: bool = False,
    remote: bool = False,
    today: date | None = None,
    retry_waits: tuple[float, ...] = RETRY_WAITS,
) -> list[MonthResult]:
    """Ingest the requested service-months into bronze.

    Recent months that TLC has not published yet are reported as "unavailable" and skipped.
    An older month that cannot be downloaded raises RuntimeError instead (see module docs).
    ``remote=True`` streams with DuckDB instead of downloading; it sends hundreds of range
    requests per file, which TLC's server may answer by refusing every request for a while.
    """
    services = tuple(services or cfg.services)
    months = list(months or cfg.months)
    unknown = set(services) - set(SUPPORTED_SERVICES)
    if unknown:
        raise ValueError(f"Unsupported services {sorted(unknown)}; choose from {SUPPORTED_SERVICES}")

    manifest = Manifest(cfg.bronze_dir / "_manifest.json")
    con = connect(cfg.duckdb_memory_limit, cfg.duckdb_threads)
    results: list[MonthResult] = []
    for service in services:
        for month in months:
            out_path = bronze_path(cfg, service, month)
            if not force and manifest.is_done(service, month, out_path):
                log.info("skip %s %s (already in bronze)", service, month)
                results.append(MonthResult(service, month, "skipped"))
                continue
            url = month_url(cfg.tlc_base_url, service, month)
            recent = is_recent(month, today)
            waits = () if recent else tuple(retry_waits)
            try:
                rows, method = _ingest_one(con, cfg, service, month, url, out_path, keep_raw, remote, waits)
            except NotAvailable as exc:
                if not recent:
                    raise RuntimeError(
                        f"{service} {month} should be published but the server refused it ({exc}). "
                        "This is most likely TLC rate-limiting this machine: wait 30-60 minutes and "
                        "run the same command again. Months already done are skipped."
                    ) from exc
                log.info("%s %s not published yet; skipping", service, month)
                results.append(MonthResult(service, month, "unavailable"))
                continue
            manifest.record(
                service,
                month,
                rows=rows,
                source=url,
                method=method,
                written_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            log.info("wrote %s %s: %s rows via %s", service, month, f"{rows:,}", method)
            results.append(MonthResult(service, month, "written", rows, method))
    return results


def _ingest_one(con, cfg, service, month, url, out_path, keep_raw, remote, retry_waits):
    if remote:
        try:
            return write_month(con, service, month, url, out_path), "remote"
        except duckdb.Error as exc:
            reason = str(exc).splitlines()[0][:120]
            log.info("remote read failed for %s %s (%s); downloading", service, month, reason)
    raw_path = cfg.raw_dir / service / f"{service}_tripdata_{month}.parquet"
    if not raw_path.exists():
        download(url, raw_path, retry_waits=retry_waits)
    try:
        return write_month(con, service, month, raw_path, out_path), "download"
    finally:
        if not keep_raw:
            raw_path.unlink(missing_ok=True)


def bronze_path(cfg: Config, service: str, month: str) -> Path:
    return cfg.bronze_dir / service / f"{service}_{month}.parquet"


def sample_path(cfg: Config, service: str, month: str) -> Path:
    return cfg.bronze_dir / f"{service}_sample" / f"{service}_{month}.parquet"


def prune_month(
    cfg: Config,
    manifest: Manifest,
    service: str,
    month: str,
    sample_percent: float = 2.0,
    seed: int = 42,
) -> bool:
    """Keep a random sample of one service-month and delete its trip-level file.

    Refuses to prune a month that has not been built into the marts, because the marts
    would then be the only copy of nothing. Returns True when the month was pruned.
    """
    src = bronze_path(cfg, service, month)
    if manifest.is_pruned(service, month) or not src.exists():
        return False
    if not manifest.is_built(service, month):
        raise RuntimeError(f"{service} {month} is not in the marts yet; refusing to prune it")
    dst = sample_path(cfg, service, month)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    con = connect(cfg.duckdb_memory_limit, cfg.duckdb_threads)
    con.execute(
        f"COPY (SELECT * FROM read_parquet({sql_literal(str(src))}) "
        f"USING SAMPLE {float(sample_percent)} PERCENT (bernoulli, {int(seed)})) "
        f"TO {sql_literal(str(tmp))} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    rows = con.execute(f"SELECT count(*) FROM read_parquet({sql_literal(str(tmp))})").fetchone()[0]
    tmp.replace(dst)
    src.unlink()
    manifest.update(
        service,
        month,
        pruned_at=datetime.now(UTC).isoformat(timespec="seconds"),
        sample_rows=int(rows),
        sample_percent=float(sample_percent),
    )
    log.info("pruned %s %s: kept %s sampled rows", service, month, f"{rows:,}")
    return True


def ingest_zones(cfg: Config, force: bool = False) -> Path:
    """Download the official taxi zone lookup (LocationID, Borough, Zone, service_zone)."""
    dest = cfg.bronze_dir / "zones" / "taxi_zone_lookup.csv"
    if dest.exists() and not force:
        log.info("skip zones (already downloaded)")
        return dest
    download(cfg.zone_lookup_url, dest)
    log.info("wrote %s", dest)
    return dest
