"""List every place outside a target module that imports it (static AST scan).

Usage:
    .venv/Scripts/python.exe .claude/skills/trim-core/find_importers.py freqtrade.freqai

Prints the target's size, then importers grouped as module-level (break at import time)
or lazy (inside functions, break only when executed), for both freqtrade/ and tests/.
"""

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_names(path: Path, tree: ast.Module):
    """Yield (name, lineno, is_top_level) for every freqtrade import in the file."""
    top_ids = set()
    for stmt in tree.body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Import | ast.ImportFrom) and not isinstance(
                stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                top_ids.add(id(sub))
    current = module_name(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno, id(node) in top_ids
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                pkg = current.split(".")
                if path.name != "__init__.py":
                    pkg = pkg[:-1]
                pkg = pkg[: len(pkg) - (node.level - 1)]
                base = ".".join(pkg + ([base] if base else []))
            yield base, node.lineno, id(node) in top_ids
            for alias in node.names:
                yield f"{base}.{alias.name}", node.lineno, id(node) in top_ids


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    target = sys.argv[1].strip(".")
    target_dir = ROOT / Path(*target.split("."))
    target_files = (
        list(target_dir.rglob("*.py")) if target_dir.is_dir() else [target_dir.with_suffix(".py")]
    )
    target_files = [f for f in target_files if f.is_file()]
    loc = sum(
        len(f.read_text(encoding="utf-8", errors="ignore").splitlines()) for f in target_files
    )
    print(f"Target {target}: {len(target_files)} files, {loc} lines\n")

    results: dict[str, list[str]] = {"top": [], "lazy": []}
    for base in ("freqtrade", "tests"):
        for path in sorted((ROOT / base).rglob("*.py")):
            if path in target_files or "api_server/ui" in path.as_posix():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            seen = set()
            for name, lineno, top in imported_names(path, tree):
                if (name == target or name.startswith(target + ".")) and lineno not in seen:
                    seen.add(lineno)
                    rel = path.relative_to(ROOT).as_posix()
                    results["top" if top else "lazy"].append(f"{rel}:{lineno}  ({name})")

    for kind, title in (
        ("top", "Module-level imports (break at import)"),
        ("lazy", "Lazy imports (break when executed)"),
    ):
        print(f"== {title}: {len(results[kind])}")
        for line in results[kind]:
            print("  ", line)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
