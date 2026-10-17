"""
Run and track research jobs (``mce.joblog``): every run gets a record in
``user_data/logs/jobs/jobs.jsonl`` (command, code commit, start, end, status, outputs) and its
full output in ``user_data/logs/jobs/<id>.log``.

    .venv/Scripts/python.exe scripts/research/jobs.py run --name download-r100 \\
        --note "ranks 21-100 store" --output user_data/research/pit_r100 -- \\
        .venv/Scripts/python.exe scripts/research/download_pit_universe.py --top 100
    .venv/Scripts/python.exe scripts/research/jobs.py list [-n 20] [--status failed] [--name r100]
    .venv/Scripts/python.exe scripts/research/jobs.py show [last | <id>] [--lines 40]

``run`` streams the command's output to the console and to the job log, records the exit code
(0 = ok, otherwise failed; Ctrl-C = interrupted) and returns it.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
from mce import joblog


def resolve(cmd: list[str]) -> list[str]:
    """The program as an absolute path when given relative to the working directory (Windows
    does not start ``.venv/Scripts/python.exe`` by itself), else as found on PATH."""
    exe = Path(cmd[0])
    if exe.exists():
        return [str(exe.resolve()), *cmd[1:]]
    return [shutil.which(cmd[0]) or cmd[0], *cmd[1:]]


def run(
    name: str, cmd: list[str], note: str = "", outputs: list[str] | None = None, root=joblog.JOBS
) -> int:
    cmd = resolve(cmd)
    record = joblog.start(name, cmd=cmd, note=note, root=root)
    log = Path(record["log"])
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    code: int | None = None
    status = "failed"
    try:
        with log.open("w", encoding="utf-8", newline="\n") as f:
            f.write(f"# job {record['id']}\n# cmd {cmd}\n# started {record['started_at']}\n")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            for line in proc.stdout or []:
                sys.stdout.write(line)
                f.write(line)
                f.flush()
            code = proc.wait()
        status = "ok" if code == 0 else "failed"
    except KeyboardInterrupt:
        status = "interrupted"
    except OSError as e:  # the command could not start
        with log.open("a", encoding="utf-8") as f:
            f.write(f"cannot start: {e}\n")
    joblog.finish(
        record,
        status,
        exit_code=code,
        outputs=outputs or [],
        summary=joblog.tail(log),
        root=root,
    )
    print(f"[job {record['id']}] {status} (exit {code}), log {log}")
    return code if code is not None else 1


def _local(ts: str | None) -> str:
    if not ts:
        return ""
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return t.tz_convert("Asia/Ho_Chi_Minh").strftime("%m-%d %H:%M")


def list_jobs(
    n: int = 20, status: str | None = None, name: str | None = None, root=joblog.JOBS
) -> pd.DataFrame:
    t = joblog.table(root)
    if status:
        t = t[t["status"] == status]
    if name:
        t = t[t["name"].str.contains(name, case=False, na=False)]
    t = t.tail(n)
    out = pd.DataFrame(
        {
            "id": t["id"],
            "status": t["status"],
            "bắt đầu (VN)": [_local(x) for x in t["started_at"]],
            "phút": (t["duration_s"].astype(float) / 60).round(1),
            "exit": t["exit_code"],
            "commit": t["commit"],
            "ghi chú": t["note"],
        }
    )
    print(out.to_string(index=False) if len(out) else "no job recorded")
    return out


def show(job_id: str = "last", lines: int = 40, root=joblog.JOBS) -> dict:
    t = joblog.table(root)
    if t.empty:
        print("no job recorded")
        return {}
    row = t.iloc[-1] if job_id == "last" else t[t["id"] == job_id].iloc[0]
    rec = row.to_dict()
    print(
        json.dumps({k: v for k, v in rec.items() if k != "summary"}, ensure_ascii=False, indent=1)
    )
    print(f"--- last {lines} lines of {rec['log']} ---")
    print(joblog.tail(rec["log"], lines) or rec["summary"])
    return rec


def main(argv: list[str] | None = None, root=joblog.JOBS) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, cmd = argv[:cut], argv[cut + 1 :]
    parser = argparse.ArgumentParser(description="Run and track research jobs")
    sub = parser.add_subparsers(dest="action", required=True)
    p_run = sub.add_parser("run", help="run a command as a tracked job (command after --)")
    p_run.add_argument("--name", required=True)
    p_run.add_argument("--note", default="")
    p_run.add_argument("--output", action="append", default=[], help="file or folder it writes")
    p_list = sub.add_parser("list", help="recent jobs")
    p_list.add_argument("-n", type=int, default=20)
    p_list.add_argument("--status", choices=[*joblog.STATUSES, "running"])
    p_list.add_argument("--name")
    p_show = sub.add_parser("show", help="one job: record and log tail")
    p_show.add_argument("job_id", nargs="?", default="last")
    p_show.add_argument("--lines", type=int, default=40)
    args = parser.parse_args(argv)
    if args.action == "run":
        if not cmd:
            parser.error("run needs a command after --")
        return run(args.name, cmd, args.note, args.output, root)
    if args.action == "list":
        list_jobs(args.n, args.status, args.name, root)
        return 0
    show(args.job_id, args.lines, root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
