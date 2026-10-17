"""
Research job log: one record per run of a research job (download, check, study, audit...), so
what ran, when, on which code, with which result and where its full output is can be looked up
in seconds.

    user_data/logs/jobs/jobs.jsonl   JSON lines, a "start" then an "end" event per job
    user_data/logs/jobs/<id>.log     full stdout + stderr of a job run through
                                     scripts/research/jobs.py

``start`` and ``finish`` write the two events; ``job`` wraps a block of Python code. A job with a
start and no end is ``running`` (or the process was killed: its log shows how far it got).
``table`` merges the events into one row per job. Writing the log never stops a job: a failure
to write is reported on stderr and the job goes on.
"""

import json
import os
import platform
import re
import subprocess
import sys
import traceback
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


REPO = Path(__file__).resolve().parents[3]
JOBS = REPO / "user_data" / "logs" / "jobs"
INDEX = "jobs.jsonl"
STATUSES = ("ok", "failed", "interrupted")
SUMMARY_LINES = 15


def _now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


def new_id(name: str, now: datetime | None = None) -> str:
    """``YYYYmmdd-HHMMSS-<name>`` (UTC), the name reduced to letters, digits, '-' and '_'."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:40] or "job"
    return f"{_now(now):%Y%m%d-%H%M%S}-{slug}"


def git_state(repo: Path = REPO) -> dict:
    """Short commit and the number of changed files (``dirty``); empty when git is unavailable."""
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return {"commit": head, "dirty": len([line for line in status.splitlines() if line.strip()])}


def _append(root: Path, record: dict) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        with (root / INDEX).open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        print(f"joblog: cannot write {root / INDEX}: {e}", file=sys.stderr)


def start(
    name: str,
    *,
    cmd: Sequence[str] | str | None = None,
    params: dict | None = None,
    note: str = "",
    root: Path = JOBS,
    now: datetime | None = None,
) -> dict:
    """Record the start of a job; returns the record (pass it to ``finish``)."""
    t = _now(now)
    job_id = new_id(name, t)
    record = {
        "event": "start",
        "id": job_id,
        "name": name,
        "cmd": list(cmd) if isinstance(cmd, Sequence) and not isinstance(cmd, str) else cmd,
        "cwd": str(Path.cwd()),
        "params": params or {},
        "note": note,
        "git": git_state(),
        "host": platform.node(),
        "pid": os.getpid(),
        "started_at": t.isoformat(timespec="seconds"),
        "log": str(root / f"{job_id}.log"),
    }
    _append(root, record)
    return record


def _outputs(paths: Sequence[str | Path]) -> list[dict]:
    out = []
    for p in paths:
        path = Path(p)
        out.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "bytes": path.stat().st_size if path.is_file() else None,
            }
        )
    return out


def finish(
    record: dict,
    status: str,
    *,
    exit_code: int | None = None,
    outputs: Sequence[str | Path] = (),
    summary: str = "",
    root: Path = JOBS,
    now: datetime | None = None,
) -> dict:
    """Record the end of the job started as ``record``: ``status`` one of ``STATUSES``,
    ``outputs`` the files it wrote (size checked now), ``summary`` its last lines or error."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    t = _now(now)
    begun = datetime.fromisoformat(record["started_at"])
    end = {
        "event": "end",
        "id": record["id"],
        "ended_at": t.isoformat(timespec="seconds"),
        "status": status,
        "exit_code": exit_code,
        "duration_s": round((t - begun).total_seconds(), 1),
        "outputs": _outputs(outputs),
        "summary": summary,
    }
    _append(root, end)
    return end


@contextmanager
def job(
    name: str,
    *,
    params: dict | None = None,
    note: str = "",
    outputs: Sequence[str | Path] = (),
    root: Path = JOBS,
) -> Generator[dict]:
    """Log a block of Python code as one job. The yielded record's ``"summary"`` key, if set by
    the block, becomes the end summary. Exceptions are recorded and re-raised."""
    record = start(name, cmd=sys.argv, params=params, note=note, root=root)
    try:
        yield record
    except KeyboardInterrupt:
        finish(record, "interrupted", outputs=outputs, summary="KeyboardInterrupt", root=root)
        raise
    except BaseException as e:
        tail = "".join(traceback.format_exception(e)[-SUMMARY_LINES:])
        finish(record, "failed", exit_code=1, outputs=outputs, summary=tail, root=root)
        raise
    finish(record, "ok", exit_code=0, outputs=outputs, summary=record.get("summary", ""), root=root)


def read_events(root: Path = JOBS) -> list[dict]:
    path = root / INDEX
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a line cut by a crash: skip it, keep the rest
    return events


def table(root: Path = JOBS) -> pd.DataFrame:
    """One row per job, oldest first: start fields, end fields, ``status`` ``running`` when no
    end was recorded."""
    events = read_events(root)
    starts = {e["id"]: e for e in events if e.get("event") == "start"}
    ends = {e["id"]: e for e in events if e.get("event") == "end"}
    rows = []
    for job_id, s in starts.items():
        e = ends.get(job_id, {})
        rows.append(
            {
                "id": job_id,
                "name": s.get("name"),
                "status": e.get("status", "running"),
                "started_at": s.get("started_at"),
                "ended_at": e.get("ended_at"),
                "duration_s": e.get("duration_s"),
                "exit_code": e.get("exit_code"),
                "commit": (s.get("git") or {}).get("commit"),
                "dirty": (s.get("git") or {}).get("dirty"),
                "note": s.get("note", ""),
                "log": s.get("log"),
                "cmd": s.get("cmd"),
                "params": s.get("params"),
                "outputs": e.get("outputs", []),
                "summary": e.get("summary", ""),
            }
        )
    cols = ["id", "name", "status", "started_at", "ended_at", "duration_s", "exit_code"]
    cols += ["commit", "dirty", "note", "log", "cmd", "params", "outputs", "summary"]
    return pd.DataFrame(rows, columns=cols)


def tail(path: str | Path, n: int = SUMMARY_LINES) -> str:
    """The last ``n`` lines of a text file ('' when it does not exist)."""
    p = Path(path)
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])
