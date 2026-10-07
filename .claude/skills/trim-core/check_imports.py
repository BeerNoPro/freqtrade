"""Find imports of freqtrade modules that no longer exist (including lazy imports in functions).

Usage:
    .venv/Scripts/python.exe .claude/skills/trim-core/check_imports.py

mypy runs with ignore_missing_imports = true, so it does not report imports of deleted
modules. This scan does: it resolves every `import freqtrade.x` / `from freqtrade.x import y`
in freqtrade/, tests/ and scripts/ against the files on disk and exits with code 1 when
something points to a module that is gone.
"""

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def is_freqtrade(name: str) -> bool:
    return name == "freqtrade" or name.startswith("freqtrade.")


def module_exists(name: str) -> bool:
    path = ROOT / Path(*name.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def resolve_from(path: Path, node: ast.ImportFrom) -> str:
    base = node.module or ""
    if not node.level:
        return base
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    else:
        parts = parts[:-1]
    parts = parts[: len(parts) - (node.level - 1)]
    return ".".join(parts + ([base] if base else []))


def check_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError as e:
        return [f"{path.relative_to(ROOT)}: syntax error {e}"]
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = resolve_from(path, node)
            if not is_freqtrade(base):
                continue
            names = [base]
            # `from freqtrade.x import sub` may import a submodule
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                if not module_exists(base) and not module_exists(candidate):
                    names.append(candidate)
        else:
            continue
        for name in names:
            if not is_freqtrade(name):
                continue
            # walk up until an existing module is found; report only if the leaf is missing
            if module_exists(name):
                continue
            parent = name.rsplit(".", 1)[0]
            if module_exists(parent) and isinstance(node, ast.ImportFrom):
                continue  # attribute import from an existing module
            problems.append(
                f"{path.relative_to(ROOT).as_posix()}:{node.lineno}: missing module {name}"
            )
    return problems


def main() -> int:
    problems: list[str] = []
    for base in ("freqtrade", "tests", "scripts"):
        for path in sorted((ROOT / base).rglob("*.py")):
            if "api_server/ui" in path.as_posix():
                continue
            problems.extend(check_file(path))
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s) found")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
