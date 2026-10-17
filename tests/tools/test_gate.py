import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "gate.py"
_spec = importlib.util.spec_from_file_location("gate", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["gate"] = gate
_spec.loader.exec_module(gate)

TG_TOKEN = "1234567890:" + "A" * 35  # gate: allow-secret


def diff_for(path: str, *added: str) -> str:
    lines = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", "@@ -0,0 +1 @@"]
    return "\n".join(lines + [f"+{line}" for line in added])


@pytest.mark.parametrize(
    "line",
    [
        f'"token": "{TG_TOKEN}"',  # gate: allow-secret
        "ghp_" + "a1B2" * 9,
        "k" * 64,
        "-----BEGIN RSA PRIVATE KEY-----",  # gate: allow-secret
        'api_key = "abcd1234efgh"',  # gate: allow-secret
        '"jwt_secret_key": "somethingrandom"',  # gate: allow-secret
        "password='hunter2hunter2'",  # gate: allow-secret
    ],
)
def test_scan_secrets_flags_added_secrets(line):
    findings = gate.scan_secrets(diff_for("freqtrade/x.py", line))
    assert len(findings) == 1
    assert findings[0].startswith("freqtrade/x.py: possible")


@pytest.mark.parametrize(
    "line",
    [
        'password = ""',
        'secret = conf.get("secret")',
        "token: str | None = None",
        "-api_key = 'abcd1234efgh' removed lines are not scanned",  # gate: allow-secret
        f'"token": "{TG_TOKEN}"  # gate: allow-secret',
    ],
)
def test_scan_secrets_ignores_safe_lines(line):
    if line.startswith("-"):
        diff = diff_for("a.py").replace("@@ -0,0 +1 @@", "@@ -1 +0,0 @@\n" + line)
    else:
        diff = diff_for("a.py", line)
    assert gate.scan_secrets(diff) == []


@pytest.mark.parametrize(
    "path,forbidden",
    [
        ("user_data/config.json", True),
        ("user_data/config_futures.json", True),
        ("user_data/tradesv3.dryrun.sqlite", True),
        ("user_data/data/binance/BTC_USDT-1h.feather", True),
        ("user_data/backtest_results/a.zip", True),
        ("user_data/data/.gitkeep", False),
        ("tests/testdata/config.tests.json", False),
        ("user_data/strategies/MyStrategy.py", False),
    ],
)
def test_scan_secrets_forbidden_files(path, forbidden):
    findings = gate.scan_secrets(diff_for(path, "x = 1"))
    assert (findings == [f"{path}: file must not be committed"]) is forbidden


def test_pytest_summary_picks_result_line():
    output = "....\n[100%]\n3369 passed, 6 skipped in 223.99s (0:03:43)\n"
    assert gate.pytest_summary(output) == "3369 passed, 6 skipped in 223.99s (0:03:43)"
    assert gate.pytest_summary("= 1 failed, 2 passed in 1s =") == "1 failed, 2 passed in 1s"
    assert gate.pytest_summary("collecting") == ""


@pytest.fixture
def fake_steps(monkeypatch, tmp_path):
    """Replace the real commands by canned results."""
    results = {name: (0, f"{name} ok") for name in gate.STEPS}
    called = []

    def fake_build(args):
        def make(name):
            def step():
                called.append(name)
                return results[name]

            return step

        return {name: make(name) for name in gate.STEPS}

    monkeypatch.setattr(gate, "build_steps", fake_build)
    monkeypatch.setattr(gate, "LOG_FILE", tmp_path / "gate.log")
    return results, called


def test_main_all_pass(fake_steps, capsys):
    _, called = fake_steps
    assert gate.main([]) == 0
    out = capsys.readouterr().out
    assert called == gate.STEPS
    assert out.count("PASS") == len(gate.STEPS)
    assert "gate: OK" in out


def test_main_failure_prints_tail_and_logs(fake_steps, capsys, tmp_path):
    results, _ = fake_steps
    results["mypy"] = (1, "\n".join(f"line {i}" for i in range(100)))
    assert gate.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL  mypy" in out
    assert "| line 99" in out
    assert "| line 69" not in out  # only the tail is printed
    assert "gate: FAIL (mypy)" in out
    assert "line 0" in (tmp_path / "gate.log").read_text(encoding="utf-8")


def test_main_only_and_skip(fake_steps):
    _, called = fake_steps
    assert gate.main(["--only", "ruff", "secrets"]) == 0
    assert called == ["ruff", "secrets"]
    called.clear()
    assert gate.main(["--skip", "pytest", "mypy"]) == 0
    assert "pytest" not in called
    assert "mypy" not in called


def test_build_steps_uses_given_tests(monkeypatch):
    calls = []
    monkeypatch.setattr(gate, "run", lambda cmd: calls.append(cmd) or (0, ""))
    args = gate.argparse.Namespace(tests=["tests/rpc"])
    gate.build_steps(args)["pytest"]()
    assert calls[0][2:4] == ["pytest", "tests/rpc"]
    assert "no:warnings" in calls[0]
