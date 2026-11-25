"""Row-level validation. Bad rows are quarantined with a reason, never dropped silently."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

REQUIRED = ("title", "company", "apply_url")


def validate(row: dict[str, Any], as_of: datetime) -> list[str]:
    """Return the reasons a row must be rejected. An empty list means it is valid."""
    problems = [f"missing_{field}" for field in REQUIRED if not row[field]]
    if row["apply_url"] and not row["apply_url"].startswith(("http://", "https://")):
        problems.append("bad_apply_url")
    if row["posted_at"] and row["posted_at"] > as_of + timedelta(days=1):
        problems.append("posted_in_future")
    low, high = row["salary_min"], row["salary_max"]
    if low is not None and high is not None and low > high:
        problems.append("salary_min_above_max")
    return problems
