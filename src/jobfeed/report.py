"""Markdown data-quality report, computed in SQL."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .store import Warehouse


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    def line(cells: list[Any]) -> str:
        return "| " + " | ".join(str(c) for c in cells) + " |"

    return "\n".join([line(headers), line(["---"] * len(headers)), *map(line, rows)])


def build_report(wh: Warehouse, as_of: datetime) -> str:
    run = wh.latest_run()
    if run is None:
        return "No runs yet.\n"
    totals = wh.query(
        """
        SELECT count(*) AS jobs,
               count(DISTINCT company) AS companies,
               (SELECT count(*) FROM unique_roles) AS roles,
               coalesce(avg(is_remote::INT), 0) AS remote,
               coalesce(avg((salary_max IS NOT NULL)::INT), 0) AS salary,
               coalesce(avg((posted_at IS NOT NULL)::INT), 0) AS posted,
               coalesce(avg((location IS NOT NULL)::INT), 0) AS located
        FROM jobs
        """
    )[0]
    freshness = wh.query(
        """
        SELECT CASE WHEN posted_at IS NULL THEN 'unknown'
                    WHEN age <= 24 THEN 'under 1 day'
                    WHEN age <= 72 THEN '1 to 3 days'
                    WHEN age <= 168 THEN '3 to 7 days'
                    ELSE 'over 7 days' END AS bucket,
               count(*) AS n, min(coalesce(age, 1e9)) AS ord
        FROM (SELECT posted_at, date_diff('minute', posted_at, CAST(? AS TIMESTAMP)) / 60.0 AS age
              FROM jobs)
        GROUP BY bucket ORDER BY ord
        """,
        [as_of],
    )
    reasons = wh.query(
        "SELECT reason, count(*) AS n FROM (SELECT unnest(reasons) AS reason FROM rejected_jobs "
        "WHERE run_id = ?) GROUP BY reason ORDER BY n DESC, reason",
        [run["run_id"]],
    )
    companies = wh.query(
        "SELECT company, count(*) AS n FROM unique_roles "
        "GROUP BY company ORDER BY n DESC, company LIMIT 5"
    )
    pay = wh.query(
        "SELECT title, count(*) AS n, median((salary_min + salary_max) / 2) AS mid "
        "FROM unique_roles WHERE salary_max IS NOT NULL AND salary_currency = 'USD' "
        "GROUP BY title ORDER BY mid DESC LIMIT 7"
    )
    counts = ("fetched", "rejected", "duplicates", "inserted", "seen_again")
    parts = [
        f"# Data quality report, {as_of:%Y-%m-%d %H:%M} UTC",
        f"## Latest run ({run['source']})",
        _table(list(counts), [[run[c] for c in counts]]),
        "### Rejected rows by reason",
        _table(["reason", "rows"], [[r["reason"], r["n"]] for r in reasons])
        if reasons
        else "None.",
        "## Warehouse",
        _table(
            ["jobs", "unique roles", "companies", "remote"],
            [[totals["jobs"], totals["roles"], totals["companies"], f"{totals['remote']:.0%}"]],
        ),
        "### Completeness",
        _table(
            ["posted date", "location", "salary"],
            [[f"{totals[k]:.0%}" for k in ("posted", "located", "salary")]],
        ),
        "### Freshness",
        _table(["age", "jobs"], [[r["bucket"], r["n"]] for r in freshness]),
        "### Most active companies",
        _table(["company", "unique roles"], [[r["company"], r["n"]] for r in companies]),
        "### Median advertised salary (USD, annualised midpoint)",
        _table(
            ["title", "roles", "median"], [[r["title"], r["n"], f"${r['mid']:,.0f}"] for r in pay]
        ),
    ]
    return "\n\n".join(parts) + "\n"
