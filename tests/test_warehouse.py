from datetime import timedelta

import pytest

from jobfeed.pipeline import run
from jobfeed.report import build_report
from jobfeed.sources import sample_records
from jobfeed.store import Warehouse


@pytest.fixture
def wh():
    with Warehouse() as warehouse:
        yield warehouse


def jobs(wh):
    return wh.query("SELECT * FROM jobs ORDER BY job_uid")


def test_rerun_is_idempotent(wh, as_of):
    first = run(wh, sample_records(1), as_of, "sample")
    snapshot = jobs(wh)
    second = run(wh, sample_records(1), as_of, "sample")
    assert first["inserted"] > 100
    assert second["inserted"] == 0
    assert jobs(wh) == snapshot


def test_counts_reconcile(wh, as_of):
    s = run(wh, sample_records(1), as_of, "sample")
    assert s["fetched"] == s["rejected"] + s["duplicates"] + s["inserted"] + s["seen_again"]
    assert wh.count("raw_jobs") == s["fetched"]
    assert wh.count("rejected_jobs") == s["rejected"] > 0


def test_next_day_only_inserts_new_jobs(wh, as_of):
    a = run(wh, sample_records(1), as_of, "sample")
    b = run(wh, sample_records(2), as_of + timedelta(days=1), "sample")
    assert b["seen_again"] == a["inserted"]
    assert wh.count("jobs") == a["inserted"] + b["inserted"]


def test_seen_timestamps(wh, as_of, record):
    later = as_of + timedelta(days=2)
    run(wh, [record()], as_of, "t")
    run(wh, [record()], later, "t")
    row = jobs(wh)[0]
    assert (row["first_seen_at"], row["last_seen_at"]) == (as_of, later)


def test_changes_are_applied_and_nulls_do_not_erase(wh, as_of, record):
    run(wh, [record()], as_of, "t")
    update = record(ai_salary_max_value=150_000, locations_derived=None)
    run(wh, [update], as_of + timedelta(days=1), "t")
    row = jobs(wh)[0]
    assert row["salary_max"] == 150_000
    assert row["location"] == "Chicago, Illinois, United States"


def test_late_old_snapshot_does_not_overwrite(wh, as_of, record):
    posted = "2026-01-10"
    run(wh, [record(date_posted=posted, ai_salary_max_value=150_000)], as_of, "t")
    stats = run(wh, [record(date_posted=posted)], as_of - timedelta(days=3), "t")
    assert stats["seen_again"] == 1
    row = jobs(wh)[0]
    assert row["salary_max"] == 150_000
    assert row["first_seen_at"] == as_of - timedelta(days=3)
    assert row["last_seen_at"] == as_of


def test_failed_load_rolls_everything_back(wh, as_of, record, monkeypatch):
    def boom(*_):
        raise RuntimeError("disk full")

    monkeypatch.setattr(wh, "_upsert", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        run(wh, [record()], as_of, "t")
    assert [wh.count(t) for t in ("runs", "raw_jobs", "jobs")] == [0, 0, 0]


def test_failed_fetch_loads_nothing(wh, as_of, record):
    def broken():
        yield record()
        raise ConnectionError

    with pytest.raises(ConnectionError):
        run(wh, broken(), as_of, "t")
    assert wh.count("runs") == 0


def test_empty_feed_is_recorded(wh, as_of):
    assert run(wh, [], as_of, "t")["fetched"] == 0
    assert wh.count("runs") == 1
    assert "None." in build_report(wh, as_of)


def test_reposts_collapse_to_the_newest(wh, as_of, record):
    run(
        wh,
        [record(), record(url="https://jobs.example.com/new", date_posted="2026-01-15T10:00:00")],
        as_of,
        "t",
    )
    assert wh.count("jobs") == 2
    rows = wh.shortlist(as_of)
    assert [r["apply_url"] for r in rows] == ["https://jobs.example.com/new"]


def test_shortlist_filters(wh, as_of, record):
    run(
        wh,
        [
            record(url="https://e.com/1", location_type="TELECOMMUTE"),
            record(url="https://e.com/2", organization="B"),
            record(
                url="https://e.com/3",
                organization="C",
                ai_salary_max_value=None,
                ai_salary_min_value=None,
            ),
            record(url="https://e.com/4", organization="D", date_posted="2025-12-01"),
            record(url="https://e.com/5", organization="E", title="Analyst"),
        ],  # fmt: skip
        as_of,
        "t",
    )

    def urls(**kw):
        return {r["apply_url"][-1] for r in wh.shortlist(as_of, **kw)}

    assert urls() == {"1", "2", "3", "5"}
    assert urls(remote_only=True) == {"1"}
    assert urls(keyword="ENGINEER") == {"1", "2", "3"}
    assert urls(min_salary=120_000) == {"1", "2", "5"}
    assert urls(max_age_days=90) == {"1", "2", "3", "4", "5"}
    assert len(wh.shortlist(as_of, limit=2)) == 2


def test_keyword_is_not_sql(wh, as_of, record):
    run(wh, [record()], as_of, "t")
    assert wh.shortlist(as_of, keyword="'; DROP TABLE jobs; --") == []
    assert wh.count("jobs") == 1


def test_parquet_round_trip(wh, as_of, record, tmp_path):
    run(wh, [record()], as_of, "t")
    out = tmp_path / "o'brien" / "jobs.parquet"
    assert wh.export_parquet(out) == 1
    path = str(out).replace("'", "''")
    assert wh.query(f"SELECT title FROM '{path}'") == [{"title": "Data Engineer"}]


def test_warehouse_persists(tmp_path, as_of, record):
    path = tmp_path / "w" / "jobs.duckdb"
    with Warehouse(path) as a:
        run(a, [record()], as_of, "t")
    with Warehouse(path) as b:
        assert b.count("jobs") == 1


def test_report_lists_rejections(wh, as_of, record):
    run(wh, [record(), record(organization=None)], as_of, "t")
    text = build_report(wh, as_of)
    assert "| missing_company | 1 |" in text and "| under 1 day | 1 |" in text


def test_report_before_any_run(wh, as_of):
    assert build_report(wh, as_of) == "No runs yet.\n"
