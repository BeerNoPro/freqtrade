"""Check (and optionally fix) text file encoding and line endings.

Policy (mirrors .gitattributes and .editorconfig):
- every text file: valid UTF-8 without BOM, LF line endings, ends with a newline
- Windows scripts (*.ps1, *.psm1, *.psd1): UTF-8 *with* BOM and CRLF, because Windows
  PowerShell 5.1 reads BOM-less files as ANSI and breaks non-ASCII (Vietnamese) text
- *.bat, *.cmd: CRLF without BOM

Usage:
    python scripts/check_encoding.py                 # files changed vs HEAD (+ untracked)
    python scripts/check_encoding.py --all           # every tracked + untracked text file
    python scripts/check_encoding.py --staged        # files staged for commit
    python scripts/check_encoding.py --fix [paths]   # rewrite files to match the policy
    python scripts/check_encoding.py a.py b.md       # explicit files (used by pre-commit)

With --all, the git index is also checked: files must be stored with LF in the repository.
Exit code 1 when problems remain.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"

CRLF_SUFFIXES = {".ps1", ".psm1", ".psd1", ".bat", ".cmd"}
BOM_SUFFIXES = {".ps1", ".psm1", ".psd1"}
BINARY_SUFFIXES = {
    ".feather", ".parquet", ".pickle", ".pkl", ".gz", ".zip", ".ico", ".png", ".jpg",
    ".jpeg", ".gif", ".whl", ".sqlite", ".db", ".pyc", ".so", ".dll", ".exe", ".woff",
    ".woff2", ".ttf", ".eot",
}  # fmt: skip
# Test fixtures may rely on exact bytes; only encoding/BOM/CRLF rules apply there.
NO_FINAL_NEWLINE_RULE = ("tests/testdata/",)


@dataclass
class Expected:
    eol: bytes
    bom: bool


def expected_for(path: str) -> Expected:
    suffix = Path(path).suffix.lower()
    return Expected(
        eol=b"\r\n" if suffix in CRLF_SUFFIXES else b"\n",
        bom=suffix in BOM_SUFFIXES,
    )


def is_binary(path: str, data: bytes) -> bool:
    return Path(path).suffix.lower() in BINARY_SUFFIXES or b"\x00" in data[:8192]


def check_bytes(path: str, data: bytes) -> list[str]:
    """Return a list of policy violations for the given file content."""
    if not data:
        return []
    exp = expected_for(path)
    problems = []
    has_bom = data.startswith(BOM)
    body = data[len(BOM) :] if has_bom else data
    try:
        body.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"not valid UTF-8 ({e.reason} at byte {e.start})"]
    if has_bom and not exp.bom:
        problems.append("has UTF-8 BOM (expected none)")
    if exp.bom and not has_bom:
        problems.append("missing UTF-8 BOM")

    crlf = body.count(b"\r\n")
    lf_only = body.count(b"\n") - crlf
    cr_only = body.count(b"\r") - crlf
    if cr_only:
        problems.append("contains bare CR line endings")
    if exp.eol == b"\n" and crlf:
        problems.append(f"CRLF line endings ({crlf} lines, expected LF)")
    if exp.eol == b"\r\n" and lf_only:
        problems.append(f"LF line endings ({lf_only} lines, expected CRLF)")
    if not path.startswith(NO_FINAL_NEWLINE_RULE) and not body.endswith(b"\n"):
        problems.append("no newline at end of file")
    return problems


def fix_bytes(path: str, data: bytes) -> bytes:
    """Return the content rewritten to match the policy (data must be valid UTF-8)."""
    exp = expected_for(path)
    body = data.removeprefix(BOM)
    text = body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    # Normalising keeps "ends with newline" as it was; only add one where the rule applies.
    if text and not path.startswith(NO_FINAL_NEWLINE_RULE) and not text.endswith(b"\n"):
        text += b"\n"
    if exp.eol == b"\r\n":
        text = text.replace(b"\n", b"\r\n")
    return (BOM if exp.bom else b"") + text


def git_lines(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, encoding="utf-8"
    ).stdout
    return [line for line in out.splitlines() if line]


def select_files(args: argparse.Namespace) -> list[str]:
    if args.paths:
        return [Path(p).as_posix() for p in args.paths]
    untracked = git_lines("ls-files", "--others", "--exclude-standard")
    if args.all:
        files = git_lines("ls-files") + untracked
    elif args.staged:
        files = git_lines("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    else:
        files = git_lines("diff", "--name-only", "--diff-filter=ACMR", "HEAD") + untracked
    return sorted(set(files))


def check_index() -> list[str]:
    """Files stored in the git index must use LF (git normalises on add)."""
    problems = []
    for line in git_lines("ls-files", "--eol"):
        meta, path = line.split("\t", 1)
        index_eol = meta.split()[0]
        if index_eol in ("i/crlf", "i/mixed"):
            problems.append(f"{path}: stored in git with {index_eol[2:]} line endings")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="files to check (default: changed files)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="check all tracked + untracked files")
    group.add_argument("--staged", action="store_true", help="check files staged for commit")
    parser.add_argument("--fix", action="store_true", help="rewrite files to match the policy")
    args = parser.parse_args(argv)

    problems: list[str] = []
    fixed = 0
    for rel in select_files(args):
        path = ROOT / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if is_binary(rel, data):
            continue
        issues = check_bytes(rel, data)
        if not issues:
            continue
        if args.fix and not any(i.startswith("not valid UTF-8") for i in issues):
            path.write_bytes(fix_bytes(rel, data))
            fixed += 1
            print(f"fixed  {rel}: {'; '.join(issues)}")
        else:
            problems.extend(f"{rel}: {issue}" for issue in issues)

    if args.all:
        problems.extend(check_index())

    for p in problems:
        print(f"ERROR  {p}")
    print(f"{fixed} file(s) fixed, {len(problems)} problem(s) remaining")
    if problems and not args.fix:
        print("Run: python scripts/check_encoding.py --fix  (or scripts/format_code.py)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
