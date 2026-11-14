"""Month-by-month pipeline: ingest a month, build it into the marts, prune older months.

Every step is safe to repeat. Months already ingested are skipped, a rebuilt month replaces
itself in the marts (dbt ``delete+insert``), and a month is only pruned after it was built.
So an interrupted backfill continues with the same command.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nycdemand.config import Config
from nycdemand.ingest.tlc import Manifest, bronze_path, ingest_tlc, prune_month

log = logging.getLogger(__name__)

DBT_DIR = Path("dbt")


def dbt_build_month(month: str, dbt_dir: Path = DBT_DIR) -> None:
    """Run ``dbt build`` for one month (models and data tests). Run from the repo root."""
    from dbt.cli.main import dbtRunner  # imported late: dbt is slow to import

    args = ["build", "--no-version-check", "--quiet"]
    args += ["--project-dir", str(dbt_dir), "--profiles-dir", str(dbt_dir)]
    args += ["--vars", f"{{month: '{month}'}}"]
    try:
        result = dbtRunner().invoke(args)
    finally:
        # dbt-duckdb keeps the warehouse open (and locked) for the life of the process;
        # release it so notebooks and later steps can read the file.
        from dbt.adapters.duckdb.connections import DuckDBConnectionManager

        DuckDBConnectionManager.close_all_connections()
    if not result.success:
        raise RuntimeError(f"dbt build failed for {month}; run `make dbt-build MONTH={month}` for details")


@dataclass
class MonthStatus:
    month: str
    status: str  # built | already-built | unavailable
    pruned: tuple[str, ...] = ()


def backfill(
    cfg: Config,
    months: Iterable[str],
    services: Iterable[str] | None = None,
    prune: bool = True,
    keep_whole: int = 1,
    rebuild: bool = False,
    build: Callable[[str], None] = dbt_build_month,
) -> list[MonthStatus]:
    """Ingest, build and (optionally) prune the given months, oldest first.

    ``keep_whole`` is how many of the newest built months keep their trip-level files. At
    least one is kept so that dbt's sources never point at an empty folder.
    ``rebuild`` re-downloads and rebuilds months even if they are already in the marts.
    """
    services = tuple(services or cfg.services)
    keep_whole = max(1, keep_whole)
    manifest_path = cfg.bronze_dir / "_manifest.json"
    statuses: list[MonthStatus] = []

    for month in sorted(months):
        results = ingest_tlc(cfg, services=services, months=[month], force=rebuild)
        if any(r.status == "unavailable" for r in results):
            log.warning("%s: not published for every service yet; skipping", month)
            statuses.append(MonthStatus(month, "unavailable"))
            continue

        manifest = Manifest(manifest_path)  # re-read: ingest_tlc wrote to it
        if not rebuild and all(manifest.is_built(s, month) for s in services):
            log.info("%s: already in the marts", month)
            status = "already-built"
        else:
            missing = [s for s in services if not bronze_path(cfg, s, month).exists()]
            if missing:
                raise RuntimeError(
                    f"{month}: trip files for {missing} were pruned before the month was built; "
                    "run the backfill with --rebuild for this month"
                )
            log.info("%s: building marts", month)
            build(month)
            built_at = datetime.now(UTC).isoformat(timespec="seconds")
            for service in services:
                manifest.update(service, month, built_at=built_at)
            status = "built"

        pruned: list[str] = []
        if prune:
            for service in services:
                built = sorted(
                    key.split("/")[1]
                    for key in manifest.entries
                    if key.startswith(f"{service}/") and manifest.is_built(*key.split("/"))
                )
                for old in built[:-keep_whole]:
                    if prune_month(cfg, manifest, service, old):
                        pruned.append(f"{service}/{old}")
        statuses.append(MonthStatus(month, status, tuple(pruned)))
    return statuses
