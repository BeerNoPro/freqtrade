"""
Watchdog of the bot on the VPS (deploy/systemd/signal-watchdog.timer, every 10 minutes). It
checks what the runner cannot report about itself and sends a Telegram message when something is
wrong, again every 6 hours while it lasts, and once when it is fixed.

- runner: service active and its last end-of-cycle ``product:`` line at most 4h20m old;
- recorder: service active and its heartbeat at most 15 minutes old;
- disk: under 1 GB free (with the days left at the recorder's ~30 MB a day);
- memory: under 40 MB available while the swap is over 90 % used;
- daily jobs: the runner's store sync succeeded today by 03:00 UTC (yesterday before that), the
  latest audit without critical issues, the backup, offload and audit services not failed.

Standard library only and run by the system ``python3``: it keeps working when the bot's venv
or the runner is broken. Telegram token and chat id: the repository ``.env`` (as the runner).

    python3 scripts/research/watchdog.py [--dry-run]   # --dry-run prints instead of sending
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


APP = Path(__file__).resolve().parents[2]
RUNNER_MAX_AGE = timedelta(hours=4, minutes=20)  # one 4h cycle plus margin
BEAT_MAX_AGE = timedelta(minutes=15)
DISK_MIN_GB = 1.0
RECORDER_GB_PER_DAY = 0.03
MEM_MIN_MB = 40
SWAP_MAX_USED = 0.90
SYNC_DUE = timedelta(hours=3)  # store sync starts 01:30 UTC
RESEND = timedelta(hours=6)
DAILY_UNITS = ("signal-backup.service", "signal-offload.service", "signal-audit.service")
LOG_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
PREFIX = "CẢNH BÁO VẬN HÀNH (VPS) · "
RESOLVED = "ĐÃ ỔN LẠI (VPS) · "


@dataclass
class Problem:
    key: str
    text: str


@dataclass
class Machine:
    """What the checks read; faked in tests."""

    app: Path
    now: datetime
    unit_state: Callable[[str], str]  # systemctl --user is-active / is-failed output
    disk_free_gb: Callable[[], float]
    meminfo: Callable[[], dict[str, int]]  # /proc/meminfo in kB


def _log_time(line: str) -> datetime | None:
    m = LOG_TIME.match(line)
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC) if m else None


def _last_line(path: Path, needle: str) -> str | None:
    if not path.exists():
        return None
    found = None
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if needle in line:
                found = line.rstrip("\n")
    return found


def _since(now: datetime, t: datetime) -> str:
    minutes = int((now - t).total_seconds() // 60)
    return f"{minutes // 60}h{minutes % 60:02d}"


def check_runner(m: Machine) -> list[Problem]:
    state = m.unit_state("is-active signal-forward.service")
    if state != "active":
        return [
            Problem(
                "runner",
                f"runner không chạy (systemd: {state}). Xem journalctl --user -u signal-forward",
            )
        ]
    line = _last_line(m.app / "user_data/logs/forward.log", " INFO product: ")
    t = _log_time(line) if line else None
    if t is None:
        return [Problem("runner_cycle", "runner chưa ghi chu kỳ nào vào forward.log")]
    if m.now - t > RUNNER_MAX_AGE:
        text = f"runner không xong chu kỳ nào từ {t:%Y-%m-%d %H:%M} UTC ({_since(m.now, t)} trước)"
        return [Problem("runner_cycle", text)]
    return []


def check_recorder(m: Machine) -> list[Problem]:
    state = m.unit_state("is-active signal-recorder.service")
    if state != "active":
        return [Problem("recorder", f"bộ ghi không chạy (systemd: {state})")]
    beat = m.app / "user_data/research/live/.heartbeat"
    text = beat.read_text(encoding="utf-8").strip() if beat.exists() else ""
    if not text.isdigit():
        return [Problem("recorder_beat", "bộ ghi không có nhịp tim")]
    t = datetime.fromtimestamp(int(text) / 1000, UTC)
    if m.now - t > BEAT_MAX_AGE:
        return [
            Problem("recorder_beat", f"bộ ghi im lặng từ {t:%H:%M} UTC ({_since(m.now, t)} trước)")
        ]
    return []


def check_disk(m: Machine) -> list[Problem]:
    free = m.disk_free_gb()
    if free >= DISK_MIN_GB:
        return []
    days = int(free / RECORDER_GB_PER_DAY)
    text = (
        f"ổ đĩa còn {free:.2f} GB, khoảng {days} ngày nữa đầy (bộ ghi ~30 MB/ngày). Bật đẩy Google "
        "Drive hoặc dọn dữ liệu cũ (docs/deploy-vps.md mục 5)"
    )
    return [Problem("disk", text)]


def check_memory(m: Machine) -> list[Problem]:
    info = m.meminfo()
    avail = info.get("MemAvailable", 0) / 1024
    swap_total = info.get("SwapTotal", 0)
    swap_used = (swap_total - info.get("SwapFree", 0)) / swap_total if swap_total else 1.0
    if avail < MEM_MIN_MB and swap_used > SWAP_MAX_USED:
        text = f"RAM gần cạn: còn {avail:.0f} MB, swap dùng {swap_used:.0%}"
        return [Problem("memory", text)]
    return []


def check_daily(m: Machine) -> list[Problem]:
    out: list[Problem] = []
    # the runner writes the UTC day of its last successful store sync (01:30 UTC) to this file
    marker = m.app / "user_data/research/forward/last_full_sync"
    done = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    midnight = m.now.replace(hour=0, minute=0, second=0, microsecond=0)
    expected = midnight if m.now - midnight >= SYNC_DUE else midnight - timedelta(days=1)
    if done < f"{expected:%Y-%m-%d}":
        text = (
            f"đồng bộ kho hằng ngày (01:30 UTC) chưa thành công từ {done or 'trước giờ'}: xem "
            "dòng 'full store sync' trong forward.log"
        )
        out.append(Problem("sync", text))
    report = _last_line(m.app / "user_data/logs/audit.log", " INFO report ")
    critical = re.search(r"(\d+) critical", report or "")
    if report and critical and int(critical.group(1)) > 0:
        text = (
            f"kiểm toán kho gần nhất ({report[:10]}) có {critical.group(1)} lỗi critical "
            "(user_data/reports/audit/latest/)"
        )
        out.append(Problem("audit", text))
    for unit in DAILY_UNITS:
        if m.unit_state(f"is-failed {unit}") == "failed":
            name = unit.removesuffix(".service")
            text = f"job {name} lỗi. Xem journalctl --user -u {name}"
            out.append(Problem(f"failed:{name}", text))
    return out


CHECKS = (check_runner, check_recorder, check_disk, check_memory, check_daily)


def problems(m: Machine) -> list[Problem]:
    found: list[Problem] = []
    for check in CHECKS:
        try:
            found += check(m)
        except Exception as e:  # a check that breaks is itself a problem worth a message
            found.append(
                Problem(f"check:{check.__name__}", f"watchdog {check.__name__} lỗi: {e!r}")
            )
    return found


def notify(found: list[Problem], state: dict, now: datetime, send: Callable[[str], None]) -> dict:
    """Send the new problems, the ones still there after ``RESEND``, and the resolved ones; a
    message that fails to send is tried again on the next run. Returns the new state."""
    new_state = dict(state)
    for p in found:
        seen = state.get(p.key)
        if seen and now - datetime.fromisoformat(seen["sent"]) < RESEND:
            continue
        text = PREFIX + p.text
        if seen:
            text += f" (từ {datetime.fromisoformat(seen['since']):%Y-%m-%d %H:%M} UTC)"
        try:
            send(text)
        except Exception as e:
            print(f"send failed: {e}", file=sys.stderr)
            continue
        since = seen["since"] if seen else now.isoformat(timespec="seconds")
        new_state[p.key] = {
            "since": since,
            "sent": now.isoformat(timespec="seconds"),
            "text": p.text,
        }
    current = {p.key for p in found}
    for key, seen in state.items():
        if key in current:
            continue
        try:
            send(RESOLVED + seen["text"])
        except Exception as e:
            print(f"send failed: {e}", file=sys.stderr)
            continue
        new_state.pop(key, None)
    return new_state


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        out[key.strip()] = value.strip().strip("'\"")
    return out


def credentials(env_file: Path, environ: dict[str, str] | None = None) -> tuple[str, str] | None:
    values = {**read_env(env_file), **(environ if environ is not None else os.environ)}
    token = values.get("FREQTRADE__TELEGRAM__TOKEN") or values.get("TELEGRAM_BOT_TOKEN") or ""
    admins = values.get("TELEGRAM_ADMIN_CHAT_IDS", "").replace(",", " ").split()
    chat = (
        values.get("FREQTRADE__TELEGRAM__CHAT_ID")
        or values.get("TELEGRAM_CHAT_ID")
        or (admins[0] if admins else "")
    )
    return (token, chat) if token and chat else None


def telegram_sender(token: str, chat: str) -> Callable[[str], None]:
    def send(text: str) -> None:
        body = json.dumps({"chat_id": chat, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (fixed https URL)
                resp.read()
        except urllib.error.URLError as e:  # never let the token reach a log
            raise RuntimeError(str(e).replace(token, "***")) from None

    return send


def _systemctl(args: str) -> str:
    done = subprocess.run(
        ["systemctl", "--user", *args.split()], capture_output=True, text=True, check=False
    )
    return done.stdout.strip() or done.stderr.strip()


def _meminfo() -> dict[str, int]:
    out = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        name, value = line.split(":", 1)
        out[name] = int(value.split()[0])
    return out


def _now() -> datetime:
    return datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watchdog of the bot on the VPS")
    parser.add_argument("--dry-run", action="store_true", help="print the messages, send none")
    parser.add_argument("--app", type=Path, default=APP, help="bot checkout (default: this repo)")
    args = parser.parse_args(argv)
    app = args.app
    machine = Machine(
        app=app,
        now=_now(),
        unit_state=_systemctl,
        disk_free_gb=lambda: shutil.disk_usage(app).free / 1e9,
        meminfo=_meminfo,
    )
    state_path = app / "user_data/logs/watchdog_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    found = problems(machine)
    if args.dry_run:
        for p in found:
            print(PREFIX + p.text)
        print(f"{len(found)} problem(s)")
        return 0
    creds = credentials(app / ".env")
    if creds is None:
        print("no Telegram token / chat id in .env: nothing sent", file=sys.stderr)
        return 1
    new_state = notify(found, state, machine.now, telegram_sender(*creds))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
