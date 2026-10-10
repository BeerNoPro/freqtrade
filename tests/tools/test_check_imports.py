import ast
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / ".claude" / "skills" / "trim-core" / "check_imports.py"
_spec = importlib.util.spec_from_file_location("check_imports", SCRIPT)
check_imports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_imports)


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """Minimal repository layout: freqtrade/ package, tests/ and scripts/."""
    monkeypatch.setattr(check_imports, "ROOT", tmp_path)
    files = {
        "freqtrade/__init__.py": "",
        "freqtrade/a.py": "x = 1\n",
        "freqtrade/sub/__init__.py": "",
        "freqtrade/sub/b.py": "from ..a import x\n",
        "freqtrade/rpc/api_server/ui/installed/junk.py": "import freqtrade.not_here\n",
        "tests/__init__.py": "",
        "tests/test_ok.py": (
            "import freqtrade.a\nfrom freqtrade.a import x\nfrom freqtrade import sub\n"
        ),
        "scripts/tool.py": "import json\n",
    }
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_helpers():
    assert check_imports.is_freqtrade("freqtrade")
    assert check_imports.is_freqtrade("freqtrade.exchange")
    assert not check_imports.is_freqtrade("freqtrade_client")
    assert not check_imports.is_freqtrade("pandas")


def test_resolve_relative_import(fake_repo):
    path = fake_repo / "freqtrade" / "sub" / "b.py"
    node = ast.parse("from ..a import x").body[0]
    assert check_imports.resolve_from(path, node) == "freqtrade.a"
    init = fake_repo / "freqtrade" / "sub" / "__init__.py"
    node = ast.parse("from .b import y").body[0]
    assert check_imports.resolve_from(init, node) == "freqtrade.sub.b"


def test_clean_repo_passes(fake_repo, capsys):
    # The ui folder is skipped even though it imports a missing module
    assert check_imports.main() == 0
    assert "0 problem(s) found" in capsys.readouterr().out


def test_missing_modules_are_reported(fake_repo, capsys):
    write(fake_repo, "freqtrade/c.py", "import freqtrade.gone\n")
    write(
        fake_repo,
        "tests/test_lazy.py",
        "def test_x():\n    from freqtrade.sub.missing import y\n",
    )
    write(fake_repo, "freqtrade/sub/d.py", "from .nope import z\n")
    # attribute import from an existing module is fine
    write(fake_repo, "scripts/ok.py", "from freqtrade.a import whatever\n")

    assert check_imports.main() == 1
    out = capsys.readouterr().out
    assert "freqtrade/c.py:1: missing module freqtrade.gone" in out
    assert "tests/test_lazy.py:2: missing module freqtrade.sub.missing" in out
    assert "freqtrade/sub/d.py:1: missing module freqtrade.sub.nope" in out
    assert "scripts/ok.py" not in out
    assert "3 problem(s) found" in out


def test_syntax_error_is_reported(fake_repo, capsys):
    write(fake_repo, "freqtrade/broken.py", "def (:\n")
    assert check_imports.main() == 1
    assert "freqtrade/broken.py: syntax error" in capsys.readouterr().out
