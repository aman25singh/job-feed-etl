"""Where raw records come from: the live API, or generated sample data."""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import requests

RETRYABLE = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """The feed could not be read."""


@dataclass(frozen=True)
class ApiConfig:
    api_key: str
    base_url: str = "https://data.fantastic.jobs"
    path: str = "/v1/active-ats"
    rapidapi_host: str | None = None

    @classmethod
    def from_env(cls) -> ApiConfig:
        key = os.getenv("JOBFEED_API_KEY", "").strip()
        if not key:
            raise SourceError(
                "JOBFEED_API_KEY is not set. Copy .env.example to .env, or use --sample."
            )
        return cls(
            api_key=key,
            base_url=os.getenv("JOBFEED_BASE_URL") or cls.base_url,
            path=os.getenv("JOBFEED_PATH") or cls.path,
            rapidapi_host=os.getenv("JOBFEED_RAPIDAPI_HOST") or None,
        )

    @property
    def headers(self) -> dict[str, str]:
        if self.rapidapi_host:
            return {"x-rapidapi-key": self.api_key, "x-rapidapi-host": self.rapidapi_host}
        return {"Authorization": f"Bearer {self.api_key}"}


class ApiSource:
    """Offset-paginated reader with capped retries."""

    def __init__(
        self,
        config: ApiConfig,
        session: requests.Session | None = None,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.max_attempts = max_attempts
        self.sleep = sleep

    def fetch(
        self, query: str, location: str, time_frame: str, limit: int, page_size: int = 100
    ) -> Iterator[dict[str, Any]]:
        offset = 0
        while offset < limit:
            size = min(page_size, limit - offset)
            page = self._get_page(
                {
                    "title": query,
                    "location": location,
                    "time_frame": time_frame,
                    "limit": size,
                    "offset": offset,
                }
            )
            yield from page[:size]
            if len(page) < size:
                return
            offset += size

    def _get_page(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        url = self.config.base_url.rstrip("/") + self.config.path
        problem = ""
        for attempt in range(1, self.max_attempts + 1):
            wait = min(2.0**attempt, 60)
            try:
                resp = self.session.get(url, headers=self.config.headers, params=params, timeout=30)
            except (requests.ConnectionError, requests.Timeout) as exc:
                problem = type(exc).__name__
            else:
                if resp.status_code not in RETRYABLE:
                    return self._parse(resp)
                problem = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = min(float(retry_after), 60)
            if attempt < self.max_attempts:
                self.sleep(wait)
        raise SourceError(f"gave up after {self.max_attempts} attempts: {problem}")

    @staticmethod
    def _parse(resp: requests.Response) -> list[dict[str, Any]]:
        if resp.status_code >= 400:
            raise SourceError(f"HTTP {resp.status_code} from the job API")
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceError("the job API did not return JSON") from exc
        if isinstance(data, dict):
            data = next((data[k] for k in ("results", "data", "jobs", "items") if k in data), None)
        if not isinstance(data, list):
            raise SourceError("unexpected response shape from the job API")
        return [item for item in data if isinstance(item, dict)]


SAMPLE_EPOCH = datetime(2026, 1, 15, 12)
_TITLES = (
    "Data Engineer", "Senior Data Engineer", "Data Analyst", "Data Scientist",
    "Quantitative Analyst", "Analytics Engineer", "Machine Learning Engineer",
)  # fmt: skip
_COMPANIES = tuple(
    f"{first} {last}"
    for first in ("Northwind", "Blue Harbor", "Cedar", "Orbit", "Fennel", "Quill", "Granite")
    for last in ("Analytics", "Capital", "Health", "Logistics", "Payments", "Insurance", "Retail")
)
_CITIES = (
    "Chicago, Illinois", "New York, New York", "Austin, Texas", "Boston, Massachusetts",
    "Seattle, Washington", "Denver, Colorado", "Atlanta, Georgia", "Columbus, Ohio",
)  # fmt: skip


def sample_records(day: int = 1, per_day: int = 120) -> list[dict[str, Any]]:
    """Synthetic postings in the vendor's documented shape.

    Day ``n`` contains the last three days of postings, so consecutive days
    overlap the way a real feed does. A few records are broken on purpose.
    """
    out: list[dict[str, Any]] = []
    for d in range(max(1, day - 2), day + 1):
        out.extend(_sample_day(d, per_day))
    return out


def _sample_day(day: int, count: int) -> list[dict[str, Any]]:
    rng = random.Random(1000 + day)
    base = SAMPLE_EPOCH + timedelta(days=day - 1)
    records = []
    for i in range(count):
        low = rng.randrange(70, 160) * 1000
        hourly = rng.random() < 0.1
        job_id = day * 10_000 + i
        remote = rng.random() < 0.35
        rec: dict[str, Any] = {
            "id": job_id,
            "title": rng.choice(_TITLES),
            "organization": rng.choice(_COMPANIES),
            "locations_derived": [f"{rng.choice(_CITIES)}, United States"],
            "countries_derived": ["United States"],
            "location_type": "TELECOMMUTE" if remote else None,
            "ai_work_arrangement": "Remote Solely" if remote else rng.choice(["Hybrid", "On-site"]),
            "date_posted": (base - timedelta(minutes=rng.randrange(1440))).isoformat(),
            "url": f"https://jobs.example.com/{job_id}",
            "source": rng.choice(["greenhouse", "lever.co", "workday", "ashby"]),
            "source_type": "ats",
            "ai_salary_currency": "USD",
            "ai_salary_min_value": low // 2080 if hourly else low,
            "ai_salary_max_value": low // 2080 + 15 if hourly else low + 30_000,
            "ai_salary_unit_text": "HOUR" if hourly else "YEAR",
        }
        if rng.random() < 0.3:
            for key in ("ai_salary_min_value", "ai_salary_max_value", "ai_salary_currency"):
                rec[key] = None
        fault = rng.random()
        if fault < 0.02:
            rec["organization"] = None
        elif fault < 0.03:
            rec["url"] = None
        elif fault < 0.04:
            rec["date_posted"] = (base + timedelta(days=400)).isoformat()
        elif fault < 0.05 and rec["ai_salary_min_value"]:
            rec["ai_salary_min_value"] = rec["ai_salary_max_value"] * 2
        records.append(rec)
        if rng.random() < 0.04:  # the feed repeats itself
            records.append(dict(rec))
        if rng.random() < 0.04:  # the same job reposted under a new link
            records.append({**rec, "id": job_id + 5_000, "url": f"{rec['url']}-repost"})
    return records
