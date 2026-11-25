from datetime import datetime

import pytest
import requests

AS_OF = datetime(2026, 1, 15, 12)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if a test reaches for the real API."""

    def blocked(*args, **kwargs):
        raise AssertionError("network access is forbidden in tests")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    for name in ("API_KEY", "BASE_URL", "PATH", "RAPIDAPI_HOST"):
        monkeypatch.delenv(f"JOBFEED_{name}", raising=False)


@pytest.fixture
def as_of():
    return AS_OF


@pytest.fixture
def record():
    """Build a vendor record, overriding any field."""

    def build(**overrides):
        return {
            "id": 1,
            "title": "Data Engineer",
            "organization": "Acme",
            "locations_derived": ["Chicago, Illinois, United States"],
            "countries_derived": ["United States"],
            "location_type": None,
            "date_posted": "2026-01-15T08:00:00",
            "url": "https://jobs.example.com/1",
            "source": "greenhouse",
            "ai_salary_currency": "USD",
            "ai_salary_min_value": 100_000,
            "ai_salary_max_value": 130_000,
            "ai_salary_unit_text": "YEAR",
        } | overrides

    return build
