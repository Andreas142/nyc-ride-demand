"""Command line entry point: ``nycdemand <command>``."""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import date

from nycdemand.config import DEFAULT_CONFIG, load_config, month_range
from nycdemand.ingest.tlc import SUPPORTED_SERVICES, Manifest, ingest_tlc, ingest_zones
from nycdemand.ingest.weather import ingest_weather
from nycdemand.pipeline import backfill


def _months(cfg, start: str | None, end: str | None) -> list[str]:
    """Requested months, never past the current month (TLC cannot have published those)."""
    this_month = date.today().strftime("%Y-%m")
    return month_range(start or cfg.start_month, min(end or cfg.end_month, this_month))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nycdemand", description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to project.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    zones = sub.add_parser("ingest-zones", help="download the taxi zone lookup")
    zones.add_argument("--force", action="store_true")

    tlc = sub.add_parser("ingest-tlc", help="ingest monthly trip records into bronze")
    tlc.add_argument("--services", nargs="+", choices=SUPPORTED_SERVICES)
    tlc.add_argument("--start", help="first month, YYYY-MM (default: config)")
    tlc.add_argument("--end", help="last month, YYYY-MM (default: config)")
    tlc.add_argument("--force", action="store_true", help="rebuild months already in bronze")
    tlc.add_argument("--keep-raw", action="store_true", help="keep downloaded raw files")
    tlc.add_argument(
        "--remote",
        action="store_true",
        help="stream with DuckDB instead of downloading (many requests; TLC may block them)",
    )

    weather = sub.add_parser("ingest-weather", help="ingest hourly Central Park weather")
    weather.add_argument("--start-year", type=int)
    weather.add_argument("--end-year", type=int)
    weather.add_argument("--force", action="store_true")

    fill = sub.add_parser("backfill", help="month by month: ingest, build the marts, prune older trip files")
    fill.add_argument("--start", help="first month, YYYY-MM (default: config)")
    fill.add_argument("--end", help="last month, YYYY-MM (default: config)")
    fill.add_argument("--services", nargs="+", choices=SUPPORTED_SERVICES)
    fill.add_argument("--no-prune", action="store_true", help="keep every trip-level file")
    fill.add_argument("--keep-whole", type=int, default=1, help="newest built months kept unpruned")
    fill.add_argument("--rebuild", action="store_true", help="re-download and rebuild built months")

    sub.add_parser("status", help="summarise what is in bronze and in the marts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = load_config(args.config)

    if args.command == "ingest-zones":
        ingest_zones(cfg, force=args.force)
    elif args.command == "ingest-tlc":
        months = _months(cfg, args.start, args.end)
        results = ingest_tlc(
            cfg,
            services=args.services,
            months=months,
            force=args.force,
            keep_raw=args.keep_raw,
            remote=args.remote,
        )
        counts: dict[str, int] = defaultdict(int)
        for r in results:
            counts[r.status] += 1
        print(", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing to do")
    elif args.command == "ingest-weather":
        ingest_weather(cfg, args.start_year, args.end_year, force=args.force)
    elif args.command == "backfill":
        months = _months(cfg, args.start, args.end)
        statuses = backfill(
            cfg,
            months,
            services=args.services,
            prune=not args.no_prune,
            keep_whole=args.keep_whole,
            rebuild=args.rebuild,
        )
        counts = defaultdict(int)
        for s in statuses:
            counts[s.status] += 1
        print(", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "nothing to do")
    elif args.command == "status":
        manifest = Manifest(cfg.bronze_dir / "_manifest.json")
        by_service: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        for key, info in sorted(manifest.entries.items()):
            service, month = key.split("/")
            by_service[service].append((month, info))
        if not by_service:
            print("bronze is empty; run `make sample` first")
        for service, items in by_service.items():
            total = sum(info["rows"] for _, info in items)
            built = sum("built_at" in info for _, info in items)
            pruned = sum("pruned_at" in info for _, info in items)
            print(
                f"{service:6} {len(items):3} months  {items[0][0]}..{items[-1][0]}  "
                f"{total:,} rows  in marts: {built}  pruned to a sample: {pruned}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
