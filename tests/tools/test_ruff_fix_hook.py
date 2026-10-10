import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / ".claude" / "hooks" / "ruff_fix.py"
_spec = importlib.util.spec_from_file_location("ruff_fix", SCRIPT)
ruff_fix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ruff_fix)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Fake project root with a ruff executable available."""
    monkeypatch.setattr(ruff_fix, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(ruff_fix, "find_ruff", lambda: "ruff")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ruff_fix.subprocess, "run", fake_run)
    return tmp_path, calls


def run_hook(monkeypatch, payload) -> int:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(data))
    return ruff_fix.main()


def test_invalid_payload_is_ignored(project, monkeypatch):
    _, calls = project
    assert run_hook(monkeypatch, "not json") == 0
    assert calls == []


def test_non_python_and_missing_files_are_ignored(project, monkeypatch):
    root, calls = project
    (root / "notes.md").write_text("# x\n")
    assert run_hook(monkeypatch, {"tool_input": {"file_path": str(root / "notes.md")}}) == 0
    assert run_hook(monkeypatch, {"tool_input": {"file_path": str(root / "gone.py")}}) == 0
    assert run_hook(monkeypatch, {}) == 0
    assert calls == []


def test_runs_ruff_with_project_relative_path(project, monkeypatch):
    root, calls = project
    (root / "pkg").mkdir()
    target = root / "pkg" / "mod.py"
    target.write_text("x = 1\n")

    assert run_hook(monkeypatch, {"tool_input": {"file_path": str(target)}}) == 0
    assert [c[0][:2] for c in calls] == [["ruff", "format"], ["ruff", "check"]]
    for cmd, kwargs in calls:
        assert Path(cmd[-1]) == Path("pkg") / "mod.py"
        assert not Path(cmd[-1]).is_absolute()
        assert kwargs["cwd"] == root


@pytest.mark.skipif(sys.platform != "win32", reason="Drive letters only exist on Windows")
def test_drive_letter_case_does_not_break_relative_path(project, monkeypatch):
    root, calls = project
    target = root / "mod.py"
    target.write_text("x = 1\n")
    path = str(target)
    # Same file, drive letter in the other case (c:\ vs C:\)
    swapped = path[0].swapcase() + path[1:]

    assert run_hook(monkeypatch, {"tool_input": {"file_path": swapped}}) == 0
    assert calls[0][0][-1] == "mod.py"


def test_file_outside_project_is_ignored(project, monkeypatch, tmp_path_factory):
    _, calls = project
    other = tmp_path_factory.mktemp("elsewhere") / "mod.py"
    other.write_text("x = 1\n")
    assert run_hook(monkeypatch, {"tool_input": {"file_path": str(other)}}) == 0
    assert calls == []


def test_missing_ruff_is_ignored(project, monkeypatch):
    root, calls = project
    monkeypatch.setattr(ruff_fix, "find_ruff", lambda: None)
    (root / "mod.py").write_text("x = 1\n")
    assert run_hook(monkeypatch, {"tool_input": {"file_path": str(root / "mod.py")}}) == 0
    assert calls == []


def test_remaining_lint_errors_are_reported(project, monkeypatch, capsys):
    root, _ = project
    (root / "mod.py").write_text("x = 1\n")

    def fake_run(cmd, **kwargs):
        if cmd[1] == "check":
            return subprocess.CompletedProcess(
                cmd, 1, stdout="mod.py:1:1: F821 undefined", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ruff_fix.subprocess, "run", fake_run)
    assert run_hook(monkeypatch, {"tool_input": {"file_path": str(root / "mod.py")}}) == 2
    assert "F821" in capsys.readouterr().err
