import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "format_code.py"
_spec = importlib.util.spec_from_file_location("format_code", SCRIPT)
format_code = importlib.util.module_from_spec(_spec)
sys.modules["format_code"] = format_code
_spec.loader.exec_module(format_code)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Record ruff calls and check_encoding invocations instead of running them."""
    monkeypatch.setattr(format_code, "ROOT", tmp_path)
    monkeypatch.setattr(format_code, "find_ruff", lambda: "ruff")
    state = {"runs": [], "enc_args": None, "lint_rc": 0, "enc_rc": 0}

    def fake_run(cmd):
        state["runs"].append(cmd)
        if cmd[1:2] == ["check"] and "--fix" not in cmd:
            return state["lint_rc"]
        return 0

    def fake_encoding_main(args):
        state["enc_args"] = args
        return state["enc_rc"]

    monkeypatch.setattr(format_code, "run", fake_run)
    monkeypatch.setattr(format_code.check_encoding, "main", fake_encoding_main)
    return tmp_path, state


def test_all_formats_whole_project(env):
    _, state = env
    assert format_code.main(["--all"]) == 0
    assert state["runs"][0] == ["ruff", "check", "--fix", "--quiet", *format_code.PY_ALL]
    assert state["runs"][1] == ["ruff", "format", "--quiet", *format_code.PY_ALL]
    assert state["runs"][2] == ["ruff", "check", *format_code.PY_ALL]
    assert state["enc_args"] == ["--all", "--fix"]


def test_changed_files_skip_deleted_ones(env, monkeypatch):
    root, state = env
    (root / "a.py").write_text("x = 1\n")
    (root / "doc.md").write_text("# doc\n")
    monkeypatch.setattr(
        format_code.check_encoding, "select_files", lambda args: ["a.py", "deleted.py", "doc.md"]
    )
    assert format_code.main([]) == 0
    assert state["runs"][0] == ["ruff", "check", "--fix", "--quiet", "a.py"]
    assert state["runs"][-1] == ["ruff", "check", "a.py"]
    assert state["enc_args"] == ["--fix", "a.py", "doc.md"]


def test_only_non_python_files_skip_ruff(env, monkeypatch):
    root, state = env
    (root / "doc.md").write_text("# doc\n")
    monkeypatch.setattr(format_code.check_encoding, "select_files", lambda args: ["doc.md"])
    assert format_code.main([]) == 0
    assert state["runs"] == []
    assert state["enc_args"] == ["--fix", "doc.md"]


def test_no_changed_files(env, monkeypatch, capsys):
    _, state = env
    monkeypatch.setattr(format_code.check_encoding, "select_files", lambda args: [])
    assert format_code.main([]) == 0
    assert "No changed files." in capsys.readouterr().out
    assert state["runs"] == []


@pytest.mark.parametrize("lint_rc,enc_rc", [(1, 0), (0, 1)])
def test_remaining_problems_fail(env, lint_rc, enc_rc, capsys):
    _, state = env
    state["lint_rc"], state["enc_rc"] = lint_rc, enc_rc
    assert format_code.main(["--all"]) == 1
    assert "problems remain" in capsys.readouterr().out
