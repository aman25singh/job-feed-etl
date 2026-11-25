import hashlib
from datetime import datetime

import pytest

from jobfeed.quality import validate
from jobfeed.transform import FIELDS, dedupe, make_uid, normalize, parse_timestamp


def test_uid_contract():
    assert make_uid("u", "t", "c") == hashlib.sha256(b"u|t|c").hexdigest()[:16]


@pytest.mark.parametrize("field", ["url", "title", "organization"])
def test_identity_fields_change_the_uid(record, field):
    assert (
        normalize(record())["job_uid"]
        != normalize(record(**{field: "https://other.example"}))["job_uid"]
    )


def test_other_fields_do_not_change_the_uid(record):
    a = normalize(record())
    b = normalize(record(date_posted="2026-01-01", ai_salary_min_value=1, source="lever.co"))
    assert a["job_uid"] == b["job_uid"]


def test_documented_shape(record):
    row = normalize(record())
    assert tuple(row) == FIELDS
    assert row["company"] == "Acme"
    assert row["location"] == "Chicago, Illinois, United States"
    assert row["posted_at"] == datetime(2026, 1, 15, 8)
    assert (row["salary_min"], row["salary_max"]) == (100_000, 130_000)
    assert row["apply_url"] == row["job_url"] == "https://jobs.example.com/1"


def test_nested_fallback_shape():
    row = normalize(
        {
            "job": {"title": "ML Engineer", "location": {"text": "Remote, US"}},
            "employer": {"name": "Initech"},
            "url": "https://x.example/1",
            "datePosted": "2026-01-10",
            "remote": "true",
        }
    )
    assert (row["title"], row["company"], row["location"]) == (
        "ML Engineer", "Initech", "Remote, US",
    )  # fmt: skip
    assert row["is_remote"] is True


def test_empty_record_does_not_crash():
    row = normalize({})
    assert tuple(row) == FIELDS and len(row["job_uid"]) == 16 and row["is_remote"] is False


def test_whitespace_is_cleaned(record):
    assert normalize(record(title="  Data\n Engineer "))["title"] == "Data Engineer"


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"location_type": "TELECOMMUTE"}, True),
        ({"ai_work_arrangement": "Remote OK"}, True),
        ({"ai_work_arrangement": "Hybrid"}, False),
        ({"remote": True}, True),
        ({"remote": "no"}, False),
        ({}, False),
    ],
)
def test_remote(record, fields, expected):
    assert normalize(record(**fields))["is_remote"] is expected


@pytest.mark.parametrize(
    "unit, low, high, expected",
    [
        ("HOUR", 50, 60, (104_000, 124_800)),
        ("MONTH", 8_000, 9_000, (96_000, 108_000)),
        (None, 90_000, None, (90_000, 90_000)),
        ("FORTNIGHT", 3_000, 4_000, (None, None)),
        ("YEAR", "n/a", None, (None, None)),
        ("YEAR", -5, 0, (None, None)),
    ],
)
def test_salary_is_annualised(record, unit, low, high, expected):
    row = normalize(
        record(ai_salary_unit_text=unit, ai_salary_min_value=low, ai_salary_max_value=high)
    )
    assert (row["salary_min"], row["salary_max"]) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-01-15T08:00:00Z", datetime(2026, 1, 15, 8)),
        ("2026-01-15T08:00:00-05:00", datetime(2026, 1, 15, 13)),
        ("2026-01-15", datetime(2026, 1, 15)),
        ("yesterday", None),
        (None, None),
        (12345, None),
    ],
)
def test_parse_timestamp(value, expected):
    assert parse_timestamp(value) == expected


def test_dedupe_keeps_first_in_order():
    rows = [{"job_uid": u, "n": i} for i, u in enumerate("abab")]
    assert dedupe(rows) == rows[:2]


@pytest.mark.parametrize(
    "fields, reason",
    [
        ({"title": ""}, "missing_title"),
        ({"organization": None}, "missing_company"),
        ({"url": None}, "missing_apply_url"),
        ({"url": "javascript:alert(1)"}, "bad_apply_url"),
        ({"date_posted": "2027-01-01"}, "posted_in_future"),
        ({"ai_salary_min_value": 200_000}, "salary_min_above_max"),
    ],
)
def test_bad_rows_are_rejected_with_a_reason(record, as_of, fields, reason):
    assert validate(normalize(record(**fields)), as_of) == [reason]


def test_good_row_passes(record, as_of):
    assert validate(normalize(record()), as_of) == []


def test_unknown_date_and_salary_are_allowed(record, as_of):
    rec = record(date_posted=None, ai_salary_min_value=None, ai_salary_max_value=None)
    assert validate(normalize(rec), as_of) == []
