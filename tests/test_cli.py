import pytest

from jobfeed.cli import main


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_full_sample_workflow(capsys, tmp_path):
    main(["run", "--sample"])
    main(["run", "--sample", "--day", "2"])
    main(["report", "--out", "out/report.md"])
    main(["shortlist", "--remote"])
    main(["export"])
    out = capsys.readouterr().out
    assert "inserted=" in out and "Remote" in out
    assert (tmp_path / "warehouse/jobs.parquet").exists()
    assert "Data quality report" in (tmp_path / "out/report.md").read_text()


@pytest.mark.parametrize("command", ["report", "shortlist", "export"])
def test_commands_need_a_warehouse(command):
    with pytest.raises(SystemExit, match="run --sample"):
        main([command])


def test_live_run_without_key_creates_nothing(tmp_path):
    with pytest.raises(SystemExit, match="JOBFEED_API_KEY"):
        main(["run"])
    assert not (tmp_path / "warehouse").exists()
