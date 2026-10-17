"""Format changed files before committing.

Steps:
1. ruff check --fix   (lint auto-fixes, import sorting)
2. ruff format        (code style)
3. scripts/check_encoding.py --fix   (UTF-8, BOM, line endings, final newline)
4. ruff check         (report lint errors that need a manual fix)

Usage:
    python scripts/format_code.py            # files changed vs HEAD (+ untracked)
    python scripts/format_code.py --staged   # files staged for commit
    python scripts/format_code.py --all      # whole project (freqtrade, tests, scripts, .claude)
    python scripts/format_code.py a.py b.md  # explicit files

Files are not staged automatically: review the diff, then `git add`.
Exit code 1 when lint errors or encoding problems remain.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_encoding  # noqa: E402


PY_ALL = ["freqtrade", "tests", "scripts", ".claude"]


def find_ruff() -> str:
    for candidate in (
        Path(sys.executable).with_name("ruff.exe"),
        Path(sys.executable).with_name("ruff"),
        ROOT / ".venv" / "Scripts" / "ruff.exe",
        ROOT / ".venv" / "bin" / "ruff",
    ):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("ruff")
    if not found:
        sys.exit("ruff not found - install the dev requirements first")
    return found


def run(cmd: list[str]) -> int:
    print("$", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="files to format (default: changed files)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="format the whole project")
    group.add_argument("--staged", action="store_true", help="format files staged for commit")
    args = parser.parse_args(argv)

    if args.all:
        py_targets = PY_ALL
        enc_args = ["--all", "--fix"]
    else:
        files = [
            f for f in check_encoding.select_files(args) if (ROOT / f).is_file()
        ]  # deleted files are skipped
        if not files:
            print("No changed files.")
            return 0
        py_targets = [f for f in files if f.endswith(".py")]
        enc_args = ["--fix", *files]

    ruff = find_ruff()
    if py_targets:
        run([ruff, "check", "--fix", "--quiet", *py_targets])
        run([ruff, "format", "--quiet", *py_targets])

    print("$ check_encoding", " ".join(enc_args[:2]), "...")
    enc_rc = check_encoding.main(enc_args)

    lint_rc = run([ruff, "check", *py_targets]) if py_targets else 0
    ok = lint_rc == 0 and enc_rc == 0
    print("format_code: OK" if ok else "format_code: problems remain (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
