"""DuckDB warehouse: raw landing, quarantine, incremental upsert and analysis queries."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from .transform import FIELDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id       VARCHAR PRIMARY KEY,
    as_of        TIMESTAMP NOT NULL,
    source       VARCHAR NOT NULL,
    fetched      INTEGER NOT NULL,
    rejected     INTEGER NOT NULL,
    duplicates   INTEGER NOT NULL,
    inserted     INTEGER NOT NULL,
    seen_again   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_jobs (
    run_id       VARCHAR NOT NULL,
    record       JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS rejected_jobs (
    run_id       VARCHAR NOT NULL,
    reasons      VARCHAR[] NOT NULL,
    record       JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    job_uid         VARCHAR PRIMARY KEY,
    title           VARCHAR NOT NULL,
    company         VARCHAR NOT NULL,
    location        VARCHAR,
    country         VARCHAR,
    is_remote       BOOLEAN NOT NULL,
    posted_at       TIMESTAMP,
    salary_min      DOUBLE,
    salary_max      DOUBLE,
    salary_currency VARCHAR,
    job_url         VARCHAR,
    apply_url       VARCHAR NOT NULL,
    source          VARCHAR,
    first_seen_at   TIMESTAMP NOT NULL,
    last_seen_at    TIMESTAMP NOT NULL
);
-- One row per distinct role. Reposts under new links collapse to the newest.
CREATE OR REPLACE VIEW unique_roles AS
SELECT * FROM jobs
QUALIFY row_number() OVER (
    PARTITION BY lower(title), lower(company), lower(coalesce(location, ''))
    ORDER BY posted_at DESC NULLS LAST, last_seen_at DESC, job_uid
) = 1;
"""

# Columns that may legitimately change between sightings. The rest form the key.
MUTABLE = [f for f in FIELDS if f not in ("job_uid", "title", "company", "apply_url")]


class Warehouse:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))
        self.con.execute(SCHEMA)

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *_: object) -> None:
        self.con.close()

    def load(
        self,
        run_id: str,
        as_of: datetime,
        source: str,
        raw: list[dict[str, Any]],
        rejected: list[tuple[list[str], dict[str, Any]]],
        rows: list[dict[str, Any]],
        duplicates: int,
    ) -> dict[str, int]:
        """Load one run in a single transaction. Any failure rolls all of it back."""
        con = self.con
        con.execute("BEGIN")
        try:
            before = self.count("jobs")
            if raw:
                con.executemany(
                    "INSERT INTO raw_jobs VALUES (?, ?)", [(run_id, json.dumps(r)) for r in raw]
                )
            if rejected:
                con.executemany(
                    "INSERT INTO rejected_jobs VALUES (?, ?, ?)",
                    [(run_id, why, json.dumps(r)) for why, r in rejected],
                )
            if rows:
                self._upsert(rows, as_of)
            inserted = self.count("jobs") - before
            stats = {
                "fetched": len(raw),
                "rejected": len(rejected),
                "duplicates": duplicates,
                "inserted": inserted,
                "seen_again": len(rows) - inserted,
            }
            con.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, as_of, source, *stats.values()],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return stats

    def _upsert(self, rows: list[dict[str, Any]], as_of: datetime) -> None:
        cols = [*FIELDS, "first_seen_at", "last_seen_at"]
        # An older snapshot replayed late must not overwrite newer values.
        newer = "excluded.last_seen_at >= jobs.last_seen_at"
        sets = [
            f"{c} = CASE WHEN {newer} THEN coalesce(excluded.{c}, jobs.{c}) ELSE jobs.{c} END"
            for c in MUTABLE
        ]
        sets.append("first_seen_at = least(jobs.first_seen_at, excluded.first_seen_at)")
        sets.append("last_seen_at = greatest(jobs.last_seen_at, excluded.last_seen_at)")
        self.con.executemany(
            f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT (job_uid) DO UPDATE SET {', '.join(sets)}",
            [[*(r[f] for f in FIELDS), as_of, as_of] for r in rows],
        )

    def count(self, table: str) -> int:
        return self.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        cur = self.con.execute(sql, params or [])
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    def latest_run(self) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM runs ORDER BY as_of DESC, run_id DESC LIMIT 1")
        return rows[0] if rows else None

    def shortlist(
        self,
        as_of: datetime,
        keyword: str | None = None,
        remote_only: bool = False,
        min_salary: float | None = None,
        max_age_days: int = 7,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        """Newest matching roles, with reposts collapsed."""
        where = ["posted_at >= CAST(? AS TIMESTAMP) - to_days(CAST(? AS INTEGER))"]
        params: list[Any] = [as_of, max_age_days]
        if keyword:
            where.append("contains(lower(title), lower(?))")
            params.append(keyword)
        if remote_only:
            where.append("is_remote")
        if min_salary is not None:
            where.append("salary_max >= ?")
            params.append(min_salary)
        return self.query(
            "SELECT title, company, location, is_remote, salary_min, salary_max, posted_at, "
            f"apply_url FROM unique_roles WHERE {' AND '.join(where)} "
            "ORDER BY posted_at DESC, job_uid LIMIT ?",
            [*params, limit],
        )

    def export_parquet(self, path: str | Path) -> int:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        target = str(path).replace("'", "''")
        self.con.execute(
            f"COPY (SELECT * FROM jobs ORDER BY job_uid) TO '{target}' (FORMAT parquet)"
        )
        return self.count("jobs")
