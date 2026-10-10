"""Run every pre-commit check in one go and print a compact summary.

Steps (in order):
    format   scripts/format_code.py on changed files (ruff fix + format + encoding)
    imports  .claude/skills/trim-core/check_imports.py (imports of deleted modules)
    ruff     ruff check freqtrade tests scripts .claude
    mypy     mypy freqtrade
    pytest   pytest (whole suite without tests/exchange_online, or --tests paths)
    cli      freqtrade --version
    secrets  staged diff: secrets in added lines, forbidden files (configs, sqlite, data)

Each step prints one line. A failing step also prints the tail of its output;
the full output of every step goes to the log file (path printed at the end).

Usage:
    python scripts/gate.py                         # all steps
    python scripts/gate.py --tests tests/rpc       # run only these tests
    python scripts/gate.py --skip mypy pytest      # skip steps
    python scripts/gate.py --only secrets          # run selected steps

Exit code 1 when any step fails.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = Path(tempfile.gettempdir()) / "freqtrade-gate.log"
TAIL_LINES = 30
STEPS = ["format", "imports", "ruff", "mypy", "pytest", "cli", "secrets"]
PYTEST_FLAGS = ["-q", "-n", "auto", "-p", "no:cacheprovider", "-p", "no:warnings", "--no-header"]

# Added lines matching one of these are reported. A line can opt out with "gate: allow-secret".
SECRET_PATTERNS = {
    "telegram token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "64-char key": re.compile(r"\b[A-Za-z0-9]{64}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "secret value": re.compile(
        r"""(?i)["']?(api_?key|api_?secret|secret|password|token|jwt_secret_key|ws_token)["']?"""
        r"""\s*[:=]\s*["'][^"'\s]{8,}["']"""
    ),
}
ALLOW_MARK = "gate: allow-secret"
FORBIDDEN_FILES = [
    re.compile(r"^user_data/config[^/]*\.json$"),
    re.compile(r"\.sqlite(-shm|-wal)?$"),
    re.compile(r"^user_data/(data|backtest_results|hyperopt_results|reports|logs)/(?!\.gitkeep$)"),
    re.compile(r"(^|/)\.env$"),
]


def venv_tool(name: str) -> str:
    """Executable next to the running interpreter (the project .venv)."""
    bin_dir = Path(sys.executable).parent
    for candidate in (bin_dir / f"{name}.exe", bin_dir / name):
        if candidate.is_file():
            return str(candidate)
    return name


def run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def scan_secrets(diff: str) -> list[str]:
    """Return findings for added lines of a unified diff and for forbidden files."""
    findings: list[str] = []
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
            if any(p.search(current) for p in FORBIDDEN_FILES):
                findings.append(f"{current}: file must not be committed")
            continue
        if not line.startswith("+") or ALLOW_MARK in line:
            continue
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(line):
                findings.append(f"{current}: possible {name}: {line[1:].strip()[:100]}")
                break
    return findings


def step_secrets() -> tuple[int, str]:
    rc, diff = run(["git", "diff", "--cached", "--no-color", "--no-ext-diff", "-U0"])
    if rc != 0:
        return rc, diff
    findings = scan_secrets(diff)
    if findings:
        return 1, "\n".join(findings)
    added = sum(1 for line in diff.splitlines() if line.startswith("+") and line[:4] != "+++ ")
    return 0, f"{added} staged line(s) clean"


def pytest_summary(output: str) -> str:
    """Last pytest result line, e.g. '3369 passed, 6 skipped in 224s'."""
    for line in reversed(output.splitlines()):
        if re.search(r"\b(passed|failed|error|no tests ran)\b", line):
            return line.strip("= ").strip()
    return ""


def build_steps(args: argparse.Namespace) -> dict[str, Callable[[], tuple[int, str]]]:
    py = sys.executable
    tests = args.tests or ["tests", "--ignore=tests/exchange_online"]
    return {
        "format": lambda: run([py, "scripts/format_code.py"]),
        "imports": lambda: run([py, ".claude/skills/trim-core/check_imports.py"]),
        "ruff": lambda: run(
            [venv_tool("ruff"), "check", "freqtrade", "tests", "scripts", ".claude"]
        ),
        "mypy": lambda: run([venv_tool("mypy"), "freqtrade"]),
        "pytest": lambda: run([py, "-m", "pytest", *tests, *PYTEST_FLAGS]),
        "cli": lambda: run([venv_tool("freqtrade"), "--version"]),
        "secrets": step_secrets,
    }


def short_detail(name: str, output: str) -> str:
    if name == "pytest":
        return pytest_summary(output)
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1][:100] if lines else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tests", nargs="+", metavar="PATH", help="test paths for pytest")
    parser.add_argument("--skip", nargs="+", choices=STEPS, default=[], help="steps to skip")
    parser.add_argument("--only", nargs="+", choices=STEPS, help="run only these steps")
    args = parser.parse_args(argv)

    steps = build_steps(args)
    selected = [s for s in STEPS if (args.only is None or s in args.only) and s not in args.skip]
    failed = []
    log_parts = []
    for name in selected:
        start = time.monotonic()
        rc, output = steps[name]()
        elapsed = time.monotonic() - start
        status = "PASS" if rc == 0 else "FAIL"
        print(f"{status}  {name:<8} {elapsed:6.1f}s  {short_detail(name, output)}", flush=True)
        log_parts.append(f"===== {name} (rc={rc}) =====\n{output}\n")
        if rc != 0:
            failed.append(name)
            tail = output.splitlines()[-TAIL_LINES:]
            print("\n".join(f"      | {line}" for line in tail))

    LOG_FILE.write_text("\n".join(log_parts), encoding="utf-8")
    verdict = f"gate: FAIL ({', '.join(failed)})" if failed else "gate: OK"
    print(f"{verdict} - full log: {LOG_FILE.as_posix()}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
