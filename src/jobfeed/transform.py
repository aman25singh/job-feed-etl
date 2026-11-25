"""Map vendor records onto one stable schema. Pure functions, no I/O."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

FIELDS = (
    "job_uid", "title", "company", "location", "country", "is_remote", "posted_at",
    "salary_min", "salary_max", "salary_currency", "job_url", "apply_url", "source",
)  # fmt: skip

# Multipliers that turn a quoted pay period into an annual figure.
ANNUAL = {"HOUR": 2080, "DAY": 260, "WEEK": 52, "MONTH": 12, "YEAR": 1}


def make_uid(apply_url: str | None, title: str | None, company: str | None) -> str:
    """Deterministic 16-character id.

    The basis string and length are part of the stored schema. Changing either
    one means re-keying every stored row.
    """
    basis = f"{apply_url}|{title}|{company}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _get(rec: dict[str, Any], *path: str) -> Any:
    cur: Any = rec
    for key in path:
        cur = cur.get(key) if isinstance(cur, dict) else None
    return cur


def _first(*values: Any) -> Any:
    return next((v for v in values if v not in (None, "", [])), None)


def _text(value: Any) -> str | None:
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value if isinstance(v, str) and v)
    if value is None or isinstance(value, dict):
        return None
    return " ".join(str(value).split()) or None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and number != float("inf") else None


def parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO 8601 into naive UTC. Naive input is taken to be UTC already."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _is_remote(rec: dict[str, Any]) -> bool:
    if str(rec.get("location_type") or "").upper() == "TELECOMMUTE":
        return True
    if rec.get("ai_work_arrangement") in ("Remote Solely", "Remote OK"):
        return True
    flag = _first(rec.get("remote"), rec.get("remote_derived"))
    return str(flag or "").strip().lower() in ("true", "1", "yes", "remote")


def _salary(rec: dict[str, Any]) -> tuple[float | None, float | None]:
    """Annualised salary range. Unknown pay periods are dropped, not guessed."""
    low = _number(_first(rec.get("ai_salary_min_value"), rec.get("salary_min")))
    high = _number(_first(rec.get("ai_salary_max_value"), rec.get("salary_max")))
    single = _number(rec.get("ai_salary_value"))
    low, high = low or single or high, high or single or low
    factor = ANNUAL.get(str(rec.get("ai_salary_unit_text") or "YEAR").upper())
    if factor is None or low is None or high is None:
        return None, None
    return low * factor, high * factor


def normalize(rec: dict[str, Any]) -> dict[str, Any]:
    """Vendor record to stable schema. Never raises on missing or odd fields.

    The first name in each list is the vendor's documented field. The rest are
    shapes used by other job feeds.
    """
    title = _text(_first(rec.get("title"), _get(rec, "job", "title")))
    company = _text(
        _first(rec.get("organization"), rec.get("company"), _get(rec, "employer", "name"))
    )
    job_url = _text(_first(rec.get("url"), rec.get("job_url"), rec.get("source_url")))
    apply_url = _text(_first(rec.get("apply_url"), rec.get("application_url"), job_url))
    salary_min, salary_max = _salary(rec)
    return {
        "job_uid": make_uid(apply_url, title, company),
        "title": title,
        "company": company,
        "location": _text(
            _first(
                rec.get("locations_derived"),
                rec.get("location"),
                _get(rec, "job", "location", "text"),
            )
        ),
        "country": _text(_first(rec.get("countries_derived"), rec.get("country"))),
        "is_remote": _is_remote(rec),
        "posted_at": parse_timestamp(
            _first(rec.get("date_posted"), rec.get("posted_at"), rec.get("datePosted"))
        ),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": _text(_first(rec.get("ai_salary_currency"), rec.get("currency"))),
        "job_url": job_url,
        "apply_url": apply_url,
        "source": _text(_first(rec.get("source"), rec.get("origin"))),
    }


def dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first row for each ``job_uid``, preserving order."""
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        seen.setdefault(row["job_uid"], row)
    return list(seen.values())
