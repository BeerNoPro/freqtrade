"""VPS watchdog: each check finds its problem and stays silent on a healthy server; a problem
is sent once, again after 6 hours, and a message says when it is fixed."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import watchdog as wd


NOW = datetime(2026, 10, 17, 16, 30, tzinfo=UTC)
HEALTHY_MEM = {"MemAvailable": 200_000, "SwapTotal": 1_000_000, "SwapFree": 800_000}


def _server(tmp_path: Path, product_at: str = "2026-10-17 16:03:00", beat_age_min: float = 1.0):
    logs = tmp_path / "user_data" / "logs"
    live = tmp_path / "user_data" / "research" / "live"
    logs.mkdir(parents=True)
    live.mkdir(parents=True)
    (logs / "forward.log").write_text(
        "2026-10-17 01:42:13,070 INFO full store sync exit 0: klines_1d +131 rows\n"
        "2026-10-17 16:02:01,000 INFO store synced: 0 daily candles, 20 coins at 4h\n"
        f"{product_at},464 INFO product: {{'allocated': 0}}, 0 alerts, delivery {{}}\n",
        encoding="utf-8",
    )
    (logs / "audit.log").write_text(
        "2026-10-17 03:02:27,899 INFO report /x/pit-20261017: 1028 issues, 0 critical\n",
        encoding="utf-8",
    )
    beat = NOW - timedelta(minutes=beat_age_min)
    (live / ".heartbeat").write_text(str(int(beat.timestamp() * 1000)), encoding="utf-8")
    forward = tmp_path / "user_data" / "research" / "forward"
    forward.mkdir(parents=True)
    (forward / "last_full_sync").write_text("2026-10-17", encoding="utf-8")
    return tmp_path


def _machine(app: Path, states: dict[str, str] | None = None, free_gb: float = 3.9, mem=None):
    states = states or {}

    def unit_state(args: str) -> str:
        if args.startswith("is-failed"):
            return states.get(args, "active")
        return states.get(args, "active")

    return wd.Machine(app, NOW, unit_state, lambda: free_gb, lambda: mem or HEALTHY_MEM)


def test_a_healthy_server_has_no_problem(tmp_path: Path) -> None:
    assert wd.problems(_machine(_server(tmp_path))) == []


def test_runner_stopped_or_stuck(tmp_path: Path) -> None:
    app = _server(tmp_path, product_at="2026-10-17 12:01:00")
    stuck = wd.problems(_machine(app))
    assert [p.key for p in stuck] == ["runner_cycle"]
    assert "từ 2026-10-17 12:01 UTC (4h29 trước)" in stuck[0].text
    down = wd.problems(_machine(app, {"is-active signal-forward.service": "failed"}))
    assert down[0].key == "runner" and "systemd: failed" in down[0].text


def test_recorder_silent_or_stopped(tmp_path: Path) -> None:
    app = _server(tmp_path, beat_age_min=40)
    assert [p.key for p in wd.problems(_machine(app))] == ["recorder_beat"]
    stopped = _machine(app, {"is-active signal-recorder.service": "inactive"})
    assert [p.key for p in wd.problems(stopped)] == ["recorder"]


def test_disk_and_memory(tmp_path: Path) -> None:
    app = _server(tmp_path)
    disk = wd.problems(_machine(app, free_gb=0.6))
    assert [p.key for p in disk] == ["disk"]
    assert "còn 0.60 GB, khoảng 20 ngày nữa đầy" in disk[0].text
    tight = {"MemAvailable": 20_000, "SwapTotal": 1_000_000, "SwapFree": 50_000}
    assert [p.key for p in wd.problems(_machine(app, mem=tight))] == ["memory"]
    # low RAM alone is normal on a 512 MB droplet while the swap has room
    low_ram = {"MemAvailable": 20_000, "SwapTotal": 1_000_000, "SwapFree": 700_000}
    assert wd.problems(_machine(app, mem=low_ram)) == []


def test_daily_jobs(tmp_path: Path) -> None:
    app = _server(tmp_path)
    marker = app / "user_data" / "research" / "forward" / "last_full_sync"
    marker.write_text("2026-10-16", encoding="utf-8")  # today's sync did not succeed
    audit = app / "user_data" / "logs" / "audit.log"
    audit.write_text(audit.read_text().replace("0 critical", "3 critical"), encoding="utf-8")
    m = _machine(app, {"is-failed signal-backup.service": "failed"})
    found = wd.problems(m)
    assert [p.key for p in found] == ["sync", "audit", "failed:signal-backup"]
    assert "chưa thành công từ 2026-10-16" in found[0].text
    assert "gần nhất (2026-10-17) có 3 lỗi critical" in found[1].text


def test_store_sync_is_due_at_03_utc_and_a_failure_stays_across_midnight(tmp_path: Path) -> None:
    app = _server(tmp_path)
    marker = app / "user_data" / "research" / "forward" / "last_full_sync"
    marker.write_text("2026-10-16", encoding="utf-8")

    def at(hour: int, day: int = 17) -> list[str]:
        m = _machine(app)
        m.now = NOW.replace(day=day, hour=hour, minute=0)
        m.unit_state = lambda args: "active"
        (app / "user_data/research/live/.heartbeat").write_text(
            str(int(m.now.timestamp() * 1000)), encoding="utf-8"
        )
        log = app / "user_data" / "logs" / "forward.log"
        log.write_text(f"{m.now:%Y-%m-%d %H:%M:%S},000 INFO product: {{}}\n", encoding="utf-8")
        return [p.key for p in wd.problems(m)]

    assert at(2) == []  # before 03:00 UTC yesterday's sync is enough
    assert at(4) == ["sync"]  # today's is due
    assert at(1, day=18) == ["sync"]  # still missing after midnight: no false "fixed"
    marker.write_text("2026-10-18", encoding="utf-8")
    assert at(4, day=18) == []


def test_a_broken_check_is_itself_reported(tmp_path: Path) -> None:
    def boom() -> float:
        raise OSError("disk gone")

    m = wd.Machine(_server(tmp_path), NOW, lambda a: "active", boom, lambda: HEALTHY_MEM)
    found = wd.problems(m)
    assert [p.key for p in found] == ["check:check_disk"] and "disk gone" in found[0].text


def test_notify_once_resend_after_6h_and_resolved() -> None:
    sent: list[str] = []
    p = wd.Problem("disk", "ổ đĩa còn 0.60 GB")
    s1 = wd.notify([p], {}, NOW, sent.append)
    assert sent == [wd.PREFIX + "ổ đĩa còn 0.60 GB"]
    s2 = wd.notify([p], s1, NOW + timedelta(hours=1), sent.append)
    assert len(sent) == 1 and s2 == s1  # no repeat within 6 hours
    s3 = wd.notify([p], s2, NOW + timedelta(hours=6), sent.append)
    assert sent[-1].endswith("(từ 2026-10-17 16:30 UTC)") and len(sent) == 2
    s4 = wd.notify([], s3, NOW + timedelta(hours=7), sent.append)
    assert sent[-1] == wd.RESOLVED + "ổ đĩa còn 0.60 GB" and s4 == {}


def test_a_failed_send_is_retried_next_run() -> None:
    def down(text: str) -> None:
        raise RuntimeError("telegram unreachable")

    p = wd.Problem("runner", "runner không chạy")
    assert wd.notify([p], {}, NOW, down) == {}
    sent: list[str] = []
    assert "runner" in wd.notify([p], {}, NOW, sent.append) and len(sent) == 1


def test_credentials_from_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        'TELEGRAM_BOT_TOKEN="123:abc"\nTELEGRAM_ADMIN_CHAT_IDS=42, 43\n', encoding="utf-8"
    )
    assert wd.credentials(env, environ={}) == ("123:abc", "42")
    assert wd.credentials(tmp_path / "none.env", environ={}) is None


def test_send_one_message_for_deploy_sh(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=1:x\nTELEGRAM_CHAT_ID=7\n", encoding="utf-8")
    sent: list[tuple[str, str, str]] = []

    def sender(token: str, chat: str):
        return lambda text: sent.append((token, chat, text))

    monkeypatch.setattr(wd, "telegram_sender", sender)
    assert wd.main(["--app", str(tmp_path), "--send", "DEPLOY OK · 2026-10-17"]) == 0
    assert sent == [("1:x", "7", "DEPLOY OK · 2026-10-17")]
    (tmp_path / ".env").unlink()
    assert wd.main(["--app", str(tmp_path), "--send", "x"]) == 1  # no token: nothing sent


def test_dry_run_prints_and_sends_nothing(tmp_path: Path, capsys, monkeypatch) -> None:
    app = _server(tmp_path, product_at="2026-10-17 10:00:00")
    monkeypatch.setattr(wd, "APP", app)
    monkeypatch.setattr(wd, "_now", lambda: NOW)
    monkeypatch.setattr(wd, "_systemctl", lambda args: "active")
    monkeypatch.setattr(wd, "_meminfo", lambda: HEALTHY_MEM)
    assert wd.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "runner không xong chu kỳ" in out
    assert not (app / "user_data" / "logs" / "watchdog_state.json").exists()
    assert json.dumps({}) == "{}"
