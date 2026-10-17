"""Research job log: every job leaves a start and an end record, failures and unfinished jobs
are visible, and a command run through ``jobs.py run`` keeps its full output."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jobs
import pytest
from mce import joblog


T0 = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)


def test_start_and_finish_make_one_row_with_duration(tmp_path: Path) -> None:
    out = tmp_path / "result.md"
    out.write_text("report", encoding="utf-8")
    rec = joblog.start(
        "study r100", params={"band": [21, 100]}, note="IS run", root=tmp_path, now=T0
    )
    joblog.finish(
        rec,
        "ok",
        exit_code=0,
        outputs=[out],
        summary="done",
        root=tmp_path,
        now=T0 + timedelta(seconds=90),
    )
    t = joblog.table(tmp_path)
    assert len(t) == 1
    row = t.iloc[0]
    assert row["id"] == "20261016-100000-study-r100"
    assert row["status"] == "ok"
    assert row["duration_s"] == 90.0
    assert row["params"] == {"band": [21, 100]}
    assert row["outputs"] == [{"path": str(out), "exists": True, "bytes": 6}]
    assert row["summary"] == "done"


def test_job_without_end_is_running_and_bad_status_is_refused(tmp_path: Path) -> None:
    rec = joblog.start("download", root=tmp_path, now=T0)
    assert joblog.table(tmp_path).iloc[0]["status"] == "running"
    with pytest.raises(ValueError):
        joblog.finish(rec, "done", root=tmp_path)


def test_context_manager_records_a_failure_and_reraises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), joblog.job("check", root=tmp_path):
        raise RuntimeError("no OI data")
    row = joblog.table(tmp_path).iloc[0]
    assert row["status"] == "failed"
    assert row["exit_code"] == 1
    assert "no OI data" in row["summary"]


def test_context_manager_ok_keeps_the_summary_set_by_the_block(tmp_path: Path) -> None:
    with joblog.job("check", root=tmp_path) as rec:
        rec["summary"] = "120 events"
    row = joblog.table(tmp_path).iloc[0]
    assert (row["status"], row["summary"]) == ("ok", "120 events")


def test_a_cut_line_does_not_hide_the_other_jobs(tmp_path: Path) -> None:
    joblog.start("a", root=tmp_path, now=T0)
    with (tmp_path / joblog.INDEX).open("a", encoding="utf-8") as f:
        f.write('{"event": "start", "id": "broken"\n')  # a crash cut the line
    joblog.start("b", root=tmp_path, now=T0 + timedelta(seconds=1))
    assert list(joblog.table(tmp_path)["name"]) == ["a", "b"]


def test_run_keeps_the_output_and_the_exit_code(tmp_path: Path, capsys) -> None:
    code = jobs.main(
        [
            "run",
            "--name",
            "demo",
            "--note",
            "test",
            "--",
            sys.executable,
            "-c",
            "print('xin chào'); import sys; sys.exit(3)",
        ],
        root=tmp_path,
    )
    assert code == 3
    row = joblog.table(tmp_path).iloc[0]
    assert (row["status"], row["exit_code"], row["note"]) == ("failed", 3, "test")
    log = Path(row["log"]).read_text(encoding="utf-8")
    assert "xin chào" in log
    assert "xin chào" in row["summary"]
    assert "xin chào" in capsys.readouterr().out


def test_run_ok_list_and_show(tmp_path: Path, capsys) -> None:
    assert (
        jobs.main(["run", "--name", "ok", "--", sys.executable, "-c", "print(1)"], root=tmp_path)
        == 0
    )
    capsys.readouterr()
    shown = jobs.list_jobs(root=tmp_path)
    assert list(shown["status"]) == ["ok"]
    rec = jobs.show("last", root=tmp_path)
    assert rec["status"] == "ok"
    assert json.loads(json.dumps(rec["cmd"]))[-1] == "print(1)"


def test_relative_program_path_is_made_absolute(tmp_path: Path, monkeypatch) -> None:
    exe = Path(sys.executable)
    monkeypatch.chdir(exe.parent)
    assert jobs.resolve([exe.name, "-c", "1"])[0] == str(exe.resolve())
    assert jobs.resolve(["no-such-program-xyz"]) == ["no-such-program-xyz"]


def test_run_without_a_command_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        jobs.main(["run", "--name", "x"], root=tmp_path)
