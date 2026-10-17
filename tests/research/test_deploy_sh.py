"""deploy/deploy.sh: the safe-moment rule, the runner-idle check and which services a change
restarts, run through bash with the clock and the logs faked (no git, no systemd)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "deploy" / "deploy.sh"


def _find_bash() -> str | None:
    """bash of Git for Windows next to git.exe (System32's bash.exe is WSL, often without a
    distribution); bash on PATH elsewhere."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git:
        for rel in ("../bin/bash.exe", "../../bin/bash.exe"):
            candidate = (Path(git).parent / rel).resolve()
            if candidate.exists():
                return str(candidate)
    return None


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _bash(body: str, tmp_path: Path) -> subprocess.CompletedProcess:
    script = (
        f'export APP="{tmp_path.as_posix()}"\n'
        f'source "{SCRIPT.as_posix()}"\n'
        "set +e\n"
        # fake systemd: the runner service is active
        "systemctl() { return 0; }\n" + body
    )
    return subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, timeout=60, check=False
    )


def _clock(hhmm: str) -> str:
    h, m = hhmm.split(":")
    return (
        f'date() {{ case "$*" in *%H*) echo {h};; *%M*) echo {m};; '
        '*) command date "$@";; esac; }\n'
    )


def _forward_log(tmp_path: Path, lines: list[str]) -> None:
    logs = tmp_path / "user_data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "forward.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


IDLE = [
    "2026-10-17 08:02:04,552 INFO store synced: 0 daily candles, 20 coins at 4h",
    "2026-10-17 08:02:31,847 INFO product: {'allocated': 0}, 0 alerts, delivery {}",
]
BUSY = [*IDLE, "2026-10-17 12:02:01,100 INFO store synced: 0 daily candles, 20 coins at 4h"]


@pytest.mark.parametrize(
    ("hhmm", "safe"),
    [
        ("08:05", False),  # in the 12 minutes after the 08:00 close
        ("08:12", True),
        ("11:59", True),
        ("00:30", True),
        ("01:25", False),  # daily store sync window 01:25-03:10 UTC
        ("02:59", False),
        ("03:10", True),
    ],
)
def test_safe_moment(tmp_path: Path, hhmm: str, safe: bool) -> None:
    _forward_log(tmp_path, IDLE)
    r = _bash(_clock(hhmm) + "FORWARD=yes\nsafe_now && echo SAFE || echo WAIT\n", tmp_path)
    assert r.stdout.strip() == ("SAFE" if safe else "WAIT"), r.stderr


def test_not_safe_while_the_runner_is_inside_a_cycle(tmp_path: Path) -> None:
    _forward_log(tmp_path, BUSY)
    r = _bash(_clock("12:30") + "FORWARD=yes\nsafe_now && echo SAFE || echo WAIT\n", tmp_path)
    assert r.stdout.strip() == "WAIT"
    # a change that does not touch the runner does not wait for it
    r = _bash(_clock("12:30") + "FORWARD=no\nsafe_now && echo SAFE || echo WAIT\n", tmp_path)
    assert r.stdout.strip() == "SAFE"


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ("docs/deploy-vps.md\nREADME.md", "no no no"),
        ("scripts/research/mce/messages.py", "yes no no"),
        ("scripts/research/mce/recorder.py", "yes yes no"),
        ("requirements.txt", "yes yes no"),
        ("deploy/systemd/signal-forward.service", "yes yes yes"),
        ("deploy/deploy.sh\nfreqtrade/main.py", "no no no"),
        ("scripts/research/watchdog.py\nscripts/research/deploy_preflight.py", "no no no"),
        ("scripts/research/watchdog.py\nscripts/research/mce/messages.py", "yes no no"),
        ("deploy/systemd/signal-watchdog.timer", "yes yes yes"),
    ],
)
def test_services_restarted_by_a_change(tmp_path: Path, changed: str, expected: str) -> None:
    r = _bash(f"plan_services $'{changed}'\necho $FORWARD $RECORDER $UNITS\n", tmp_path)
    assert r.stdout.strip() == expected, r.stderr
