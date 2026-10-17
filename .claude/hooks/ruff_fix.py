"""PostToolUse hook: format and lint-fix a Python file right after Claude edits it.

Reads the hook payload from stdin, runs `ruff format` and `ruff check --fix` on the
edited file, and reports remaining lint errors back to Claude (exit code 2) so they
get fixed in the same turn. Never blocks on its own failures.

Unused imports/variables (F401, F841) are ignored here: during a multi-step edit an
import is often added before the code that uses it, and `--fix` would delete it.
The pre-commit ruff hook and scripts/format_code.py still enforce them at commit time.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[2]))
# Rules that are normal in a half-finished edit - checked at commit time instead
EDIT_TIME_IGNORE = "F401,F841"


def find_ruff() -> str | None:
    for candidate in (
        PROJECT_DIR / ".venv" / "Scripts" / "ruff.exe",
        PROJECT_DIR / ".venv" / "bin" / "ruff",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path.endswith(".py") or not Path(file_path).is_file():
        return 0

    ruff = find_ruff()
    if ruff is None:
        return 0

    # Pass a path relative to the project root: on Windows the drive letter case of
    # CLAUDE_PROJECT_DIR and file_path can differ, which breaks ruff per-file-ignores.
    try:
        target = os.path.relpath(os.path.normcase(file_path), os.path.normcase(PROJECT_DIR))
    except ValueError:
        target = file_path
    if target.startswith(".."):
        return 0

    subprocess.run([ruff, "format", "--quiet", target], cwd=PROJECT_DIR, check=False)
    result = subprocess.run(
        [
            ruff,
            "check",
            "--fix",
            "--quiet",
            "--output-format",
            "concise",
            "--ignore",
            EDIT_TIME_IGNORE,
            target,
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 and result.stdout.strip():
        print(f"ruff found issues in {file_path}:\n{result.stdout}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
