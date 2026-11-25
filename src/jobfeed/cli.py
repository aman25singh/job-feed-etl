"""Command line entry point: ``jobfeed run | report | shortlist | export``."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .pipeline import run
from .report import build_report
from .sources import SAMPLE_EPOCH, ApiConfig, ApiSource, SourceError, sample_records
from .store import Warehouse


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _as_of(wh: Warehouse) -> datetime:
    """Judge freshness relative to now, or to the sample clock for sample data."""
    latest = wh.latest_run()
    return latest["as_of"] if latest and latest["source"] == "sample" else _now()


def _require(path: str) -> None:
    if not Path(path).exists():
        raise SystemExit(f"{path} not found. Run 'jobfeed run --sample' first.")


def cmd_run(args: argparse.Namespace) -> None:
    if args.sample:
        source, as_of = "sample", SAMPLE_EPOCH + timedelta(days=args.day - 1)
        records = sample_records(args.day)
    else:
        source, as_of = "api", _now()
        try:
            api = ApiSource(ApiConfig.from_env())
            records = list(api.fetch(args.query, args.location, args.time_frame, args.limit))
        except SourceError as exc:
            raise SystemExit(f"error: {exc}") from None
    with Warehouse(args.db) as wh:
        stats = run(wh, records, as_of, source)
        print("  ".join(f"{k}={v}" for k, v in stats.items()), f" total={wh.count('jobs')}")


def cmd_report(args: argparse.Namespace) -> None:
    _require(args.db)
    with Warehouse(args.db) as wh:
        text = build_report(wh, _as_of(wh))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text)


def cmd_shortlist(args: argparse.Namespace) -> None:
    _require(args.db)
    with Warehouse(args.db) as wh:
        rows = wh.shortlist(
            _as_of(wh), args.keyword, args.remote, args.min_salary, args.days, args.limit
        )
    if not rows:
        print("No matching jobs.")
    for r in rows:
        pay = f"${r['salary_min']:,.0f} to ${r['salary_max']:,.0f}" if r["salary_max"] else "n/a"
        place = "Remote" if r["is_remote"] else (r["location"] or "unknown")
        print(f"{r['posted_at']:%Y-%m-%d}  {r['title']} at {r['company']}")
        print(f"            {place}  |  {pay}  |  {r['apply_url']}")


def cmd_export(args: argparse.Namespace) -> None:
    _require(args.db)
    with Warehouse(args.db) as wh:
        print(f"wrote {wh.export_parquet(args.out)} rows to {args.out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobfeed", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.set_defaults(func=func)
        p.add_argument("--db", default="warehouse/jobs.duckdb")
        return p

    p = add("run", cmd_run, "fetch and load one batch")
    p.add_argument("--sample", action="store_true", help="use generated data, no API key")
    p.add_argument("--day", type=int, default=1, help="which sample day to load")
    p.add_argument("--query", default="data engineer")
    p.add_argument("--location", default="United States")
    p.add_argument("--time-frame", default="24h", choices=["1h", "24h", "7d"])
    p.add_argument("--limit", type=int, default=300)

    p = add("report", cmd_report, "print the data-quality report")
    p.add_argument("--out", help="write to a file instead")

    p = add("shortlist", cmd_shortlist, "list jobs worth applying to")
    p.add_argument("--keyword")
    p.add_argument("--remote", action="store_true")
    p.add_argument("--min-salary", type=float)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--limit", type=int, default=15)

    p = add("export", cmd_export, "write the jobs table to Parquet")
    p.add_argument("--out", default="warehouse/jobs.parquet")
    return parser


def main(argv: list[str] | None = None) -> None:
    load_dotenv(find_dotenv(usecwd=True))
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
